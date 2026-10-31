from datetime import date
import pytest
import term_constraints
from search_constraints import extract
from pipeline import plan, effective_filters, retrieve
from test_retrieval import database

NOW = date(2026, 10, 31)

@pytest.mark.parametrize('text,expected', [
    ('next semester', '2027 Spring'), ('this semester', '2026 Fall'),
    ('this fall', '2026 Fall'), ('next spring', '2027 Spring'),
    ('Spring 27', '2027 Spring'), ("Spring '27", '2027 Spring'),
    ('27 Spring', '2027 Spring'), ('Autumn 26', '2026 Fall'),
])
def test_term_spellings_and_relative_calendar(text, expected):
    result = extract('ethics courses in ' + text, today=NOW)
    assert result['term'] == expected
    assert not result['clarifications']
    assert 'ethics' in result['query']

@pytest.mark.parametrize('text', ['next fall', 'Fall 27', 'Summer 2027', 'winter',
    'last semester', 'next year', 'in 2028', 'next fall 2026'])
def test_unloaded_or_ambiguous_terms_are_clarified(text):
    result = extract('courses ' + text, today=NOW)
    assert result['clarifications']

@pytest.mark.parametrize('text', ['no Fall classes', "I'm not looking for Spring 2027 courses",
    'courses not in Spring 2027', 'courses except next semester'])
def test_negative_scope_never_becomes_positive_term(text):
    result = extract(text, today=NOW)
    assert result['term'] == 'any'
    assert result['clarifications']


def test_relative_resolution_uses_injected_campus_date(monkeypatch):
    monkeypatch.setattr(term_constraints, 'campus_today', lambda: NOW)
    db = database()
    p = plan(db, 'ethics courses next fall', {})
    assert effective_filters(p)[1]
    assert retrieve(db, p) == []
    assert extract('next semester', today=date(2026, 3, 1))['term'] == '2026 Fall'
    assert extract('next fall', today=date(2026, 3, 1))['term'] == '2026 Fall'


def test_multiple_and_conflicting_terms_remain_safe(monkeypatch):
    monkeypatch.setattr(term_constraints, 'campus_today', lambda: NOW)
    assert extract('Fall 26 or Spring 27')['clarifications']
    p = plan(database(), 'courses next semester', {})
    filters, issues = effective_filters(p, {'term': '2026 Fall'})
    assert issues
    assert filters['term'] == '2026 Fall'
