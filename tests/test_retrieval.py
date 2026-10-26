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


def test_named_gen_ed_category_is_required():
    from pipeline import plan
    db = database()
    db.courses['TEST 0']['catalog']['gen_ed'] = 'Aesthetics and Culture'
    db._build_index()
    for question in ['Any aesthetics and culture recommendations?',
                     'Geneds to fulfill aesthetics and culture?',
                     'Aesthetics & Culture gen eds']:
        p = plan(db, question, {})
        assert p['gen_ed_category'] == 'Aesthetics and Culture'
        assert [c['code'] for c in retrieve(db, p)] == ['TEST 0']
        assert retrieve(db, p, taken={'TEST 0'}) == []
        assert retrieve(db, p, filters={'gen_ed': 'Ethics and Civics'}) == []


def test_empty_catalog_and_no_matches_do_not_call_answer_model(monkeypatch):
    import pipeline
    from types import SimpleNamespace
    monkeypatch.setattr(pipeline, 'hedged_stream', lambda *a: (_ for _ in ()).throw(AssertionError('model called')))
    for empty in (True, False):
        db = database()
        if empty:
            db.courses = {}
            db._build_index()
        history = []
        events = list(pipeline.stream_answer(SimpleNamespace(with_options=lambda **kw: None),
            db, history, 'Aesthetics and Culture Gen Eds', {}, [], [], True,
            lambda x: x, lambda x: None, lambda *a: None))
        texts = ' '.join(e.get('text', e.get('delta', '')) for e in events)
        assert ('not been loaded' in texts) if empty else ('loaded catalog' in texts)
        assert not history


def test_workload_cap_never_relaxes_and_also_applies_to_explicit_codes():
    from courses import NO_Q
    db = database()
    db.courses['TEST 0']['offerings'] = [{**NO_Q, 'workload_hrs_mean': 9}]
    p = dict(queries=['Interesting'], codes=['TEST 0'], term='any', gen_ed=False, max_workload=6)
    assert retrieve(db, p) == []
    db.courses['TEST 0']['offerings'][0]['workload_hrs_mean'] = 5
    assert [c['code'] for c in retrieve(db, p)] == ['TEST 0']
    assert retrieve(db, p, filters={'max_workload': 4}) == []


def test_topic_miss_does_not_return_unrelated_filtered_courses():
    from pipeline import plan
    db = database()
    p = plan(db, 'CS classes about underwater basketweaving', {})
    # A general subject request can browse; a specific topic must actually match.
    p['subject'] = 'TEST'
    assert retrieve(db, p) == []
    assert retrieve(db, dict(queries=[''], codes=[], term='any', gen_ed=False, max_workload=0),
                    filters={'subject': 'TEST'})


def test_conflicting_terms_do_not_override_each_other():
    p = dict(queries=['Interesting'], codes=['TEST 0'], term='2027 Spring', gen_ed=False, max_workload=0)
    assert retrieve(database(), p, filters={'term': '2026 Fall'}) == []


def test_light_workload_preserves_stricter_saved_preference():
    from pipeline import plan
    assert plan(database(), 'easy classes', {'max_workload_hrs': 3})['max_workload'] == 3


def test_plain_more_options_preserves_search_and_excludes_all_shown_courses():
    from pipeline import plan, is_follow_up
    from courses import NO_Q
    db = database()
    for course in db.courses.values():
        course['offerings'] = [{**NO_Q, 'workload_hrs_mean': 4}]
    history = [
        {'role': 'user', 'content': 'Ethics and Civics Gen Eds in Fall 2026 under 5 hours'},
        {'role': 'assistant', 'content': '(Recommended: TEST 1, TEST 2)'},
        {'role': 'user', 'content': 'Show me more options'},
        {'role': 'assistant', 'content': '(Recommended: TEST 3, TEST 4)'},
    ]
    assert is_follow_up('more', history)
    p = plan(db, 'more', {}, history)
    assert p['gen_ed_category'] == 'Ethics and Civics'
    assert p['term'] == '2026 Fall'
    assert p['numeric_filters']['workload_under'] == 5
    results = retrieve(db, p)
    assert results
    assert not {'TEST 0', 'TEST 1', 'TEST 2', 'TEST 3', 'TEST 4'} & {c['code'] for c in results}
    assert all(c['gen_ed'] == 'Ethics and Civics' and c['workload_hrs'] < 5 for c in results)
    updated = plan(db, 'More options in Spring 2027 at most 8 hours', {}, history)
    assert updated['term'] == '2027 Spring'
    assert updated['numeric_filters'] == {'max_workload': 8}


def test_more_workload_is_comparison_not_new_search():
    from pipeline import plan
    history = [{'role': 'assistant', 'content': '(Recommended: TEST 1, TEST 2)'}]
    p = plan(database(), 'Which of these has more workload?', {}, history)
    assert not p['queries'] and p['codes'] == ['TEST 1', 'TEST 2']
