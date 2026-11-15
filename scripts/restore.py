"""Extract local files, reconstruct Markdown, and account for every PDF page."""

import argparse
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.dom import minidom
from xml.parsers.expat import ExpatError
from zipfile import BadZipFile, ZipFile

from reconstruct import Line, Page, reconstruct, wrap_for_chat


HERE = Path(__file__).resolve().parent
SKILLS = Path.home() / ".codex" / "skills"
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
BATCH_SIZE = 20
SUPPORTED_OCR = {"eng", "chi_sim", "deu", "jpn", "spa"}
TSV_FIELDS = (
    "level", "page_num", "block_num", "par_num", "line_num", "word_num",
    "left", "top", "width", "height", "conf", "text",
)


def converter_directory(explicit: Path | None) -> Path:
    choices = [explicit] if explicit else [HERE.parents[1] / "markdown-converter", SKILLS / "markdown-converter"]
    for folder in choices:
        if (folder / "scripts" / "runtime" / "convert.mjs").is_file():
            return folder.resolve()
    raise ValueError("Local markdown-converter is required for DOCX and scanned PDFs. Set --converter-dir.")


def node_modules(explicit: Path | None) -> Path:
    bundle = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies"
    choices = [explicit] if explicit else [bundle / "node" / "node_modules"]
    for folder in choices:
        if (folder / "tesseract.js" / "package.json").is_file():
            return folder.resolve()
    raise ValueError("Local tesseract.js is required for OCR. Set --node-modules-dir to the bundled packages directory.")


def run_local(command: list[str], retries: int = 0) -> str:
    for _ in range(retries + 1):
        result = subprocess.run(
            command, capture_output=True, encoding="utf-8", errors="replace", timeout=900, check=False
        )
        if result.returncode == 0:
            return result.stdout
    detail = result.stderr.strip()
    raise RuntimeError(f"Local extraction exited with code {result.returncode}. {detail}".strip())


def docx_markdown(source: Path, scratch: Path, args: argparse.Namespace) -> str:
    """Remove typed footnote references from a private copy before Mammoth extraction."""
    clean_docx = scratch / "source-without-footnotes.docx"
    with ZipFile(source) as original, ZipFile(clean_docx, "w") as duplicate:
        for item in original.infolist():
            data = original.read(item)
            if item.filename == "word/document.xml":
                with minidom.parseString(data) as root:
                    for reference in root.getElementsByTagNameNS(WORD, "footnoteReference"):
                        reference.parentNode.removeChild(reference)
                    data = root.toxml(encoding="utf-8")
            duplicate.writestr(item, data)
    runtime = converter_directory(args.converter_dir) / "scripts" / "runtime" / "convert.mjs"
    target = scratch / "word.md"
    run_local([args.node, str(runtime), str(clean_docx), "--to", "markdown", "-o", str(target)])
    return target.read_text(encoding="utf-8")


def table_markdown(rows: list[list[str]]) -> str:
    def format_row(row: list[str]) -> str:
        cells = [cell.replace("|", r"\|").replace("\n", "<br>") for cell in row]
        return "| " + " | ".join(cells) + " |"

    return "\n".join([format_row(rows[0]), format_row(["---"] * len(rows[0]))]
                     + [format_row(row) for row in rows[1:]])


def grid_rows(table, words, pdf) -> list[list[str]]:
    cells = [row.cells for row in table.rows]
    if len(cells) < 2 or len(cells[0]) < 2 or any(None in row for row in cells):
        return []
    selection = [
        (pdf.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2), word)
        for word in words
    ]
    rows = []
    for row in cells:
        values = []
        for cell in row:
            bounds = pdf.Rect(cell)
            lines = {}
            for center, word in selection:
                if bounds.contains(center):
                    lines.setdefault((word[5], word[6]), []).append(word[4])
            values.append("\n".join(" ".join(line) for line in lines.values()))
        rows.append(values)
    # Stroke edges can be detected twice, producing an empty sliver rather than a real column.
    keep = [
        index for index in range(len(cells[0]))
        if any(row[index] for row in rows) or any(pdf.Rect(row[index]).width >= 4 for row in cells)
    ]
    return [[row[index] for index in keep] for row in rows] if len(keep) >= 2 else []


