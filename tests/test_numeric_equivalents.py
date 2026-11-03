import pytest
from courses import NO_Q
from pipeline import plan, retrieve, effective_filters
from search_constraints import extract
from test_retrieval import database

@pytest.mark.parametrize('text,key', [
    ('under five hours per week', 'workload_under'), ('fewer than 5 hours a week', 'workload_under'),
    ('less than five hrs/wk', 'workload_under'), ('workload < 5 h', 'workload_under'),
    ('5 hours or less', 'max_workload'), ('workload <= 5 hours', 'max_workload'),
    ('at most 5h/week', 'max_workload'), ('no more than five hours', 'max_workload'),
    ('five hours maximum', 'max_workload'), ('≤5 hours', 'max_workload'),
])
def test_equivalent_workload_forms(text, key):
    result = extract(text)
    assert result['filters'] == {key: 5}
    assert not result['clarifications']

@pytest.mark.parametrize('text,key', [
    ('rated at least 4.5', 'min_overall'), ('overall rating of 4.5 or higher', 'min_overall'),
    ('overall rating four point five+', 'min_overall'), ('rated 4.5+', 'min_overall'),
    ('professor rating at least four and a half', 'min_instructor'),
    ('instructor rating >=4.5', 'min_instructor'), ('lectures rated 4.5+', 'min_lectures'),
])
def test_equivalent_rating_forms(text, key):
    result = extract(text)
    assert result['filters'] == {key: 4.5}
    assert not result['clarifications']

@pytest.mark.parametrize('text', ['between 3 and 5 hours', '3-5 hour workload', 'at least 5 hours',
    'overall rating between 3 and 4', 'overall rating 3-4', 'rated above 4', 'rated at most 4',
    'rated at least six', 'under minus three hours', 'under forty-one hours', 'not fewer than five hours'])
def test_unsupported_or_invalid_constraints_request_clarification(text):
    p = plan(database(), 'courses ' + text, {})
    assert p['clarifications']
    assert retrieve(database(), p) == []


def test_supported_numeric_words_do_not_weaken_exact_course_guards():
    db = database()
    db.courses['TEST 0']['offerings'] = [{**NO_Q, 'workload_hrs_mean': 12}]
    p = plan(db, 'Recommend TEST 0 only if under five hours', {})
    assert retrieve(db, p) == []


def test_inclusive_and_strict_bounds_exclude_unknowns():
    db = database()
    for i, hours in enumerate([4.9, 5, 5.1, None]):
        db.courses[f'TEST {i}']['offerings'] = [{**NO_Q, 'workload_hrs_mean': hours}]
    assert [c['code'] for c in retrieve(db, plan(db, 'under five hours', {}))] == ['TEST 0']
    assert set(c['code'] for c in retrieve(db, plan(db, 'five hours or less', {}))) == {'TEST 0', 'TEST 1'}


def test_stricter_selected_and_saved_limits_remain_in_force():
    p = plan(database(), 'rated at least 4 and at most five hours', {'max_workload_hrs': 3})
    f, issues = effective_filters(p, {'min_overall': 4.8})
    assert f == {'min_overall': 4.8, 'max_workload': 3, 'term': '2027 Spring'}
    assert not issues


def test_numbers_in_topics_are_not_rewritten():
    assert extract('history of twenty-first century computing')['query'] == 'history of twenty-first century computing'
