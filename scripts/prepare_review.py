"""Build local source evidence and an unapproved review template from an existing conversion."""
from __future__ import annotations
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
from time import perf_counter

from audit import audit


def safe_preview(markdown):
    # Treat source HTML as data. Keep only structural HTML table tags/merged cells.
    def tag(match):
        text=match[0]
        name=re.match(r'</?\s*([a-z]+)\b',text,re.I)
        if name and name[1].lower() in {'table','thead','tbody','tfoot','tr','th','td','caption','colgroup','col'}:
            attrs=''.join(f' {k.lower()}="{v}"' for k,v in re.findall(r'\b(colspan|rowspan)\s*=\s*[\"\']?(\d+)',text,re.I))
            return '<'+('/' if text.startswith('</') else '')+name[1].lower()+attrs+'>'
        return html.escape(text)
    return re.sub(r'<[^>\n]*>',tag,markdown)


def prepare(review_dir, markdown_path, pages=None, max_pages=12, source_images=True):
    started=perf_counter()
    source=json.loads((review_dir/'source.json').read_text('utf-8'))
    raw=markdown_path.read_bytes(); markdown=raw.decode('utf-8-sig')
    checked=audit(source,markdown,review_dir,output_sha256=hashlib.sha256(raw).hexdigest())
    index={l['source_id']:l for p in source['pages'] for l in p['lines']}
    contexts=[]
    for finding in checked['findings']:
        ids=finding.get('source_ids',[])
        if not ids and finding.get('block') is not None:
            ids=source['blocks'][finding['block']]['source_ids']
        related=[p for p in source['pages'] if p['page']==finding.get('page')]
        evidence=[]
        for p in related:
            positions=[i for i,l in enumerate(p['lines']) if l['source_id'] in ids]
            chosen={j for i in positions for j in range(max(0,i-1),min(len(p['lines']),i+2))}
            evidence += [p['lines'][i] for i in sorted(chosen)]
        contexts.append({'finding':finding,'source_context':evidence})
    suggested=sorted(set(checked['sample_pages']))
    requested=list(dict.fromkeys(suggested+[f['page'] for f in checked['findings'] if f.get('page')]))
    selected=sorted(set(pages)) if pages is not None else requested[:max_pages]
    valid={p['page'] for p in source['pages']}
    if set(selected)-valid:raise ValueError('Requested page outside source page range')
    rendered=[]
    if source_images and Path(source['source']).suffix.lower()=='.pdf':
        import pymupdf
        from PIL import Image,ImageDraw
        sheets=[]
        with pymupdf.open(source['source']) as pdf:
            for number in selected:
                pix=pdf[number-1].get_pixmap(matrix=pymupdf.Matrix(1,1),alpha=False)
                name=f'source-p{number}.png';pix.save(review_dir/name)
                rendered.append(number)
                im=Image.open(review_dir/name).convert('RGB');im.thumbnail((612,792))
                tile=Image.new('RGB',(630,824),'white');tile.paste(im,(9,26))
                ImageDraw.Draw(tile).text((9,5),f'Physical page {number}',fill='black')
                sheets.append(tile)
            for offset in range(0,len(sheets),4):
                batch=sheets[offset:offset+4];sheet=Image.new('RGB',(1260,824*((len(batch)+1)//2)),'#ddd')
                for i,tile in enumerate(batch):sheet.paste(tile,((i%2)*630,(i//2)*824))
                sheet.save(review_dir/f'contact-{offset//4+1}.png')
    summary={'status':checked['status'],'source_accounting':checked['source_accounting'],
             'engines':sorted({p['engine'] for p in source['pages']}),
             'suggested_sample_pages':suggested,'rendered_pages':rendered,
             'unrendered_finding_pages':sorted({f['page'] for f in checked['findings'] if f.get('page')}-set(rendered)),
             'findings':contexts,'deletions':source['deletions'],'transitions':source['transitions']}
    (review_dir/'review-context.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    template={'source_sha256':source['source_sha256'],'output_sha256':hashlib.sha256(raw).hexdigest(),
              'targeted_locations':[],'sample_pages':[],
              'resolved_findings':{f['id']:'' for f in checked['findings']},'unresolved':[]}
    # A template never asserts that inspection occurred; an existing review.json stays intact.
    (review_dir/'review.template.json').write_text(json.dumps(template,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    normal=markdown.replace('\r\n','\n').replace('\r','\n')
    blocks=normal.split('\n\n');pick=set(range(min(10,len(blocks))))|set(range(max(0,len(blocks)-10),len(blocks)))
    for pattern in [r'^>',r'^#### ',r'^\|',r'^<table',r'^\$\$',r'\\\$\d',r'^\d+\\\.']:
        match=next((i for i,b in enumerate(blocks) if re.search(pattern,b)),None)
        if match is not None:pick.add(match)
    for name,text in [('preview-safe.md',normal),('preview-sample.md','\n\n'.join(blocks[i] for i in sorted(pick)))]:
        (review_dir/name).write_text(safe_preview(text),encoding='utf-8',newline='\n')
    body=['<h1>Source review evidence</h1>','<p>These are review candidates. No inspection is recorded automatically.</p>',
          '<pre>'+html.escape(json.dumps({k:v for k,v in summary.items() if k not in ('findings','deletions','transitions')},ensure_ascii=False,indent=2))+'</pre>']
    for c in contexts:
        f=c['finding'];body+=['<h2>'+html.escape(f['id'])+'</h2>','<p>'+html.escape(f['message'])+'</p>',
                             '<pre>'+html.escape('\n'.join(l['source_id']+': '+l['text'] for l in c['source_context']))+'</pre>']
    for number in rendered:body+=['<h2>Page '+str(number)+'</h2>',f'<img src="source-p{number}.png" width="612">']
    (review_dir/'review-summary.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src file: data:; style-src \'unsafe-inline\'"><style>body{max-width:1000px;margin:30px auto;font:16px/1.5 Arial}pre{white-space:pre-wrap}</style>'+''.join(body),encoding='utf-8',newline='\n')
    result={k:summary[k] for k in ('status','engines','source_accounting','rendered_pages','unrendered_finding_pages')}
    result['prepare_seconds']=round(perf_counter()-started,6)
    (review_dir/'prepare-timings.json').write_text(json.dumps(result,indent=2),encoding='utf-8',newline='\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review_dir',type=Path)
    parser.add_argument('markdown_path',type=Path)
    parser.add_argument('--pages',help='Comma-separated physical pages; defaults to samples and finding pages')
    parser.add_argument('--max-pages',type=int,default=12)
    parser.add_argument('--no-source-images',action='store_true')
    args=parser.parse_args()
    if args.max_pages<1:parser.error('--max-pages must be positive')
    pages=[int(n) for n in args.pages.split(',')] if args.pages else None
    print(json.dumps(prepare(args.review_dir.resolve(),args.markdown_path.resolve(),pages,args.max_pages,not args.no_source_images),ensure_ascii=False))
