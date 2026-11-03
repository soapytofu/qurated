import pytest
from courses import CourseDB
from guardrails import validate_answer
from test_retrieval import database


def cs_database():
    db = database()
    course = db.courses.pop('TEST 0')
    course['code'] = 'COMPSCI 1240'
    db.courses[course['code']] = course
    db._build_index()
    return db


@pytest.mark.parametrize('code', ['cs124', 'CS 124', 'cs1240', 'CS 1240', 'COMPSCI1240'])
def test_explicit_aliases_resolve_and_respect_taken(code):
    db = cs_database()
    assert db.lookup(code)['code'] == 'COMPSCI 1240'
    assert db.is_taken('COMPSCI 1240', {code})
    result, _ = validate_answer({'picks': [{'code': code, 'reason': 'Matches.', 'quote': ''}],
                                'advice': 'Check the catalog.'},
                               [{'code': 'COMPSCI 1240'}], [], db, lambda *a: None)
    assert result[0]['code'] == 'COMPSCI 1240'
    with pytest.raises(ValueError):
        validate_answer({'picks': [{'code': code, 'reason': 'Matches.', 'quote': ''}],
                         'advice': 'Check the catalog.'}, [], [], db, lambda *a: None)


def test_no_general_trailing_zero_rewrite_and_ambiguous_alias():
    db = cs_database()
    assert db.lookup('TEST 100') is None
    assert db.lookup('CS 125') is None
    first = db.courses['TEST 1']
    second = db.courses['TEST 2']
    first['also_listed_as'] = second['also_listed_as'] = ['OTHER 1']
    assert db.lookup('other1') is None


@pytest.mark.parametrize('query', ['cs124', 'CS 1240', 'COMPSCI1240'])
def test_direct_search_alias_obeys_filters_exclusions_and_pagination(query):
    db = cs_database()
    assert [c['code'] for c in db.browse(query=query)['cards']] == ['COMPSCI 1240']
    assert db.browse(query=query, f={'term': '2026 Fall'})['total'] == 0
    assert db.browse(query=query, exclude={'CS124'})['total'] == 0
    assert db.browse(query=query, offset=1) == {'total': 1, 'cards': []}
    assert db.browse(query=query, limit=0)['cards'] == []


@pytest.mark.parametrize('query', ['cs', 'cs12', 'cs124', 'cs1240'])
def test_taken_autocomplete_accepts_cs_shorthand(query):
    assert cs_database().suggest(query)[0]['code'] == 'COMPSCI 1240'


def test_ambiguous_alias_is_not_automatically_selected_by_browse():
    db = cs_database()
    db.courses['TEST 1']['also_listed_as'] = ['OTHER 1']
    db.courses['TEST 2']['also_listed_as'] = ['OTHER 1']
    db._build_index()
    assert db.lookup('OTHER1') is None
    assert db.browse(query='OTHER1')['cards'] == []


def both_alias_records():
    import copy
    db = cs_database()
    old = copy.deepcopy(db.courses['COMPSCI 1240'])
    old['code'] = 'COMPSCI 124'
    db.courses[old['code']] = old
    db._build_index()
    return db


def test_known_equivalences_exclude_both_loaded_records_when_taken():
    db = both_alias_records()
    assert db.is_taken('COMPSCI 1240', {'CS124'})
    assert db.is_taken('COMPSCI 124', {'CS1240'})
    assert db.browse(query='CS1240', exclude={'CS124'})['total'] == 0


def test_guardrails_canonicalize_to_allowed_equivalent_without_weakening_allowlist():
    db = both_alias_records()
    answer = dict(picks=[dict(code='CS124', reason='Fits your request.', quote='')], advice='Compare the details.')
    picks, _ = validate_answer(answer, [{'code': 'COMPSCI 1240'}], [], db, lambda *a: None)
    assert picks[0]['code'] == 'COMPSCI 1240'
    with pytest.raises(ValueError):
        validate_answer(answer, [{'code': 'TEST 1'}], [], db, lambda *a: None)
    with pytest.raises(ValueError):
        validate_answer(answer, [{'code': 'COMPSCI 1240'}], ['CS124'], db, lambda *a: None)


