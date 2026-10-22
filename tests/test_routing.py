import pytest
import server


@pytest.mark.parametrize('question', [
    'Geneds to fulfill aesthetics and culture?',
    'Any aesthetics and culture recommendations?',
    'Aesthetics & Culture',
    'gen eds',
    'general education',
    'Ethics and Civics',
    'Histories, Societies, Individuals',
    'Science and Technology in Society',
])
def test_requirement_requests_always_search(monkeypatch,question):
    class NoRouter:
        def with_options(self, **kwargs):
            pytest.fail('deterministic course request must not be routed by a model')
    monkeypatch.setattr(server,'client',NoRouter())
    assert server.needs_q_data(question,[]) == (True,0.0)
