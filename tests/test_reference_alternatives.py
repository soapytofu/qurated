import pytest
import pipeline
from test_retrieval import database


def intro_database():
    db = database()
    rows = list(db.courses.values())[:4]
    db.courses = {}
    for row, code, title in zip(rows, ['COMPSCI 50', 'COMPSCI 32', 'COMPSCI 50X', 'BIO 1'],
                               ['Introduction to Computer Science', 'Introduction to Computer Science with Python',
                                'Introduction to Computer Science', 'Introduction to Biology']):
        row.update(code=code, title=title)
        row['catalog']['offered'][0]['term'] = '2027 Spring'
        row['catalog']['description'] = title
        db.courses[code] = row
    db.courses['COMPSCI 50']['also_listed_as'] = ['COMPSCI 50X']
    db.courses['COMPSCI 50X']['also_listed_as'] = ['COMPSCI 50']
    db._build_index()
    return db


@pytest.mark.parametrize('message', [
    'Recommend a computer science course other than CS50 in Spring 2027',
    'Suggest alternatives to CS50 for Spring 2027',
    'CS50 alternatives for Spring 2027',
    'Find courses like CS50 in Spring 2027',
])
def test_reference_is_not_recommended_or_reintroduced_as_crosslisting(message):
    db = intro_database()
    p = pipeline.plan(db, message, {})
    courses = pipeline.retrieve(db, p)
    assert p['term'] == '2027 Spring'
    assert p['queries'] and not p['codes']
    assert 'COMPSCI 32' in {c['code'] for c in courses}
    assert not {'COMPSCI 50', 'COMPSCI 50X'} & {c['code'] for c in courses}


def test_multiple_exclusions_and_explicit_replacement():
    db = intro_database()
    p = pipeline.plan(db, 'Courses other than CS50 and CS32 in Spring 2027', {})
    assert set(p['exclude']) == {'COMPSCI 50', 'COMPSCI 32'}
    p = pipeline.plan(db, 'Not CS50, but CS32 in Spring 2027', {})
    assert p['codes'] == ['COMPSCI 32']
    assert p['exclude'] == ['COMPSCI 50']


def test_explicit_topic_and_history_do_not_turn_exclusion_into_comparison():
    db = intro_database()
    history = [{'role': 'assistant', 'content': '(Recommended: COMPSCI 50)'}]
    p = pipeline.plan(db, 'Find biology courses other than CS50 in Spring 2027', {}, history)
    assert [c['code'] for c in pipeline.retrieve(db, p)] == ['BIO 1']


def test_ordinary_exact_lookup_and_comparison_still_work():
    db = intro_database()
    assert pipeline.plan(db, 'What is CS50 like?', {})['codes'] == ['COMPSCI 50']
    assert pipeline.plan(db, 'Compare CS50 and CS32', {})['codes'] == ['COMPSCI 50', 'COMPSCI 32']
    similar = pipeline.plan(db, 'Something like CS50 but less work', {})
    assert similar['exclude'] == ['COMPSCI 50']
    assert 'introduction' in ' '.join(similar['queries'])
