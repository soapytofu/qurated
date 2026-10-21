"""Fast single-call answer pipeline (the "V9" winner in experiments/bench.py).

Retrieval happens on the server in milliseconds; one Sonnet call (thinking off) returns
{picks, advice} as structured JSON, which we stream: each card is sent the moment its JSON
object is complete, then the advice text streams word by word. Median ~5s end to end.
"""
import json
import queue
import re
import threading
import time

from courses import UPCOMING

ANSWER_MODEL = "claude-sonnet-5-5"
MAX_CARDS = 3
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

CHAT_SYSTEM = """You are Qurated, the Harvard Undergraduate Association's course advisor. Answer briefly and \
warmly (2-5 sentences) using the conversation so far. If the student needs new course suggestions, tell them to \
ask for them directly. Harvard approved a cap on A grades in May 2026 (faculty asked to follow it from Fall 2026), \
so Q reviews about grading predate it; mention this if grades come up. Details: \
https://oue.fas.harvard.edu/faculty-resources/report-on-grading/"""

CODE_RE = re.compile(r"\b[A-Za-z][A-Za-z-]{1,9}\s?\d{1,4}[A-Za-z]{0,3}\b")
PICK_RE = re.compile(r'\{\s*"code"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"reason"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,'
                     r'\s*"quote"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}')
ADVICE_START = re.compile(r'"advice"\s*:\s*"')
REFERENT = re.compile(r"\b(those|these|them|they|which one|of the (two|three|four)|between|compare|vs\.?|versus|"
                      r"the first|the second|the last|that one|either)\b", re.I)
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
    return bool(previous_picks(history)) and bool(REFERENT.search(message))


def plan(db, message, preferences, history=()):
    """Instant heuristic plan (beat a Haiku planner on latency at near-equal quality)."""
    if is_follow_up(message, history):
        # "Which of those...": answer over the courses we just recommended, no new search.
        return {"queries": [], "codes": previous_picks(history)[:6], "max_workload": 0, "term": "any", "gen_ed": False}
    m = message.lower()
    term = "2027 Spring" if "spring" in m else "2026 Fall" if "fall" in m else "any"
    light = re.search(r"\b(light|easy|chill|less work|low workload|not much work|manageable)\b", m)
    pref_cap = (preferences or {}).get("max_workload_hrs") or 0
    codes = []
    for raw in CODE_RE.findall(message):
        c = db.lookup(" ".join(raw.split()).upper()) or db.lookup(re.sub(r"(\D)(\d)", r"\1 \2", raw, 1).upper())
        if c and c["code"] not in codes:
            codes.append(c["code"])
    # Term words are handled by the term filter; as search keywords they'd match thousands of reviews.
    query = re.sub(r"\b(spring|fall|autumn|20\d\d|semester|term|next|this|upcoming)\b", " ", message, flags=re.I)
    query = re.sub(r"\bgen ?eds?\b|general education", " ", query, flags=re.I) if re.search(r"\bgen ?eds?\b|general education", m) else query
    return {"queries": [" ".join(query.split()) or message], "codes": codes, "max_workload": 6 if light else (pref_cap or 0), "term": term,
            "gen_ed": bool(re.search(r"\bgen ?eds?\b|general education", m))}


def describe_filters(f):
    if not f:
        return ""
    names = {"term": "term", "subject": "subject", "min_overall": "overall rating ≥", "min_recommend": "recommend ≥",
             "max_workload": "workload ≤ hrs/wk", "gen_ed": "Gen Ed", "divisional": "divisional", "qrd": "QRD",
             "avoid_days": "no classes on", "earliest_start": "no class before (minutes after midnight)",
             "small_class": "small class (cap ≤ 20)", "has_reviews": "has Q reviews", "grading": "grading",
             "format": "format"}
    return "Active filters (every candidate already satisfies these): " + "; ".join(
        f"{names.get(k, k)} {v}" for k, v in f.items())


