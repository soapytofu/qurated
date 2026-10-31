"""Conservative parsing of explicit search constraints; no model calls or guessed years."""
import re

from term_constraints import extract_terms

from courses import UPCOMING, EXTRA_Q_SCORES

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

SCORE_PLUS = re.compile(r"\b(?P<label>" + '|'.join(f'(?:{v})' for v in SCORE_LABELS.values()) + r")"
                       r"\s+(?:rated\s+)?(?P<value>[+-]?\d+(?:\.\d+)?)\s*\+", re.I)


def negated(match):
    """A negated recognized constraint must not be interpreted as a positive one."""
    return bool(re.search(r"\b(?:not|never|except|excluding|avoid|don't want|do not want)"
                          r"(?:\s+(?:in|during|for|with))?\s*$", match.string[:match.start()], re.I))


def extract(message, today=None):
    """Return remaining topic text, exact numerical filters, term and clarification messages."""
    filters, clarifications = {}, []

    def workload(match):
        if negated(match):
            clarifications.append('Please state the term or numerical limit you want, rather than a negated constraint. You can also use Filters.')
            return ' '
        value = float(match['value'])
        if not 0 < value <= 40:
            clarifications.append('Please use a workload limit greater than 0 and at most 40 hours/week.')
            return ' '
        key = 'workload_under' if match['op'].lower() in ('under', 'less than', 'below') else 'max_workload'
        filters[key] = min(filters.get(key, value), value)
        return ' '

    def score(match):
        if negated(match):
            clarifications.append('Please state the term or numerical limit you want, rather than a negated constraint. You can also use Filters.')
            return ' '
        value = float(match['value'])
        if not 1 <= value <= 5:
            clarifications.append('Q ratings are out of 5. Please choose a minimum between 1 and 5.')
            return ' '
        key = next(key for key, pattern in SCORE_LABELS.items() if re.fullmatch(pattern, match['label'], re.I))
        filters[key] = max(filters.get(key, value), value)
        return ' '

    remaining, chosen_term, term_issues = extract_terms(message, today=today)
    clarifications.extend(term_issues)
    remaining = WORKLOAD.sub(workload, remaining)
    from numeric_constraints import normalize_numbers
    remaining, numeric_issues = normalize_numbers(remaining)
    clarifications.extend(numeric_issues)
    remaining = WORKLOAD.sub(workload, remaining)
    remaining = SCORE.sub(score, remaining)
    remaining = SCORE_PLUS.sub(score, remaining)
    return {'query': remaining, 'filters': filters, 'term': chosen_term,
            'clarifications': list(dict.fromkeys(clarifications))}
