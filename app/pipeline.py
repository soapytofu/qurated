"""Retrieve candidate courses, request structured JSON, then validate before displaying it.

Progress events stream immediately; generated cards and prose are released only after the
complete answer passes deterministic checks. Rebenchmark latency after changing this flow.
"""
import json
import queue
import re
import threading
import time
from itertools import zip_longest
import search_planner
import search_constraints

from courses import UPCOMING, GEN_EDS, EXTRA_Q_SCORES, tokenize
from guardrails import POLICY, SAFE_FAILURE, validate_answer

ANSWER_MODEL = "claude-sonnet-5-5"
PRICE = (2.0, 10.0)  # $/MTok in, out for ANSWER_MODEL (cost goes to the server log only)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {"type": "array", "items": {"type": "object", "properties": {
            "code": {"type": "string"}, "reason": {"type": "string"},
            "quote": {"type": "string", "description": "short excerpt copied verbatim from student_comments, or empty"}},
            "required": ["code", "reason", "quote"], "additionalProperties": False}},
        "advice": {"type": "string"},
    },
    "required": ["picks", "advice"],
    "additionalProperties": False,
}

SYSTEM = """You are Qurated, the Harvard Undergraduate Association's course advisor for Harvard College \
students. You get a student's request plus candidate courses retrieved from Harvard Q reports (past students' \
ratings, workload in hours/week, reviews) and the Fall 2026 / Spring 2027 my.harvard catalog (terms offered, \
meeting times, Gen Eds).
Be brief: the student sees your picks as cards that already show ratings, workload, tags and meeting times.
- picks: at most 3 candidates that best fit (fewer if few fit; only candidate codes; if they ask about one specific \
course, pick just that course; [] if none fit or no course pick is needed). If they asked for a Gen Ed, pick only \
courses whose gen_ed is set. Never pick courses the student has already taken. Each reason: one sentence, max 15 \
words, specific to the request. quote: a short excerpt copied exactly from that course's student_comments, or "".
- advice: 1-2 short sentences, max 40 words total (never empty). Name the best pick and why, citing one number \
(rating, hours/week or respondents) or a meeting time; if it matters, flag a small sample or a course with no Q \
reviews. If nothing fits, say so and suggest how to rephrase. Ratings are 1-5; FAS average ~4.3.
- All Q reviews predate Harvard's new grading policy capping A grades (approved May 2026; faculty asked to follow \
it from Fall 2026). If the student asks about grades, an "easy A" or grading leniency, say that past reviews may not \
reflect grading now."""

CODE_RE = re.compile(r"\b[A-Za-z][A-Za-z-]{1,9}\s?\d{1,4}[A-Za-z]{0,3}\b")
REFERENT = re.compile(r"\b(those|these|them|they|which one|of the (two|three|four)|between|compare|vs\.?|versus|"
                      r"the first|the second|the last|that one|either)\b", re.I)
LIGHT_WORKLOAD = re.compile(r"\b(?:(?:light|low|manageable) workload|light (?:courses?|classes?|gen ?eds?)|"
                            r"easy|chill|manageable|less work|not much work)\b", re.I)

MORE_OPTIONS = re.compile(r"\b(?:(?:more|other|different|additional) (?:options|courses|classes|recommendations)|"
                          r"alternatives|something else|(?:show|give)(?: me)? more)\b|^more[?!. ]*$", re.I)

RECOMMENDED = re.compile(r"\(Recommended: ([^)]*)\)")
POOL = re.compile(r"\(Discussing: ([^)]*)\)")


def previous_picks(history):
    """Courses a follow-up refers to: the set under discussion, else the last recommendations."""
    for turn in reversed(history):
        if turn["role"] == "assistant":
            m = POOL.search(turn["content"]) or RECOMMENDED.search(turn["content"])
            return [c.strip() for c in m.group(1).split(",")] if m else []
    return []


def is_follow_up(message, history):
    return bool(previous_picks(history)) and bool(REFERENT.search(message) or MORE_OPTIONS.search(message))


