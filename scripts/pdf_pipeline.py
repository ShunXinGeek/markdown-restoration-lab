"""Local PDF text flow with explicit provenance and a deletion ledger."""
from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import json
import hashlib
import re
import statistics
import unicodedata
from time import perf_counter

import pdfplumber

import pdf_text as flow
from formulas import is_formula, formula_latex


def canonical(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\s\u00ad-]+", "", text).casefold()


def column_split(page) -> float | None:
    """Require sustained text on both sides of a gutter, not a clause number."""
    gutters = []
    for row in page.extract_text_lines():
        chars = sorted([c for c in row["chars"] if c["text"].strip()], key=lambda c: c["x0"])
        for index in range(1, len(chars)):
            left, right = chars[index - 1], chars[index]
            gap = right["x0"] - left["x1"]
            if (gap > 22 and index >= 12 and len(chars) - index >= 12
                    and left["x1"] - chars[0]["x0"] > page.width * .18
                    and chars[-1]["x1"] - right["x0"] > page.width * .18):
                gutters.append((left["x1"] + right["x0"]) / 2)
    if len(gutters) >= 4 and statistics.pstdev(gutters) < page.width * .04:
        return statistics.median(gutters)
    return None


def extract_page(page) -> dict:
    split = column_split(page)
    if split:
        # Full-width headings form bands. Each band reads left column then right.
        wide = [r for r in page.extract_text_lines() if r["x0"] < split - 30
                and r["x1"] > split + 30 and len(r["text"]) < 100
                and r["bottom"] < page.height * .2
                and any(abs((c["x0"] + c["x1"]) / 2 - split) < 8 for c in r["chars"])]
        cut = max((r["bottom"] for r in wide), default=0)
        lines = flow._lines(page.crop((0, 0, page.width, cut + 1))) if cut else []
        lines += flow._lines(page.crop((0, cut + 1, split, page.height)))
        lines += flow._lines(page.crop((split, cut + 1, page.width, page.height)))
    else:
        lines = flow._lines(page)
    lines = [replace(line, source_id=f"p{page.page_number}-l{i}") for i, line in enumerate(lines)]
    tables = []
    for table in page.find_tables():
        cells = table.extract()
        if len(cells) >= 2 and max(map(len, cells), default=0) >= 2:
            tables.append({"bbox": list(table.bbox), "cells": cells})
    aligned_numeric_rows = 0
    for row in page.extract_text_lines():
        chars = sorted(row['chars'], key=lambda c: c['x0'])
        large_gaps = sum(b['x0'] - a['x1'] > 20 for a,b in zip(chars,chars[1:]))
        if large_gaps >= 2 and len(re.findall(r'\d+',row['text'])) >= 2 and len(row['text']) < 110:
            aligned_numeric_rows += 1
    math = any(is_formula(l.text) for l in lines)
    formula_lines = {}
    for line in lines:
        if is_formula(line.text):
            chars = [c for c in page.chars if line.x0 - 1 <= c["x0"] <= line.x1 + 1
                     and line.top - 2 <= c["top"] <= line.bottom]
            formula_lines[line.source_id] = formula_latex(chars)
    note_rules = [edge['top'] for edge in page.edges
                  if abs(edge.get('bottom', edge['top']) - edge['top']) < 2
                  and page.height * .45 < edge['top'] < page.height * .95
                  and 30 < edge['x1'] - edge['x0'] < page.width * .8
                  and edge['x0'] < page.width * .25]
    return {"page": page.page_number, "width": page.width, "height": page.height,
            "note_separators": note_rules,
            "column_split": split, "lines": lines, "tables": tables, "math": math,
            "table_hint": aligned_numeric_rows >= 3,
            "formula_lines": formula_lines,
            "rotated_glyphs": sum(not c.get('upright', True) or abs(c.get('matrix', (1,0))[1]) > .2 for c in page.chars),
            "scanned": any(i["width"] * i["height"] > .85 * page.width * page.height for i in page.images)}


def _markdown_text(text: str) -> str:
    # Preserve printed paragraph labels rather than creating auto-numbered lists.
    text = re.sub(r"^(\d+)\.\s", lambda m: m[1] + r"\. ", text)
    text = re.sub(r"\*\s*\*\s*\*", lambda _: r"\* \* \*", text)
    text = re.sub(r"(?<!\\)\[(\d+(?:[-,]\d+)*)\]", lambda m: r"\[" + m[1] + r"\]", text)
    # Prose currency must not become a math delimiter. Formula blocks bypass this.
    text = re.sub(r'(?<!\\)\$(?=\d)', r'\\$', text)
    return text


