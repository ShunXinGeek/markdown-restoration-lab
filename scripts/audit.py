"""Whole-document checks. A passed audit is not a claim of exhaustive proofreading."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from pdf_pipeline import canonical


def plain(text: str) -> str:
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|>\s*)", "", text)
    text = re.sub(r"\\([.*\[\]|])", r"\1", text)
    text = re.sub(r"(?m)^\|(?:\s*:?-+:?\s*\|)+\s*$", "", text)
    return text


def audit(bundle: dict, markdown: str, review_dir: Path) -> dict:
    findings = list(bundle["issues"])
    def add(code, message, page=None, severity="warning", **extra):
        findings.append({"code": code, "message": message, "page": page, "severity": severity, **extra})
    expected = "\n\n".join(b["markdown"] for b in bundle["blocks"])
    source_path = Path(bundle['source'])
    if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != bundle['source_sha256']:
        add('source_changed', 'Source PDF changed since extraction. Reconvert before review.', severity='error')
    if canonical(plain(expected)) != canonical(plain(markdown)):
        add("edited_content", "Output differs from the source-linked draft; verify changed passages.",
            expected_characters=len(canonical(plain(expected))),
            actual_characters=len(canonical(plain(markdown))))
    assigned = Counter(sid for block in bundle["blocks"] for sid in block["source_ids"])
    page_coverage = []
    for page in bundle["pages"]:
        lines = page["kept_lines"]
        missing = [l["source_id"] for l in lines if not assigned[l["source_id"]]]
        duplicate = [l["source_id"] for l in lines if assigned[l["source_id"]] > 1]
        if missing or duplicate:
            add("source_coverage", "Retained lines are missing or assigned more than once.", page["page"],
                "error", missing=missing, duplicate=duplicate)
        page_coverage.append({"page": page["page"], "source_lines": len(page["lines"]),
                              "retained_lines": len(lines), "mapped_lines": len(lines) - len(missing),
                              "engine": page["engine"], "status": page.get('status', 'text'),
                              "ocr_language": page.get('ocr_language')})
        # Short small-font runs may be OCR-corrupted footnote references.
        for line in lines:
            if page.get('status') == 'ocr' and re.search(r'[A-Za-z0-9]\s*[·•]\s*[A-Za-z0-9]|\d\s+\d', line['text']):
                add('ocr_identifier', 'OCR punctuation or spaced digits may alter an identifier/amount; compare source.',
                    page['page'], source_ids=[line['source_id']])
            if any(span[2] == '?' for span in line.get('reference_spans', [])):
                add('uncertain_note_reference', 'Small OCR glyph may be a damaged footnote mark.', page['page'],
                    source_ids=[line['source_id']])
            if re.search(r"\b(?:ofthe|inthe|tothe|andlor|wornen|Vwlation|weal')\b", line["text"]):
                add("ocr_word", "Possible fused or misrecognized word.", page["page"], source_ids=[line["source_id"]])
    seen = {}
    line_index = {l["source_id"]: l for p in bundle["pages"] for l in p["kept_lines"]}
    headings = []
    formulas = 0
    tables = 0
    for index, block in enumerate(bundle["blocks"]):
        md = block["markdown"]
        page = block["pages"][0]
        norm = canonical(block["text"])
        original = " ".join(line_index[sid]["text"] for sid in block["source_ids"] if sid in line_index)
        if block["kind"] in ("paragraph", "heading") and canonical(original) != norm:
            add("text_alignment", "Extracted block differs from its retained source lines.", page,
                block=index, source_ids=block["source_ids"])
        if block["kind"] in ("table", "formula"):
            before_numbers = Counter(re.findall(r"(?<!\w)[-+]?\d+(?:[.,]\d+)*", original))
            after_numbers = Counter(re.findall(r"(?<!\w)[-+]?\d+(?:[.,]\d+)*", plain(md)))
            if before_numbers != after_numbers:
                add("numeric_alignment", "Table/formula numbers differ from source text; inspect cells/symbols.", page, block=index)
        if len(norm) > 100 and norm in seen:
            add("duplicate_passage", f"Passage repeats block {seen[norm]}.", page, block=index)
        seen[norm] = index
        if block["kind"] == "paragraph":
            short_limit = 8 if re.search(r'[\u3400-\u9fff]', md) else 25
            numbered = bool(re.match(r'^\(\w+\)\s', md))
            if len(md) > 6000 or (len(md) < short_limit and not numbered and not re.fullmatch(r"[\\*\s]+", md)):
                add("paragraph_length", "Unusually short or long block; may be a caption or paragraph boundary.", page, block=index)
            if "\n" in md:
                add("hard_wrap", "Physical newlines remain within a prose block.", page, block=index)
        if re.search(r"�|(?:\(cid:\d+\))", md):
            add("broken_encoding", "Replacement characters or unmapped glyphs remain.", page, block=index)
        if re.search(r"(?:\w-\s+\w|\b[a-zA-Z]-[a-zA-Z]{2,}\b)", block["text"]):
            add("hyphen_review", "Check a line-break/compound-word hyphen in this block.", page, block=index)
        if re.search(r"!\[[^\]]*\]\(|<img\b", md):
            add("image_asset", "Unexpected image syntax.", page, "error", block=index)
        heading = re.match(r"^(#{1,6})\s", md)
        if heading:
            level = len(heading[1])
            if headings and level > headings[-1] + 1:
                add("heading_jump", "Heading level skips a parent.", page, block=index)
            headings.append(level)
        if block["kind"] == "table":
            tables += 1
            rows = [r for r in md.splitlines() if r.lstrip().startswith("|")]
            columns = [len(re.split(r"(?<!\\)\|", r)) for r in rows]
            if columns and len(set(columns)) > 1:
                add("table_columns", "Markdown table row widths differ.", page, "error", block=index)
        if block["kind"] == "formula" or "$" in md:
            formulas += 1
            dollars = re.findall(r"(?<!\\)\$", md)
            if len(dollars) % 2:
                add("formula_delimiter", "Unpaired math delimiters.", page, "error", block=index)
            for opening, closing in [("{", "}"), ("(", ")"), ("[", "]")]:
                if md.count(opening) != md.count(closing):
                    add("formula_brackets", "Formula brackets need review.", page, block=index)
            if len(re.findall(r'\\left\b', md)) != len(re.findall(r'\\right\b', md)):
                add('formula_nesting', 'Unpaired LaTeX left/right delimiters.', page, block=index)
    # A deletion is accounted for only if the exact deleted line is absent as a block.
    output_block_texts = {canonical(plain(b)) for b in markdown.split('\n\n')}
    for deletion in bundle["deletions"]:
        text = deletion.get("text", "")
        if len(canonical(text)) > 12 and canonical(text) in output_block_texts:
            add("residual_removed_block", "A removed margin/note block remains in output.", deletion["page"], "error")
    if re.search(r"\[\^[^\]]+\]", markdown):
        add("footnote_syntax", "Footnote links/definitions remain.", severity="error")
    bibliography = re.search(r'(?im)^#{0,6}\s*(?:References|Bibliography|参考文献)\s*$', markdown)
    if bibliography:
        citations = set(re.findall(r'\\?\[(\d+)\\?\]', markdown[:bibliography.start()]))
        entries = set(re.findall(r'(?m)^\s*(?:\[)?(\d+)(?:\\?\.|\])\s', markdown[bibliography.end():]))
        if citations - entries:
            add('unmatched_reference', 'Body citation has no matching bibliography entry in this excerpt.',
                labels=sorted(citations - entries))
    count = len(bundle["pages"])
    sample = sorted({1, count, max(1, (count + 1) // 2)})
    for feature in ("tables", "math", "column_split", "scanned"):
        selected = next((p["page"] for p in bundle["pages"] if p.get(feature)), None)
        if selected:
            sample.append(selected)
    review_file = review_dir / "review.json"
    review = json.loads(review_file.read_text(encoding="utf-8")) if review_file.exists() else {}
    fingerprint = hashlib.sha256(markdown.encode()).hexdigest()
    verified = (review.get("output_sha256") == fingerprint
                and review.get("source_sha256") == bundle["source_sha256"]
                and {1, count}.issubset(set(review.get('sample_pages', [])))
                and isinstance(review.get('targeted_locations'), list))
    for i, finding in enumerate(findings):
        finding["id"] = f"{finding['code']}:{finding.get('page')}:{finding.get('block', '')}:{i}"
        finding["resolved"] = (verified and finding['severity'] != 'error'
                               and bool(review.get("resolved_findings", {}).get(finding["id"])))
    pending = [f for f in findings if not f["resolved"]]
    errors = [f for f in pending if f["severity"] == "error"]
    result = {"schema_version": 1, "source": bundle["source"], "output_sha256": fingerprint,
              "status": ("failed" if errors else "review_required" if pending or not verified
                         else "reviewed_with_limitations" if review.get('unresolved') else "reviewed"),
              "page_count": count, "page_coverage": page_coverage, "block_count": len(bundle["blocks"]),
              "extraction_status": 'partial' if any(p.get('status') == 'unreadable' for p in bundle['pages']) else 'complete',
              "unreadable_pages": [p['page'] for p in bundle['pages'] if p.get('status') == 'unreadable'],
              "table_count": tables, "formula_blocks": formulas, "findings": findings,
              "transitions": bundle["transitions"], "deletions": dict(Counter(d["reason"] for d in bundle["deletions"])),
              "sample_pages": sorted(set(sample)), "review": review if verified else {},
              "limitations": ["Checks are candidate detection, not proof of semantic fidelity.",
                              "Source-image review covers logged locations and samples, not every page."]}
    (review_dir / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("review_dir", type=Path)
    parser.add_argument("markdown", type=Path)
    args = parser.parse_args()
    bundle = json.loads((args.review_dir / "source.json").read_text(encoding="utf-8"))
    result = audit(bundle, args.markdown.read_text(encoding="utf-8"), args.review_dir)
    print(json.dumps({k: result[k] for k in ("status", "page_count", "block_count", "deletions")}, ensure_ascii=False))
    raise SystemExit(2 if result["status"] == "failed" else 0)
