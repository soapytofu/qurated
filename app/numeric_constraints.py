"""Normalize equivalent numeric requests without weakening unsupported comparisons."""
import re
from courses import EXTRA_Q_SCORES

SMALL = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
TENS = {'twenty': 20, 'thirty': 30, 'forty': 40}
WORD = '(?:' + '|'.join(SMALL) + r'|(?:twenty|thirty|forty)(?:[ -](?:one|two|three|four|five|six|seven|eight|nine))?)'
NUMBER = rf'(?:[+-]?\d+(?:\.\d+)?|(?:minus\s+|negative\s+)?{WORD}(?:\s+(?:point\s+{WORD}|and\s+a\s+half))?)'
UNIT = r'(?:hours?|hrs?|h)(?:\s*(?:/|per|a)\s*(?:week|wk))?\b'
OP = r'(?:no more than|at most|up to|maximum(?: of)?|max(?: of)?|less than|fewer than|under|below|<=|≤|<)'
PREFIX_WORKLOAD = re.compile(rf'(?<!\w)(?P<op>{OP})\s*(?P<num>{NUMBER})\s*{UNIT}', re.I)
SUFFIX_WORKLOAD = re.compile(rf'(?<!\w)(?P<num>{NUMBER})\s*{UNIT}\s*(?:or less|or fewer|maximum|max)\b', re.I)
RANGE_WORKLOAD = re.compile(rf'\b(?:between\s+{NUMBER}\s+and\s+{NUMBER}|{NUMBER}\s*[-–]\s*{NUMBER})\s*{UNIT}', re.I)
MIN_WORKLOAD = re.compile(rf'(?<!\w)(?:at least|more than|over|above|>=|≥|>)\s*{NUMBER}\s*{UNIT}', re.I)
LABELS = {
    'overall rating': r'overall(?:\s+(?:course\s+)?rating)?|course rating|ratings?|rated',
    'instructor rating': r'(?:instructor|professor)(?: overall)?(?: rating)?',
    'lectures': r'(?:effective )?lectures?(?: rating)?',
    'recommend': r'(?:would )?recommend(?:ation)?(?: rating)?',
    **{label: re.escape(label) for _, label, *_ in EXTRA_Q_SCORES},
}
LABEL = '|'.join(f'(?:{p})' for p in LABELS.values())
SCORE = re.compile(rf'\b(?P<label>{LABEL})\s+(?:rated\s+|of\s+)?'
                   rf'(?:(?P<op>at least|minimum(?: of)?|>=|≥|above|over|>|under|below|less than|<=|≤|<|at most)\s*)?'
                   rf'(?P<num>{NUMBER})(?:\s*(?:/\s*5|out of (?:5|five)))?'
                   rf'(?P<suffix>\s*(?:\+|or higher|or above|or more))?(?![\w.])', re.I)
SCORE_RANGE = re.compile(rf'\b(?:{LABEL})\s+(?:of\s+)?(?:between\s+{NUMBER}\s+and\s+{NUMBER}|{NUMBER}\s*[-–]\s*{NUMBER})(?![\w.])', re.I)


def value(text):
    try:
        return float(text)
    except ValueError:
        text = text.lower().replace('-', ' ')
        sign = -1 if text.startswith(('minus ', 'negative ')) else 1
        text = re.sub(r'^(?:minus|negative)\s+', '', text)
        if ' point ' in text:
            whole, fraction = text.split(' point ', 1)
            if fraction not in SMALL or SMALL[fraction] > 9:
                raise ValueError('Use digits for decimal ratings.')
            return sign * (value(whole) + SMALL[fraction] / 10)
        half = text.endswith(' and a half')
        text = text.removesuffix(' and a half')
        return sign * (sum(SMALL.get(w, TENS.get(w, 0)) for w in text.split()) + (0.5 if half else 0))


def normalize_numbers(message):
    """Return canonical numeric wording plus clarification for unsupported inequalities."""
    issues = []
    def unsupported_workload(match):
        issues.append('Please state one maximum workload in hours/week. Workload ranges and minimums are not supported; you can also use Filters.')
        return ' '
    def unsupported_score(match):
        issues.append('Please state an inclusive minimum Q rating (at least 1–5). Rating ranges and other comparisons are not supported.')
        return ' '
    def workload(match):
        op = match.groupdict().get('op', '').lower()
        canonical = 'under' if op in ('under', 'less than', 'fewer than', 'below', '<') else 'at most'
        try:
            number = value(match['num'])
        except ValueError:
            return unsupported_workload(match)
        return f'{canonical} {number:g} hours'
    def score(match):
        op, suffix = (match['op'] or '').lower(), (match['suffix'] or '').strip()
        if op in ('above', 'over', '>', 'under', 'below', 'less than', '<=', '≤', '<', 'at most') or not (op or suffix):
            return unsupported_score(match)
        label = next(k for k, pattern in LABELS.items() if re.fullmatch(pattern, match['label'], re.I))
        try:
            number = value(match['num'])
        except ValueError:
            return unsupported_score(match)
        return f'{label} at least {number:g}'
    remaining = RANGE_WORKLOAD.sub(unsupported_workload, message)
    remaining = PREFIX_WORKLOAD.sub(workload, remaining)
    remaining = SUFFIX_WORKLOAD.sub(workload, remaining)
    remaining = MIN_WORKLOAD.sub(unsupported_workload, remaining)
    remaining = SCORE_RANGE.sub(unsupported_score, remaining)
    remaining = SCORE.sub(score, remaining)
    return remaining, list(dict.fromkeys(issues))