def plan(db, message, preferences, history=()):
    """Extract deterministic constraints before optional semantic query expansion."""
    extracted = search_constraints.extract(message)

    def finish(value):
        return {**value, "numeric_filters": extracted["filters"], "term": extracted["term"],
                "clarifications": extracted["clarifications"]}

    if is_follow_up(message, history) and MORE_OPTIONS.search(message):
        previous = previous_picks(history)
        # Preserve the latest substantive search rather than searching the words "more options".
        baseline = None
        for index in range(len(history) - 1, -1, -1):
            turn = history[index]
            if turn["role"] != "user":
                continue
            if MORE_OPTIONS.search(turn["content"]) and not previous_picks(history[:index]):
                continue  # The original request has expired; fall back to known course titles.
            candidate = plan(db, turn["content"], preferences, history[:index])
            if candidate["queries"] and not candidate["codes"]:
                baseline = candidate
                break
        if baseline is None:
            titles = [(db.lookup(code) or {}).get("title", "") for code in previous]
            baseline = {"queries": [" ".join(titles)], "codes": [], "term": "any", "gen_ed": False,
                        "max_workload": (preferences or {}).get("max_workload_hrs") or 0}
        shown = []
        for turn in history:
            if turn["role"] == "assistant":
                for match in RECOMMENDED.finditer(turn["content"]):
                    shown.extend(code.strip() for code in match.group(1).split(","))
        value = finish({**baseline, "codes": [], "exclude": list(dict.fromkeys(shown + previous))})
        value["term"] = extracted["term"] if extracted["term"] != "any" else baseline["term"]
        value["numeric_filters"] = {**baseline.get("numeric_filters", {}), **extracted["filters"]}
        if any(key in extracted["filters"] for key in ("max_workload", "workload_under")):
            value["max_workload"] = (preferences or {}).get("max_workload_hrs") or 0
            value["light_workload"] = False
            # The new explicit bound replaces the prior conversational bound.
            for key in ("max_workload", "workload_under"):
                if key not in extracted["filters"]:
                    value["numeric_filters"].pop(key, None)
        return value
    if is_follow_up(message, history):
        # "Which of those...": answer over the courses we just recommended, no new search.
        return finish({"queries": [], "codes": previous_picks(history)[:6], "max_workload": 0, "term": "any", "gen_ed": False})
    m = message.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", m.replace("&", "and")).strip()
    category = next((name for name in GEN_EDS if
                     re.sub(r"[^a-z0-9]+", " ", name.lower()).strip() in normalized), None)
    term = extracted["term"]
    light = LIGHT_WORKLOAD.search(message)
    if light and search_constraints.negated(light):
        extracted["clarifications"].append('Please specify the workload limit you want in hours/week, or use Filters.')
        light = None
    pref_cap = (preferences or {}).get("max_workload_hrs") or 0
    codes, suggestions = [], {}
    for raw in CODE_RE.findall(message):
        c = db.lookup(" ".join(raw.split()).upper()) or db.lookup(re.sub(r"(\D)(\d)", r"\1 \2", raw, 1).upper())
        if c and c["code"] not in codes:
            codes.append(c["code"])
        elif not c and (nearby := db.code_suggestions(raw)):
            suggestions[raw] = nearby
    # Term words are handled by the term filter; as search keywords they'd match thousands of reviews.
    query = re.sub(r"\b(spring|fall|autumn|20\d\d|semester|term|next|this|upcoming)\b", " ", extracted["query"], flags=re.I)
    query = re.sub(r"\bgen ?eds?\b|general education", " ", query, flags=re.I) if re.search(r"\bgen ?eds?\b|general education", m) else query
    subject = "COMPSCI" if (re.search(r"\b(?:CS|COMPSCI|computer science)\b", message, re.I)
                            and not re.search(r"\b(not|outside|except|avoid|or)\b", message, re.I)) else None
    # Requirement names and subject constraints are filters, not topical keywords.
    if category:
        pattern = r"[\s,&]+".join(re.escape(word) for word in category.lower().replace(",", "").split())
        pattern = pattern.replace("and", "(?:and|&)")
        query = re.sub(pattern, " ", query, flags=re.I)
    if subject:
        query = re.sub(r"\b(?:CS|COMPSCI|computer science)\b", " ", query, flags=re.I)
    if light:
        query = LIGHT_WORKLOAD.sub(" ", query)
    query = re.sub(r"\b(fulfill|fulfills|satisfy|satisfies|requirements?|interested|someone)\b", " ", query, flags=re.I)
    query = " ".join(tokenize(query))
    return search_planner.deterministic(finish({"queries": [query], "codes": codes, "max_workload": min(6, pref_cap) if light and pref_cap else 6 if light else (pref_cap or 0), "term": term,
            "gen_ed": bool(category or re.search(r"\bgen ?eds?\b|general education", m)),
            "gen_ed_category": category, "code_suggestions": suggestions, "subject": subject,
            "light_workload": bool(light)}))


