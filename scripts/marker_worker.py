"""Isolate Marker's model dependencies from current MarkItDown/pdfplumber."""
import json
from pathlib import Path
import sys

from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict, shutdown_models
from marker.renderers.json import JSONRenderer
from marker.schema import BlockTypes


if __name__ == '__main__':
    source, page_arg, out_dir = sys.argv[1:]
    page_numbers = [int(p) for p in page_arg.split(',') if p.strip()]
    out_dir = Path(out_dir)
    config = {'mode': 'no_ocr', 'disable_image_extraction': True, 'use_llm': False,
              'paginate_output': False}
    # Load the models once and reuse them for every page in this batch.
    models = create_model_dict(device='cpu', load_indra=True, no_server=True)
    try:
        for number in page_numbers:
            page_config = dict(config, page_range=[number - 1])
            document = PdfConverter(artifact_dict=models, config=page_config).build_document(source)
            for page in document.pages:
                for block in page.contained_blocks(document, (BlockTypes.Footnote, BlockTypes.Picture, BlockTypes.Figure)):
                    block.ignore_for_output = True
            data = JSONRenderer(page_config)(document).model_dump()
            (out_dir / f'marker-p{number}.json').write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        shutdown_models(models)
