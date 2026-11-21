"""Remove native Word footnotes; materialize legal labels before Markdown conversion."""
from io import BytesIO
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def number_text(number: int, style: str) -> str:
    if style == 'decimal':
        return str(number)
    if style == 'decimalZero':
        return str(number).zfill(2)
    if style in ('lowerLetter', 'upperLetter'):
        out = ''
        while number > 0:
            number, remainder = divmod(number - 1, 26)
            out = chr(97 + remainder) + out
        return out.upper() if style == 'upperLetter' else out
    if style in ('lowerRoman', 'upperRoman'):
        if not 0 < number < 4000:
            raise ValueError('Roman label is out of range')
        out = ''
        for value, text in [(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),
                            (50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]:
            while number >= value:
                out += text
                number -= value
        return out.lower() if style == 'lowerRoman' else out
    raise ValueError(f'Numbering style requires source review: {style}')


def value(parent, path, default=None):
    node = parent.find(path) if parent is not None else None
    return node.get(W + 'val', default) if node is not None else default


def prepared_docx(source: Path, legal: bool) -> tuple[bytes, dict]:
    report = {'removed_footnote_references': 0, 'materialized_labels': [], 'review_required': []}
    with ZipFile(source) as original:
        parts = {item.filename: original.read(item) for item in original.infolist()}
    root = ET.fromstring(parts['word/document.xml'])
    for parent in root.iter():
        for child in list(parent):
            if child.tag == W + 'footnoteReference':
                parent.remove(child)
                report['removed_footnote_references'] += 1
    # Remove native note bodies from the private package, preserving reserved separators.
    if 'word/footnotes.xml' in parts:
        notes = ET.fromstring(parts['word/footnotes.xml'])
        for node in list(notes):
            if node.get(W + 'type') not in ('separator', 'continuationSeparator'):
                notes.remove(node)
        parts['word/footnotes.xml'] = ET.tostring(notes, encoding='utf-8', xml_declaration=True)
    numbering = ET.fromstring(parts['word/numbering.xml']) if 'word/numbering.xml' in parts else ET.Element('empty')
    abstracts = {n.get(W + 'abstractNumId'): n for n in numbering.findall(W + 'abstractNum')}
    nums = {n.get(W + 'numId'): n for n in numbering.findall(W + 'num')}
    styles_root = ET.fromstring(parts['word/styles.xml']) if 'word/styles.xml' in parts else ET.Element('empty')
    styles = {s.get(W + 'styleId'): s for s in styles_root.findall(W + 'style')}
    counters = {}
    def inherited_numpr(style_id, seen=None):
        seen = set() if seen is None else seen
        if style_id in seen or style_id not in styles:
            return None
        seen.add(style_id)
        style = styles[style_id]
        found = style.find(W + 'pPr/' + W + 'numPr')
        return found if found is not None else inherited_numpr(value(style, W + 'basedOn'), seen)
    for index, paragraph in enumerate(root.iter(W + 'p'), 1):
        prop = paragraph.find(W + 'pPr')
        numpr = prop.find(W + 'numPr') if prop is not None else None
        if numpr is None:
            numpr = inherited_numpr(value(prop, W + 'pStyle'))
        if numpr is None or value(numpr, W + 'numId') == '0':
            continue
        if not legal:
            report['review_required'].append(f'Paragraph {index}: verify visible automatic list/paragraph number.')
            continue
        try:
            num_id = value(numpr, W + 'numId')
            level = int(value(numpr, W + 'ilvl', '0'))
            num = nums[num_id]
            abstract = abstracts[value(num, W + 'abstractNumId')]
            levels = {int(n.get(W + 'ilvl')): n for n in abstract.findall(W + 'lvl')}
            overrides = {int(n.get(W + 'ilvl')): n for n in num.findall(W + 'lvlOverride')}
            def level_node(which):
                replacement = overrides[which].find(W + 'lvl') if which in overrides else None
                return replacement if replacement is not None else levels[which]
            def start_at(which):
                default = value(level_node(which), W + 'start', '1')
                return int(value(overrides.get(which), W + 'startOverride', default))
            spec = level_node(level)
            if value(spec, W + 'numFmt') == 'bullet':
                continue
            state = counters.setdefault(num_id, {})
            state[level] = state.get(level, start_at(level) - 1) + 1
            # Respect lvlRestart=0 (never); otherwise a parent increment resets descendants.
            for child in list(state):
                if child > level:
                    restart = int(value(level_node(child), W + 'lvlRestart', str(child)))
                    if restart and level <= restart - 1:
                        del state[child]
            template = value(spec, W + 'lvlText', f'%{level + 1}.')
            label = re.sub(r'%([1-9])', lambda m: number_text(
                state.get(int(m[1])-1, start_at(int(m[1])-1)),
                'decimal' if spec.find(W + 'isLgl') is not None else
                value(level_node(int(m[1])-1), W + 'numFmt', 'decimal')), template)
            run = ET.Element(W + 'r')
            text = ET.SubElement(run, W + 't')
            text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            text.text = label + ' '
            paragraph.insert(1 if prop is not None else 0, run)
            if prop is None:
                prop = ET.Element(W + 'pPr')
                paragraph.insert(0, prop)
            existing = prop.find(W + 'numPr')
            if existing is not None:
                prop.remove(existing)
            # Explicit numId=0 overrides numbering inherited from a paragraph style.
            disabled = ET.SubElement(prop, W + 'numPr')
            ET.SubElement(disabled, W + 'numId').set(W + 'val', '0')
            report['materialized_labels'].append({'paragraph': index, 'label': label})
        except (KeyError, ValueError, TypeError) as error:
            report['review_required'].append(f'Paragraph {index}: {error}; compare Word/PDF visible label.')
    parts['word/document.xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    target = BytesIO()
    with ZipFile(target, 'w') as out:
        for name, data in parts.items():
            out.writestr(name, data)
    return target.getvalue(), report


def convert_word(source: Path, legal: bool) -> tuple[str, dict]:
    from markitdown import MarkItDown
    from text_restore import Line, Page, reconstruct
    from pdf_text import _reflow_docx
    data, report = prepared_docx(source, legal)
    with tempfile.TemporaryDirectory(prefix='markitdown-word-') as temp:
        clean = Path(temp) / 'without-footnotes.docx'
        clean.write_bytes(data)
        markdown = MarkItDown(enable_plugins=False).convert_local(str(clean)).markdown
    # Keep Word/Markdown structures intact; only normalize prose and confirmed footnotes.
    markdown = reconstruct([Page(1, [Line(line) for line in markdown.splitlines()], paged=False)],
                           legal=legal, structured=True)
    return _reflow_docx(markdown), report
