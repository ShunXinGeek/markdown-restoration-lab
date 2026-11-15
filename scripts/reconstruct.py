"""Layout-aware cleanup and Markdown reconstruction."""

import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import median


@dataclass
class Line:
    text: str
    top: float | None = None
    size: float | None = None
    literal: bool = False


@dataclass
class Page:
    number: int
    lines: list[Line] = field(default_factory=list)
    status: str = "text"
    paged: bool = True


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
DECIMAL = re.compile(r"^([0-9]{1,9})\.(?=[ \t]|$)")
BRACKET = re.compile(r"^\[([0-9]{1,9})\](?![:(\[])(?=\s|$)")
STRUCTURE = re.compile(
    r"^\s*(?:#{1,6}[ \t]+|>|\|(?=.*\|)|[-+*][ \t]+|[0-9]{1,9}[.)][ \t]+|"
    r"[0-9]{1,9}\\\.[ \t]+|\\?\[[0-9]{1,9}\\?\](?:[ \t]|:|$)|"
    r"\[\S+\]:|(?:[0-9]+(?:\.[0-9]+)+|[（(][0-9一二三四五六七八九十]+[）)])[ \t]+|"
    r"第[一二三四五六七八九十百零〇0-9]+[章节条款]|[一二三四五六七八九十]+、)"
)
PAGE_NUMBER = re.compile(
    r"^(?:[0-9]{1,6}|[-–—]\s*[0-9]{1,6}\s*[-–—]|"
    r"(?:page|p\.)\s*[0-9]+(?:\s*(?:of|/)\s*[0-9]+)?|第\s*[0-9]+\s*页)$",
    re.IGNORECASE,
)
DIVIDER = re.compile(r"^(?:_{4,}|-{3,}|[─━—=]{4,})$")
OCR_NOISE = re.compile(r"[\u0000-\u0008\u000b\u000e-\u001f\u007f\ufffd]")
NOTE = re.compile(r"^(?:[¹²³⁰⁴⁵⁶⁷⁸⁹]+|[①-⑳]|\[[0-9]{1,3}\]|[0-9]{1,3}[.)]?)(?:\s+|(?=[\u3400-\u9fff]))\S")
RULED_NOTE = re.compile(r"^(?:[¹²³⁰⁴⁵⁶⁷⁸⁹]+|[①-⑳]|\[[0-9]{1,3}\]|[0-9]{1,3})(?:\s+|(?=[\u3400-\u9fff]))\S")
DEFINITION = re.compile(r"^\[\^([^\]\s]+)\]:")
CJK = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\uac00-\ud7af\u3040-\u30ff]")
ABBREVIATION = re.compile(r"(?:\b(?:No|Nos|Art|Sec|para|Dr|Mr|Ms|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.|(?:\b[A-Z]\.){2,})$", re.I)
END = re.compile(r"[。！？.!?；;][”’」』）》）\])\"']*$")


def opaque(lines: list[Line]) -> list[bool]:
    """Protect fenced or indented code, table rows, and HTML tables or code blocks."""
    masks = []
    fence = None
    html_end = None
    indented_code = False
    for index, line in enumerate(lines):
        match = FENCE.match(line.text)
        if fence:
            masks.append(True)
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            continue
        head = line.text.lstrip().lower()
        if html_end:
            masks.append(True)
            if html_end in head:
                html_end = None
            continue
        if head.startswith(("<table", "<pre")):
            html_end = "</table>" if head.startswith("<table") else "</pre>"
            masks.append(True)
            if html_end in head:
                html_end = None
            continue
        indented = line.text.startswith(("    ", "\t"))
        starts_block = index == 0 or not lines[index - 1].text.strip()
        if (indented and (indented_code or starts_block)) or (indented_code and not head):
            indented_code = True
            masks.append(True)
            continue
        indented_code = False
        if match:
            fence = match[1]
        masks.append(bool(line.literal or match or (head.startswith("|") and "|" in head[1:])))
    return masks


def repeated_margins(pages: list[Page]) -> set[tuple[bool, str]]:
    repeats = Counter()
    for page in pages:
        margin = {
            (line.top < 0.12, line.text.strip())
            for line in page.lines
            if line.top is not None
            and (line.top < 0.12 or line.top > 0.88)
            and not line.literal
            and 3 <= len(line.text.strip()) <= 100
            and not STRUCTURE.match(line.text)
            and not END.search(line.text.strip())
        }
        repeats.update(margin)
    return {key for key, count in repeats.items() if count >= 2}


