import pytest
from courses import instructor_scores, NO_Q
from test_retrieval import database
from pipeline import retrieve
import server


def test_scores_weight_responses_and_ignore_missing_or_invalid_values():
    def instructor(mean,count):
        return {'questions':{'Gives effective lectures or presentations, if applicable':{'mean':mean,'count':count}}}
    scores=instructor_scores({'instructors':[instructor(5,10),instructor(3,30),
        instructor(None,20),instructor(0,50),instructor(5,0),instructor(5,float('inf'))]})
    assert scores['effective_lectures']==3.5
    assert scores['instructor_score'] is None
    assert all(value is None for value in instructor_scores({}).values())


def test_historical_score_filters_match_browse_chat_and_cards():
    db=database()
    for i,scores in enumerate([(4.5,4.8),(3.9,4.9),(4.8,None)]):
        db.courses[f'TEST {i}']['offerings']=[{**NO_Q,'term':'2025 Fall','instructor_score':scores[0],
            'effective_lectures':scores[1]}]
    f={'min_instructor':4,'min_lectures':4.5}
    assert [x['code'] for x in db.browse(f)['cards']]==['TEST 0']
    p={'queries':['Interesting'],'codes':['TEST 1','TEST 2'],'gen_ed':False,'term':'any','max_workload':0}
    assert [x['code'] for x in retrieve(db,p,filters=f)]==['TEST 0']
    card=db.card('TEST 0')
    assert card['effective_lectures']==4.8 and card['q_term']=='2025 Fall'
    assert db.brief('TEST 0')['instructor_score']==4.5
    assert not db.matches(db.courses['TEST 49'],f)


def test_instructor_filter_api_bounds():
    assert server.clean_filters({'min_instructor':'4.5','min_lectures':4})=={'min_instructor':4.5,'min_lectures':4}
    assert server.clean_filters({'min_instructor':float('inf'),'min_lectures':6})=={}


from courses import EXTRA_Q_SCORES, additional_scores

@pytest.mark.parametrize("field,label,section,prefix", EXTRA_Q_SCORES)
def test_all_additional_scores_extract_filter_and_reach_clients(field,label,section,prefix):
    answer = {"mean": 4.25, "count": 10}
    report = {section: {prefix: answer}} if section != "instructors" else {"instructors": [{"questions": {prefix: answer}}]}
    scores = {**additional_scores(report), **instructor_scores(report)}
    assert scores[field] == 4.25
    db = database()
    db.courses["TEST 0"]["offerings"] = [{**NO_Q, **scores, "term": "2025 Fall"}]
    f = server.clean_filters({"min_" + field: "4"})
    assert f == {"min_" + field: 4}
    assert [c["code"] for c in db.browse(f)["cards"]] == ["TEST 0"]
    assert db.brief("TEST 0")[field] == 4.25
    assert db.card("TEST 0")[field] == 4.25
    assert not db.browse({"min_" + field: 4.5})["cards"]
    assert server.clean_filters({"min_" + field: float("nan")}) == {}
    answer["mean"] = 0
    assert {**additional_scores(report), **instructor_scores(report)}[field] is None
