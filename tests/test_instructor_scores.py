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
    assert instructor_scores({})=={'effective_lectures':None,'instructor_score':None}


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
