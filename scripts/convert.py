"""Convert local files; distinguish extraction completeness from source review."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from time import perf_counter


def write_output(target: Path, markdown: str, force: bool):
    markdown = markdown.replace('\r\n', '\n').replace('\r', '\n')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not force:
        with target.open('x', encoding='utf-8', newline='\n') as file:
            file.write(markdown)
        return
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                     suffix='.tmp', delete=False, newline='\n') as file:
        file.write(markdown)
        temporary = Path(file.name)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    started = perf_counter()
    timings = {}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path, nargs='?')
    parser.add_argument('--chat', action='store_true', help='print copyable Markdown; no output file')
    parser.add_argument('--engine', choices=('auto', 'geometry', 'marker'), default='auto')
    parser.add_argument('--review-dir', type=Path)
    parser.add_argument('--ocr-language', default='eng+chi_sim', help='installed Windows language tag or eng/chi_sim alias')
    parser.add_argument('--no-ocr', action='store_true')
    parser.add_argument('--legal-numbering', action='store_true')
    parser.add_argument('--paged-text', action='store_true', help='plain text has known physical page boundaries')
    parser.add_argument('--force', action='store_true', help='replace the chosen output file')
    args = parser.parse_args()
    if bool(args.output) == args.chat:
        parser.error('Choose either an output .md path or --chat')
    source = args.input.resolve(strict=True)
    if args.output:
        if args.output.resolve() == source or args.output.suffix.lower() != '.md':
            parser.error('Choose a .md output path different from the input')
        if args.output.exists() and not args.force:
            raise FileExistsError(f'Output exists: {args.output}. Choose a new path or explicitly use --force.')
    suffix = source.suffix.lower()
    parent = args.output.resolve().parent if args.output else Path.cwd()
    review_dir = args.review_dir or parent / '.markitdown' / source.stem
    if suffix in ('.pdf', '.docx'):
        review_dir.mkdir(parents=True, exist_ok=True)
    result = {'status': 'complete', 'review_status': 'review_required', 'unreadable_pages': []}
    if suffix == '.pdf':
        from pdf_pipeline import convert_pdf
        from audit import audit
        bundle = convert_pdf(source, args.engine, review_dir, args.ocr_language, not args.no_ocr)
        timings.update(bundle.get('timings_seconds', {}))
        markdown = '\n\n'.join(b['markdown'] for b in bundle['blocks']).strip() + '\n'
        stage = perf_counter()
        checked = audit(bundle, markdown, review_dir)
        timings['audit'] = round(perf_counter() - stage, 6)
        result.update(status=checked['extraction_status'], review_status=checked['status'],
                      unreadable_pages=checked['unreadable_pages'], pages=checked['page_count'],
                      page_status={p['page']: p['status'] for p in bundle['pages']})
    elif suffix == '.docx':
        from word_restore import convert_word
        markdown, report = convert_word(source, args.legal_numbering)
        (review_dir / 'docx-review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    elif suffix in ('.txt', '.text', '.md', '.markdown'):
        from text_restore import Line, Page, reconstruct
        text = source.read_text(encoding='utf-8-sig')
        sections = text.split('\f')
        if len(sections) > 1 and not sections[-1].strip():
            sections.pop()
        pages = [Page(i + 1, [Line(line) for line in section.splitlines()],
                      paged=args.paged_text or '\f' in text) for i, section in enumerate(sections)]
        markdown = reconstruct(pages, legal=args.legal_numbering, structured=suffix in ('.md', '.markdown'))
    elif suffix == '.doc':
        raise ValueError('Convert legacy .doc to a temporary .docx with local Word/WPS/LibreOffice first.')
    else:
        from markitdown import MarkItDown
        markdown = MarkItDown(enable_plugins=False).convert_local(str(source)).markdown
    result['review_dir'] = str(review_dir.resolve()) if suffix in ('.pdf', '.docx') else None
    result['output'] = None
    if not markdown.strip():
        result['status'] = 'empty' if result['status'] != 'partial' else 'partial'
    elif args.output:
        target = args.output.with_suffix('.partial.md') if result['status'] == 'partial' else args.output
        if target.resolve() == source:
            raise ValueError('Output must differ from input')
        stage = perf_counter()
        write_output(target, markdown, args.force)
        timings['write_markdown'] = round(perf_counter() - stage, 6)
        result['output'] = str(target.resolve())
    elif args.chat and result['status'] == 'complete':
        from text_restore import wrap_for_chat
        sys.stdout.write(wrap_for_chat(markdown))
    if suffix == '.pdf':
        timings['total'] = round(perf_counter() - started, 6)
        result['timings_seconds'] = timings
        (review_dir / 'timings.json').write_text(json.dumps(timings, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
    return 2 if result['status'] in ('partial', 'empty') or result['review_status'] == 'failed' else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
