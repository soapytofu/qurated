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
