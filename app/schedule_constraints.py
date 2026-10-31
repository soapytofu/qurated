"""Translate supported schedule requests into the same constraints as the filter UI."""
import re

DAY_NAMES = {'monday': 'M', 'mon': 'M', 'tuesday': 'T', 'tue': 'T', 'tues': 'T',
             'wednesday': 'W', 'wed': 'W', 'thursday': 'Th', 'thu': 'Th', 'thur': 'Th', 'thurs': 'Th',
             'friday': 'F', 'fri': 'F', 'saturday': 'Sa', 'sat': 'Sa', 'sunday': 'Su', 'sun': 'Su'}
DAY = r'(?:' + '|'.join(sorted(DAY_NAMES, key=len, reverse=True)) + r')s?\b'
DAY_LIST = rf'{DAY}(?:\s*(?:,\s*(?:and\s+)?|and\s+|or\s+|/\s*){DAY})*'
AVOID = re.compile(rf"\b(?:no|avoid(?:ing)?|without|can't (?:do|attend)|cannot (?:do|attend))\s+(?:classes?\s+)?(?:on\s+)?(?P<days>{DAY_LIST})(?:\s+classes?)?", re.I)
OFF = re.compile(rf'\b(?P<days>{DAY_LIST})\s+(?:off|free)\b', re.I)
TIME = r'(?:noon|midnight|\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)(?!\w|:)'
EARLIEST = re.compile(rf'\b(?P<op>no\s+(?:classes?\s+)?before|not\s+(?:before|earlier than)|'
                      rf'(?:classes?\s+)?(?:starting|starts?|beginning|begins?)\s+(?:at or after|no earlier than|after)|'
                      rf'(?:only\s+)?after)\s+(?P<time>{TIME})', re.I)
UNSUPPORTED = re.compile(rf'\b(?:(?:ends?|ending|finishes?|finishing)\s+(?:by|before|after)|no\s+(?:classes?\s+)?after|'
                         rf'(?:classes?\s+)?only\s+before)\s+{TIME}|\b(?:avoid(?:ing)?|no)\s+weekends?\b', re.I)
NEGATIVE_PREFIX = re.compile(r"\b(?:not|don't|do not|never)\s*$", re.I)


def parse_time(text):
    text = text.lower().strip().replace('.', '')
    if text in ('noon', 'midnight'):
        return 720 if text == 'noon' else 0
    match = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text)
    if not match:
        return None
    hour, minute, period = int(match[1]), int(match[2] or 0), match[3]
    if minute > 59 or (period and not 1 <= hour <= 12) or (not period and hour > 23):
        return None
    if not period and match[2] is None:
        return None  # "after 10" does not tell us morning or evening.
    return ((hour % 12 + (12 if period == 'pm' else 0)) if period else hour) * 60 + minute


def extract_schedule(message):
    filters, issues = {}, []
    def unsupported(match):
        issues.append('I can enforce days to avoid and an earliest start time. Please use those constraints or Filters; end-time and weekend limits are not supported.')
        return ' '
    def avoid(match):
        if NEGATIVE_PREFIX.search(match.string[:match.start()]):
            issues.append('Please state the days you want to avoid directly, or use Filters.')
            return ' '
        found = []
        for day in re.findall(DAY, match['days'], re.I):
            key = day.lower()
            key = key if key in DAY_NAMES else key[:-1]
            found.append(DAY_NAMES[key])
        if any(day in ('Sa', 'Su') for day in found):
            return unsupported(match)
        filters['avoid_days'] = list(dict.fromkeys(filters.get('avoid_days', []) + found))
        return ' '
    def earliest(match):
        value = parse_time(match['time'])
        if value is None or NEGATIVE_PREFIX.search(match.string[:match.start()]):
            issues.append('Please give an unambiguous earliest start time, such as 10am or 13:30, or use Filters.')
            return ' '
        op = match['op'].lower()
        strict = op.endswith('after') and not op.endswith('at or after')
        value += int(strict)
        if value >= 24 * 60:
            issues.append('Please choose an earliest start time before midnight.')
        else:
            filters['earliest_start'] = max(filters.get('earliest_start', 0), value)
        return ' '
    remaining = UNSUPPORTED.sub(unsupported, message)
    remaining = AVOID.sub(avoid, remaining)
    remaining = OFF.sub(avoid, remaining)
    remaining = EARLIEST.sub(earliest, remaining)
    return remaining, filters, list(dict.fromkeys(issues))


def merge_schedule(requested, selected=None):
    """Intersect day exclusions and earliest times; merging twice is idempotent."""
    merged = dict(selected or {})
    if requested.get('avoid_days'):
        merged['avoid_days'] = list(dict.fromkeys(merged.get('avoid_days', []) + requested['avoid_days']))
    if 'earliest_start' in requested:
        merged['earliest_start'] = max(merged.get('earliest_start', 0), requested['earliest_start'])
    return merged
