"""End-to-end fixtures are generated locally, independent of the user's PDF."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pymupdf
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_review import prepare, safe_preview
from convert import write_output


def synthetic_pdf(path):
    doc=pymupdf.open()
    for page_num in range(1,5):
        p=doc.new_page(width=612,height=792)
        p.insert_text((60,24),f'Synthetic court extract Page {page_num} of 4',fontsize=10)
        p.insert_text((295,760),f'- {page_num} -',fontsize=10)
        if page_num==1:
            p.insert_text((250,90),'DECISION',fontname='tibo',fontsize=14)
            p.insert_text((108,122),'I. FINDINGS',fontname='tibo',fontsize=14)
            p.insert_text((108,154),'A. Framework',fontname='tibo',fontsize=14)
            for y,x,t in [(186,108,'BODY_ALPHA The first paragraph starts here and'),
                          (218,72,'continues into a second line with relevant facts.'),
                          (250,108,'BODY_BETA The next paragraph cites 123'),
                          (282,72,'F. Supp. at 701 as part of the ordinary legal reasoning.'),
                          (314,108,'BODY_GAMMA These numbered elements remain body text:')]:
                p.insert_text((x,y),t,fontname='tiro',fontsize=14)
            for y,t in [(600,'(1) REQUIRED_ELEMENT_ONE must be established.'),
                        (628,'(2) REQUIRED_ELEMENT_TWO must also be established.'),
                        (656,'(3) REQUIRED_ELEMENT_THREE completes the test.')]:
                p.insert_text((108,y),t,fontname='tiro',fontsize=12)
        elif page_num==2:
            for i in range(7):p.insert_text((72,100+i*32),f'BODY_DELTA_{i} This is an ordinary line of substantive prose.',fontname='tiro',fontsize=14)
            prefix='This statement';x=72+pymupdf.get_text_length(prefix,fontname='tiro',fontsize=14)
            p.insert_text((72,360),prefix,fontname='tiro',fontsize=14)
            p.insert_text((x,354),'1',fontname='tiro',fontsize=8)
            p.insert_text((x+5,360),' includes a note reference.',fontname='tiro',fontsize=14)
            p.draw_line((72,690),(200,690))
            p.insert_text((108,712),'1 EXCLUDE_NOTE_START is a footnote that continues',fontname='tiro',fontsize=12)
            p.insert_text((72,726),'onto the following page without a sentence break',fontname='tiro',fontsize=12)
        elif page_num==3:
            for i in range(7):p.insert_text((72,100+i*32),f'BODY_EPSILON_{i} Retained reasoning after the page transition.',fontname='tiro',fontsize=14)
            p.draw_line((72,690),(200,690))
            p.insert_text((72,712),'EXCLUDE_NOTE_CONTINUED contains only continued footnote text.',fontname='tiro',fontsize=12)
        else:
            for y,x,t,size in [(100,108,'BODY_ZETA The monthly charge is $9.99.',14),
                               (132,72,'A separate amount of $250 is also mentioned.',14),
                               (180,108,'Speaker: QUOTE_ONE begins on this physical line',12),
                               (194,108,'and continues on a second physical line.',12),
                               (222,108,'Reply: QUOTE_TWO starts a new quoted paragraph.',12),
                               (270,108,'1. REQUIRED_ORDER_ONE is granted.',14),
                               (302,108,'2. REQUIRED_ORDER_TWO is denied.',14),
                               (334,72,'Counsel of Record',14)]:
                p.insert_text((x,y),t,fontname='tiro',fontsize=size)
    doc.save(path)


def test_synthetic_pdf_conversion_and_review(tmp_path):
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    source=tmp_path/'synthetic.pdf';synthetic_pdf(source)
    output=tmp_path/'result.md';review=tmp_path/'review'
    proc=subprocess.run([sys.executable,'-X','utf8',str(scripts/'convert.py'),str(source),str(output),
                         '--review-dir',str(review),'--engine','geometry','--no-ocr'],capture_output=True,text=True,encoding='utf-8')
    assert proc.returncode==0,proc.stderr
    md=output.read_text('utf-8')
    for token in ['BODY_ALPHA','BODY_BETA','BODY_GAMMA','BODY_ZETA','QUOTE_ONE','QUOTE_TWO',
                  'REQUIRED_ELEMENT_ONE','REQUIRED_ELEMENT_TWO','REQUIRED_ELEMENT_THREE',
                  'REQUIRED_ORDER_ONE','REQUIRED_ORDER_TWO','Counsel of Record']:
        assert token in md
    for prefix in ('BODY_DELTA_','BODY_EPSILON_'):
        for i in range(7):assert prefix+str(i) in md
    assert 'EXCLUDE_NOTE' not in md
    assert '### F. Supp.' not in md
    assert '\n\nBODY_GAMMA' in md
    assert '\n\nCounsel of Record' in md
    assert '\\$9.99' in md and '1\\. REQUIRED_ORDER_ONE' in md
    assert '\r\n' not in output.read_bytes().decode('utf-8')
    a=json.loads((review/'audit.json').read_text('utf-8'))
    assert a['source_accounting']['unaccounted_lines']==0
    assert a['source_accounting']['raw_lines']==a['source_accounting']['retained_lines']+a['source_accounting']['deleted_lines']
    assert all(p['retained_lines']==p['mapped_lines'] for p in a['page_coverage'])
    assert a['status']=='review_required'
    timings=json.loads((review/'timings.json').read_text('utf-8'))
    assert {'extract_text','reconstruct','audit','total'}<=timings.keys()
    assert timings['total']>=timings['extract_text']
    reviewed={'do_not_overwrite':True};(review/'review.json').write_text(json.dumps(reviewed),encoding='utf-8')
    result=prepare(review,output,max_pages=4)
    assert result['source_accounting']['unaccounted_lines']==0
    assert set(result['rendered_pages'])=={1,2,3,4}
    assert (review/'contact-1.png').is_file()
    assert json.loads((review/'review.json').read_text('utf-8'))==reviewed
    template=json.loads((review/'review.template.json').read_text('utf-8'))
    assert template['sample_pages']==[] and template['targeted_locations']==[]
    assert all(not evidence for evidence in template['resolved_findings'].values())
    assert template['output_sha256']==hashlib.sha256(output.read_bytes()).hexdigest()
    assert not any(f['resolved'] for f in json.loads((review/'audit.json').read_text('utf-8'))['findings'])


def test_write_output_normalizes_newlines_without_overwriting(tmp_path):
    p=tmp_path/'out.md';write_output(p,'one\r\ntwo\rthree\n',False)
    assert p.read_bytes()==b'one\ntwo\nthree\n'
    with pytest.raises(FileExistsError):write_output(p,'replacement',False)


def test_source_html_is_data_but_table_spans_survive_preview():
    safe=safe_preview('<script>alert(1)</script><table><tr><td colspan="2" onclick="bad()">Text</td></tr></table>')
    assert '<script>' not in safe and '&lt;script&gt;' in safe
    assert '<td colspan="2">Text</td>' in safe and 'onclick' not in safe


def test_audit_cli_hashes_actual_crlf_bytes(tmp_path):
    from dataclasses import asdict
    from pdf_text import Line
    from pdf_pipeline import _paragraphs
    source=tmp_path/'source.dat';source.write_bytes(b'Synthetic source only')
    l=Line('A complete substantive sentence that must remain intact.',90,104,72,500,14,
           source_id='p1-l0',page=1)
    s={'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
       'pages':[{'page':1,'lines':[asdict(l)],'kept_lines':[asdict(l)],'engine':'geometry'}],
       'blocks':_paragraphs([l]),'deletions':[],'issues':[],'transitions':[]}
    (tmp_path/'source.json').write_text(json.dumps(s),encoding='utf-8')
    out=tmp_path/'final.md';out.write_bytes((l.text+'\r\n').encode('utf-8'))
    review={'source_sha256':s['source_sha256'],'output_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
            'sample_pages':[1],'targeted_locations':[],'resolved_findings':{},'unresolved':[]}
    (tmp_path/'review.json').write_text(json.dumps(review),encoding='utf-8')
    proc=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/audit.py'),str(tmp_path),str(out)],
                        capture_output=True,text=True,encoding='utf-8')
    assert proc.returncode==0 and json.loads(proc.stdout)['status']=='reviewed'


def test_bottom_table_content_survives_actual_pdf_pipeline(tmp_path):
    from pdf_pipeline import convert_pdf
    doc=pymupdf.open();p=doc.new_page(width=612,height=792)
    for i in range(8):p.insert_text((72,100+i*32),'A normal substantive body line for context.',fontsize=14)
    for y in [580,614,648,682]:p.draw_line((72,y),(500,y))
    for x in [72,280,500]:p.draw_line((x,580),(x,682))
    for y,a,b in [(600,'Requirement','Value'),(634,'1 TABLE_REQUIRED_ONE','100'),(668,'2 TABLE_REQUIRED_TWO','200')]:
        p.insert_text((78,y),a,fontsize=12);p.insert_text((286,y),b,fontsize=12)
    source=tmp_path/'table.pdf';doc.save(source)
    s=convert_pdf(source,'geometry',tmp_path/'review',ocr=False)
    text='\n\n'.join(b['markdown'] for b in s['blocks'])
    assert 'TABLE_REQUIRED_ONE' in text and 'TABLE_REQUIRED_TWO' in text
    assert not any(d['reason']=='footnote' for d in s['deletions'])
    assert any(b['kind']=='table' for b in s['blocks'])
