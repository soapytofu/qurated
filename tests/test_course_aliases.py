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