def retrieve(db, p, taken=frozenset(), k=8, filters=None):
    dept = None
    filters = dict(filters or {})
    if p["term"] in UPCOMING:
        filters.setdefault("term", p["term"])
    if p["gen_ed"] and not filters.get("gen_ed"):
        filters["any_gen_ed"] = True
    # Over-fetch when a term filter will prune results afterwards (search ranks before the term check).
    limit = 40 if p["term"] in UPCOMING else 10
    filters = filters or {}
    if filters.get("term"):
        limit = 40
    lists = [db.search(q, limit=limit, undergrad=True, exclude=taken, dept=dept, filters=filters,
                       max_workload=p["max_workload"] or None) for q in p["queries"]]
    if p["max_workload"] and not any(lists):  # text-implied workload cap too strict: relax it (filters still apply)
        lists = [db.search(q, limit=limit, undergrad=True, exclude=taken, dept=dept, filters=filters)
                 for q in p["queries"]]
    if filters and p["queries"] and not any(lists):
        # Keywords + filters found nothing: fall back to the best-rated courses that satisfy the filters.
        lists = [[db.brief(c["code"]) for c in db.browse(filters, exclude=taken, limit=10)["cards"]]]
    seen, out = set(), []
    for lst in lists:
        for b in lst:
            if b["code"] in seen:
                continue
            if p["term"] in UPCOMING and not (isinstance(b["offered"], list) and p["term"] in b["offered"]):
                continue  # student asked about a specific term; skip courses not offered then
            cross = set((db.lookup(b["code"]) or {}).get("also_listed_as", []))
            if cross & seen:
                continue  # same course under another code
            seen.add(b["code"]); seen |= cross; out.append(b)
    explicit = [db.brief(c["code"]) for code in p["codes"][:6]
                if (c := db.lookup(code)) and not db.is_taken(c["code"], taken) and db.matches(c, filters)]
    explicit_codes = {b["code"] for b in explicit}
    cands = (explicit + [b for b in out if b["code"] not in explicit_codes])[:k]
    enriched = []
    for i, b in enumerate(cands):
        is_explicit = b["code"] in explicit_codes
        n_comments = 3 if is_explicit else (2 if i < 3 else 0)
        d = db.detail(b["code"], max_comments=max(n_comments, 1), compact=True)
        cat = d.get("catalog") or {}
        offered = [{"term": o["term"], "sections": o["sections"][: (30 if is_explicit else 3)],
                    "n_sections": o["n_sections"]} for o in cat.get("offered", []) if o["term"] in UPCOMING]
        enriched.append({**{k2: v for k2, v in b.items() if k2 not in ("sample_comment", "description")},
                         "description": (cat.get("description") or "")[:180] or None,
                         "offered_sections": offered,
                         "student_comments": [x[:200] for x in d.get("comments", [])[:n_comments]]})
    return enriched


def _unescape_partial(s):
    """Decode a JSON string body that may be cut off mid-stream; returns (text, finished)."""
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch == '"':
            return "".join(out), True
        if ch == "\\":
            if i + 1 >= len(s):
                break
            nxt = s[i + 1]
            if nxt == "u":
                if i + 6 > len(s):
                    break
                out.append(chr(int(s[i + 2:i + 6], 16))); i += 6; continue
            out.append({"n": "\n", "t": "\t", "r": "", "b": "", "f": ""}.get(nxt, nxt)); i += 2; continue
        out.append(ch); i += 1
    return "".join(out), False


HEDGE_AFTER = 2.5  # seconds without a first token before we fire a backup request


