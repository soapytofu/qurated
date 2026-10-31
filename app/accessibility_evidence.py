"""Keep instructor availability separate from physical access information we do not hold."""
import re

PHYSICAL_ACCESS = re.compile(
    r"\b(?:wheelchairs?|step[- ]free|barrier[- ]free|mobility (?:aid|access|impairment)|"
    r"physically accessible|physical access(?:ibility)?|accessible (?:classrooms?|buildings?|"
    r"lecture halls?|campus|rooms?)|(?:classroom|building|room) accessibility|"
    r"(?:ramps?|elevators?|lifts?) (?:access|available|availability))\b", re.I)

PHYSICAL_ACCESS_REPLY = (
    "I don't have verified classroom or building accessibility information, so I can't identify "
    "wheelchair-accessible classes from this catalog. Q reviews' instructor accessibility score "
    "measures availability outside class, not physical access. Confirm the assigned room and "
    "your access needs with the course staff and Harvard's disability access office. "
    "I can still help compare course topics, terms, and meeting times.")


def physical_access_reply(message):
    return PHYSICAL_ACCESS_REPLY if PHYSICAL_ACCESS.search(message) else None


def label_instructor_availability(candidate):
    """Do not expose the ambiguous raw metric name to the answer model."""
    candidate = dict(candidate)
    if "accessibility" in candidate:
        candidate["instructor_availability_outside_class"] = candidate.pop("accessibility")
        candidate["instructor_availability_definition"] = (
            "Past students' 1–5 rating of the instructor's availability outside class. "
            "It contains no information about physical classroom accessibility.")
    return candidate