def describe_filters(f):
    if not f:
        return ""
    names = {"term": "term", "subject": "subject", "min_overall": "overall rating ≥", "min_recommend": "recommend ≥",
             "max_workload": "workload ≤ hours/week:", "workload_under": "workload < hours/week:",
             "min_instructor": "instructor rating ≥", "min_lectures": "effective lectures ≥",
             "any_gen_ed": "any Gen Ed", "gen_ed": "Gen Ed", "divisional": "divisional", "qrd": "QRD",
             "avoid_days": "no classes on", "earliest_start": "no class before (minutes after midnight)",
             "small_class": "small class (cap ≤ 20)", "has_reviews": "has Q reviews", "grading": "grading",
             "format": "format"}
    names.update({"min_" + field: label.lower() + " ≥" for field, label, *_ in EXTRA_Q_SCORES})
    parts = []
    for key, value in f.items():
        if key in ("max_workload", "workload_under"):
            parts.append(f'workload {"<" if key == "workload_under" else "≤"} {value:g} hours/week')
        elif key == "earliest_start":
            hour, minute = divmod(int(value), 60)
            parts.append(f'starts at or after {hour % 12 or 12}:{minute:02d}{"am" if hour < 12 else "pm"}')
        elif key == "avoid_days":
            parts.append('no classes on ' + '/'.join(value))
        elif isinstance(value, bool):
            if value:
                parts.append(names.get(key, key))
        else:
            parts.append(f'{names.get(key, key)} {value:g}' if isinstance(value, (int, float))
                         else f'{names.get(key, key)} {value}')
    return "Required: " + "; ".join(parts)



def effective_filters(p, selected=None):
    """Intersect all requested constraints. Return conflicts instead of choosing a winner."""
    filters = dict(selected or {})
    conflicts = list(p.get("clarifications", []))
    for key, value in (("subject", p.get("subject")), ("gen_ed", p.get("gen_ed_category")),
                       ("term", p.get("term") if p.get("term") in UPCOMING else None)):
        if not value:
            continue
        if filters.get(key) and filters[key] != value:
            conflicts.append(f'Your question asks for {value}, but Filters selects {filters[key]}. '
                             'Change your question or the selected filter so they agree.')
        else:
            filters[key] = value
    if p["gen_ed"] and not filters.get("gen_ed"):
        filters["any_gen_ed"] = True
    requested = dict(p.get("numeric_filters", {}))
    if p["max_workload"]:
        requested["max_workload"] = min(requested.get("max_workload") or p["max_workload"], p["max_workload"])
    for key, value in requested.items():
        combine = max if key.startswith("min_") else min
        filters[key] = combine(filters.get(key, value), value)
    return filters, conflicts


