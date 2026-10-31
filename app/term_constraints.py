"""Resolve catalog terms using Harvard's calendar date; clarify unsupported requests."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

from courses import UPCOMING

SEASON = r"spring|fall|autumn|summer|winter"
YEAR = r"(?:20\d{2}|['’]?\d{2})(?!\d)"
TERM = re.compile(
    rf"\b(?:(?P<relative>next|this|upcoming|coming|last|previous)\s+)?"
    rf"(?:(?P<season>{SEASON})(?:\s+(?:of\s+)?(?P<year>{YEAR}))?"
    rf"|(?P<year_first>{YEAR})\s+(?P<season_second>{SEASON}))\b", re.I)
SEMESTER = re.compile(r"\b(?P<relative>next|this|current|last|previous|upcoming)\s+(?:semester|term)\b", re.I)
YEAR_ONLY = re.compile(r"\b(?:next|this|upcoming|coming)\s+(?:academic\s+)?year\b|\b(?:in|for)\s+20\d{2}\b", re.I)
NEGATION = re.compile(
    r"\b(?:no|not|never|except|excluding|avoid|without|don't want|do not want|don't need|do not need)"
    r"(?:\s+(?:looking|searching|interested|courses?|classes?|offered|available|that|are|in|during|for|with|any|the)){0,6}\s*$", re.I)


def campus_today():
    return datetime.now(ZoneInfo('America/New_York')).date()


def extract_terms(message, today=None):
    """Return (remaining query, chosen term, clarifications); today is injectable for tests."""
    today = today or campus_today()
    terms, issues = [], []

    def add(value):
        if value not in UPCOMING:
            issues.append(f'I only have upcoming catalog schedules for {" and ".join(UPCOMING)}; '
                          f'I cannot confirm offerings for {value}. Please choose a loaded term.')
        elif value not in terms:
            terms.append(value)

    def denied(match):
        if NEGATION.search(match.string[:match.start()]):
            issues.append('Please state which term you want, rather than excluding a term. You can also use Filters.')
            return True
        return False

    def season(match):
        if denied(match):
            return ' '
        name = (match['season'] or match['season_second']).title().replace('Autumn', 'Fall')
        raw_year = match['year'] or match['year_first']
        relative = (match['relative'] or '').lower()
        year = int(raw_year.lstrip("'’")) if raw_year else None
        if year is not None and year < 100:
            year += 2000
        start_month = {'Spring': 1, 'Summer': 6, 'Fall': 8, 'Winter': 12}[name]
        resolved = today.year
        if relative in ('next', 'upcoming', 'coming'):
            resolved += today.month >= start_month
        elif relative in ('last', 'previous'):
            resolved -= today.month < start_month
        if relative and year is not None and year != resolved:
            issues.append(f'Your relative term and year disagree ({relative} {name.lower()} versus {year}). Please use one explicit term and year.')
            return ' '
        if year is None:
            if relative:
                year = resolved
            else:
                loaded = next((t for t in UPCOMING if t.endswith(name)), None)
                if loaded:
                    add(loaded)
                    return ' '
                year = today.year
        add(f'{year} {name}')
        return ' '

    def semester(match):
        if denied(match):
            return ' '
        year, name = today.year, 'Fall' if today.month >= 8 else 'Spring'
        relative = match['relative'].lower()
        if relative in ('next', 'upcoming'):
            year, name = (year + 1, 'Spring') if name == 'Fall' else (year, 'Fall')
        elif relative in ('last', 'previous'):
            year, name = (year, 'Spring') if name == 'Fall' else (year - 1, 'Fall')
        add(f'{year} {name}')
        return ' '

    remaining = TERM.sub(season, message)
    remaining = SEMESTER.sub(semester, remaining)
    if YEAR_ONLY.search(remaining):
        issues.append('Please choose an explicit season and year, such as Spring 2027.')
        remaining = YEAR_ONLY.sub(' ', remaining)
    if len(terms) > 1:
        issues.append('Please choose one term per search: ' + ' or '.join(terms) + '.')
    return remaining, terms[0] if len(terms) == 1 else 'any', list(dict.fromkeys(issues))
