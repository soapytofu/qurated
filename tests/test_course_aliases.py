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
