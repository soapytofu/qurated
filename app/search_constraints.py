"""Conservative parsing of explicit search constraints; no model calls or guessed years."""
import re

from courses import UPCOMING, EXTRA_Q_SCORES

TERM = re.compile(r"\b(?:(?P<season>spring|fall|autumn)(?:\s+(?:of\s+)?(?P<year>20\d{2}))?"
                  r"|(?P<year_first>20\d{2})\s+(?P<season_second>spring|fall|autumn))\b", re.I)
WORKLOAD = re.compile(r"\b(?:workload\s+(?:of\s+)?)?"
                      r"(?P<op>under|less than|below|at most|no more than|up to|maximum(?: of)?|max(?: of)?)"
                      r"\s+(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?:hours?|hrs?)"
                      r"(?:\s*(?:/|per|a)\s*(?:week|wk))?\b", re.I)
SCORE_LABELS = {
    'min_overall': r'overall(?:\s+(?:course\s+)?rating)?|course rating',
    'min_recommend': r'(?:would )?recommend(?:ation)?(?: rating)?',
    'min_instructor': r'instructor(?: overall)?(?: rating)?',
    'min_lectures': r'(?:effective )?lectures?(?: rating)?',
    **{'min_' + field: re.escape(label) for field, label, *_ in EXTRA_Q_SCORES},
}
SCORE = re.compile(r"\b(?P<label>" + '|'.join(f'(?:{v})' for v in SCORE_LABELS.values()) + r")"
                   r"\s+(?:rated\s+)?(?:at least|minimum(?: of)?|>=|≥)\s*(?P<value>[+-]?\d+(?:\.\d+)?)"
                   r"(?:\s*(?:/\s*5|out of 5))?(?![\d.])", re.I)


def extract(message):
    """Return remaining topic text, exact numerical filters, term and clarification messages."""
    terms, filters, clarifications = [], {}, []

    def term(match):
        season = (match['season'] or match['season_second']).title().replace('Autumn', 'Fall')
        year = match['year'] or match['year_first']
        value = f'{year} {season}' if year else next((t for t in UPCOMING if t.endswith(season)), None)
        if value not in UPCOMING:
            clarifications.append(f'I only have upcoming catalog schedules for {" and ".join(UPCOMING)}; '
                                  f'I cannot confirm offerings for {value or season}. Please choose a loaded term.')
        elif value not in terms:
            terms.append(value)
        return ' '

    def workload(match):
        value = float(match['value'])
        if not 0 < value <= 40:
            clarifications.append('Please use a workload limit greater than 0 and at most 40 hours/week.')
            return ' '
        key = 'workload_under' if match['op'].lower() in ('under', 'less than', 'below') else 'max_workload'
        filters[key] = min(filters.get(key, value), value)
        return ' '

    def score(match):
        value = float(match['value'])
        if not 1 <= value <= 5:
            clarifications.append('Q ratings are out of 5. Please choose a minimum between 1 and 5.')
            return ' '
        key = next(key for key, pattern in SCORE_LABELS.items() if re.fullmatch(pattern, match['label'], re.I))
        filters[key] = max(filters.get(key, value), value)
        return ' '

    remaining = TERM.sub(term, message)
    remaining = WORKLOAD.sub(workload, remaining)
    remaining = SCORE.sub(score, remaining)
    if len(terms) > 1:
        clarifications.append('Please choose one term per search: ' + ' or '.join(terms) + '.')
    return {'query': remaining, 'filters': filters, 'term': terms[0] if len(terms) == 1 else 'any',
            'clarifications': list(dict.fromkeys(clarifications))}