def hedged_stream(client, kwargs, hedge_after=HEDGE_AFTER):
    """Stream text deltas from whichever of up to two identical requests starts first.

    API first-token latency occasionally spikes to 15-20s; a second request usually starts in ~1s.
    Yields ("text", delta) items and finally ("final", message)."""
    q = queue.Queue()
    cancelled = set()

    def worker(i):
        try:
            with client.messages.stream(**kwargs) as stream:
                for ev in stream:
                    if i in cancelled:
                        return
                    if ev.type == "content_block_delta" and ev.delta.type == "text_delta":
                        q.put((i, "text", ev.delta.text))
                q.put((i, "final", stream.get_final_message()))
        except Exception as e:  # noqa: BLE001 - surfaced to the caller below
            q.put((i, "error", e))

    threading.Thread(target=worker, args=(0,), daemon=True).start()
    started, winner, errors = 1, None, []
    deadline = time.monotonic() + hedge_after
    while True:
        timeout = max(deadline - time.monotonic(), 0.05) if (winner is None and started == 1) else 60
        try:
            i, kind, payload = q.get(timeout=timeout)
        except queue.Empty:
            if winner is None and started == 1:
                print("[hedge] no first token yet; firing backup request", flush=True)
                threading.Thread(target=worker, args=(1,), daemon=True).start()
                started = 2
                continue
            raise TimeoutError("model stream stalled")
        if kind == "error":
            errors.append(payload)
            if winner == i or len(errors) >= started:
                raise payload
            continue
        if winner is None:
            winner = i
            cancelled.add(1 - i)  # stop reading the loser
        if i != winner:
            continue
        yield kind, payload
        if kind == "final":
            return


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
    """Yields SSE strings. `history` is a list of plain {"role","content"} turns for this chat (mutated)."""
    client = client.with_options(timeout=30.0, max_retries=1)  # hedging handles slow starts; this catches hard stalls
    t0 = time.perf_counter()
    ctx = context_line(preferences, favorites, taken)
    convo = history[-6:]  # last 3 exchanges are plenty for follow-ups
    if not search:
        content = (ctx + "\n\n" if ctx else "") + message
        text = ""
        for kind, payload in hedged_stream(client, dict(
                model=ANSWER_MODEL, max_tokens=1500, system=CHAT_SYSTEM, thinking={"type": "between_tools"},
                output_config={"effort": "low"}, messages=convo + [{"role": "user", "content": content}])):
            if kind == "final":
                final = payload
                break
            text += payload
            yield sse({"type": "text", "delta": payload})
        history += [{"role": "user", "content": message}, {"role": "assistant", "content": text}]
        return _cost(final.usage), time.perf_counter() - t0

    p = plan(db, message, preferences, history)
    yield sse({"type": "status", "text": "Searching courses"})
    if filters.get("term") if filters else False:
        p["term"] = filters["term"]  # an explicit filter beats a term guessed from the wording
    cands = retrieve(db, p, frozenset(taken), filters=filters)
    yield sse({"type": "status", "text": "Picking your best fits"})
    ctx = "\n".join(x for x in (ctx, describe_filters(filters)) if x)
    content = (ctx + "\n\n" if ctx else "") + f"Request: {message}\n\nCandidates:\n{json.dumps(cands)}"
    valid = {c["code"] for c in cands}
    buf, sent, advice_sent, advice_text = "", 0, 0, ""
    picks = []
    final = None
    for kind, payload in hedged_stream(client, dict(
            model=ANSWER_MODEL, max_tokens=2500, system=SYSTEM, thinking={"type": "between_tools"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
            messages=convo + [{"role": "user", "content": content}])):
        if kind == "final":
            final = payload
            break
        buf += payload
        # Cards: emit each pick as soon as its JSON object closes.
        matches = list(PICK_RE.finditer(buf))
        for m in matches[sent:]:
            code, reason, quote = (json.loads(f'"{g}"') for g in m.groups())
            sent += 1
            if len(picks) >= MAX_CARDS or code not in valid or db.is_taken(code, taken):
                continue
            card = card_for(code)
            if card:
                card["reason"], card["quote"] = reason, verify_quote(card["code"], quote)
                picks.append(card["code"])
                yield sse({"type": "card", "card": card})
        # Advice: stream the string as it's generated.
        a = ADVICE_START.search(buf)
        if a:
            advice_text, _ = _unescape_partial(buf[a.end():])
            if len(advice_text) > advice_sent:
                if advice_sent == 0:
                    yield sse({"type": "status", "text": "Writing advice"})
                yield sse({"type": "text", "delta": advice_text[advice_sent:]})
                advice_sent = len(advice_text)
    if final.stop_reason == "refusal":
        yield sse({"type": "error", "text": "Sorry, I can't help with that one. Try asking about courses."})
    elif not picks and not advice_text:
        yield sse({"type": "error", "text": "I couldn't find a good match. Try describing the course differently."})
    summary = advice_text + (f"\n(Recommended: {', '.join(picks)})" if picks else "")
    if p["queries"] == [] and p["codes"]:  # follow-up: keep the whole set in play for the next "compare..."
        summary += f"\n(Discussing: {', '.join(p['codes'])})"
    history += [{"role": "user", "content": message}, {"role": "assistant", "content": summary or "(no answer)"}]
    return _cost(final.usage), time.perf_counter() - t0


def _cost(u):
    inp = (u.input_tokens or 0) + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
    return (inp * PRICE[0] + (u.output_tokens or 0) * PRICE[1]) / 1e6
