"""Deterministic boundaries between catalog facts, historical opinions and unknown policies."""
import re

from courses import UPCOMING, tokenize

HYPOTHETICAL = re.compile(
    r"does(?:n['’]t| not) matter what|regardless of what|even if (?:he|she|they)|"
    r"(?:he|she|they) could teach|would take anything|whatever (?:he|she|they) teach|"
    r"(?:joking|sarcastic|sarcasm)", re.I)
ATTRIBUTED = re.compile(r"\b(?:(?:historical|past|previous) (?:student )?(?:reviews?|offerings?)|"
                        r"(?:past )?students? (?:said|reported)|reviews? (?:say|said|describe|reported))\b", re.I)
UNCERTAIN = re.compile(r"\b(?:cannot confirm|can't confirm|not verified|unknown|"
                       r"check (?:the )?(?:current|syllabus))\b", re.I)
POLICIES = {
    'no_exams': re.compile(r"\b(?:no exams?|without exams?|exam[- ]free)\b", re.I),
    'optional_attendance': re.compile(r"\b(?:no (?:mandatory|required) attendance|"
        r"without (?:mandatory|required) attendance|attendance (?:is )?(?:optional|not (?:mandatory|required)))\b", re.I),
}
NEW = re.compile(r"\b(?:is|a|this|brand)[ -]+new (?:course|class)\b|\bis new\b", re.I)
ABSENCE = re.compile(r"\b(?:not offered|isn['’]t offered|no .{0,100}(?:courses?|classes?).{0,80}"
                     r"(?:offered|available)|no such courses? exist)\b", re.I)
BOUNDED_ABSENCE = re.compile(r"\b(?:cannot confirm|can't confirm|not listed|not in (?:the )?(?:loaded|current) catalog|"
                             r"no matching|couldn['’]t find|not loaded|don't have|do not have)\b", re.I)


def catalog_text(course):
    cat = course.get('catalog') or {}
    return ' '.join(str(cat.get(k) or '') for k in ('description', 'class_notes'))


def policies_requested(message):
    # Historical-evidence questions are not requests to enforce a current policy.
    if ATTRIBUTED.search(message):
        return []
    return [key for key, pattern in POLICIES.items() if pattern.search(message)]


def supports_policies(course, requested):
    text = catalog_text(course)
    return all(POLICIES[key].search(text) for key in requested)


def policy_limit_reply(requested):
    labels = {'no_exams': 'no exams', 'optional_attendance': 'optional attendance'}
    return ("I can't confirm " + ' and '.join(labels[k] for k in requested) +
            " for a matching course from the loaded catalog. Past student reviews do not establish "
            "the current assessment or attendance rules. Check the current syllabus or ask the course staff; "
            "I can help compare courses using the information that is available.")


def only_hypothetical_support(course, queries):
    """Reject review-only lexical matches when every matching sentence is hypothetical.

    Ordinary review searches ('engaging lectures', 'life changing') remain supported. This
    does not require exact title matching or discard real course evidence in student reviews.
    """
    wanted = set(tokenize(' '.join(queries)))
    if not wanted:
        return False
    cat = course.get('catalog') or {}
    factual = ' '.join([course.get('title', ''), course.get('dept', ''), course.get('code', ''),
                        catalog_text(course),
                        *[s.get('topic') or '' for o in cat.get('offered', []) for s in o.get('sections', [])]])
    if wanted & set(tokenize(factual)):
        return False
    hypothetical = False
    for comment in course.get('comments', []):
        for sentence in re.split(r'(?<=[.!?])\s+', comment['text']):
            if wanted & set(tokenize(sentence)):
                if not HYPOTHETICAL.search(sentence):
                    return False
                hypothetical = True
    return hypothetical


def evidence_summary(course):
    cat = course.get('catalog') or {}
    return {'catalog_terms_loaded': list(UPCOMING),
            'current_policies_confirmed': [k for k in POLICIES if supports_policies(course, [k])],
            'reviews_describe_past_offerings': True,
            'missing_reviews_do_not_establish_newness': True,
            'missing_offering_does_not_establish_nonoffering': True}


def validate_claims(text, courses):
    """Check high-risk factual claims in both early card reasons and final advice.

    These checks supplement schema/quote validation; unsupported course facts must not be
    released just because their code is allowed or their accompanying quote is verbatim.
    """
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        if UNCERTAIN.search(sentence):
            continue
        mentioned = [c for c in courses if re.sub(r'\s+', '', c['code']).lower()
                     in re.sub(r'\s+', '', sentence).lower()]
        targets = mentioned or courses
        for course in targets:
            official = catalog_text(course)
            for policy, pattern in POLICIES.items():
                if pattern.search(sentence) and not supports_policies(course, [policy]):
                    past_support = ATTRIBUTED.search(sentence) and any(
                        pattern.search(comment['text']) for comment in course.get('comments', []))
                    if not past_support:
                        raise ValueError('unverified current assessment or attendance policy')
            if NEW.search(sentence) and not NEW.search(official):
                raise ValueError('unverified course newness')
            if only_hypothetical_support(course, [sentence]):
                raise ValueError('hypothetical review mistaken for course fact')
        if ABSENCE.search(sentence) and not BOUNDED_ABSENCE.search(sentence):
            raise ValueError('unverified nonoffering claim')
