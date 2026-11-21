"""Convert files with MarkItDown, cleaning running furniture from text PDFs."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, replace


@dataclass
class Line:
    text: str
    top: float
    bottom: float
    x0: float
    x1: float
    size: float = 0
    bold: bool = False
    lead_bold: bool = False
    font: str = ""
    reference_spans: tuple[tuple[int, int, str], ...] = ()
    source_id: str = ""
    page: int = 0


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _page_number(text: str) -> bool:
    return bool(re.fullmatch(r"(?:[-–—]\s*\d+\s*[-–—]|第\s*\d+\s*页|(?:page\s*)?\d+(?:\s*(?:/|of|共)\s*\d+)?(?:\s*页)?)", text.strip(), re.I))


def _lines(page) -> list[Line]:
    lines = []
    # OCR overlays often position each word separately, less than 3pt apart.
    scanned = any(img["width"] * img["height"] > 0.85 * page.width * page.height for img in page.images)
    normal_size = statistics.median(c["size"] for c in page.chars) if page.chars else 0
    for index, row in enumerate(page.extract_text_lines(layout=False, **({"x_tolerance_ratio": 0.10} if scanned else {}))):
        chars = [c for c in row["chars"] if c["text"].strip()]
        if not chars:
            continue
        fonts = [c["fontname"].split("+")[-1].lower() for c in chars]
        lines.append(Line(
            row["text"].strip(), row["top"], row["bottom"], row["x0"], row["x1"],
            statistics.median(c["size"] for c in chars),
            sum("bold" in f for f in fonts) >= 0.85 * len(fonts),
            "bold" in fonts[0], Counter(fonts).most_common(1)[0][0],
            _reference_spans(row["text"].strip(), chars, normal_size),
            f"p{page.page_number}-l{index}", page.page_number,
        ))
    if scanned and lines:
        normal = statistics.median(l.size for l in lines)
        lines = [l for l in lines if not (l.top > page.height * .98 and
                 l.size < normal * .5 and not any(c.isalnum() for c in l.text))]
    return lines


def _reference_spans(text: str, chars: list[dict], normal_size: float) -> tuple:
    """Locate small-font note labels without deleting normal-size body numbers."""
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    compact_text = "".join(text[i] for i in positions)
    glyph_text = "".join(c["text"] for c in chars)
    if compact_text != glyph_text:
        return ()
    spans = []
    offset = 0
    run_start = 0
    small_run = ""
    # Flush each small-font run at the first full-size character or end of line.
    for char in [*chars, {"size": normal_size, "text": ""}]:
        if char["size"] < normal_size * 0.85:
            if not small_run:
                run_start = offset
            small_run += char["text"]
        elif small_run:
            match = re.fullmatch(r"[*†‡]*?([A-Za-z]{1,3}|\d{1,3}|[†‡])", small_run)
            if match:
                start = run_start + match.start(1)
                end = run_start + match.end(1)
                spans.append((positions[start], positions[end - 1] + 1, match[1]))
            elif small_run in ('"', "'", '”'):
                spans.append((positions[run_start], positions[run_start + len(small_run) - 1] + 1, '?'))
            small_run = ""
        offset += len(char["text"])
    return tuple(spans)


def _remove_footnotes(pages: list[tuple[float, list[Line]]]) -> list[tuple[float, list[Line]]]:
    """Remove conventional bottom notes, then their matched small-font labels.

    Layout and OCR can make notes ambiguous. Targeted source review resolves
    remaining note text or corrupted reference marks.
    """
    labels_by_page = {}
    result = []
    marker = re.compile(r"^\[?\(?([A-Za-z]{1,3}|\d{1,3}|[†‡*]+)[\].),:：、]?\s+\S")
    for height, lines in pages:
        if not lines:
            result.append((height, lines))
            continue
        normal_size = statistics.median(line.size for line in lines)
        active = False
        kept = []
        previous = None
        for line in lines:
            small = line.size < normal_size * 0.9
            match = marker.match(line.text) if small and line.top > height * 0.65 else None
            isolated = previous is not None and line.top - previous.bottom > normal_size
            if match and (isolated or active):
                labels_by_page.setdefault(line.page, set()).add(match[1])
                active = True
            if not (active and small):
                kept.append(line)
                active = False
            previous = line
        result.append((height, kept))

    cleaned = []
    for height, lines in result:
        kept = []
        for line in lines:
            text = line.text
            for start, end, label in reversed(line.reference_spans):
                math_context = (bool(re.search(r'[=∫∑√]', text))
                                or bool(re.search(r'(?:^|\s)[A-Za-z]$', text[:start])))
                if label in labels_by_page.get(line.page, set()) and not math_context:
                    text = text[:start] + text[end:]
            if text.strip():
                kept.append(replace(line, text=text.strip()))
        cleaned.append((height, kept))
    return cleaned


def _margin_key(line: Line, height: float):
    if (_starts_block(line.text) or re.search(r"[。！？;；]$", line.text)
            or (line.bold and not re.search(r"\b(?:Ch\.|Sec\.|Part|Chapter)\b", line.text))):
        return None
    if line.top < height * 0.16:
        side, distance = "top", line.top
    elif line.bottom > height * 0.88:
        side, distance = "bottom", height - line.bottom
    else:
        return None
    # The same running header can have a different section, page or date on each page.
    pattern = _key(line.text)
    if not line.bold and not _starts_block(line.text):
        chinese_locator = re.match(
            r"^(?:\d+(?:[-–]\d+)?\s+)?第\s*\d+\s*(?:章|部|節|节|條|条)(?:\s|$)", pattern
        )
        if side == "bottom" or chinese_locator or re.match(r"(?:part|chapter|section|page|vol(?:ume)?)\s+\d", pattern):
            pattern = re.sub(r"\d+", "#", pattern)
    return side, round(distance / 4), round(line.x0 / 6), line.font, pattern


def _remove_furniture(pages: list[tuple[float, list[Line]]]) -> list[list[Line]]:
    counts = Counter()
    # Running book titles can alternate on facing pages and move slightly in scans.
    # Confirm the changing folio against the physical page index before suppressing it.
    folios = {}
    for i, (height, lines) in enumerate(pages):
        if not lines or lines[0].top > height * .09:
            continue
        text = lines[0].text.strip()
        match = re.search(r"^(\d+)\s+|\s+(\d+)$", text)
        if match:
            label = re.sub(r"\s+", "", text[:match.start()] + text[match.end():]).casefold()
            if len(label) > 4:
                folios[i] = (label, int(match.group(1) or match.group(2)) - i)
    recurring = Counter(folios.values())
    for height, lines in pages:
        counts.update({_margin_key(line, height) for line in lines} - {None})

    cleaned: list[list[Line]] = []
    for i, (height, lines) in enumerate(pages):
        keep = []
        for line in lines:
            key = _margin_key(line, height)
            repeated = key is not None and counts[key] >= 2
            numbered = key is not None and key[0] == "bottom" and _page_number(line.text)
            folio = line is lines[0] and i in folios and recurring[folios[i]] >= 2
            if not (repeated or numbered or folio):
                keep.append(line)
        cleaned.append(keep)
    return cleaned


def _starts_block(text: str) -> bool:
    if re.match(r"^\*(?:\s*\*){2}", text):
        return False
    return bool(re.match(r"^(?:[#>•*]\s|[-–]\s|\d+[.)、]\s|\[\d+\]\s|\((?:\d+[a-z]?|[A-Za-z]|[ivxlcdm]+)\)\s|\|)", text))


def _line_separator(previous: Line, current: Line, typical_gap: float, body_x: float, right_edge: float) -> str:
    gap = current.top - previous.top
    if any(re.fullmatch(r"(?:\*\s*){3,}", l.text) for l in (previous, current)):
        return "\n\n"
    if gap > typical_gap * 1.25 + 0.5:
        return "\n\n"
    # A short introduction can precede an inset quotation even when both
    # begin at the same horizontal position. Look for extra vertical space.
    if (gap > typical_gap * 1.17 and previous.x1 < right_edge - 100
            and abs(previous.x0 - current.x0) < 2 and not previous.text.endswith("-")):
        return "\n\n"
    # Independently centered Latin caption / citation lines in a title block.
    if (abs(previous.x0 + previous.x1 - current.x0 - current.x1) < 6
            and min(previous.x0, current.x0) > body_x + 26
            and max(previous.x1, current.x1) < right_edge - 22
            and re.match(r"^[A-Z]", current.text) and not previous.text.endswith("-")):
        return "\n\n"
    if _starts_block(current.text) or previous.bold != current.bold:
        return "\n\n"
    if current.lead_bold and not previous.lead_bold and current.x0 < previous.x0 - 5:
        return "\n\n"
    # A first-line indent after a short finished line is different from the
    # hanging indent on every continuation line of a numbered clause.
    if (current.x0 > previous.x0 + 9 and current.x0 > body_x + 5
            and previous.x1 < right_edge - 22 and re.search(r"[.!?。！？]$", previous.text)):
        return "\n\n"
    if (re.search(r"[。！？.!?:：；;]$", previous.text)
            and previous.x1 < right_edge - 22
            and re.match(r"^[A-Z\u3400-\u9fff]", current.text)):
        return "\n\n"
    if ("|" in previous.text and "|" in current.text) or (
        re.search(r" {2,}", previous.text) and re.search(r" {2,}", current.text)
    ):
        return "\n"
    return " "


def _page_text(lines: list[Line]) -> str:
    if not lines:
        return ""
    gaps = [b.top - a.top for a, b in zip(lines, lines[1:]) if 0 < b.top - a.top < 40]
    type_size = statistics.median(line.size or line.bottom - line.top for line in lines)
    typical_gap = type_size * 1.2
    if gaps:
        # Use the lower quartile because paragraph breaks increase the baseline gap.
        typical_gap = min(sorted(gaps)[(len(gaps) - 1) // 4], type_size * 1.4)
    body_x = statistics.median(line.x0 for line in lines)
    right_edges = sorted(line.x1 for line in lines)
    right_edge = right_edges[int((len(right_edges) - 1) * 0.75)]
    def block_start(line: Line) -> str:
        if (line.bold or line.lead_bold) and re.match(r"^\d+\.\s", line.text):
            return "## " + line.text
        return line.text

    result = [block_start(lines[0])]
    for previous, current in zip(lines, lines[1:]):
        separator = _line_separator(previous, current, typical_gap, body_x, right_edge)
        if separator == " ":
            result[-1] = _join_text(result[-1], current.text)
        else:
            result.extend((separator, block_start(current)))
    return "".join(result)


def _join_pages(previous: Line, next_lines: list[Line], previous_height: float, next_height: float) -> bool:
    next_line = next_lines[0]
    if any(re.fullmatch(r"(?:\*\s*){3,}", line.text) for line in (previous, next_line)):
        return False
    if previous.bottom < previous_height * 0.72 or next_line.top > next_height * 0.28:
        return False
    if re.search(r"[。！？.!?:：；;]$", previous.text):
        edge = max(line.x1 for line in next_lines)
        start = min(line.x0 for line in next_lines)
        if previous.x1 < edge - max(8, previous.size) or next_line.x0 > start + 4:
            return False
    if _starts_block(next_line.text) or previous.bold != next_line.bold:
        return False
    # Some CJK fonts expose bold only on the Latin section number. A closed
    # parenthetical caption is complete; an open one may run onto the next page.
    if (previous.lead_bold and re.match(r"^\d+\.\s", previous.text)
            and previous.text.endswith((")", "）"))
            and previous.text.count("(") == previous.text.count(")")
            and previous.text.count("（") == previous.text.count("）")):
        return False
    # A centered caption followed by a left-aligned body starts a new block,
    # including when the page break happens between the two.
    if previous.font != next_line.font and ("italic" in previous.font or next_line.lead_bold):
        return False
    return True


def _join_text(left: str, right: str) -> str:
    if left.endswith("\u00ad"):
        return left[:-1] + right
    if left.endswith("-") and re.match(r"^\w", right):
        token = re.search(r"([\w-]+)-$", left)
        if token:
            prefix = token[1]
            tail = re.match(r"\w+", right)[0]
            # Preserve chemical names, single-letter prefixes and numeric ranges.
            if len(prefix) == 1 or tail[0].isdigit() or "-" in prefix:
                return left + right
            if tail[0].islower():
                from wordfreq import zipf_frequency
                compound = prefix + "-" + tail
                joined = prefix + tail
                if zipf_frequency(compound, "en") > zipf_frequency(joined, "en") + 0.4:
                    return left + right
                return left[:-1] + right
    if (re.search(r"[\u3400-\u9fff，。、：；？！「『（《]$", left)
            and re.match(r"^[\u3400-\u9fff，。、：；？！）」』》]", right)):
        return left + right
    return left + " " + right


def _reflow_docx(markdown: str) -> str:
    from text_restore import Line as TextLine, opaque
    all_lines = markdown.splitlines(keepends=True)
    protected = opaque([TextLine(line.rstrip('\r\n')) for line in all_lines])
    blocks = re.split(r"(\n\s*\n)", markdown)
    line_offset = 0
    for index, block in enumerate(blocks):
        # Offsets count physical newlines, including blank lines between blocks.
        first_line = line_offset
        line_offset += block.count('\n')
        if not block or block.isspace():
            continue
        lines = block.splitlines()
        if any(protected[first_line:first_line + len(lines)]):
            continue
        if len(lines) < 2 or any(
            re.match(r"^(?:\s*(?:[#>]|[-+*]\s|\d+[.)]\s|\|)|```|~~~)", line)
            for line in lines
        ):
            continue
        joined = lines[0].strip()
        for line in lines[1:]:
            joined = _join_text(joined, line.strip())
        blocks[index] = joined
    return "".join(blocks)