def retrieve(db, p, taken=frozenset(), k=8, filters=None):
    if k <= 0:
        return []
    taken = frozenset(taken) | frozenset(p.get("exclude", []))
    dept = None
    filters, conflicts = effective_filters(p, filters)
    if conflicts:
        return []
    # Over-fetch when a term filter will prune results afterwards (search ranks before the term check).
    limit = 40 if p["term"] in UPCOMING else 10
    filters = filters or {}
    if filters.get("term"):
        limit = 40
    lists = [db.search(q, limit=limit, undergrad=True, exclude=taken, dept=dept, filters=filters,
                       max_workload=p["max_workload"] or None) for q in p["queries"]]
    seen, out = set(), []
    # Interleave query rankings so a literal query cannot crowd out semantic matches.
    for lst in zip_longest(*lists):
        for b in lst:
            if b is None:
                continue
            if b["code"] in seen:
                continue
            if p["term"] in UPCOMING and not (isinstance(b["offered"], list) and p["term"] in b["offered"]):
                continue  # student asked about a specific term; skip courses not offered then
            cross = set((db.lookup(b["code"]) or {}).get("also_listed_as", []))
            if cross & seen:
                continue  # same course under another code
            seen.add(b["code"]); seen |= cross; out.append(b)
    explicit = [db.brief(c["code"], filters=filters) for code in p["codes"][:6]
                if (c := db.filtered_lookup(code, filters, taken))]
    explicit_codes = {b["code"] for b in explicit}
    cands, identities = [], set()
    for candidate in explicit + out:
        keys = db.identity_keys(candidate["code"])
        if keys & identities:
            continue
        identities.update(keys)
        cands.append(candidate)
        if len(cands) >= k:
            break
    enriched = []
    for i, b in enumerate(cands):
        is_explicit = b["code"] in explicit_codes
        n_comments = 3 if is_explicit else (2 if i < 3 else 0)
        d = db.detail(b["code"], max_comments=max(n_comments, 1), compact=True, filters=filters)
        cat = d.get("catalog") or {}
        offered = [{"term": o["term"], "sections": o["sections"][: (30 if is_explicit else 3)],
                    "n_sections": o["n_sections"]} for o in cat.get("offered", []) if o["term"] in UPCOMING]
        enriched.append({**{k2: v for k2, v in b.items() if k2 not in ("sample_comment", "description")},
                         "description": (cat.get("description") or "")[:180] or None,
                         "offered_sections": offered,
                         "student_comments": [x[:200] for x in d.get("comments", [])[:n_comments]]})
    return enriched


HEDGE_AFTER = 2.5  # seconds without a first token before we fire a backup request


def hedged_stream(client, kwargs, hedge_after=HEDGE_AFTER):
    """Stream text deltas from whichever of up to two identical requests starts first.

    API first-token latency occasionally spikes to 15-20s; a second request usually starts in ~1s.
    Yields ("text", delta) items and finally ("final", message)."""
    q = queue.Queue(maxsize=64)
    cancelled = [threading.Event(), threading.Event()]

    def publish(i, kind, payload):
        while not cancelled[i].is_set():
            try:
                q.put((i, kind, payload), timeout=0.1)
                return
            except queue.Full:
                pass

    def worker(i):
        try:
            with client.messages.stream(**kwargs) as stream:
                for ev in stream:
                    if cancelled[i].is_set():
                        return
                    if ev.type == "content_block_delta" and ev.delta.type == "text_delta":
                        publish(i, "text", ev.delta.text)
                publish(i, "final", stream.get_final_message())
        except Exception as e:
            publish(i, "error", e)

    threading.Thread(target=worker, args=(0,), daemon=True).start()
    started, winner, errors = 1, None, []
    deadline = time.monotonic() + hedge_after
    overall_deadline = time.monotonic() + 60
    try:
        while True:
            remaining = overall_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("answer deadline exceeded")
            timeout = min(remaining, max(deadline - time.monotonic(), 0.05)) if winner is None and started == 1 else remaining
            try:
                i, kind, payload = q.get(timeout=timeout)
            except queue.Empty:
                if winner is None and started == 1:
                    threading.Thread(target=worker, args=(1,), daemon=True).start()
                    started = 2
                    continue
                raise TimeoutError("model stream stalled")
            if winner is not None and i != winner:
                continue
            if kind == "error":
                errors.append(payload)
                if winner == i or len(errors) >= started:
                    raise payload
                continue
            if winner is None:
                winner = i
                cancelled[1 - i].set()
            yield kind, payload
            if kind == "final":
                return
    finally:
        for event in cancelled:
            event.set()


def context_line(preferences, favorites, taken):
    parts = []
    prefs = {k: v for k, v in (preferences or {}).items() if v not in (None, "", [], 0, False)}
    if prefs:
        parts.append("Student preferences: " + json.dumps(prefs))
    if favorites:
        parts.append("Favorited: " + ", ".join(favorites))
    if taken:
        parts.append("Already taken (never pick): " + ", ".join(taken))
    return "\n".join(parts)