def test_equivalent_records_are_not_recommended_twice():
    from pipeline import plan, retrieve
    db = both_alias_records()
    p = plan(db, 'Compare CS124 and CS1240', {})
    assert len(retrieve(db, p, k=2)) >= 1
    codes = [c['code'] for c in retrieve(db, p)]
    assert len(set(codes) & {'COMPSCI 124', 'COMPSCI 1240'}) == 1
    answer = dict(picks=[dict(code=c, reason='Fits.', quote='') for c in ['CS124', 'CS1240']], advice='Compare details.')
    picks, _ = validate_answer(answer, [{'code': 'COMPSCI 124'}, {'code': 'COMPSCI 1240'}], [], db, lambda *a: None)
    assert len(picks) == 1


def test_known_renumbered_course_uses_the_record_matching_selected_term():
    from pipeline import plan, retrieve
    db = both_alias_records()
    db.courses['COMPSCI 124']['catalog'] = None
    filters = {'term': '2027 Spring'}
    assert db.browse(query='CS124', f=filters)['cards'][0]['code'] == 'COMPSCI 1240'
    assert retrieve(db, plan(db, 'CS124', {}), filters=filters)[0]['code'] == 'COMPSCI 1240'
    assert retrieve(db, plan(db, 'CS124', {}), taken={'CS124'}, filters=filters) == []


def test_ambiguous_crosslist_is_not_allowed_even_with_one_candidate():
    db = cs_database()
    db.courses['TEST 1']['also_listed_as'] = ['OTHER 1']
    db.courses['TEST 2']['also_listed_as'] = ['OTHER 1']
    answer = dict(picks=[dict(code='OTHER 1', reason='Fits.', quote='')], advice='Check details.')
    with pytest.raises(ValueError):
        validate_answer(answer, [{'code': 'TEST 1'}], [], db, lambda *a: None)


def test_equivalence_lookups_are_cached_per_index_snapshot(monkeypatch):
    db = both_alias_records()
    original = db.lookup
    calls = []
    def lookup(code):
        calls.append(code)
        return original(code)
    monkeypatch.setattr(db, 'lookup', lookup)
    for _ in range(20):
        assert db.is_taken('COMPSCI 1240', {'cs124'})
    assert len(calls) == 2
    db._build_index()
    assert db.is_taken('COMPSCI 1240', {'cs124'})
    assert len(calls) == 4


def test_named_course_does_not_fall_back_to_other_courses_mentioning_its_code():
    from pipeline import plan, retrieve
    db = cs_database()
    db.courses['TEST 1']['catalog']['description'] = 'Students who took CS124 sometimes take this unrelated course.'
    db._build_index()
    p = plan(db, 'Tell me about CS124', {})
    assert [c['code'] for c in retrieve(db, p)] == ['COMPSCI 1240']
    assert retrieve(db, p, taken={'CS124'}) == []
    excluded = plan(db, 'Courses except CS124', {})
    assert not excluded['clarifications']
    assert excluded['exclude'] == ['COMPSCI 1240']
    assert all(c['code'] != 'COMPSCI 1240' for c in retrieve(db, excluded))


@pytest.mark.parametrize('historical_record', [False, True])
@pytest.mark.parametrize('query', ['cs152', 'CS 152', 'COMPSCI152', 'CS1520'])
def test_cs152_finds_current_spring_offering_without_relaxing_filters(query, historical_record):
    from copy import deepcopy
    from pipeline import plan, retrieve
    db = database()
    current = db.courses.pop('TEST 0')
    current.update(code='COMPSCI 1520', title='Programming Languages')
    db.courses[current['code']] = current
    if historical_record:
        old = deepcopy(current)
        old.update(code='COMPSCI 152', catalog=None, latest_term='2024 Spring')
        db.courses[old['code']] = old
    db._build_index()
    filters = {'term': '2027 Spring', 'subject': 'COMPSCI'}
    result = db.browse(query=query, f=filters)
    assert result['total'] == 1
    assert result['cards'][0]['code'] == 'COMPSCI 1520'
    assert result['cards'][0]['term'] == '2027 Spring'
    p = plan(db, query, {})
    assert [c['code'] for c in retrieve(db, p, filters=filters)] == ['COMPSCI 1520']
    assert retrieve(db, p, filters={'term': '2026 Fall'}) == []
    assert retrieve(db, p, filters=filters, taken={'CS152'}) == []
    assert db.browse(query=query, exclude={'CS1520'})['total'] == 0
    picks, _ = validate_answer({'picks': [{'code': 'CS152', 'reason': 'Matches.', 'quote': ''}],
                               'advice': 'Check the current catalog.'},
                              [{'code': 'COMPSCI 1520'}], [], db, lambda *args: None)
    assert picks[0]['code'] == 'COMPSCI 1520'
