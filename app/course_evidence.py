"""Deterministic boundaries between catalog facts, historical opinions and unknown policies."""
import re

from courses import UPCOMING, tokenize

HYPOTHETICAL = re.compile(
    r"does(?:n['’]t| not) matter what|regardless of what|even if (?:he|she|they)|"
    r"(?:he|she|they) could teach|would take anything|whatever (?:he|she|they) teach|"
    r"(?:joking|sarcastic|sarcasm)", re.I)
ATTRIBUTED = re.compile(r"\b(?:(?:historical|past|previous) (?:student )?(?:reviews?|offerings?)|"
                        r"(?:past )?students? (?:said|reported)|reviews? (?:say|said|describe|reported))\b", re.I)
CLAUSES = re.compile(r"[;,]|\b(?:but|although|though|however|whereas|while|and|so)\b", re.I)
UNCERTAIN_PREFIX = re.compile(r"\b(?:cannot confirm|can't confirm|cannot establish|"
    r"no evidence (?:that|for)|not (?:known|clear|verified) (?:whether|if|that))\b", re.I)
NEGATED_PREFIX = re.compile(r"\b(?:no|not|without|never|no longer|isn['’]t|aren['’]t|wasn['’]t|weren['’]t)"
                            r"(?:\s+\w+){0,3}\s*$|\bnot (?:true|accurate|correct) that(?:\s+\w+){0,6}\s*$", re.I)
POLICIES = {
    'no_exams': re.compile(r"\b(?:no exams?|without exams?|exam[- ]free)\b", re.I),
    'optional_attendance': re.compile(r"\b(?:no (?:mandatory|required) attendance|"
        r"without (?:mandatory|required) attendance|attendance (?:is )?(?:optional|not (?:mandatory|required)))\b", re.I),
}
NEW = re.compile(r"\b(?:is|a|this|brand)[ -]+new (?:course|class)\b|\bis new\b", re.I)
ABSENCE = re.compile(r"\b(?:not offered|isn['’]t offered|no .{0,100}(?:courses?|classes?).{0,80}"
                     r"(?:offered|available)|no such courses? exist)\b", re.I)
BOUNDED_ABSENCE = re.compile(r"\b(?:cannot confirm|can't confirm|not listed|not in (?:the )?(?:loaded|current) catalog|"
                             r"no matching|couldn['’]t find|not loaded|don't have|do not have|does not mean)\b", re.I)
POLICY_CONFLICTS = {
    'no_exams': re.compile(r"\b(?:(?:exams?|midterms?|final(?: exams?)?)\s+(?:(?:is|are|will be)\s+)?"
                          r"(?:required|mandatory)|(?:required|mandatory)\s+(?:exams?|midterms?|final exams?))\b", re.I),
    'optional_attendance': re.compile(r"\b(?:attendance\s+(?:(?:is|will be|remains)\s+)?(?:required|mandatory)|"
        r"(?:required|mandatory)\s+(?:attendance|(?:for\s+)?(?:lectures?|sections?|labs?)))\b", re.I),
}


def qualified_claim(clause, match):
    """Uncertainty must qualify this claim, not an unrelated attribute or later disclaimer."""
    before, after = clause[:match.start()], clause[match.end():]
    return bool(UNCERTAIN_PREFIX.search(before) or
                (re.search(r"\b(?:whether|if)\b", before, re.I) and
                 re.search(r"\b(?:unknown|not verified|check|confirm)\b", clause, re.I)) or
                re.match(r"\s+(?:is|remains)\s+(?:unknown|not verified)\b", after, re.I))


def affirmative_policy(clause, match):
    before, after = clause[:match.start()], clause[match.end():]
    return not (ATTRIBUTED.search(clause) or qualified_claim(clause, match) or
                NEGATED_PREFIX.search(before) or
                re.search(r"\b(?:if|whether|might|could)\b", before, re.I) or
                re.match(r"\s+(?:(?:may|can|must|should)\b|(?:are|is)\s+(?:optional|missed|skipped)\b)", after, re.I))


def catalog_text(course):
    cat = course.get('catalog') or {}
    return ' '.join(str(cat.get(k) or '') for k in ('description', 'class_notes'))


def scoped_course(course, candidate=None):
    """Keep validation on the same Q-report terms supplied to the answer model."""
    if candidate is None or 'q_terms' not in candidate:
        return course  # Compatibility with callers that do not select report terms.
    terms = set(candidate['q_terms'] or [])
    return {**course, 'comments': [comment for comment in course.get('comments', [])
                                  if comment.get('term') in terms]}


def policies_requested(message):
    # Historical-evidence questions are not requests to enforce a current policy.
    if ATTRIBUTED.search(message):
        return []
    return [key for key, pattern in POLICIES.items() if pattern.search(message)]


def supports_policies(course, requested):
    text = catalog_text(course)
    clauses = re.split(r'[.!?;]', text)
    for key in requested:
        # A policy with an exception or an explicit opposite does not establish the
        # unconditional constraint the student requested (e.g. mandatory sections).
        if any(not NEGATED_PREFIX.search(text[:m.start()])
               for m in POLICY_CONFLICTS[key].finditer(text)):
            return False
        supported = False
        for clause in clauses:
            for match in POLICIES[key].finditer(clause):
                if not affirmative_policy(clause, match):
                    continue
                if key == 'optional_attendance' and re.search(
                        r"\b(?:lectures?|sections?|labs?)\b", clause, re.I):
                    continue  # A policy for one component does not cover every meeting.
                supported = True
        if not supported:
            return False
    return True


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
        targets = courses
        for clause in CLAUSES.split(sentence):
            mentioned = [c for c in courses if re.search(
                r'(?<![A-Za-z0-9])' + r'\s*'.join(re.escape(p) for p in c['code'].split()) +
                r'(?![A-Za-z0-9])', clause, re.I)]
            targets = mentioned or targets
            for course in targets:
                official = catalog_text(course)
                for policy, pattern in POLICIES.items():
                    claims = [m for m in pattern.finditer(clause) if not qualified_claim(clause, m)]
                    if claims and not supports_policies(course, [policy]):
                        past_support = ATTRIBUTED.search(clause) and any(
                            pattern.search(comment['text']) for comment in course.get('comments', []))
                        if not past_support:
                            raise ValueError('unverified current assessment or attendance policy')
                if any(not qualified_claim(clause, m) for m in NEW.finditer(clause)) and not NEW.search(official):
                    raise ValueError('unverified course newness')
                if not UNCERTAIN_PREFIX.search(clause) and only_hypothetical_support(course, [clause]):
                    raise ValueError('hypothetical review mistaken for course fact')
            if ABSENCE.search(clause) and not BOUNDED_ABSENCE.search(clause):
                raise ValueError('unverified nonoffering claim')
