"""Bounded local PDF rasterization and Windows OCR, preserving physical page IDs."""
import json
from pathlib import Path
import re
import subprocess

import fitz
from PIL import Image
from pdf_text import Line


def select_lines(result):
    candidates = result.get('candidates', [])
    if not candidates:
        return []
    by_language = {c['language']: c['lines'] for c in candidates}
    rows = by_language.get('zh-Hans-CN', candidates[0]['lines'])
    latin = by_language.get('en-US', [])
    selected = []
    used_latin = set()
    cjk = r'[\u3400-\u9fff]'
    for row in rows:
        x0, y0, x1, y1 = row['bbox']
        overlapping = [(i, other) for i, other in enumerate(latin) if
            min(y1, other['bbox'][3]) - max(y0, other['bbox'][1]) > min(y1-y0, other['bbox'][3]-other['bbox'][1]) * .5
            and min(x1, other['bbox'][2]) > max(x0, other['bbox'][0])]
        if not re.search(cjk, row['text']) and overlapping:
            for i, other in overlapping:
                if i not in used_latin:
                    selected.append(other)
                    used_latin.add(i)
        else:
            if re.search(cjk, row['text']):
                text = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff，。！？、；：])', '', row['text'])
                text = re.sub(r'\s+([，。！？、；：])', r'\1', text)
                row = {**row, 'text': text}
            selected.append(row)
            used_latin.update(i for i, _ in overlapping)
    # English-only regions missed by the Chinese recognizer remain recoverable.
    selected.extend(row for i, row in enumerate(latin) if i not in used_latin)
    return selected


def recover_pages(source: Path, pages: list[dict], review_dir: Path,
                  language: str = 'eng+chi_sim', enabled: bool = True) -> list[dict]:
    issues, pending = [], []
    with fitz.open(source) as document:
        for page in pages:
            page['status'] = 'text' if page['lines'] else 'unreadable'
            if page['lines']:
                continue
            number = page['page']
            raster = document[number - 1]
            scale = min(240 / 72, 3200 / max(raster.rect.width, raster.rect.height))
            pix = raster.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
            image = Image.frombytes('L', (pix.width, pix.height), pix.samples)
            # Only near-white, ink-free pages qualify automatically as blank.
            low, high = image.getextrema()
            if low == high and low >= 250:
                page['status'] = 'blank'
                continue
            if not enabled:
                issues.append({'code': 'ocr_disabled', 'page': number, 'severity': 'error',
                               'message': 'Nonblank page has no text layer and OCR is disabled.'})
                continue
            target = review_dir / f'ocr-p{number}.png'
            pix.save(target)
            pending.append({'number': number, 'image': str(target.resolve()),
                            'width': pix.width, 'height': pix.height})
    # One bounded process per 20-page batch; a failed batch does not discard text pages.
    by_number = {p['page']: p for p in pages}
    for start in range(0, len(pending), 20):
        batch = pending[start:start + 20]
        manifest = review_dir / f'ocr-batch-{start // 20 + 1}.json'
        manifest.write_text(json.dumps(batch), encoding='utf-8')
        try:
            process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-File', str(Path(__file__).with_name('windows_ocr.ps1')),
                '-Manifest', str(manifest.resolve()), '-Language', language],
                capture_output=True, encoding='utf-8-sig', errors='replace', timeout=600)
            (review_dir / f'ocr-batch-{start // 20 + 1}.log').write_text(process.stderr, encoding='utf-8')
            if process.returncode:
                raise RuntimeError(process.stderr.strip()[-1600:])
            results = json.loads(process.stdout)
            if not isinstance(results, list) or {r['number'] for r in results} != {r['number'] for r in batch}:
                raise ValueError('OCR result page IDs do not match the batch.')
            (review_dir / f'ocr-batch-{start // 20 + 1}-result.json').write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as error:
            results = [{'number': item['number'], 'error': str(error)} for item in batch]
        dimensions = {item['number']: item for item in batch}
        for result in results:
            page = by_number[result['number']]
            dims = dimensions[page['page']]
            sx, sy = page['width'] / dims['width'], page['height'] / dims['height']
            for row in select_lines(result):
                if not row['text'].strip():
                    continue
                x0, top, x1, bottom = row['bbox']
                page['lines'].append(Line(row['text'], top * sy, bottom * sy, x0 * sx, x1 * sx,
                    size=(bottom - top) * sy, font='WindowsOCR', page=page['page'],
                    source_id=f"p{page['page']}-ocr{len(page['lines'])}"))
            if page['lines']:
                page['status'] = 'ocr'
                page['ocr_language'] = result['language']
                issues.append({'code': 'ocr_review', 'page': page['page'], 'severity': 'warning',
                    'message': 'OCR text and reading order need source-image review, especially numbers and tables.'})
            else:
                issues.append({'code': 'ocr_failed', 'page': page['page'], 'severity': 'error',
                    'message': result.get('error') or 'Nonblank page produced no OCR text.'})
    return issues
