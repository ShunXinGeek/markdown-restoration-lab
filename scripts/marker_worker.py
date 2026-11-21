"""Isolate Marker's model dependencies from current MarkItDown/pdfplumber."""
import json
from pathlib import Path
import sys

from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict, shutdown_models
from marker.renderers.json import JSONRenderer
from marker.schema import BlockTypes


if __name__ == '__main__':
    source, page_number, output = sys.argv[1:]
    config = {'mode': 'no_ocr', 'disable_image_extraction': True, 'use_llm': False,
              'page_range': [int(page_number)], 'paginate_output': False}
    models = create_model_dict(device='cpu', load_indra=True, no_server=True)
    try:
        document = PdfConverter(artifact_dict=models, config=config).build_document(source)
        for page in document.pages:
            for block in page.contained_blocks(document, (BlockTypes.Footnote, BlockTypes.Picture, BlockTypes.Figure)):
                block.ignore_for_output = True
        data = JSONRenderer(config)(document).model_dump()
        Path(output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        shutdown_models(models)
