"""A Q-report's term is separate from the term in which a student wants to enroll."""
import re

TERM = r'(?:Spring|Fall|Autumn)\s*(?:20\d{2})|(?:20\d{2})\s+(?:Spring|Fall|Autumn)'
EVIDENCE = r'(?:Q(?:\s+Guide)?(?:\s+(?:reports?|reviews?))?|reviews?|reports?|ratings?|student comments|comments)'
PATTERNS = [
    re.compile(r'\b(?P<term>' + TERM + r')\s+' + EVIDENCE + r'\b', re.I),
    re.compile(r'\b' + EVIDENCE + r'(?:\s+(?:say|said))?\s+(?:from|for|in|during|of)\s+(?P<term>' + TERM + r')\b', re.I),
    re.compile(r'\bstudents\s+(?:say|said)\s+(?:in|during)\s+(?P<term>' + TERM + r')\b', re.I),
]


def extract(message, db):
    matches = {}
    for pattern in PATTERNS:
        for match in pattern.finditer(message):
            # In "Fall 2025 reviews in Spring 2027", reviews already belongs to Fall.
            # Do not bind the same evidence noun again and consume an enrollment date.
            if not any(match.start() < end and match.end() > start for start, end in matches):
                matches[(match.start(), match.end())] = match
    terms, issues = [], []
    for m in matches.values():
        if re.search(r'\b(?:not|without|except(?: for)?|excluding|avoid|no)(?:\s+using)?\s*$',
                     message[:m.start()], re.I):
            issues.append('Please choose which Q reports to include rather than exclude a Q-report term.')
        parts = re.sub(r'([A-Za-z])(\d)', r'\1 \2', m['term']).title().replace('Autumn', 'Fall').split()
        terms.append(' '.join(parts if parts[0].isdigit() else reversed(parts)))
    terms = list(dict.fromkeys(terms))
    cleaned = message
    for start, end in sorted(matches, reverse=True):
        cleaned = cleaned[:start] + ' ' + cleaned[end:]
    # Evidence-query scaffolding is not a course topic, so it must not dominate retrieval.
    all_reports = re.search(r'\b(?:(?:all|every)\s+(?:Q\s+)?(?:reports?|reviews?)|'
                            r'average across all terms)\b', message, re.I)
    if all_reports and not terms:
        cleaned = cleaned[:all_reports.start()] + ' ' + cleaned[all_reports.end():]
    if terms or all_reports:
        cleaned = re.sub(r'\b(?:what did|what do|tell me what|students (?:say|said)|'
                         r'from|during|average across|average of|use)\b', ' ', cleaned, flags=re.I)
    if len(terms) > 1:
        issues.append('Please choose one Q-report term per search, or use the average across all reports.')
    loaded = {o.get('term') for c in db.courses.values() for o in c.get('offerings', []) if o.get('term')}
    if terms and len(terms) == 1 and terms[0] not in loaded:
        issues.append(f"I don't have Q reports from {terms[0]} in the loaded data. Choose a loaded Q-report term or the average across all reports.")
    return {'message': cleaned, 'filters': ({'q_term': terms[0]} if len(terms) == 1 else
                                           {'q_term': None} if all_reports else {}),
            'clarifications': issues}
