"""Use Marker's typed layout locally; retain source-line provenance."""
from pathlib import Path
import json
import re
import subprocess
import os
from bs4 import BeautifulSoup
from markdownify import markdownify

def marker_batch_json(source: Path, pages: list[dict], review_dir: Path,
                      batch_size: int = 20) -> dict[int, dict]:
    """Run Marker once per batch; model loading is shared by every page in the batch."""
    python = os.environ.get('MARKITDOWN_MARKER_PYTHON', r'D:\CodexTools\pdf-layout\.venv\Scripts\python.exe')
    worker = str(Path(__file__).with_name('marker_worker.py'))
    review_dir.mkdir(parents=True, exist_ok=True)
    typed_by_page = {}
    for start in range(0, len(pages), batch_size):
        batch = pages[start:start + batch_size]
        numbers = [p['page'] for p in batch]
        command = [python, '-X', 'utf8', worker, str(source.resolve()),
                   ','.join(str(n) for n in numbers), str(review_dir.resolve())]
        process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                 timeout=180 * max(1, len(numbers)))
        (review_dir / f"marker-batch-{numbers[0]}-{numbers[-1]}.log").write_text(
            process.stdout + process.stderr, encoding='utf-8')
        if process.returncode:
            raise RuntimeError(process.stderr[-1200:])
        for number in numbers:
            path = review_dir / f"marker-p{number}.json"
            typed_by_page[number] = json.loads(path.read_text(encoding='utf-8'))
    return typed_by_page


def marker_pages(source: Path, pages: list[dict], review_dir: Path) -> dict[int, list[dict]]:
    typed = marker_batch_json(source, pages, review_dir)
    return {page['page']: blocks_from_page(typed[page['page']], page) for page in pages}


def marker_page(source: Path, page: dict, review_dir: Path) -> list[dict]:
    return marker_pages(source, [page], review_dir)[page['page']]


def blocks_from_page(typed: dict, page: dict) -> list[dict]:
    result = []
    used = set()
    def walk(node):
        kind = node["block_type"].split(".")[-1]
        if kind in ("Footnote", "PageHeader", "PageFooter", "Picture", "Figure", "Diagram"):
            return
        if node.get("children"):
            for child in node["children"]:
                walk(child)
            return
        html = node.get("html", "")
        soup = BeautifulSoup(html, "html.parser")
        for image in soup.find_all("img"):
            image.decompose()
        text = soup.get_text(" ", strip=True)
        if not text:
            return
        x0, top, x1, bottom = node["bbox"]
        members = [l for l in page["kept_lines"] if l.source_id not in used
                   and top - 5 <= (l.top + l.bottom) / 2 <= bottom + 5
                   and x0 - 5 <= (l.x0 + l.x1) / 2 <= x1 + 5]
        # Output only blocks anchored to retained text; deletion ledger stays authoritative.
        if not members:
            return
        ids = [l.source_id for l in members]
        used.update(ids)
        for math in soup.find_all("math"):
            latex = math.get_text()
            fence = "$$" if math.get("display") in ("block", "true") else "$"
            math.replace_with(fence + latex + fence)
        md = markdownify(str(soup), heading_style="ATX").strip()
        table = soup.find("table")
        merged_cells = table and (table.find(attrs={"rowspan": True}) or table.find(attrs={"colspan": True}))
        if merged_cells:
            md = str(table)
        md = re.sub(r"\n(?=[^\n])", " ", md) if kind in ("Text", "Caption", "SectionHeader") else md
        result.append({"kind": "table" if kind == "Table" else "formula" if kind == "Equation"
                       else "heading" if kind == "SectionHeader" else "paragraph",
                       "text": text, "markdown": md, "pages": [page["page"]], "source_ids": ids})
        if table and not merged_cells:
            result[-1]['cells'] = [[cell.get_text(' ', strip=True) for cell in row.find_all(['td','th'])]
                                  for row in table.find_all('tr')]
    for node in typed["children"]:
        walk(node)
    missing = {l.source_id for l in page["kept_lines"]} - used
    if missing:
        raise ValueError(f"Marker did not assign {len(missing)} retained lines. See marker page JSON.")
    from pdf_pipeline import canonical
    retained = canonical(' '.join(l.text for l in page['kept_lines']))
    rendered = canonical(' '.join(b['text'] for b in result))
    # Reject changed reading order or lost text even when all bounding boxes overlap.
    # Formula/table serialization can legitimately change order and is checked separately.
    if not page['tables'] and not page['math'] and retained != rendered:
        raise ValueError('Marker candidate changed the retained prose order or characters; geometry selected.')
    return result
