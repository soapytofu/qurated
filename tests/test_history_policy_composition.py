"""Historical review scope must not become a current-policy enrollment constraint."""
from test_q_metric_sorting import history_database
from pipeline import plan, retrieve


def test_dated_review_question_keeps_historical_policy_attribution():
    db = history_database()
    db.courses['TEST 0']['comments'] = [{'term': '2023 Fall', 'text': 'There were no exams.'}]
    request = plan(db, 'What did reviews say in Fall 2023 about TEST 0 having no exams?', {})
    assert request['review_filters'] == {'q_term': '2023 Fall'}
    assert request['required_policies'] == []
    candidates = retrieve(db, request)
    assert [candidate['code'] for candidate in candidates] == ['TEST 0']
    assert candidates[0]['q_terms'] == ['2023 Fall']
    assert candidates[0]['student_comments'] == ['There were no exams.']
