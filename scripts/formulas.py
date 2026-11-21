"""Conservative text-layer math reconstruction, retaining a source-audit warning."""
import re
import statistics


SYMBOLS = {'α': r'\alpha ', 'β': r'\beta ', 'γ': r'\gamma ', 'δ': r'\delta ',
           'θ': r'\theta ', 'λ': r'\lambda ', 'μ': r'\mu ', 'π': r'\pi ',
           'σ': r'\sigma ', 'Σ': r'\Sigma ', '∑': r'\sum ', '∫': r'\int ',
           '√': r'\surd ', '∞': r'\infty ', '≤': r'\le ', '≥': r'\ge ',
           '≠': r'\ne ', '≈': r'\approx ', '×': r'\times ', '−': '-',
           '²': '^{2}', '³': '^{3}', '₀': '_{0}', '₁': '_{1}', '₂': '_{2}'}


def is_formula(text: str) -> bool:
    return bool(len(text) < 180 and re.search(r'[=∫∑√≤≥≠≈]', text)
                and not re.search(r'\b(?:http|www|Section|Article)\b', text)
                and len(re.findall(r'\b[A-Za-z]{4,}\b', text)) <= 2)


def formula_latex(chars: list[dict]) -> str:
    visible = sorted([c for c in chars if c['text'].strip()], key=lambda c: c['x0'])
    size = statistics.median(c['size'] for c in visible)
    body = [c for c in visible if c['size'] >= size * .9]
    baseline = statistics.median(c['bottom'] for c in body)
    chunks = []
    for char in visible:
        text = SYMBOLS.get(char['text'], char['text'])
        if 'bold' in char.get('fontname','').lower() and re.fullmatch(r'[A-Za-z0-9]', char['text']):
            text = r'\mathbf{' + text + '}'
        if char['size'] < size * .85:
            if char['bottom'] < baseline - size * .15:
                text = '^{' + text + '}'
            elif char['bottom'] > baseline + size * .15:
                text = '_{' + text + '}'
        chunks.append(text)
    label = re.search(r'\((\d+)\)$', ''.join(c['text'] for c in visible))
    if label and len(visible) > len(label[0]):
        start = len(visible) - len(label[0])
        if visible[start]['x0'] - visible[start-1]['x1'] > size * 3:
            chunks = chunks[:start] + [r'\tag{' + label[1] + '}']
    return ''.join(chunks).strip()