def stream_answer(client, db, history, message, preferences, favorites, taken, search, sse, card_for, verify_quote,
                  filters=None):
    """Buffer and validate model output before emitting cards or prose to the browser."""
    client = client.with_options(timeout=30.0, max_retries=1)
    t0 = time.perf_counter()
    p, cands = None, []
    planner_cost = 0.0
    if search:
        if not db.courses:
            yield sse({"type": "error", "text": "The course catalog has not been loaded yet. Course recommendations will be available after the catalog is imported."})
            return 0.0, time.perf_counter() - t0
        p = plan(db, message, preferences, history)
        filters, conflicts = effective_filters(p, filters)
        if conflicts:
            yield sse({"type": "text", "delta": " ".join(conflicts)})
            return 0.0, time.perf_counter() - t0
        if p.get("code_suggestions"):
            questions = [f"For {raw}, did you mean {' or '.join(options)}?"
                         for raw, options in p["code_suggestions"].items()]
            yield sse({"type": "text", "delta": " ".join(questions)})
            return 0.0, time.perf_counter() - t0
        yield sse({"type": "status", "text": "Understanding your course request"})
        p = search_planner.expand(client, p, message, history)
        planner_cost = p.get("planner_cost", 0.0)
        yield sse({"type": "search_plan", "queries": [q for q in p["queries"] if q],
                   "criteria": describe_filters(filters) or "No additional filters",
                   "notes": (["Light/manageable workload means at most 6 hours/week, or a stricter saved limit."]
                             if p.get("light_workload") else [])})
        yield sse({"type": "status", "text": "Searching courses"})
        filters = dict(filters or {})
        cands = retrieve(db, p, frozenset(taken), filters=filters)
        print(f"[retrieval] loaded={len(db.courses)} candidates={len(cands)} "
              f"category={p.get('gen_ed_category') or 'any'}", flush=True)
        if not cands:
            yield sse({"type": "text", "delta": "I couldn't find matching courses in the loaded catalog. Try changing your filters or search terms; this does not mean no such courses exist."})
            return planner_cost, time.perf_counter() - t0
    yield sse({"type": "status", "text": "Preparing a grounded answer"})
    # Structured data keeps source text separate from trusted system instructions.
    content = json.dumps({"request": message, "student_context": context_line(preferences, favorites, taken),
                          "active_filters": filters or {}, "candidates": cands})
    kwargs = dict(model=ANSWER_MODEL, max_tokens=2500,
                  system=SYSTEM + POLICY + ("\nNo candidates: picks must be []. Give brief general academic guidance or a greeting when appropriate; do not invent course facts." if not cands else ""),
                  thinking={"type": "between_tools"},
                  output_config={"effort": "low", "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
                  messages=history[-6:] + [{"role": "user", "content": content}])
    buf, final = "", None
    stream = hedged_stream(client, kwargs)
    try:
        for kind, payload in stream:
            if kind == "final":
                final = payload
                break
            buf += payload
            if len(buf) > 20000:
                raise ValueError("answer exceeded output limit")
    finally:
        stream.close()
    if final is None or final.stop_reason != "end_turn":
        yield sse({"type": "error", "text": SAFE_FAILURE})
        return planner_cost + (_cost(final.usage) if final else 0), time.perf_counter() - t0
    try:
        picks, advice = validate_answer(json.loads(buf), cands, taken, db, verify_quote)
    except (ValueError, TypeError, KeyError):
        yield sse({"type": "error", "text": SAFE_FAILURE})
        return planner_cost + _cost(final.usage), time.perf_counter() - t0
    for pick in picks:
        card = db.card(pick["code"], filters=filters)
        if card:
            yield sse({"type": "card", "card": {**card, "reason": pick["reason"], "quote": pick["quote"]}})
    yield sse({"type": "text", "delta": advice})
    summary = advice + ("\n(Recommended: " + ", ".join(x["code"] for x in picks) + ")" if picks else "")
    if p and not p["queries"] and p["codes"]:
        summary += "\n(Discussing: " + ", ".join(c["code"] for c in cands) + ")"
    history += [{"role": "user", "content": message}, {"role": "assistant", "content": summary}]
    return planner_cost + _cost(final.usage), time.perf_counter() - t0


def _cost(u):
    if getattr(u, "provider", None) == "openai":
        return 0.0  # Not a price estimate; server labels OpenAI cost as unavailable.
    inp = (u.input_tokens or 0) + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
    return (inp * PRICE[0] + (u.output_tokens or 0) * PRICE[1]) / 1e6
