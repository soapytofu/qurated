"""Keep instructor availability separate from physical access information we do not hold."""
import re

PHYSICAL_ACCESS = re.compile(
    r"\b(?:wheelchairs?|step[- ]free|barrier[- ]free|mobility (?:aid|access|impairment)|"
    r"physically accessible|physical access(?:ibility)?|accessible (?:classrooms?|buildings?|"
    r"lecture halls?|campus|rooms?)|(?:classroom|building|room) accessibility|"
    r"(?:ramps?|elevators?|lifts?) (?:access|available|availability))\b", re.I)
TOPIC_REQUEST = re.compile(
    r"\b(?:(?:courses?|classes?|seminars?)\s+(?:about|on|covering)|"
    r"study|studying|research(?:ing)?|learn(?:ing)? about)\s+(?P<topic>[^.!?;,]+)", re.I)
TOPIC_BEFORE_COURSE = re.compile(
    r"\b(?P<topic>(?:wheelchair|accessibility|accessible building)[\w\s-]{0,80}?"
    r"\b(?:design|engineering|history|policy|sports|technology))\s+(?:courses?|classes?|seminars?)\b", re.I)
ACCESS_REQUIREMENT = re.compile(
    r"\b(?:with|where|that (?:have|are)|in (?:an? |the )?(?:accessible|wheelchair|step[- ]free|barrier[- ]free))\b", re.I)

PHYSICAL_ACCESS_REPLY = (
    "I don't have verified classroom or building accessibility information, so I can't identify "
    "wheelchair-accessible classes from this catalog. Q reviews' instructor accessibility score "
    "measures availability outside class, not physical access. Confirm the assigned room and "
    "your access needs with the course staff and Harvard's disability access office. "
    "I can still help compare course topics, terms, and meeting times.")


def physical_access_reply(message):
    topics = []
    for pattern in (TOPIC_REQUEST, TOPIC_BEFORE_COURSE):
        for match in pattern.finditer(message):
            start, end = match.span('topic')
            # A topic can coexist with a separate room/access requirement.
            boundary = ACCESS_REQUIREMENT.search(message, start, end)
            topics.append((start, boundary.start() if boundary else end))
    for access in PHYSICAL_ACCESS.finditer(message):
        if not any(start <= access.start() and access.end() <= end for start, end in topics):
            return PHYSICAL_ACCESS_REPLY
    return None


def label_instructor_availability(candidate):
    """Do not expose the ambiguous raw metric name to the answer model."""
    candidate = dict(candidate)
    if "accessibility" in candidate:
        candidate["instructor_availability_outside_class"] = candidate.pop("accessibility")
        candidate["instructor_availability_definition"] = (
            "Past students' 1–5 rating of the instructor's availability outside class. "
            "It contains no information about physical classroom accessibility.")
    return candidate
