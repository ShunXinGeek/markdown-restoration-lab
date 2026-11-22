"""Synthetic behavioral regressions; no user's source documents are loaded."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pytest
import pdf_text as flow
from pdf_pipeline import _paragraphs, _markdown_text
from audit import audit


def line(text, y=90, x=72, size=14, page=1, ident=0, bold=False, font='Times-Roman', refs=(), end=540):
    return flow.Line(text, y, y+size, x, end, size, bold, bold, font, refs, f'p{page}-l{ident}', page)


def bundle(tmp_path, lines, blocks=None, deletions=None):
    source = tmp_path/'source.dat'; source.write_bytes(b'Synthetic source only')
    return {'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'pages': [{'page': 1, 'lines': [asdict(l) for l in lines],
                       'kept_lines': [asdict(l) for l in lines], 'engine': 'geometry'}],
            'blocks': blocks if blocks is not None else _paragraphs(lines),
            'deletions': deletions or [], 'issues': [], 'transitions': []}


def test_double_spaced_body_and_first_line_indent():
    ls = [line(t, 90+i*32, 108 if i in (0,3) else 72, ident=i)
          for i,t in enumerate(['First paragraph begins with this long line', 'continues across another full line',
                                'and ends here.', 'Second paragraph begins with a full line',
                                'continues into its next physical line', 'and concludes.'])]
    bs = _paragraphs(ls)
    assert len(bs) == 2
    assert [b['source_ids'] for b in bs] == [['p1-l0','p1-l1','p1-l2'], ['p1-l3','p1-l4','p1-l5']]


def test_mixed_body_and_single_spaced_quotation():
    ls = [line('Main prose continues across',90,ident=0), line('two physical lines.',122,ident=1),
          line('Quoted statement starts and',160,108,12,ident=2),line('continues on its own next line.',174,108,12,ident=3),
          line('A second quoted paragraph.',202,108,12,ident=4),
          line('Following body paragraph begins.',240,108,ident=5)]
    bs = _paragraphs(ls)
    assert [len(b['source_ids']) for b in bs] == [2,2,1,1]


def test_reporter_citation_is_prose():
    bs=_paragraphs([line('The cited holding appears at 932',90,ident=0),
                    line('F. Supp. at 701 and supports this argument.',122,ident=1)])
    assert len(bs)==1 and bs[0]['kind']=='paragraph'


def test_styled_heading_tree_and_wrapped_italic_subheading():
    ls=[line('I. ANALYSIS',90,250,ident=0,bold=True,font='Times-Bold',end=400),
        line('A. Liability',122,108,ident=1,bold=True,font='Times-Bold',end=250),
        line('i. The manufacturer may be liable for a component',154,137,ident=2,font='Times-Italic',end=538),
        line('part of the finished product',186,162,ident=3,font='Times-Italic',end=350),
        line('The body begins with an ordinary sentence.',218,108,ident=4),
        line('It continues without becoming a heading.',250,ident=5)]
    bs=_paragraphs(ls)
    assert [b['markdown'].split()[0] for b in bs[:3]]==['##','###','####']
    assert bs[2]['source_ids']==['p1-l2','p1-l3']
    assert bs[-1]['kind']=='paragraph'


@pytest.mark.parametrize('label',['(1)','(2)','[3]'])
def test_bottom_numbered_body_is_never_deleted_by_font_alone(label):
    ls=[line('Normal body text.',90,ident=0), line('More normal body text.',122,ident=1),
        line(label+' A substantive legal requirement.',600,108,12,ident=2)]
    findings=[];out=flow._remove_footnotes([(792,ls)],findings=findings)[0][1]
    assert [l.text for l in out]==[l.text for l in ls]
    assert findings[0]['code']=='note_candidate'


def test_ruled_note_and_matched_reference_are_removed():
    ls=[line('A proposition1 is stated.',90,ident=0,refs=((13,14,'1'),)),
        line('Another body line.',122,ident=1),line('A third body line.',154,ident=2),
        line('1 The explanatory note.',710,108,12,ident=3)]
    out=flow._remove_footnotes([(792,ls)],{1:[700]})[0][1]
    assert len(out)==3 and out[0].text=='A proposition is stated.'


def test_document_body_size_handles_quote_heavy_page():
    first=[line('Ordinary body line.',90+i*32,page=1,ident=i) for i in range(8)]
    quoted=[line('Quoted dialogue continues.',90+i*14,108,12,page=2,ident=i) for i in range(5)]
    quoted += [line('Speaker5 replies.',220,108,12,page=2,ident=5,refs=((7,8,'5'),)),
               line('5 The numbered note.',710,108,12,page=2,ident=6)]
    out=flow._remove_footnotes([(792,first),(792,quoted)],{2:[700]})[1][1]
    assert len(out)==6 and out[-1].text=='Speaker replies.'


def test_continued_footnote_requires_separator_on_next_page():
    first=[line('Body text1 with reference.',90,ident=0,refs=((9,10,'1'),)),
           line('More body.',122,ident=1),line('Further body.',154,ident=2),
           line('1 This note continues',710,108,12,ident=3)]
    second=[line('Main argument continues.',90,page=2,ident=0),
            line('Still main argument.',122,page=2,ident=1),
            line('continued note text without a new label.',710,72,12,page=2,ident=2)]
    out=flow._remove_footnotes([(792,first),(792,second)],{1:[700],2:[700]})
    assert len(out[1][1])==2
    out=flow._remove_footnotes([(792,first),(792,second)],{1:[700]})
    assert len(out[1][1])==3


def test_math_superscript_is_preserved():
    ls=[line('x2 = 4',90,ident=0,refs=((1,2,'2'),)),line('Normal prose.',122,ident=1),
        line('Normal prose again.',154,ident=2),line('2 A footnote body.',710,108,12,ident=3)]
    assert flow._remove_footnotes([(792,ls)],{1:[700]})[0][1][0].text=='x2 = 4'


def test_page_transition_preserves_new_paragraph_and_style_change():
    previous=line('This continues',710)
    assert flow._join_pages(previous,[line('across the page.',90),line('other prose',122)],792,792)
    assert not flow._join_pages(previous,[line('A new paragraph.',90,108),line('body continuation',122)],792,792)
    assert not flow._join_pages(previous,[line('Quoted text.',90,108,12)],792,792)


def test_currency_is_not_math_and_literal_order_labels_survive(tmp_path):
    ls=[line('12. The fee is $9.99/month and the award is $250.',90)]
    bs=_paragraphs(ls);md=bs[0]['markdown']
    assert md.startswith('12\\.') and '\\$9.99' in md
    a=audit(bundle(tmp_path,ls,bs),md,tmp_path)
    assert a['status']!='failed' and a['formula_blocks']==0
    assert not any(f['code']=='text_alignment' for f in a['findings'])


def test_true_broken_math_remains_a_hard_failure(tmp_path):
    ls=[line('x = 2',90)]
    bs=[{'kind':'formula','text':'x = 2','markdown':'$x = 2','source_ids':['p1-l0'],'pages':[1]}]
    a=audit(bundle(tmp_path,ls,bs),'$x = 2',tmp_path)
    assert a['status']=='failed' and any(f['code']=='formula_delimiter' for f in a['findings'])


def test_unaccounted_raw_line_is_hard_failure(tmp_path):
    ls=[line('First retained sentence.',90,ident=0),line('Missing substantive sentence.',122,ident=1)]
    s=bundle(tmp_path,ls)
    s['pages'][0]['kept_lines']=s['pages'][0]['kept_lines'][:1]
    s['blocks']=_paragraphs(ls[:1])
    a=audit(s,s['blocks'][0]['markdown'],tmp_path)
    assert a['status']=='failed' and any(f['code']=='source_partition' for f in a['findings'])


def test_every_deleted_note_requires_review_even_with_full_mapping(tmp_path):
    ls=[line('This is a retained ordinary body sentence.',90,ident=0),line('1 A removed note.',710,108,12,ident=1)]
    s=bundle(tmp_path,ls,_paragraphs(ls[:1]),[{'page':1,'source_id':'p1-l1','text':ls[1].text,'reason':'footnote'}])
    s['pages'][0]['kept_lines']=s['pages'][0]['kept_lines'][:1]
    a=audit(s,s['blocks'][0]['markdown'],tmp_path)
    assert a['source_accounting']['unaccounted_lines']==0
    assert a['status']=='review_required'
    assert any(f['code']=='footnote_deletion' and not f['resolved'] for f in a['findings'])


def test_byte_hash_accepts_crlf_without_claiming_review_automatically(tmp_path):
    ls=[line('A complete ordinary sentence with enough text.',90)]
    s=bundle(tmp_path,ls);md=s['blocks'][0]['markdown']+'\r\n'
    raw=md.encode('utf-8');fingerprint=hashlib.sha256(raw).hexdigest()
    a=audit(s,md,tmp_path,output_sha256=fingerprint)
    assert a['status']=='review_required'
    review={'source_sha256':s['source_sha256'],'output_sha256':fingerprint,
            'sample_pages':[1],'targeted_locations':[],'resolved_findings':{},'unresolved':[]}
    (tmp_path/'review.json').write_text(json.dumps(review),encoding='utf-8')
    a=audit(s,md,tmp_path,output_sha256=fingerprint)
    assert a['status']=='reviewed' and a['output_sha256']==fingerprint


def test_output_text_loss_is_flagged(tmp_path):
    ls=[line('A complete ordinary sentence containing substantive text.',90)]
    a=audit(bundle(tmp_path,ls),'A complete ordinary sentence.',tmp_path)
    assert any(f['code']=='edited_content' and not f['resolved'] for f in a['findings'])


def test_small_numbered_table_cells_are_protected_from_note_removal():
    ls=[line('Ordinary prose body.',90+i*32,ident=i) for i in range(8)]
    ls += [line('1 A required table cell.',710,108,12,ident=8)]
    out=flow._remove_footnotes([(792,ls)],{1:[700]},protected_ids={'p1-l8'})
    assert out[0][1][-1].text=='1 A required table cell.'


def test_complete_previous_note_does_not_consume_unlabelled_next_page_text():
    first=[line('Ordinary prose body.',90+i*32,ident=i) for i in range(6)]
    first += [line('1 A completed note.',710,108,12,ident=6)]
    second=[line('Ordinary body again.',90+i*32,page=2,ident=i) for i in range(6)]
    second += [line('Substantive unlabelled text.',710,108,12,page=2,ident=6)]
    findings=[]
    out=flow._remove_footnotes([(792,first),(792,second)],{1:[700],2:[700]},findings)
    assert out[1][1][-1].text=='Substantive unlabelled text.'
    assert any(f['code']=='note_continuation_candidate' for f in findings)


def test_duplicate_or_overlapping_source_assignments_are_hard_failures(tmp_path):
    ls=[line('A substantive retained sentence.',90)]
    s=bundle(tmp_path,ls)
    s['blocks']*=2
    a=audit(s,'\n\n'.join(b['markdown'] for b in s['blocks']),tmp_path)
    assert a['status']=='failed' and any(f['code']=='source_coverage' for f in a['findings'])
    s=bundle(tmp_path,ls,deletions=[{'page':1,'source_id':'p1-l0','text':ls[0].text,'reason':'footnote'}])
    a=audit(s,s['blocks'][0]['markdown'],tmp_path)
    assert a['status']=='failed' and any(f['code']=='source_partition' for f in a['findings'])


def test_changed_source_or_stale_review_never_passes(tmp_path):
    ls=[line('A complete ordinary sentence with enough text.',90)]
    s=bundle(tmp_path,ls);md=s['blocks'][0]['markdown']
    review={'source_sha256':s['source_sha256'],'output_sha256':'stale',
            'sample_pages':[1],'targeted_locations':[],'resolved_findings':{},'unresolved':[]}
    (tmp_path/'review.json').write_text(json.dumps(review),encoding='utf-8')
    assert audit(s,md,tmp_path)['status']=='review_required'
    Path(s['source']).write_bytes(b'changed source')
    a=audit(s,md,tmp_path)
    assert a['status']=='failed' and any(f['code']=='source_changed' for f in a['findings'])


def test_chinese_body_is_joined_without_added_word_spaces():
    ls=[line('这是正文的第一行，',90,ident=0,size=12),
        line('接续内容应当保留。',116,ident=1,size=12)]
    bs=_paragraphs(ls)
    assert len(bs)==1 and bs[0]['text']=='这是正文的第一行，接续内容应当保留。'
