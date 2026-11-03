import pytest
from pipeline import plan, effective_filters, retrieve
from schedule_constraints import extract_schedule, merge_schedule
from test_retrieval import database

@pytest.mark.parametrize('text,expected', [
    ('no classes before 10am', {'earliest_start':600}),
    ('classes start after 10 am', {'earliest_start':601}),
    ('starting at or after noon', {'earliest_start':720}),
    ('no classes before 13:30', {'earliest_start':810}),
    ('no classes on Mondays', {'avoid_days':['M']}),
    ('no Monday classes', {'avoid_days':['M']}),
    ('avoiding Fridays', {'avoid_days':['F']}),
    ('avoid Mon, Wed and Fri', {'avoid_days':['M','W','F']}),
    ('Tuesdays and Thursdays off', {'avoid_days':['T','Th']}),
    ('no Monday classes and no classes before 10am', {'avoid_days':['M'],'earliest_start':600}),
])
def test_schedule_language_matches_structured_filters(text, expected):
    _, f, issues = extract_schedule(text)
    assert f == expected
    assert not issues

@pytest.mark.parametrize('text', ['classes end before noon', 'no classes after 3pm',
    'classes only before 11am', 'avoid Saturdays', 'no weekends', 'start after 10',
    'no classes before 25am', "don't avoid Mondays"])
def test_unsupported_ambiguous_and_negated_schedules_are_clarified(text):
    p = plan(database(), 'courses ' + text, {})
    assert p['clarifications']
    assert retrieve(database(), p) == []


def test_structured_schedule_filters_intersect_and_are_idempotent():
    p = plan(database(), 'no Monday classes starting at or after 10am', {})
    f, issues = effective_filters(p, {'avoid_days':['F'],'earliest_start':660})
    assert not issues
    assert f == {'avoid_days':['F','M'],'earliest_start':660,'term':'2027 Spring'}
    assert effective_filters(p, f)[0] == f


def test_exact_course_retrieval_and_card_use_the_compatible_section():
    db = database(); c = db.courses['TEST 0']
    early = dict(instructors=['Monday teacher'],meetings=[dict(days=['M'],time='8:00am-9:00am')])
    good = dict(instructors=['Tuesday teacher'],meetings=[dict(days=['T'],time='1:00pm-2:00pm')])
    c['catalog']['offered'][0]['sections'] = [early, good]
    p = plan(db, 'TEST 0 no classes on Monday and no classes before 10am', {})
    f, issues = effective_filters(p)
    assert not issues
    candidates = retrieve(db, p)
    assert candidates[0]['next_meets'] == '2027 Spring: T 1:00pm-2:00pm'
    assert db.card('TEST 0', filters=f)['instructors'] == ['Tuesday teacher']
    c['catalog']['offered'][0]['sections'] = [early]
    assert retrieve(db, p) == []


def test_more_options_inherit_previous_schedule():
    history = [{'role':'user','content':'ethics courses no Monday classes'},
               {'role':'assistant','content':'(Recommended: TEST 1)'}]
    p = plan(database(), 'more options', {}, history)
    assert p['schedule_filters'] == {'avoid_days':['M']}
