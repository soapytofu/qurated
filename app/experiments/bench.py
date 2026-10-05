"""Latency / cost / quality bench for answer pipelines ("graph of thought": each variant is a node).

    uv run python experiments/bench.py [--variants V0,V1,...] [--questions 0,1,2]

Writes experiments/results.json. Costs a couple of dollars at most for the full grid.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402  (loads .env, CourseDB, client)
from courses import UPCOMING  # noqa: E402

client, db = server.client, server.db

QUESTIONS = [
    "I want a fun humanities class with a light workload",
    "intro machine learning with python",
    "a Spring 2027 Gen Ed about ethics",
    "something like CS50 but less work",
    "Which Expos 20 sections in Spring 2027 meet Tuesday/Thursday mornings?",
    "is STAT 110 worth it? how hard is it?",
    "I'm pre-med and need an easy science elective that isn't orgo",
    "a small discussion-based seminar on climate policy",
]

# $/MTok (input, output) per model; cache effects ignored for single-shot variants (prompts are small).
PRICES = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
          "claude-opus-5-5-fast": (8.0, 40.0)}


def cost_of(model, usage, fast=False):
    pin, pout = PRICES[model + ("-fast" if fast else "")]
    inp = (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) + \
          (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return (inp * pin + (usage.output_tokens or 0) * pout) / 1e6


# ---------------------------------------------------------------- planner (query rewrite + filters)
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {"type": "array", "items": {"type": "string"},
                    "description": "1-3 short keyword queries for a BM25 course search"},
        "codes": {"type": "array", "items": {"type": "string"}, "description": "course codes the student named"},
        "max_workload": {"type": "number", "description": "hours/week cap if the student wants light work, else 0"},
        "term": {"type": "string", "enum": ["2026 Fall", "2027 Spring", "any"]},
        "dept": {"type": "string", "description": "subject code or department if clearly implied, else empty"},
        "gen_ed": {"type": "boolean", "description": "true if the student wants a Gen Ed course"},
    },
    "required": ["queries", "codes", "max_workload", "term", "dept", "gen_ed"],
    "additionalProperties": False,
}
PLAN_PROMPT = """Turn a Harvard student's course request into search instructions for a keyword (BM25) search over \
course titles, descriptions, Gen Ed categories, section topics and student reviews.
- queries: 1-3 short keyword queries (synonyms help: e.g. "ethics moral philosophy justice"). No filler words.
- codes: course codes the student named explicitly (e.g. "STAT 110", "EXPOS 20"), else [].
- max_workload: e.g. 5 for "light/easy workload", 7 for "manageable", else 0.
- term: the term they asked about, else "any". dept: e.g. "COMPSCI" only if clearly implied, else "".
- gen_ed: true only if they ask for a Gen Ed (General Education) course.
Request: {message}"""


def plan(message):
    r = client.messages.create(model="claude-haiku-4-5", max_tokens=300,
                               messages=[{"role": "user", "content": PLAN_PROMPT.format(message=message)}],
                               output_config={"format": {"type": "json_schema", "schema": PLAN_SCHEMA}})
    text = next(b.text for b in r.content if b.type == "text")
    return json.loads(text), cost_of("claude-haiku-4-5", r.usage)


def heuristic_plan(message):
    m = message.lower()
    term = "2027 Spring" if "spring" in m else "2026 Fall" if "fall" in m else "any"
    light = re.search(r"\b(light|easy|chill|less work|low workload|not much work)\b", m)
    codes = [norm for norm in (" ".join(x.split()).upper() for x in re.findall(r"\b[A-Za-z]{2,8}\s?\d{1,4}[A-Za-z]?\b", message))
             if db.lookup(norm)]
    return {"queries": [message], "codes": codes, "max_workload": 6 if light else 0, "term": term, "dept": "",
            "gen_ed": bool(re.search(r"\bgen ?eds?\b|general education", m))}, 0.0


# ---------------------------------------------------------------- retrieval
def retrieve(p, taken=frozenset(), k=12, lean=False):
    seen, out = set(), []
    dept = "GENED" if p.get("gen_ed") else (p.get("dept") or None)
    lists = [db.search(q, limit=10, undergrad=True, exclude=taken, dept=dept,
                       max_workload=p.get("max_workload") or None) for q in p["queries"][:3]]
    if p.get("max_workload") and not any(lists):  # workload filter too strict: retry without it
        lists = [db.search(q, limit=10, undergrad=True, exclude=taken) for q in p["queries"][:3]]
    for i in range(10):  # round-robin across queries
        for lst in lists:
            if i < len(lst) and lst[i]["code"] not in seen:
                b = lst[i]
                if p.get("term") in UPCOMING and isinstance(b["offered"], list) and p["term"] not in b["offered"]:
                    continue
                cross = set((db.lookup(b["code"]) or {}).get("also_listed_as", []))
                if cross & seen:
                    continue  # same course under another code
                seen.add(b["code"]); seen |= cross; out.append(b)
    explicit = []
    for code in p.get("codes", [])[:2]:
        c = db.lookup(code)
        if c and c["code"] not in seen:
            explicit.append(db.brief(c["code"])); seen.add(c["code"])
        elif c:
            explicit.append(next(b for b in out if b["code"] == c["code"]))
            out = [b for b in out if b["code"] != c["code"]]
    cands = (explicit + out)[:(8 if lean else k)]
    enriched = []
    for i, b in enumerate(cands):
        n_comments = (2 if i < 3 else 0) if lean else (3 if i < 4 else 1)
        d = db.detail(b["code"], max_comments=max(n_comments, 1), compact=True)
        cat = d.get("catalog") or {}
        offered = [{"term": o["term"], "sections": o["sections"][: (30 if b in explicit else 3)],
                    "n_sections": o["n_sections"]} for o in cat.get("offered", []) if o["term"] in UPCOMING]
        enriched.append({**{k2: v for k2, v in b.items() if k2 not in ("sample_comment", "description")},
                         "description": (cat.get("description") or "")[:(180 if lean else 350)] or None,
                         "offered_sections": offered,
                         "student_comments": [x[:(200 if lean else 260)] for x in d.get("comments", [])[:n_comments]]})
    return enriched


# ---------------------------------------------------------------- single-shot answer
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
ANSWER_SYSTEM = """You are Qurated, the Harvard Undergraduate Association's course advisor. You get a student's \
request and candidate courses retrieved from Harvard Q reports (past students' ratings, workload in hours/week, \
reviews) and the Fall 2026 / Spring 2027 my.harvard catalog (terms offered, meeting times, Gen Eds).
Pick the 3-5 candidates that best fit (fewer if few fit; only codes from the candidates; for a question about one \
course, pick just that course). Each reason: 1-2 specific sentences tied to the request, grounded in the data. \
Quote only text copied exactly from that course's student_comments, or leave it empty.
advice: 2-4 short sentences comparing the picks, flagging small-sample ratings (few respondents), courses not \
offered in an upcoming term, and anything to double-check. Plain, warm, concise. Ratings are 1-5; FAS average ~4.3."""


ANSWER_SYSTEM_CONCISE = """You are Qurated, the Harvard Undergraduate Association's course advisor. You get a student's \
request and candidate courses retrieved from Harvard Q reports (past students' ratings, workload in hours/week, \
reviews) and the Fall 2026 / Spring 2027 my.harvard catalog (terms offered, meeting times, Gen Eds).
Be brief: the student sees your picks as cards that already show ratings, workload, tags and meeting times.
- picks: the 3-4 candidates that best fit (fewer if few fit; only candidate codes; if they ask about one specific \
course, pick just that course). If they asked for a Gen Ed, pick only courses whose gen_ed is set. Each reason: one \
sentence, max 20 words, specific to the request. quote: a short excerpt copied exactly from that course's \
student_comments, or "".
- advice: always 2-3 sentences (never empty). Name the best pick and why, citing a number (rating, hours/week, or \
respondents) or a meeting time; flag small samples or anything to double-check. Ratings are 1-5; FAS average ~4.3."""


def answer(message, cands, model="claude-sonnet-5-5", thinking=None, effort="low", fast=False, concise=False):
    kwargs = dict(model=model, max_tokens=4000, system=ANSWER_SYSTEM_CONCISE if concise else ANSWER_SYSTEM,
                  messages=[{"role": "user", "content": f"Request: {message}\n\nCandidates:\n{json.dumps(cands)}"}],
                  output_config={"format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
                                 **({"effort": effort} if effort and model != "claude-haiku-4-5" else {})})
    if thinking:
        kwargs["thinking"] = thinking
    api = client.beta.messages if fast else client.messages
    if fast:
        kwargs.update(speed="fast", betas=["fast-mode-2026-02-01"])
    t0 = time.perf_counter(); t_cards = None; t_first = None; buf = ""
    first_pick = re.compile(r'\{\s*"code"\s*:\s*"[^"]*"\s*,\s*"reason"\s*:\s*"(?:[^"\\]|\\.)*"\s*,\s*"quote"\s*:\s*"(?:[^"\\]|\\.)*"\s*\}')
    with api.stream(**kwargs) as stream:
        for ev in stream:
            if ev.type == "content_block_delta" and getattr(ev.delta, "type", "") == "text_delta":
                buf += ev.delta.text
                if t_first is None and first_pick.search(buf):
                    t_first = time.perf_counter() - t0  # first card is complete -> could render it now
                if t_cards is None and '"advice"' in buf:
                    t_cards = time.perf_counter() - t0  # picks array is complete -> UI can render cards
        final = stream.get_final_message()
    text = next((b.text for b in final.content if b.type == "text"), "{}")
    out = json.loads(text)
    valid = {c["code"] for c in cands}
    out["picks"] = [p for p in out["picks"] if p["code"] in valid]
    return out, cost_of(model, final.usage, fast), t_cards, t_first


# ---------------------------------------------------------------- variants (graph nodes)
def run_single_shot(message, planner, lean=False, **akw):
    t0 = time.perf_counter()
    p, c1 = plan(message) if planner else heuristic_plan(message)
    t_plan = time.perf_counter() - t0
    cands = retrieve(p, lean=lean)
    t_ret = time.perf_counter() - t0
    out, c2, t_cards, t_first = answer(message, cands, **akw)
    total = time.perf_counter() - t0
    return {"picks": out["picks"], "advice": out["advice"], "cost": c1 + c2, "t_total": total,
            "t_cards": (t_ret + t_cards) if t_cards else total, "t_first": (t_ret + t_first) if t_first else None,
            "t_plan": t_plan, "plan": p,
            "n_cands": len(cands)}


def run_agentic(message):
    t0 = time.perf_counter(); t_cards = None; picks = []; text = ""
    for ev in server.chat_stream(("bench@test", f"{message}-{time.time()}"), message, {}, [], []):
        e = json.loads(ev[6:])
        if e["type"] == "cards" and t_cards is None:
            t_cards = time.perf_counter() - t0
            picks = [{"code": c["code"], "reason": c.get("reason", ""), "quote": c.get("quote") or ""} for c in e["cards"]]
        elif e["type"] == "text":
            text += e["delta"]
    total = time.perf_counter() - t0
    return {"picks": picks, "advice": text, "cost": server.LAST_COST, "t_total": total, "t_cards": t_cards or total,
            "t_first": t_cards or total}


VARIANTS = {
    "V0 agentic (current)": lambda m: run_agentic(m),
    "V1 Haiku plan + Sonnet answer": lambda m: run_single_shot(m, True),
    "V2 Haiku plan + Sonnet, thinking off": lambda m: run_single_shot(m, True, thinking={"type": "between_tools"}),
    "V3 Haiku plan + Haiku answer": lambda m: run_single_shot(m, True, model="claude-haiku-4-5", effort=None),
    "V4 no planner + Sonnet, thinking off": lambda m: run_single_shot(m, False, thinking={"type": "between_tools"}),
    "V5 Haiku plan + Opus fast": lambda m: run_single_shot(m, True, model="claude-opus-5-5", effort="low", fast=True),
    "V6 no planner + Haiku answer": lambda m: run_single_shot(m, False, model="claude-haiku-4-5", effort=None),
    "V7 V2 + GenEd filter + concise": lambda m: run_single_shot(m, True, thinking={"type": "between_tools"}, concise=True),
    "V8 V7 without planner": lambda m: run_single_shot(m, False, thinking={"type": "between_tools"}, concise=True),
    "V9 V8 + lean context": lambda m: run_single_shot(m, False, lean=True, thinking={"type": "between_tools"}, concise=True),
}


# ---------------------------------------------------------------- judge
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"fit": {"type": "integer"}, "grounded": {"type": "integer"}, "helpful": {"type": "integer"},
                   "problems": {"type": "array", "items": {"type": "string"}}},
    "required": ["fit", "grounded", "helpful", "problems"], "additionalProperties": False,
}


def judge(question, result):
    facts = []
    for p in result["picks"]:
        card = db.card(p["code"]) or {}
        facts.append({k: card.get(k) for k in ("code", "title", "next_offered", "gen_ed", "overall", "workload_hrs",
                                               "recommend", "responded", "has_q")} | {"reason": p["reason"]})
    prompt = (f"A Harvard course advisor bot answered a student. Grade it 1-5 on each axis.\n"
              f"fit: do the picked courses match what the student asked (topic, constraints like term/workload/Gen Ed)?\n"
              f"grounded: are claims consistent with the course facts below (no invented numbers/times)?\n"
              f"helpful: would this answer genuinely help the student decide?\n"
              f"List concrete problems (empty if none).\n\nStudent: {question}\n\n"
              f"Picked courses with true facts:\n{json.dumps(facts)}\n\nAdvice text:\n{result['advice'][:2500]}")
    r = client.messages.create(model="claude-opus-5-5", max_tokens=2000, output_config={
        "effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
        messages=[{"role": "user", "content": prompt}])
    return json.loads(next(b.text for b in r.content if b.type == "text")), cost_of("claude-opus-5-5", r.usage)


def auto_checks(question, result):
    q = question.lower(); issues = []
    if not result["picks"]:
        issues.append("no cards")
    for p in result["picks"]:
        card = db.card(p["code"]) or {}
        if "spring 2027" in q and "2027 Spring" not in (card.get("next_offered") or "") and \
                "2027 Spring" not in json.dumps((db.lookup(p["code"]) or {}).get("catalog", {}).get("offered", [])):
            issues.append(f"{p['code']} not offered Spring 2027")
        if re.search(r"light|easy|less work", q) and (card.get("workload_hrs") or 0) > 8:
            issues.append(f"{p['code']} workload {card.get('workload_hrs')}")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--questions", default=",".join(str(i) for i in range(len(QUESTIONS))))
    args = ap.parse_args()
    names = [n for n in VARIANTS if any(n.startswith(v.strip().split()[0]) for v in args.variants.split(","))]
    qidx = [int(i) for i in args.questions.split(",")]
    out_path = Path(__file__).parent / "results.json"
    results = json.loads(out_path.read_text()) if out_path.exists() else []
    judge_cost = 0.0
    for name in names:
        for i in qidx:
            q = QUESTIONS[i]
            try:
                r = VARIANTS[name](q)
            except Exception as e:
                print(f"{name} | Q{i}: ERROR {e}", flush=True)
                results.append({"variant": name, "q": i, "error": str(e)}); continue
            j, jc = judge(q, r); judge_cost += jc
            row = {"variant": name, "q": i, "t_first": round(r.get("t_first") or r["t_cards"], 2),
                   "t_plan": round(r.get("t_plan") or 0, 2), "t_cards": round(r["t_cards"], 2), "t_total": round(r["t_total"], 2),
                   "cost": round(r["cost"] or 0, 4), "picks": [p["code"] for p in r["picks"]],
                   "quality": round((j["fit"] + j["grounded"] + j["helpful"]) / 3, 2), "judge": j,
                   "auto_issues": auto_checks(q, r), "advice": r["advice"][:600]}
            results.append(row)
            print(f"{name:38} | Q{i} | 1st card {row['t_first']:4.1f}s | all cards {row['t_cards']:5.1f}s | total {row['t_total']:5.1f}s | "
                  f"${row['cost']:.4f} | quality {row['quality']} | {row['picks']} {row['auto_issues'] or ''}", flush=True)
            out_path.write_text(json.dumps(results, indent=1))
    print(f"judge cost ${judge_cost:.3f}")


if __name__ == "__main__":
    main()