def layout_lines(page, pdf) -> list[Line]:
    flags = pdf.TEXTFLAGS_DICT & ~pdf.TEXT_PRESERVE_IMAGES
    layout = page.get_text("dict", flags=flags, sort=True)
    tables = []
    if page.get_drawings():
        words = page.get_text("words", sort=True)
        for table in page.find_tables().tables:
            rows = grid_rows(table, words, pdf)
            if rows:
                tables.append((pdf.Rect(table.bbox), table_markdown(rows)))
    records = [(bounds, text, None, True) for bounds, text in tables]
    for block in layout["blocks"]:
        for line in block.get("lines", []):
            spans = line["spans"]
            content = "".join(span["text"] for span in spans).rstrip()
            if not content:
                continue
            bounds = pdf.Rect(line["bbox"])
            center = (bounds.tl + bounds.br) / 2
            if any(rect.contains(center) for rect, _ in tables):
                continue
            largest = max(spans, key=lambda span: len(span["text"]))
            records.append((bounds, content, largest["size"], False))
    records.sort(key=lambda item: (round(item[0].y1 / 3), item[0].x0))
    lines = []
    previous_bounds = None
    previous_size = None
    for bounds, content, size, literal in records:
        if lines and previous_size and size:
            if abs(bounds.y1 - previous_bounds.y1) <= max(size, previous_size) * 0.25:
                left = lines[-1].text.strip()
                right = content.strip()
                number = r"(?:[0-9]{1,9}\.|\[[0-9]{1,9}\])"
                if re.fullmatch(number, left):
                    lines[-1].text = left + " " + right
                    continue
                if re.fullmatch(number, right):
                    lines[-1].text = right + " " + left
                    continue
            if bounds.y0 - previous_bounds.y1 > max(size, previous_size) * 0.7:
                lines.append(Line(""))
        if literal or lines and lines[-1].literal:
            lines.append(Line(""))
        lines.append(Line(content, (bounds.y0 - page.rect.y0) / page.rect.height, size, literal))
        previous_bounds, previous_size = bounds, size
    return lines


