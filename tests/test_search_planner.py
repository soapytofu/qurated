import json
from types import SimpleNamespace as NS
import pytest
import pipeline
import search_planner
from test_retrieval import database


def fake_client(value=None, stop='end_turn', error=None):
    def create(**kwargs):
        assert 'untrusted data' in kwargs['system']
        if error:
            raise error
        return NS(stop_reason=stop, content=[NS(type='text', text=json.dumps(value))])
    client=NS(messages=NS(create=create))
    client.with_options=lambda **kw: client
    return client


def design_database():
    db=database()
    old=list(db.courses.values())
    db.courses={}
    for i,c in enumerate(old):
        c['code']=f'COMPSCI {i+1000}'
        c['title']='Computer science design of algorithms'
        c['catalog']['description']='computer science algorithms design'
        db.courses[c['code']]=c
    target=old[0]
    target['title']='Human Computer Interaction'
    target['catalog']['description']='User interfaces, usability, prototyping and visualization'
    db._build_index()
    return db,target['code']


def test_semantic_expansion_recovers_relevant_course_and_preserves_subject(monkeypatch):
    monkeypatch.setenv('SEARCH_QUERY_EXPANSION','1')
    db,target=design_database()
    question='CS classes for someone interested in design'
    baseline=pipeline.plan(db,question,{})
    assert target in {c['code'] for c in pipeline.retrieve(db,baseline)}
    expanded=search_planner.expand(fake_client({'queries':['human computer interaction user interfaces',
                                                          'usability prototyping visualization']}),baseline,question,[])
    results=pipeline.retrieve(db,expanded)
    assert target in {c['code'] for c in results}
    assert all(c['code'].startswith('COMPSCI ') for c in results)
    assert expanded['queries'][0]==baseline['queries'][0]


@pytest.mark.parametrize('value,stop', [({'queries':[]},'end_turn'),
    ({'queries':['x']*3},'end_turn'),({'queries':[123]},'end_turn'),
    ({'queries':['x'*161]},'end_turn'),({'queries':['design']},'max_tokens')])
def test_invalid_expansion_falls_back(monkeypatch,value,stop):
    monkeypatch.setenv('SEARCH_QUERY_EXPANSION','1')
    baseline=pipeline.plan(database(),'design classes',{})
    assert search_planner.expand(fake_client(value,stop),baseline,'design classes',[])==baseline


def test_timeout_fallback_and_constraints_cannot_be_changed(monkeypatch):
    monkeypatch.setenv('SEARCH_QUERY_EXPANSION','1')
    baseline=pipeline.plan(database(),'Aesthetics and Culture Gen Ed in spring',{})
    assert search_planner.expand(fake_client(error=TimeoutError()),baseline,'request',[])==baseline
    expanded=search_planner.expand(fake_client({'queries':['art history'], 'gen_ed_category':'Ethics and Civics',
                                               'term':'2026 Fall','codes':['FAKE 1']}),baseline,'request',[])
    assert expanded['term']=='2027 Spring'
    assert expanded['gen_ed_category']=='Aesthetics and Culture'
    assert expanded['codes']==[]
    assert pipeline.retrieve(database(),expanded)==[]


def test_exact_code_skips_semantic_request(monkeypatch):
    monkeypatch.setenv('SEARCH_QUERY_EXPANSION','1')
    baseline=pipeline.plan(database(),'TEST 0',{})
    assert search_planner.expand(None,baseline,'TEST 0',[])==baseline


def test_history_is_bounded_and_supplied_as_data(monkeypatch):
    monkeypatch.setenv('SEARCH_QUERY_EXPANSION','1')
    captured={}
    def create(**kwargs):
        captured.update(json.loads(kwargs['messages'][0]['content']))
        return NS(stop_reason='end_turn',content=[NS(type='text',text='{"queries":["ethics technology"]}')])
    c=NS(messages=NS(create=create));c.with_options=lambda **kw:c
    baseline=pipeline.plan(database(),'any more options?',{})
    search_planner.expand(c,baseline,'any more options?',[{'role':'user','content':'x'*900}]*10)
    assert len(captured['recent_conversation'])==4
    assert all(len(t['content'])==500 for t in captured['recent_conversation'])


def test_default_search_does_not_call_ai_and_recovers_design_interest(monkeypatch):
    monkeypatch.delenv('SEARCH_QUERY_EXPANSION', raising=False)
    db, target = design_database()
    p = pipeline.plan(db, 'CS classes for someone interested in design', {})
    assert search_planner.expand(None, p, 'CS design', []) == p
    assert target in {c['code'] for c in pipeline.retrieve(db, p)}
    assert p == pipeline.plan(db, 'CS classes for someone interested in design', {})


def test_algorithm_design_does_not_expand_to_user_interfaces():
    p = pipeline.plan(database(), 'CS classes about algorithm design', {})
    assert p['queries'] == ['algorithm design']


@pytest.mark.parametrize('question, expected', [
    ('classes about AI', 'neural networks deep learning'),
    ('classes about climate', 'environment sustainability'),
    ('courses about cinema', 'filmmaking moving images'),
    ('creative writing classes', 'poetry storytelling workshop'),
    ('cybersecurity classes', 'computer security cryptography'),
])
def test_known_topics_use_fixed_expansions(question, expected):
    p = pipeline.plan(database(), question, {})
    assert expected in p['queries']
    assert len(p['queries']) <= 3


def test_unknown_topics_remain_literal():
    assert pipeline.plan(database(), 'classes about underwater archaeology', {})['queries'] == ['underwater archaeology']


def test_ranking_does_not_depend_on_course_insertion_order():
    db = database()
    p = pipeline.plan(db, 'interesting courses', {})
    before = [c['code'] for c in pipeline.retrieve(db, p)]
    db.courses = dict(reversed(list(db.courses.items())))
    db._build_index()
    assert [c['code'] for c in pipeline.retrieve(db, p)] == before
    assert before == sorted(before)
