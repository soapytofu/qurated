"""Parse catalog requirements without treating them as unrelated topical keywords."""
import re
from courses import GEN_EDS

QRD = re.compile(r'\b(?:(?:that\s+)?(?:also\s+)?(?:satisf(?:y|ies)|fulfill(?:s)?|meet(?:s)?|count(?:s)? toward)\s+)?(?:the\s+)?(?:QRD|quantitative reasoning with data)(?:\s+requirements?)?\b', re.I)
FORMAT = re.compile(r'\b(?:seminars?|lectures?)(?:\s+(?:courses?|classes?))?\b', re.I)
SMALL = re.compile(r'\bsmall\b(?=(?:\s+[\w-]+){0,3}\s+(?:classes?|courses?|seminars?)\b)|\bsmall\s+(?:classes?|courses?|seminars?)\b', re.I)
NEGATION = re.compile(r"\b(?:no|not|never|except|excluding|avoid|without|don't want|do not want)"
                      r"(?:\s+(?:in|with|any|a|an|the|courses?|classes?|that|satisfy|fulfill)){0,5}\s*$", re.I)


def category_pattern(name):
    words = re.findall(r'[a-z]+', name.lower())
    return r'[\s,]+'.join('(?:and|&)' if w == 'and' else re.escape(w) for w in words)


def extract_catalog(message):
    filters, issues = {}, []
    categories = []
    for name in GEN_EDS:
        for match in re.finditer(category_pattern(name), message, re.I):
            if NEGATION.search(message[:match.start()]):
                issues.append('Please specify the Gen Ed category you want, rather than a category to exclude.')
            categories.append(name)
    if len(set(categories)) > 1:
        issues.append('Please choose one Gen Ed category per search. I cannot safely combine multiple categories with and/or; you can also use Filters.')

    def recognized(match, key, value):
        if NEGATION.search(match.string[:match.start()]):
            issues.append('Please state the catalog requirement or format you want directly, or use Filters.')
        elif key in filters and filters[key] != value:
            issues.append('Please choose one course format per search: Lecture or Seminar.')
        else:
            filters[key] = value
        return ' '
    def small(match):
        # Remove only "small" so the existing course format remains available to parse.
        recognized(match, 'small_class', True)
        return re.sub(r'\bsmall\b', ' ', match.group(), flags=re.I)
    remaining = QRD.sub(lambda m: recognized(m, 'qrd', True), message)
    remaining = SMALL.sub(small, remaining)
    def course_format(match):
        if match.group().lower().startswith('lecture') and re.search(
                r'\b(?:effective|great|good|best|engaging|clear|boring)\s*$', match.string[:match.start()], re.I):
            return match.group()  # Lecture quality is a Q metric, not a course-format constraint.
        return recognized(match, 'format', 'Seminar' if match.group().lower().startswith('seminar') else 'Lecture')
    remaining = FORMAT.sub(course_format, remaining)
    return remaining, filters, list(dict.fromkeys(issues))


def merge_catalog(requested, selected=None):
    merged, issues = dict(selected or {}), []
    for key, value in requested.items():
        if key == 'format' and merged.get(key) and merged[key] != value:
            issues.append(f'Your question asks for {value}, but Filters selects {merged[key]}. Change your question or the selected filter so they agree.')
        elif key in ('format', 'qrd', 'small_class'):
            merged[key] = value
    return merged, issues


def with_catalog_constraints(requested):
    """Attach catalog constraints to every plan branch, preserving inherited constraints."""
    def decorate(finish):
        def wrapped(value):
            result = finish(value)
            filters, issues = merge_catalog(requested, result.get('catalog_filters'))
            return {**result, 'catalog_filters': filters,
                    'clarifications': result.get('clarifications', []) + issues}
        return wrapped
    return decorate