def _heading_level(line: flow.Line, body_size: float, body_x: float) -> int:
    text = line.text
    styled = line.bold or line.lead_bold
    italic = 'italic' in line.font.lower() or 'oblique' in line.font.lower()
    if styled and re.match(r'^\d+\.\s', text):
        return 2
    if styled and re.match(r'^[IVX]+[.)]\s+\w', text):
        return 2
    if styled and re.match(r'^[A-Z][.)]\s+\w', text):
        return 3
    if italic and line.x0 > body_x + 20 and re.match(r'^[ivx]+\.\s+\w', text):
        return 4
    if len(text) < 100:
        if line.size > body_size * 1.18:
            return 1 if line.size > body_size * 1.25 else 2
        if (styled and text.isupper() and len(text) < 45 and line.x0 > body_x + 50
                and line.size >= body_size * .95):
            return 1
        if re.fullmatch(r'Article\s+\d+[A-Z]?', text):
            return 4
    return 0


def _paragraphs(lines: list[flow.Line]) -> list[dict]:
    if not lines:
        return []
    ordinary = [l for l in lines if not l.bold and 'italic' not in l.font.lower()]
    body_x = min(l.x0 for l in (ordinary or lines))
    body_lines = [l for l in lines if not l.bold and 'italic' not in l.font.lower()
                  and l.x0 < body_x + 6]
    size = statistics.median(l.size or l.bottom - l.top for l in (body_lines or lines))
    edge = sorted(l.x1 for l in lines)[int((len(lines) - 1) * .75)]
    gaps = {key: flow._typical_gap(lines, line)
            for key, line in {(round(l.size), l.bold): l for l in lines}.items()}
    groups = [[lines[0]]]
    for previous, current in zip(lines, lines[1:]):
        previous_level = _heading_level(groups[-1][0], size, body_x)
        current_level = _heading_level(current, size, body_x)
        heading_continuation = (previous_level and not current_level
                                and previous.font == current.font
                                and abs(previous.size - current.size) < .8
                                and current.x0 >= groups[-1][0].x0
                                and previous.x1 > edge - 30
                                and not re.search(r'[.!?:;]$', previous.text))
        separator = flow._line_separator(previous, current, gaps[(round(previous.size), previous.bold)], body_x, edge)
        if heading_continuation or (not previous_level and not current_level and separator == " "):
            groups[-1].append(current)
        else:
            groups.append([current])
    result = []
    for group in groups:
        text = group[0].text
        for line in group[1:]:
            text = flow._join_text(text, line.text)
        first = group[0]
        level = _heading_level(first, size, body_x)
        heading = bool(level)
        quote = not heading and all(l.x0 > body_x + 20 and l.size < size * .9 for l in group)
        result.append({"kind": "heading" if heading else "paragraph", "text": text,
                       "markdown": "#" * level + " " + text if heading else ('> ' if quote else '') + _markdown_text(text),
                       "quote": quote,
                       "source_ids": [l.source_id for l in group],
                       "pages": sorted({l.page for l in group})})
    return result


