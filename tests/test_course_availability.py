"""Q report history must never establish current course availability."""
import json
from types import SimpleNamespace

import pytest

from courses import DEFAULT_TERM, NO_Q
from pipeline import effective_filters, plan, retrieve, stream_answer
from test_retrieval import database


@pytest.fixture
def db():
    result = database()
    result.courses = {code: course for code, course in list(result.courses.items())[:6]}
    for i, course in enumerate(result.courses.values()):
        course['latest_term'] = '2023 Fall'
        course['offerings'] = [{**NO_Q, 'term': '2023 Fall', 'course_overall': 5,
                                'recommend_mean': 5, 'responded': 100, 'workload_hrs_mean': 3}]
        course['catalog']['offered'] = [{
            'term': '2026 Fall' if i == 1 else '2027 Spring',
            'sections': [{'instructors': ['Current instructor'], 'meetings': []}],
        }]
    result.courses['TEST 2']['catalog'] = None
    result.courses['TEST 3']['offerings'] = []  # Current course without any Q reports.
    result.courses['TEST 3']['latest_term'] = None
    result.courses['TEST 4']['catalog']['offered'][0]['term'] = '2023 Fall'
    result.courses['TEST 5']['catalog']['offered'].insert(0, {
        'term': '2026 Fall', 'sections': [{'instructors': ['Fall instructor'], 'meetings': []}],
    })
    result._build_index()
    return result


@pytest.mark.parametrize('filters,expected', [
    (None, {'TEST 0', 'TEST 3', 'TEST 5'}),
    ({}, {'TEST 0', 'TEST 3', 'TEST 5'}),
    ({'term': ''}, {'TEST 0', 'TEST 3', 'TEST 5'}),
    ({'term': '2027 Spring'}, {'TEST 0', 'TEST 3', 'TEST 5'}),
    ({'term': '2026 Fall'}, {'TEST 1', 'TEST 5'}),
    ({'term': 'any'}, {'TEST 0', 'TEST 1', 'TEST 3', 'TEST 5'}),
])
def test_browse_and_chat_require_catalog_availability(db, filters, expected):
    assert {c['code'] for c in db.browse(filters)['cards']} == expected
    assert {c['code'] for c in db.search('interesting', filters=filters)} == expected
    assert {c['code'] for c in retrieve(db, plan(db, 'interesting courses', {}), filters=filters)} == expected


@pytest.mark.parametrize('code', ['TEST 1', 'TEST 2', 'TEST 4'])
def test_named_courses_and_followups_cannot_bypass_default_term(db, code):
    assert db.browse(query=code)['cards'] == []
    assert retrieve(db, plan(db, f'Tell me about {code}', {})) == []
    history = [{'role': 'assistant', 'content': f'(Recommended: {code})'}]
    assert retrieve(db, plan(db, 'Which of those is lightest?', {}, history)) == []
    # Direct detail and taken-course lookup still support historical records.
    assert db.detail(code)['latest_term'] == '2023 Fall'
    assert db.suggest(code)[0]['code'] == code


def test_explicit_term_overrides_default_but_not_an_explicit_conflict(db):
    p = plan(db, 'interesting courses in Fall 2026', {})
    assert effective_filters(p)[0]['term'] == '2026 Fall'
    assert {c['code'] for c in retrieve(db, p)} == {'TEST 1', 'TEST 5'}
    assert {c['code'] for c in retrieve(db, p, filters={'term': 'any'})} == {'TEST 1', 'TEST 5'}
    assert retrieve(db, p, filters={'term': '2027 Spring'}) == []
    history = [{'role': 'user', 'content': 'interesting courses in Fall 2026'},
               {'role': 'assistant', 'content': '(Recommended: TEST 1, TEST 5)'}]
    assert {c['code'] for c in retrieve(db, plan(db, 'Compare those', {}, history))} == {'TEST 1', 'TEST 5'}


def test_search_term_uses_catalog_and_cards_separate_report_dates(db):
    assert {c['code'] for c in db.search(term=DEFAULT_TERM)} == {'TEST 0', 'TEST 3', 'TEST 5'}
    for code in ['TEST 0', 'TEST 5']:
        card = db.card(code)
        assert card['term'] == DEFAULT_TERM
        assert card['q_term'] == '2023 Fall'
        assert card['instructors'] == ['Current instructor']
    for code in ['TEST 1', 'TEST 2', 'TEST 4']:
        card = db.card(code)
        assert card['term'] is None
        assert card['next_offered'] is None
        assert 'Not offered in 2027 Spring' in card['availability']
        assert card['q_term'] == '2023 Fall'


def test_no_spring_matches_never_falls_back_to_historical_or_fall_courses(db, monkeypatch):
    import pipeline
    db.courses = {code: db.courses[code] for code in ['TEST 1', 'TEST 2', 'TEST 4']}
    db._build_index()
    monkeypatch.setattr(pipeline, 'hedged_stream', lambda *a: pytest.fail('no eligible candidates'))
    events = list(stream_answer(SimpleNamespace(with_options=lambda **kw: None), db, [],
                               'interesting courses', {}, [], [], True, lambda e: e, None, None))
    assert not db.browse()['cards']
    assert not any(event['type'] == 'card' for event in events)
    assert any("couldn't find matching courses" in event.get('delta', '') for event in events)


def test_http_browse_and_chat_use_same_default_and_model_context(db, monkeypatch):
    import server
    from fastapi.testclient import TestClient
    monkeypatch.setattr(server, 'db', db)
    monkeypatch.setattr(server, '_maybe_reload', lambda: None)
    monkeypatch.setattr(server, 'needs_q_data', lambda *a: (True, 0))
    monkeypatch.setattr(server.store, 'spend', lambda *a: (0, 0))

    def model(client, kwargs):
        content = json.loads(kwargs['messages'][-1]['content'])
        assert content['active_filters']['term'] == DEFAULT_TERM
        assert {c['code'] for c in content['candidates']} == {'TEST 0', 'TEST 3', 'TEST 5'}
        assert all(o['term'] == DEFAULT_TERM for c in content['candidates'] for o in c['offered_sections'])
        yield 'text', json.dumps({'picks': [{'code': 'TEST 0', 'reason': 'Fits your interests.', 'quote': ''}],
                                 'advice': 'Compare this course.'})
        yield 'final', SimpleNamespace(stop_reason='end_turn', usage=SimpleNamespace(provider='openai'))

    monkeypatch.setattr(server.pipeline, 'hedged_stream', model)
    server.app.dependency_overrides[server.current_user] = lambda: {'email': 'availability@example.invalid'}
    try:
        with TestClient(server.app) as client:
            result = client.post('/api/browse', json={}).json()
            assert {c['code'] for c in result['cards']} == {'TEST 0', 'TEST 3', 'TEST 5'}
            assert client.post('/api/browse', json={'filters': {'term': 'any'}}).json()['total'] == 4
            response = client.post('/api/chat', json={'message': 'interesting courses'})
            assert response.status_code == 200
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            card = next(e['card'] for e in events if e['type'] == 'card')
            assert card['term'] == DEFAULT_TERM and card['q_term'] == '2023 Fall'
            assert '2027 Spring' in next(e['criteria'] for e in events if e['type'] == 'search_plan')
    finally:
        server.app.dependency_overrides.clear()