def lines_from_tsv(tsv: str, height: int) -> list[Line]:
    groups = {}
    for row in csv.DictReader(io.StringIO(tsv), fieldnames=TSV_FIELDS, delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "5" and row["text"] and row["text"].strip():
            key = tuple(row[k] for k in ("block_num", "par_num", "line_num"))
            groups.setdefault(key, []).append(row)
    lines = []
    previous_bottom = None
    for words in groups.values():
        top = min(int(word["top"]) for word in words)
        bottom = max(int(word["top"]) + int(word["height"]) for word in words)
        size = bottom - top
        if previous_bottom is not None and top - previous_bottom > size * 1.1:
            lines.append(Line(""))
        text = " ".join(word["text"] for word in words)
        text = re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", text)
        lines.append(Line(text, top / height, size))
        previous_bottom = bottom
    return lines


def rasterize_for_ocr(page, pdf, scratch: Path, number: int) -> dict | None:
    thumbnail = page.get_pixmap(matrix=pdf.Matrix(0.5, 0.5), colorspace=pdf.csGRAY, alpha=False)
    samples = thumbnail.samples
    if min(samples) >= 246:
        return None
    # Bound raster pixel counts on unusually large pages.
    scale = min(240 / 72, 3200 / max(page.rect.width, page.rect.height))
    raster = page.get_pixmap(matrix=pdf.Matrix(scale, scale), colorspace=pdf.csGRAY, alpha=False)
    image = scratch / f"page-{number}.png"
    raster.save(image)
    return {"number": number, "image": str(image), "height": raster.height}


def read_pdf(source: Path, scratch: Path, args: argparse.Namespace) -> list[Page]:
    try:
        import pymupdf as pdf
    except ImportError as error:
        raise ValueError("PDF extraction requires a local Python with PyMuPDF installed.") from error
    pages = []
    with pdf.open(source) as document:
        if document.needs_pass:
            raise ValueError("The PDF is password protected. Supply a locally unlocked copy.")
        for start in range(0, len(document), BATCH_SIZE):
            scanned = []
            batch = {}
            for index in range(start, min(start + BATCH_SIZE, len(document))):
                number = index + 1
                page = document[index]
                render_image_only = False
                lines = layout_lines(page, pdf)
                text = " ".join(line.text for line in lines)
                letters = sum(char.isalpha() for char in text)
                if letters < 40:
                    render_image_only = any(
                        (pdf.Rect(image["bbox"]) & page.rect).get_area() > 0.45 * page.rect.get_area()
                        for image in page.get_image_info()
                    )
                if text.strip(" \t\n\ufffd\x00") and not render_image_only:
                    batch[number] = Page(number, lines)
                    continue
                rendered = rasterize_for_ocr(page, pdf, scratch, number)
                if rendered is None:
                    batch[number] = Page(number, status="blank")
                else:
                    scanned.append(rendered)
            if scanned:
                manifest = scratch / "ocr-batch.json"
                manifest.write_text(json.dumps(scanned), encoding="utf-8")
                command = [
                    args.node, str(HERE / "ocr_pages.cjs"), str(manifest),
                    str(node_modules(args.node_modules_dir)), str(converter_directory(args.converter_dir)),
                    args.ocr_language,
                ]
                reports = json.loads(run_local(command, retries=1))
                results = {item["number"]: item for item in reports}
                if len(results) != len(reports) or set(results) != {item["number"] for item in scanned}:
                    raise ValueError("Local OCR results do not match the requested page numbers.")
                for item in scanned:
                    number = item["number"]
                    hits = results.get(number, {})
                    lines = lines_from_tsv(hits.get("tsv", ""), item["height"])
                    status = "ocr" if lines else "unreadable"
                    batch[number] = Page(number, lines, status)
                    Path(item["image"]).unlink()
            pages.extend(batch[index + 1] for index in range(start, min(start + BATCH_SIZE, len(document))))
    return pages


def text_pages(text: str, known_pages: bool = False) -> list[Page]:
    sections = text.split("\f")
    if len(sections) > 1 and not sections[-1].strip():
        sections.pop()  # A single final form feed commonly terminates the last OCR page.
    return [
        Page(i + 1, [Line(line) for line in section.splitlines()],
             "text" if section.strip() else "blank", known_pages or "\f" in text)
        for i, section in enumerate(sections)
    ]


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("x", encoding="utf-8", newline="\n")
    try:
        with handle:
            handle.write(text)
    except OSError:
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    delivery = cli.add_mutually_exclusive_group(required=True)
    delivery.add_argument("-o", "--output", type=Path, help="complete UTF-8 .md file")
    delivery.add_argument("--chat", action="store_true", help="wrap short Markdown for copying")
    cli.add_argument("--legal-numbering", action="store_true", help="recognize top-level legal paragraph labels")
    cli.add_argument("--paged-text", action="store_true", help="the plain text represents page(s) with a known bottom")
    cli.add_argument("--ocr-language", default="eng+chi_sim", help="local OCR language or + combination")
    cli.add_argument("--converter-dir", type=Path)
    cli.add_argument("--node-modules-dir", type=Path)
    cli.add_argument("--node", default=os.environ.get("RESTORE_MD_NODE") or shutil.which("node") or "node")
    args = cli.parse_args()
    source = args.source.resolve(strict=True)
    extension = source.suffix.lower()
    if args.output and (args.output.suffix.lower() != ".md" or args.output.exists() or args.output.resolve() == source):
        raise ValueError("Choose a new .md output path separate from the input.")
    if not set(args.ocr_language.split("+")) <= SUPPORTED_OCR:
        raise ValueError("OCR languages: " + ", ".join(sorted(SUPPORTED_OCR)))
    scratch_parent = args.output.resolve().parent if args.output else Path.cwd()
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="restore-md-", dir=scratch_parent) as work:
        scratch = Path(work)
        if extension == ".docx":
            pages = text_pages(docx_markdown(source, scratch, args))
        elif extension == ".pdf":
            pages = read_pdf(source, scratch, args)
        elif extension in {".txt", ".md", ".text"}:
            pages = text_pages(source.read_text(encoding="utf-8-sig"), args.paged_text)
        else:
            raise ValueError("Supported inputs: .docx, .pdf, .txt, .text, .md. Convert a legacy .doc to .docx locally first.")
        markdown = reconstruct(pages, args.legal_numbering, structured=extension in {".docx", ".md"})
    missing = [page.number for page in pages if page.status == "unreadable"]
    result = {
        "status": "partial" if missing else ("complete" if markdown else "empty"),
        "page_status": {page.number: page.status for page in pages} if extension == ".pdf" else None,
        "unreadable_pages": missing,
        "output": None,
    }
    if markdown and args.output:
        target = args.output.with_suffix(".partial.md") if missing else args.output
        write_new(target, markdown)
        result["output"] = str(target.resolve())
    if args.chat and markdown and not missing:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(wrap_for_chat(markdown))
    print(json.dumps(result, ensure_ascii=True), file=sys.stderr)
    return 2 if missing or not markdown else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, BadZipFile, ExpatError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from error
