import json
from types import SimpleNamespace
import pytest
from courses import NO_Q, EXTRA_Q_SCORES
from pipeline import plan, retrieve, effective_filters, stream_answer
from search_constraints import extract
from test_retrieval import database


@pytest.mark.parametrize('question,term', [('Spring 2027', '2027 Spring'), ('2026 Fall', '2026 Fall'),
    ('Autumn 2026', '2026 Fall'), ('spring', '2027 Spring')])
def test_loaded_terms(question, term):
    assert extract(question)['term'] == term
    assert not extract(question)['clarifications']


@pytest.mark.parametrize('question', ['Spring 2028', 'Fall 2025', 'Spring 2027 and Fall 2026'])
def test_unavailable_or_multiple_terms_require_clarification(question):
    p = plan(database(), 'courses in ' + question, {})
    assert p['clarifications']
    assert retrieve(database(), p) == []


@pytest.mark.parametrize('word, expected', [('under', ['TEST 0']), ('less than', ['TEST 0']),
    ('at most', ['TEST 0', 'TEST 1']), ('up to', ['TEST 0', 'TEST 1'])])
def test_natural_workload_boundaries_and_unknown_exclusion(word, expected):
    db = database()
    for i, hours in enumerate([4.9, 5, 5.1, None]):
        db.courses[f'TEST {i}']['offerings'] = [{**NO_Q, 'workload_hrs_mean': hours}]
    p = plan(db, f'courses {word} 5 hours per week', {})
    assert p['queries'] == ['']
    assert sorted(c['code'] for c in retrieve(db, p)) == expected


@pytest.mark.parametrize('question', ['under 0 hours', 'under -3 hours', 'under 41 hours',
    'lectures rated at least 6', 'overall rating at least -1'])
def test_invalid_thresholds_require_clarification(question):
    assert extract(question)['clarifications']


@pytest.mark.parametrize('field,label,section,prefix', EXTRA_Q_SCORES)
def test_all_additional_score_labels_are_understood(field, label, section, prefix):
    result = extract(f'{label} at least 4.5')
    assert result['filters'] == {'min_' + field: 4.5}
    assert not result['query'].strip()


def test_numeric_constraints_apply_to_explicit_courses_and_merge_strictly():
    db = database()
    db.courses['TEST 0']['offerings'] = [{**NO_Q, 'effective_lectures': 4.2}]
    p = plan(db, 'TEST 0 lectures rated at least 4', {})
    assert retrieve(db, p)
    assert retrieve(db, p, filters={'min_lectures': 4.5}) == []
    f, issues = effective_filters(p, {'min_lectures': 3.5})
    assert f['min_lectures'] == 4 and not issues


def test_term_conflict_explains_both_values_without_model_calls(monkeypatch):
    import pipeline
    monkeypatch.setattr(pipeline, 'hedged_stream', lambda *a: pytest.fail('should not call answer model'))
    events = list(stream_answer(SimpleNamespace(with_options=lambda **kw: None), database(), [],
        'courses in Spring 2027', {}, [], [], True, lambda e: e, None, None, filters={'term': '2026 Fall'}))
    assert len(events) == 1
    assert events[0]['type'] == 'text'
    assert '2027 Spring' in events[0]['delta'] and '2026 Fall' in events[0]['delta']


def test_search_summary_and_model_receive_effective_filters(monkeypatch):
    import pipeline
    def model(client, kwargs):
        data = json.loads(kwargs['messages'][-1]['content'])
        assert data['active_filters']['min_lectures'] == 4.5
        yield 'text', json.dumps({'picks': [], 'advice': 'Compare the matching courses.'})
        yield 'final', SimpleNamespace(stop_reason='end_turn', usage=SimpleNamespace(provider='openai'))
    monkeypatch.setattr(pipeline, 'hedged_stream', model)
    db = database()
    db.courses['TEST 0']['offerings'] = [{**NO_Q, 'effective_lectures': 4.8}]
    events = list(stream_answer(SimpleNamespace(with_options=lambda **kw: None), db, [],
        'lectures rated at least 4.5', {}, [], [], True, lambda e: e, None, lambda *a: None))
    summary = next(e for e in events if e['type'] == 'search_plan')
    assert 'effective lectures ≥ 4.5' in summary['criteria']
    assert summary['queries'] == []