def table_markdown(rows: list[list]) -> str:
    width = max(map(len, rows))
    def cell(value):
        return re.sub(r"\s*\n\s*", " ", str(value or "")).replace("|", r"\|")
    table = [[cell(v) for v in row] + [""] * (width - len(row)) for row in rows]
    out = ["| " + " | ".join(table[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out.extend("| " + " | ".join(row) + " |" for row in table[1:])
    return "\n".join(out)


def _remove_rotated_watermarks(page, ledger: list) -> list:
    # Only repeated overlays with an explicit conventional watermark label qualify.
    candidates = [c for c in page.chars if not c.get("upright", True) or abs(c.get('matrix', (1,0))[1]) > .2]
    groups = []
    for char in candidates:
        if not groups or abs(char['x0'] - groups[-1][-1]['x0']) > char['size'] * 2:
            groups.append([])
        groups[-1].append(char)
    removed = []
    for group in groups:
        text = ''.join(c['text'] for c in group)
        if re.fullmatch(r"\s*(?:DRAFT|CONFIDENTIAL|ARTICLE\s*IN\s*PRESS|SAMPLE|水印|草稿)\s*", text, re.I):
            removed.extend(group)
    if removed:
        for char in removed:
            ledger.append({"page": page.page_number, "reason": "watermark", "text": char["text"],
                           "bbox": [char["x0"], char["top"], char["x1"], char["bottom"]]})
        signatures = {(c['x0'], c['top'], c['text']) for c in removed}
        return page.filter(lambda obj: (obj.get('x0'), obj.get('top'), obj.get('text')) not in signatures)
    return page


def convert_pdf(source: Path, engine: str, review_dir: Path,
                ocr_language: str = 'eng+chi_sim', ocr: bool = True) -> dict:
    started = perf_counter()
    timings = {}
    review_dir.mkdir(parents=True, exist_ok=True)
    ledger = []
    with pdfplumber.open(source) as pdf:
        pages = [extract_page(_remove_rotated_watermarks(p, ledger)) for p in pdf.pages]
    timings['extract_text'] = perf_counter() - started
    stage = perf_counter()
    from local_ocr import recover_pages
    issues = recover_pages(source, pages, review_dir, ocr_language, ocr)
    timings['ocr'] = perf_counter() - stage
    stage = perf_counter()
    pairs = [(p["height"], p["lines"]) for p in pages]
    table_ids = {l.source_id for p in pages for table in p['tables'] for l in p['lines']
                 if table['bbox'][0] - 2 <= l.x0 <= table['bbox'][2]
                 and table['bbox'][1] - 2 <= l.top <= table['bbox'][3]}
    without_notes = flow._remove_footnotes(pairs,
                    {p['page']: p.get('note_separators', []) for p in pages}, issues, table_ids)
    cleaned = flow._remove_furniture(without_notes)
    timings['classify_notes_and_furniture'] = perf_counter() - stage
    stage = perf_counter()
    blocks = []
    transitions = []
    previous_lines = []
    previous_page = None
    marker_data = {}
    if engine in ("marker", "auto"):
        targets = [p for p in pages
                   if p['status'] == 'text'
                   and (engine == "marker"
                        or bool(p["column_split"] or p["tables"] or p["math"] or p['table_hint']))]
        if targets:
            from marker_adapter import marker_batch_json
            try:
                marker_data = marker_batch_json(source, targets, review_dir)
            except Exception as exc:
                issues.append({"code": "engine_failure", "page": targets[0]["page"], "severity": "warning",
                               "message": f"Marker batch failed; geometry draft requires review: {exc}"})
    timings['layout_engine'] = perf_counter() - stage
    stage = perf_counter()
    for page, (_, note_lines), kept in zip(pages, without_notes, cleaned):
        if page['rotated_glyphs']:
            issues.append({'code': 'rotated_text', 'page': page['page'], 'severity': 'warning',
                           'message': 'Rotated text remains; check for watermark fragments or substantive marginal text.'})
        before = {l.source_id: l for l in page["lines"]}
        note_ids = {l.source_id for l in note_lines}
        kept_ids = {l.source_id for l in kept}
        for sid, line in before.items():
            if sid not in kept_ids:
                ledger.append({"page": page["page"], "source_id": sid, "text": line.text,
                               "bbox": [line.x0, line.top, line.x1, line.bottom],
                               "reason": "footnote" if sid not in note_ids else "running_furniture"})
        for line in kept:
            original = before[line.source_id]
            if original.text != line.text:
                ledger.append({"page": page["page"], "source_id": line.source_id,
                               "reason": "footnote_reference", "before": original.text, "after": line.text})
        page["kept_lines"] = kept
        page["engine"] = "windows-ocr" if page['status'] == 'ocr' else "geometry"
        current = []
        if page['page'] in marker_data:
            from marker_adapter import blocks_from_page
            try:
                current = blocks_from_page(marker_data[page['page']], page)
                page["engine"] = "marker"
            except Exception as exc:
                issues.append({"code": "engine_failure", "page": page["page"], "severity": "warning",
                               "message": f"Marker failed; geometry draft requires review: {exc}"})
        if not current:
            table_lines = set()
            events = []
            for table in page["tables"]:
                x0, top, x1, bottom = table["bbox"]
                members = [l for l in kept if top - 2 <= l.top <= bottom and x0 - 2 <= l.x0 <= x1]
                table_lines.update(l.source_id for l in members)
                events.append((top, {"kind": "table", "text": " ".join(l.text for l in members),
                                     "markdown": table_markdown(table["cells"]),
                                     "cells": table["cells"],
                                     "source_ids": [l.source_id for l in members], "pages": [page["page"]]}))
            prose = [l for l in kept if l.source_id not in table_lines]
            # Never join across a table or across a column reset as one physical line.
            runs = []
            for line in prose:
                if (not runs or line.top < runs[-1][-1].top or
                        any(runs[-1][-1].top < y < line.top for y, _ in events)):
                    runs.append([])
                runs[-1].append(line)
            previous_run = []
            for run in runs:
                paragraphs = _paragraphs(run)
                if (previous_run and paragraphs and events and not page["tables"]
                        and flow._join_pages(previous_run[-1], run, page["height"], page["height"])
                        and events[-1][1]["kind"] == paragraphs[0]["kind"] == "paragraph"):
                    last = events[-1][1]
                    first = paragraphs.pop(0)
                    for field in ("text", "markdown"):
                        last[field] = flow._join_text(last[field], first[field])
                    last["source_ids"] += first["source_ids"]
                    transitions.append({"from_page": page["page"], "to_page": page["page"],
                                        "kind": "column", "joined": True,
                                        "previous_tail": previous_run[-1].text, "next_head": run[0].text})
                for block in paragraphs:
                    first = before[block["source_ids"][0]]
                    events.append((first.top, block))
                previous_run = run
            current = [b for _, b in events] if page["column_split"] else [b for _, b in sorted(events, key=lambda e: e[0])]
            if page["math"]:
                issues.append({"code": "formula_recovery", "page": page["page"], "severity": "warning",
                               "message": "Math symbols detected; geometry output needs formula recovery."})
        for block in current:
            if len(block["source_ids"]) == 1 and block["source_ids"][0] in page["formula_lines"]:
                block["kind"] = "formula"
                block["markdown"] = "$$\n" + page["formula_lines"][block["source_ids"][0]] + "\n$$"
                if re.search(r'[∫∑√]', block['text']):
                    issues.append({'code':'formula_semantics','page':page['page'],'severity':'warning',
                                   'source_ids':block['source_ids'],
                                   'message':'Check operator limits, radical scope and two-dimensional math against the source.'})
            elif block['kind'] == 'formula':
                issues.append({'code':'formula_semantics','page':page['page'],'severity':'warning',
                               'source_ids':block['source_ids'],
                               'message':'Parser math block needs symbol and two-dimensional structure verification.'})
        joined = False
        if previous_page is not None:
            adjacent = page["page"] == previous_page["page"] + 1
            joinable = (adjacent and previous_lines and kept and blocks and current
                        and blocks[-1]["kind"] in ("paragraph", "heading")
                        and bool(blocks[-1].get('quote')) == bool(current[0].get('quote'))
                        and (current[0]["kind"] == blocks[-1]["kind"]
                             or (blocks[-1]["kind"] == "heading" and kept[0].bold)))
            if joinable and flow._join_pages(previous_lines[-1], kept, previous_page["height"], page["height"]):
                last = blocks[-1]
                last["text"] = flow._join_text(last["text"], current[0]["text"])
                next_markdown = current[0]['markdown']
                if last.get('quote') and next_markdown.startswith('> '):
                    next_markdown = next_markdown[2:]
                last["markdown"] = flow._join_text(last["markdown"], next_markdown)
                last["source_ids"] += current[0]["source_ids"]
                last["pages"] = sorted(set(last["pages"] + current[0]["pages"]))
                current = current[1:]
                joined = True
            elif (current and blocks and current[0]["kind"] == blocks[-1]["kind"] == "table"
                  and current[0].get("cells") and blocks[-1].get("cells") and adjacent):
                last_cells, next_cells = blocks[-1]["cells"], current[0]["cells"]
                same_header = canonical(str(last_cells[0])) == canonical(str(next_cells[0]))
                edge_tables = previous_page["tables"] and page["tables"]
                if (same_header and edge_tables and previous_page["tables"][-1]["bbox"][3] > previous_page["height"] * .7
                        and page["tables"][0]["bbox"][1] < page["height"] * .3):
                    first = current.pop(0)
                    last = blocks[-1]
                    last["cells"].extend(first["cells"][1:])
                    last["markdown"] = table_markdown(last["cells"])
                    last["text"] += " " + first["text"]
                    last["source_ids"] += first["source_ids"]
                    last["pages"] = sorted(set(last["pages"] + first["pages"]))
                    ledger.append({"page": page["page"], "reason": "repeated_table_header", "text": str(next_cells[0])})
                    joined = True
            transitions.append({"from_page": previous_page["page"], "to_page": page["page"],
                                "joined": joined, "previous_tail": previous_lines[-1].text if previous_lines else "",
                                "next_head": kept[0].text if kept else ""})
        if not page["lines"] and page['status'] != 'blank':
            issues.append({"code": "empty_text_page", "page": page["page"], "severity": "error",
                           "message": "Nonblank page has no recovered text. Partial output must be identified."})
        blocks.extend(current)
        previous_lines, previous_page = kept, page
    timings['reconstruct'] = perf_counter() - stage
    bundle = {"schema_version": 1, "source": str(source.resolve()),
              "timings_seconds": {k: round(v, 6) for k, v in timings.items()},
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "pages": [], "blocks": blocks,
              "deletions": ledger, "transitions": transitions, "issues": issues}
    for page in pages:
        bundle["pages"].append({**{k: v for k, v in page.items() if k not in ("lines", "kept_lines")},
                                "lines": [asdict(l) for l in page["lines"]],
                                "kept_lines": [asdict(l) for l in page["kept_lines"]]})
    (review_dir / "source.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle
