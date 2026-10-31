"""Bounded search/discussion state, independent of the model's six-message history."""
from copy import deepcopy
import re

MAX_SHOWN = 500
MAX_DISCUSSION = 30
SOCIAL = re.compile(r"^\s*(?:thanks?(?: you)?|thank you|great(?:,? thanks)?|okay|ok|hello|hi|bye|"
                    r"sounds good|got it|you're welcome)[!. ]*$", re.I)
MORE = re.compile(r"\b(?:(?:more|other|different|additional) (?:options|courses|classes|recommendations)|"
                  r"(?:show|give)(?: me)? more)\b|^more[?!. ]*$", re.I)
REFERENT = re.compile(r"\b(?:those|these|them|they|which one|of the (?:two|three|four)|between|compare|"
                      r"versus|vs|the first|the second|the last|that one|either|the ones?)\b", re.I)
PIVOT = re.compile(r"\b(?:actually|instead|what about|how about|now|rather|change|switch|also)\b", re.I)
MODIFIER = re.compile(r"\b(?:hours?|workload|rated|ratings?|spring|fall|autumn|summer|winter|"
                      r"qrd|seminars?|lectures?|small classes|no .{0,20}classes|after \d|before \d)\b", re.I)
MARKERS = re.compile(r"\((?:Discussing|Recommended): ([^)]*)\)")
FILLER = set("show give more other different additional options recommendations courses classes "
             "like these those them they one ones which choose compare versus vs between first second last "
             "either top two three four instead actually about what how now rather also only just please "
             "okay ok fine works at most fewer than under over take taking want need interested "
             "recommend find suggest me good great interesting any something should would could "
             "per week hours hour hrs hr workload rating ratings rated overall minimum maximum "
             "with without and or in for of the a an to is it be but are do does then".split())


def new_context():
    return {'search': None, 'discussion': None, 'shown': [], 'pending': None}


def clean_plan(plan):
    return deepcopy({k: v for k, v in plan.items() if not k.startswith('_')})


def context_search(message, state):
    context = (state or {}).get('course_context') or {}
    if SOCIAL.fullmatch(message) or not (context.get('search') or context.get('pending')):
        return False
    return bool(context.get('pending') or REFERENT.search(message) or MORE.search(message)
                or PIVOT.search(message) or MODIFIER.search(message))


def _request_text(message):
    value = MORE.sub(' ', message)
    value = re.sub(r"\b(?:instead of|like) (?:these|those|them)\b", ' ', value, flags=re.I)
    value = re.sub(r"\b(?:actually|instead|what about|how about|okay|now|rather|also)\b", ' ', value, flags=re.I)
    return value


def _has_topic(plan):
    words = set(re.findall(r'[a-z]+', ' '.join(plan.get('queries', [])).lower()))
    return bool(words - FILLER)


def _merge(base, fresh, message):
    """Apply explicit amendments; absent/default fields are not instructions to erase state."""
    merged = clean_plan(base)
    merged['max_workload'] = (min(6, fresh.get('max_workload') or 6) if base.get('light_workload')
                              else fresh.get('max_workload', 0))
    for key, value in fresh.items():
        if key in {'queries', 'codes', 'exclude', 'clarifications', 'code_suggestions', 'numeric_filters',
                   'schedule_filters', 'catalog_filters', 'term', 'max_workload', 'light_workload'}:
            continue
        if value not in (None, '', False, [], {}):
            merged[key] = deepcopy(value)
    merged['clarifications'] = list(fresh.get('clarifications', []))
    merged['code_suggestions'] = deepcopy(fresh.get('code_suggestions', {}))
    if fresh.get('term') not in (None, 'any'):
        merged['term'] = fresh['term']
    for field in ('numeric_filters', 'schedule_filters', 'catalog_filters'):
        updates = fresh.get(field, {})
        if not updates:
            continue
        old = dict(merged.get(field, {}))
        if field == 'numeric_filters' and any(k in updates for k in ('max_workload', 'workload_under')):
            for key in ('max_workload', 'workload_under'):
                old.pop(key, None)
            merged['light_workload'] = False
            merged['max_workload'] = fresh.get('max_workload', 0)
        if field == 'schedule_filters' and updates.get('avoid_days'):
            updates = {**updates, 'avoid_days': list(dict.fromkeys(old.get('avoid_days', []) + updates['avoid_days']))}
        merged[field] = {**old, **updates}
    if fresh.get('light_workload'):
        merged['light_workload'] = True
        merged['max_workload'] = fresh['max_workload']
    if re.search(r'\b(?:no workload limit|any workload|workload does not matter|workload doesn.t matter)\b', message, re.I):
        merged['numeric_filters'] = {k: v for k, v in merged.get('numeric_filters', {}).items()
                                     if k not in ('max_workload', 'workload_under', 'min_workload')}
        merged.update(max_workload=fresh.get('max_workload', 0), light_workload=False)
    if re.search(r'\b(?:any term|either semester|no term preference)\b', message, re.I):
        merged['term'] = 'any'
    if re.search(r'\b(?:any|all) (?:Gen Ed categories|Gen Ed category)\b', message, re.I):
        merged.update(gen_ed_category=None, gen_ed=True)
    if re.search(r'\b(?:any|all) subjects\b', message, re.I):
        merged['subject'] = None
    if re.search(r'\b(?:any day|any meeting time|no schedule restrictions)\b', message, re.I):
        merged['schedule_filters'] = {}
    return merged


def resolve(message, context, parse):
    """Parse current content before referents, so explicit choices always take precedence."""
    fresh = parse(_request_text(message))
    pending = context.get('pending') or {}
    baseline = pending.get('plan') or (context.get('search') or {}).get('plan')
    if not baseline or SOCIAL.fullmatch(message):
        if not baseline and MORE.search(message):
            fresh = parse(message)  # no antecedent: retain the request so clarification can ask what to expand
        return {**fresh, '_intent': 'search'}
    if (pending.get('kind') == 'clarification' and baseline.get('review_filters')
            and any('Q report' in issue for issue in baseline.get('clarifications', []))
            and re.fullmatch(r'\s*(?:Spring|Fall|Autumn)\s*20\d{2}[.!? ]*', message, re.I)):
        fresh = parse('Q reports from ' + message)
    more, referent = bool(MORE.search(message)), bool(REFERENT.search(message))
    topic = _has_topic(fresh)
    independent = (re.search(r'\b(?:recommend|find|courses?|classes?|gen ?eds?)\b', message, re.I)
                   and not (more or referent or PIVOT.search(message)))
    if independent:
        return {**fresh, '_intent': 'search'}
    modifiers = (fresh.get('term') not in (None, 'any') or fresh.get('numeric_filters')
                 or fresh.get('schedule_filters') or fresh.get('catalog_filters') or fresh.get('review_filters')
                 or fresh.get('gen_ed_category') or fresh.get('light_workload'))
    contextual = bool(more or referent or PIVOT.search(message) or (not topic and modifiers) or pending)
    if not contextual:
        return {**fresh, '_intent': 'search'}
    if pending.get('kind') == 'question' and topic and not fresh.get('codes'):
        combined = parse(pending.get('message', '') + ' ' + _request_text(message))
        return {**combined, '_intent': 'amend'}
    merged = _merge(baseline, fresh, message)
    if fresh.get('codes'):
        merged.update(codes=fresh['codes'], queries=fresh['queries'])
        return {**merged, '_intent': 'compare' if referent else 'amend'}
    if fresh.get('reference_codes'):
        merged.update(codes=[], queries=fresh['queries'], exclude=fresh.get('exclude', []))
        return {**merged, '_intent': 'search'}
    if topic:
        merged.update(queries=fresh['queries'], codes=[])
        # A new named topic replaces the old topic/category/subject, while term, workload
        # and schedule remain in force. Explicit current category/subject wins.
        for key in ('subject', 'gen_ed_category'):
            merged[key] = fresh.get(key)
        merged['gen_ed'] = fresh.get('gen_ed', False)
        merged.pop('exclude', None)
        if more:
            merged['exclude'] = list(context.get('shown', []))
        return {**merged, '_intent': 'more' if more else 'amend'}
    if more:
        if len(context.get('shown', [])) >= MAX_SHOWN:
            merged['clarifications'] = ['This search has reached its saved-result limit. Start a new course search to explore further.']
            return {**merged, '_intent': 'more'}
        merged.update(codes=[], exclude=list(dict.fromkeys(merged.get('exclude', []) + context.get('shown', []))))
        if not any(merged.get('queries', [])) and baseline.get('codes'):
            # Exact lookups have no discovery query. Let callers provide a catalog-title
            # fallback instead of searching the literal words 'more options'.
            merged['queries'] = context.get('reference_queries') or ['']
        return {**merged, '_intent': 'more'}
    discussion = context.get('discussion') or {}
    if referent and discussion.get('codes') and not modifiers:
        merged.update(queries=[], codes=list(discussion['codes'])[:MAX_DISCUSSION])
        return {**merged, '_intent': 'compare'}
    return {**merged, '_intent': 'amend'}


def remember_attempt(state, plan, filters, message, db, selected_filters=None):
    context = state.setdefault('course_context', new_context())
    intent = plan.get('_intent', 'search')
    clean = clean_plan(plan)
    if intent != 'compare' or not context.get('search'):
        if intent == 'search':
            context['shown'] = []
        context['search'] = {'plan': clean, 'filters': dict(filters), 'message': message[:2000],
                             'selected_filters': dict(selected_filters or {})}
    else:
        # Preserve the discovery query for Show more, but carry updated restrictions.
        old = context['search']['plan']
        context['search']['plan'] = {**clean, 'queries': old.get('queries', []), 'codes': old.get('codes', [])}
        context['search']['filters'] = dict(filters)
        context['search']['selected_filters'] = dict(selected_filters or {})
    if clean.get('codes'):
        context['reference_queries'] = [' '.join((db.lookup(c) or {}).get('title', '') for c in clean['codes'])]
    context['pending'] = {'kind': 'attempt', 'plan': clean, 'message': message[:2000]}
    search = context['search']
    state['last'] = {'plan': deepcopy(search['plan']), 'filters': dict(search['filters']),
                     'shown': list(context['shown'])}


def pending_result(state, kind, options=None):
    pending = state['course_context'].get('pending')
    if pending:
        pending['kind'] = kind
        if options:
            pending['options'] = list(options)[:4]
    if kind != 'question':
        state['course_context']['discussion'] = None


def remember_result(state, plan, candidates, shown, filters, options):
    context = state['course_context']
    context['shown'] = list(dict.fromkeys(context['shown'] + list(shown)))[:MAX_SHOWN]
    if shown:
        pool = [c['code'] for c in candidates] if plan.get('_intent') == 'compare' else list(shown)
        context['discussion'] = {'codes': pool[:MAX_DISCUSSION], 'filters': dict(filters)}
        context['pending'] = None
    else:
        pending_result(state, 'question' if options else 'no_results', options)
    state['last']['shown'] = list(context['shown'])


def from_history(history, parse, db):
    """Compatibility for callers without a ChatState; production uses durable structured state."""
    state = {'course_context': new_context()}
    current = None
    for turn in history:
        if turn['role'] == 'user':
            if SOCIAL.fullmatch(turn['content']):
                current = None
                continue
            current = resolve(turn['content'], state['course_context'], parse)
            remember_attempt(state, current, {}, turn['content'], db)
        elif current is not None:
            found = MARKERS.findall(turn['content'])
            codes = [c.strip() for c in found[-1].split(',') if c.strip()] if found else []
            options = ['clarification'] if '(Asked a clarifying question)' in turn['content'] else []
            remember_result(state, current, [{'code': c} for c in codes], codes, {}, options)
    # A marker-only history can still establish a discussion pool.
    if not state['course_context']['search']:
        for turn in reversed(history):
            found = MARKERS.findall(turn['content']) if turn['role'] == 'assistant' else []
            if found:
                codes = [c.strip() for c in found[-1].split(',') if c.strip()]
                plan = parse(' '.join((db.lookup(c) or {}).get('title', '') for c in codes))
                remember_attempt(state, plan, {}, '', db)
                remember_result(state, plan, [{'code': c} for c in codes], codes, {}, [])
                break
    return state['course_context']
