from courses import CourseDB
from pipeline import retrieve


def database():
    db = CourseDB.__new__(CourseDB)
    db.courses = {}
    for i in range(50):
        code = f'TEST {i}'
        db.courses[code] = dict(code=code, title='Interesting course', dept='Test', also_listed_as=[],
            latest_term=None, instructors=[], offerings=[], comments=[], tags=[], summary=None,
            catalog=dict(gen_ed='Ethics and Civics', offered=[dict(
                term='2027 Spring' if i == 0 else '2026 Fall', sections=[])]))
    db._build_index()
    return db


def test_explicit_courses_obey_filters_and_taken():
    db = database()
    p = dict(queries=[], codes=['TEST 1', 'TEST 0'], term='any', gen_ed=False, max_workload=0)
    assert [c['code'] for c in retrieve(db, p, filters={'term': '2027 Spring'})] == ['TEST 0']
    assert retrieve(db, p, taken={'TEST 0'}, filters={'term': '2027 Spring'}) == []


def test_term_filter_before_ranking_cutoff():
    db = database()
    p = dict(queries=['Interesting'], codes=[], term='2027 Spring', gen_ed=True, max_workload=0)
    assert [c['code'] for c in retrieve(db, p)] == ['TEST 0']


def test_unknown_grading_is_not_satisfactory():
    db = database()
    assert not db.matches(db.courses['TEST 0'], {'grading': 'satisfactory'})
    db.courses['TEST 0']['catalog']['grading'] = 'Satisfactory/Unsatisfactory'
    assert db.matches(db.courses['TEST 0'], {'grading': 'satisfactory'})


def test_displayed_meeting_and_model_context_match_filters():
    db = database()
    c = db.courses['TEST 0']
    bad = dict(instructors=['Early teacher'], meetings=[dict(days=['M'], time='8:00am-9:00am')])
    good = dict(instructors=['Late teacher'], meetings=[dict(days=['T'], time='1:00pm-2:00pm')])
    c['catalog']['offered'] = [dict(term='2026 Fall', sections=[bad]),
                               dict(term='2027 Spring', sections=[bad, good])]
    f = dict(term='2027 Spring', earliest_start=600, avoid_days=['M'])
    card = db.browse(f)['cards'][0]
    assert card['instructors'] == ['Late teacher']
    assert card['next_offered'] == '2027 Spring · T 1:00pm-2:00pm'
    p = dict(queries=[], codes=['TEST 0'], term='2027 Spring', gen_ed=False, max_workload=0)
    candidate = retrieve(db, p, filters=f)[0]
    assert candidate['next_meets'] == '2027 Spring: T 1:00pm-2:00pm'
    assert candidate['offered_sections'][0]['sections'][0]['instructors'] == ['Late teacher']
    assert len(candidate['offered_sections'][0]['sections']) == 1


def test_unknown_meeting_cannot_confirm_schedule_fit():
    assert not CourseDB.section_fits({'meetings': [{'days': ['T']}, {'days': []}]}, {'avoid_days': ['M']})


def test_more_options_searches_new_candidates():
    from pipeline import plan
    db = database()
    history = [{'role': 'assistant', 'content': 'Here are some options.\n(Recommended: TEST 1, TEST 2)'}]
    p = plan(db, 'Show me more options like these', {}, history)
    results = retrieve(db, p)
    assert p['queries'] and not p['codes']
    assert results and not {'TEST 1', 'TEST 2'} & {c['code'] for c in results}
    comparison = plan(db, 'Which of these has more workload?', {}, history)
    assert comparison['codes'] == ['TEST 1', 'TEST 2']
