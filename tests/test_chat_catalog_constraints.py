import pytest
from catalog_constraints import extract_catalog
from pipeline import plan, effective_filters, retrieve
from test_retrieval import database

@pytest.mark.parametrize('text,expected', [
    ('ethics courses that satisfy QRD', {'qrd': True}),
    ('ethics courses that also fulfill the QRD requirement', {'qrd': True}),
    ('courses for Quantitative Reasoning with Data', {'qrd': True}),
    ('small ethics seminars', {'small_class':True,'format':'Seminar'}),
    ('small classes', {'small_class':True}), ('lecture courses', {'format':'Lecture'}),
])
def test_catalog_requirements_are_structured(text, expected):
    _, filters, issues = extract_catalog(text)
    assert filters == expected
    assert not issues

@pytest.mark.parametrize('text', ['Aesthetics and Culture or Ethics and Civics Gen Eds',
    'Ethics & Civics and Aesthetics & Culture Gen Eds', 'seminars or lectures',
    'not Ethics and Civics Gen Eds', 'no QRD', 'avoid seminars'])
def test_ambiguous_or_negated_catalog_requests_are_clarified(text):
    p = plan(database(), text, {})
    assert p['clarifications']
    assert retrieve(database(), p) == []


def catalog_db():
    db = database()
    for i, course in enumerate(db.courses.values()):
        course['title'] = 'Ethics and Computing'
        course['catalog'].update(qrd='Yes' if i == 0 else 'No', component='Seminar' if i==0 else 'Lecture', enrollment_cap=15 if i==0 else 100)
    db._build_index()
    return db


def test_qrd_combined_with_gen_ed_finds_actual_matches_without_keyword_pollution():
    db = catalog_db()
    p = plan(db, 'Ethics and Civics Gen Ed courses that also satisfy QRD', {})
    assert p['queries'] == ['']
    assert not p['clarifications']
    results = retrieve(db, p)
    assert [c['code'] for c in results] == ['TEST 0']
    assert results[0]['qrd'] == 'Yes'
    assert results[0]['component'] == 'Seminar'
    assert results[0]['enrollment_cap'] == 15


def test_small_seminar_constraints_exclude_large_lectures_and_unknown_caps():
    db = catalog_db()
    p = plan(db, 'small ethics seminars', {})
    assert [c['code'] for c in retrieve(db, p)] == ['TEST 0']
    db.courses['TEST 0']['catalog']['enrollment_cap'] = None
    assert retrieve(db, p) == []


def test_format_conflicts_with_ui_are_explicit():
    p = plan(catalog_db(), 'ethics seminars', {})
    f, issues = effective_filters(p, {'format':'Lecture'})
    assert issues and f['format'] == 'Lecture'
    assert retrieve(catalog_db(), p, filters={'format':'Lecture'}) == []


def test_exact_course_must_satisfy_catalog_requirements():
    db = catalog_db()
    assert retrieve(db, plan(db, 'TEST 1 only if it satisfies QRD', {})) == []


def test_more_options_inherit_catalog_requirements():
    history = [{'role':'user','content':'ethics courses that satisfy QRD'},
               {'role':'assistant','content':'(Recommended: TEST 1)'}]
    p = plan(catalog_db(), 'more options', {}, history)
    assert p['catalog_filters'] == {'qrd':True}


def test_q_rating_words_remain_metrics_not_course_format():
    p = plan(catalog_db(), 'ethics courses effective lectures rated at least 4.5', {})
    assert p['numeric_filters'] == {'min_lectures':4.5}
    assert p['catalog_filters'] == {}


def test_lecture_quality_is_not_misread_as_a_format_requirement():
    assert plan(catalog_db(), 'ethics courses with the best lectures', {})['catalog_filters'] == {}


@pytest.mark.parametrize('quality', ['life changing', 'life-changing', 'thought provoking',
                                    'thought-provoking', 'inspiring', 'transformative'])
def test_qualitative_lecture_search_remains_searchable_without_known_format(quality):
    text = f'{quality} lectures'
    remaining, filters, issues = extract_catalog(text)
    assert remaining == text and filters == {} and not issues
    db = database()  # Catalog component is deliberately unknown.
    db.courses['TEST 0']['comments'] = [{'term': '2026 Spring', 'text': f'The lectures were {quality}.'}]
    db._build_index()
    p = plan(db, text, {})
    assert p['catalog_filters'] == {} and not p['clarifications']
    assert [c['code'] for c in retrieve(db, p)] == ['TEST 0']


def test_lecture_quality_does_not_remove_an_explicit_format_requirement():
    remaining, filters, issues = extract_catalog('lecture courses with life-changing lectures')
    assert filters == {'format': 'Lecture'} and not issues
    assert 'life-changing lectures' in remaining