def clean_pages(pages: list[Page]) -> list[list[str]]:
    margins = repeated_margins(pages)
    masks = iter(opaque([line for page in pages for line in page.lines]))
    prepared = []
    footnote_ids = set()
    for page in pages:
        rows = [(line, next(masks)) for line in page.lines]
        sizes = [
            line.size for line, protected in rows
            if not protected and line.size and 0.12 <= line.top <= 0.76
        ]
        body_size = median(sizes) if sizes else None
        note_block = False
        definition = False
        footer = next(
            (
                i for i in reversed(range(len(rows)))
                if rows[i][0].text.strip()
                and (rows[i][0].top is None or rows[i][0].top < 0.96)
                and (rows[i][0].top is None or (False, rows[i][0].text.strip()) not in margins)
            ),
            None,
        )
        result = []
        for index, (line, protected) in enumerate(rows):
            if definition and (not line.text.strip() or line.text.startswith(("    ", "\t"))):
                continue
            definition = False
            if protected:
                note_block = False
                result.append((line.text, True))
                continue
            text = OCR_NOISE.sub("", line.text)
            value = text.strip()
            if line.top is not None and (line.top < 0.12 or line.top > 0.88):
                if (line.top < 0.12, value) in margins:
                    continue
            if page.paged and PAGE_NUMBER.fullmatch(value):
                if (line.top is not None and line.top >= 0.83) or (line.top is None and index == footer):
                    continue
            if match := DEFINITION.match(value):
                footnote_ids.add(match[1])
                definition = True
                continue
            if DIVIDER.fullmatch(value) and (
                value != "---" or page.paged and index >= len(rows) // 2
            ):
                following = next((row.text.strip() for row, _ in rows[index + 1:] if row.text.strip()), "")
                note_block = page.paged and bool(RULED_NOTE.match(following)) and index >= len(rows) // 2
                if note_block or value != "---":
                    continue
            near_bottom = line.top is not None and line.top >= 0.73
            small = bool(near_bottom and body_size and line.size and line.size <= body_size * 0.83)
            if small and NOTE.match(value):
                note_block = True
            if note_block and (not value or small or (line.top is None and not DECIMAL.match(value))):
                continue
            note_block = False
            result.append((text, False))
        prepared.append(result)
    return [
        [re.sub(r"\[\^([^\]\s]+)\]", lambda m: "" if m[1] in footnote_ids else m[0], text)
         if footnote_ids and not protected else text for text, protected in rows]
        for rows in prepared
    ]


def escape_paragraph_labels(lines: list[str], legal: bool) -> list[str]:
    """Preserve bracket labels and infer discontinuous plain numeric labels."""
    masks = opaque([Line(line) for line in lines])
    groups = []
    group = []
    output = list(lines)
    for index, (line, protected) in enumerate(zip(lines, masks)):
        if protected or (line.strip() and STRUCTURE.match(line) and not DECIMAL.match(line)):
            if group:
                groups.append(group)
                group = []
        if protected:
            continue
        if match := BRACKET.match(line):
            output[index] = rf"\[{match[1]}\]" + line[match.end():]
        if match := DECIMAL.match(line):
            number = int(match[1])
            if group and number == 1 and group[-1][1] != 0:
                groups.append(group)
                group = []
            group.append((index, number, match.end()))
    if group:
        groups.append(group)
    for group in groups:
        consecutive = all(b[1] == a[1] + 1 for a, b in zip(group, group[1:]))
        tight_list = len(group) > 1 and consecutive and group[0][1] == 1 and all(
            b[0] == a[0] + 1 for a, b in zip(group, group[1:])
        )
        if not consecutive or (legal and not tight_list):
            for index, _, end in group:
                output[index] = lines[index][:end - 1] + r"\." + lines[index][end:]
    return output


def join_prose(left: str, right: str) -> str:
    if CJK.fullmatch(left[-1]) and CJK.fullmatch(right[0]):
        return left + right
    if (left[-1] in "，、：；（《「『" or right[0] in "，。！？、；：）》」』"):
        return left + right
    return left + " " + right


def reconstruct(pages: list[Page], legal: bool = False, structured: bool = False) -> str:
    sections = clean_pages(pages)
    lines = []
    for page, section in zip(pages, sections):
        while section and not section[-1].strip():
            section.pop()
        start = next((i for i, line in enumerate(section) if line.strip()), len(section))
        if lines:
            lines.append("\f" if section and page.status != "unreadable" else "")
        lines.extend(section[start:])
    lines = escape_paragraph_labels(lines, legal and not structured)
    masks = opaque([Line(line) for line in lines])
    out = []
    across_page = False
    for index, (line, protected) in enumerate(zip(lines, masks)):
        if line == "\f":
            across_page = not protected
            continue
        if protected or structured:
            if across_page and out and out[-1]:
                out.append("")
            out.append(line)
            across_page = False
            continue
        value = line.strip()
        if not value:
            if out and out[-1]:
                out.append("")
            across_page = False
            continue
        prev = out[-1] if out else ""
        last_protected = index > 0 and masks[index - 1]
        open_sentence = bool(prev and (not END.search(prev) or ABBREVIATION.search(prev)))
        plausible = not across_page or (
            value[0].islower() or value[0].isdigit() or CJK.fullmatch(value[0])
        )
        current_structure = STRUCTURE.match(line)
        literal_number = re.match(r"^(?:[0-9]+\\\.|\\\[[0-9]+\\\])(?:\s|$)", value)
        if open_sentence and not last_protected and not current_structure and plausible:
            if not STRUCTURE.match(prev) or re.match(r"^(?:[0-9]+\\\.|\\\[)", prev):
                out[-1] = join_prose(prev, value)
                across_page = False
                continue
        if prev and (literal_number or across_page or last_protected and not current_structure
                     or open_sentence and not current_structure and not STRUCTURE.match(prev)):
            out.append("")
        elif prev and open_sentence is False and not current_structure and not STRUCTURE.match(prev):
            out.append("")
        out.append(line if current_structure or line.startswith(("    ", "\t")) else value)
        across_page = False
    return "\n".join(out).strip("\n") + "\n" if any(line.strip() for line in out) else ""


def wrap_for_chat(markdown: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", markdown)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}markdown\n{markdown.rstrip(chr(10))}\n{fence}\n"
