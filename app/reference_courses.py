"""Resolve courses used as examples or exclusions without turning them into exact lookups."""
import re
from courses import tokenize

PREFIX = re.compile(r"\b(?:alternatives?\s+(?:to|for)|other than|instead of|except(?: for)?|"
                    r"excluding|avoid|not|similar to|(?:courses?|classes?|something|options?) like)\s*$", re.I)
POSTFIX = re.compile(r"^\s+alternatives?\b", re.I)
CONNECTOR = re.compile(r"^[\s,]*(?:(?:and|or)[\s,]*)?$", re.I)
FILLER = re.compile(r"\b(?:suggest|recommend|find|show|give|me|a|an|the|some|any|courses?|classes?|"
                    r"options?|please|for|in|this|next|semester|term|spring|fall|autumn|20\d\d|"
                    r"but|less|more|light|lighter|easier|manageable|work|workload|hours?|hrs?|per|week|under|at|most|\d+)\b", re.I)


def resolve(db, message, code_pattern):
    references, spans, previous_end = [], [], None
    for match in code_pattern.finditer(message):
        before = message[:match.start()]
        prefix = PREFIX.search(before)
        inherited = previous_end is not None and CONNECTOR.fullmatch(message[previous_end:match.start()])
        postfix = POSTFIX.match(message[match.end():])
        if not (prefix or inherited or postfix):
            previous_end = None
            continue
        raw = match.group()
        course = db.lookup(raw.upper()) or db.lookup(re.sub(r"(\D)(\d)", r"\1 \2", raw, 1).upper())
        if not course:
            continue
        references.append(course)
        spans.append((prefix.start() if prefix else previous_end if inherited else match.start(),
                      match.end() + (postfix.end() if postfix else 0)))
        previous_end = match.end()
    if not references:
        return None
    cleaned = message
    for start, end in reversed(spans):
        cleaned = cleaned[:start] + " " + cleaned[end:]
    # If the reference was the only topic, use its title as the discovery query. Never add
    # review prose: a student mentioning another subject should not redirect alternatives.
    clarifications = []
    # Use the same stopwords as retrieval: a leftover connector such as "and" must
    # not suppress the reference topic and then disappear into an all-course query.
    if not tokenize(FILLER.sub(" ", cleaned)):
        titles = " ".join(dict.fromkeys(c.get('title') or '' for c in references))
        if tokenize(FILLER.sub(" ", titles)):
            cleaned += " about " + titles
        else:
            clarifications.append('Which subject or topic should the alternatives cover?')
    return {"message": cleaned, "exclude": list(dict.fromkeys(c['code'] for c in references)),
            "clarifications": clarifications}
