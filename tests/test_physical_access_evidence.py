import json

import pytest
import pipeline
import server
from accessibility_evidence import physical_access_reply


@pytest.mark.parametrize('message', [
    'I use a wheelchair. Which Spring 2027 classes have accessible classrooms?',
    'Are the lecture halls physically accessible?',
    'I need step-free access to my classroom.',
    'Does this course have an accessible building?',
])
def test_physical_access_is_honest_without_a_model(monkeypatch, message):
    monkeypatch.setattr(server, '_run_fast_chat', lambda *a, **kw: pytest.fail('model path used'))
    text = ''.join(server._fast_chat_stream({'history': [], 'prior': []}, message, {}, [], [], {}))
    events = [json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: ')]
    reply = ''.join(e.get('delta', '') for e in events)
    assert 'don\'t have verified classroom' in reply
    assert 'not physical access' in reply
    assert not any(e['type'] == 'card' for e in events)
    assert sum(e['type'] == 'done' for e in events) == 1


@pytest.mark.parametrize('message', [
    'Which instructors are accessible outside class?',
    'Recommend courses about disability policy.',
    'I want accessible explanations of calculus.',
])
def test_normal_academic_requests_are_not_blocked(message):
    assert physical_access_reply(message) is None


def test_model_sees_unambiguous_metric_semantics():
    raw = {'code': 'TEST 0', 'accessibility': 4.9, 'overall': 4.2}
    c = pipeline._for_model([raw], 'accessible instructors', {})[0]
    assert 'accessibility' not in c
    assert c['instructor_availability_outside_class'] == 4.9
    assert 'no information about physical classroom' in c['instructor_availability_definition']
    assert raw['accessibility'] == 4.9
