"""Qurated: the HUA course advisor, grounded in Q reports.

    uv run uvicorn server:app --reload --port 8765
"""
import json
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path

import anthropic
import httpx2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import db as store
import pipeline
from courses import CATALOG_DIR, CourseDB, REPORTS_DIR, TAGS_FILE

load_dotenv(Path(__file__).parent / ".env")

MODEL = "claude-sonnet-5-5"
# $ per million tokens for MODEL, used for the per-reply cost line.
PRICE = {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20}
# Force IPv4: on some networks (e.g. campus Wi-Fi) IPv6 to the API silently hangs ~16s before falling back,
# which showed up as random 17-20s "API stalls". IPv4 connects in ~20ms.
client = anthropic.Anthropic(http_client=anthropic.DefaultHttpxClient(
    transport=httpx2.HTTPTransport(local_address="0.0.0.0")))
db = CourseDB()
sessions: dict[tuple, list] = {}  # (user email, session id) -> append-only message history
prior_questions: dict[tuple, list] = {}  # same key -> the student's earlier messages, for the router

# ---- Access control ----
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
ALLOWED_DOMAINS = {d.strip().lower() for d in os.environ.get("ALLOWED_EMAIL_DOMAINS", "college.harvard.edu").split(",") if d.strip()}
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}  # see costs
DAILY_LIMIT = int(os.environ.get("DAILY_QUESTION_LIMIT", "40"))  # per student, protects your API credit
questions_today: dict[tuple, int] = defaultdict(int)  # (email, date) -> count
_report_count = len(list(REPORTS_DIR.glob("*.json")))

SYSTEM = """You are Qurated, the Harvard Undergraduate Association's course advisor for Harvard College students. You help students find FAS courses \
that fit their interests, requirements, schedule and workload tolerance.

You have tools over two sources:
- Harvard Q reports (official student course evaluations) from Fall 2025 and Spring 2026: overall ratings, \
workload in hours/week outside class, "would you recommend" scores, instructor ratings, why students enrolled, \
and students' written advice to future students.
- The my.harvard FAS course catalog for Fall 2026 and Spring 2027: which terms a course is offered, meeting \
days/times, instructors, description, Gen Ed category, Divisional Distribution, QRD, level, consent and syllabus link.

How to work:
- Search before recommending. One or two well-chosen searches usually suffice; open details only for the \
few courses you're seriously considering (at most 3-4 per reply).
- Ground every claim about a course in the Q data. When you describe what a course is like, lean on what \
students actually wrote. Never invent ratings, workloads or quotes.
- Search results include tags and a one-line student consensus distilled from each course's comments; \
use them to shortlist, then open details to confirm before quoting students.
- Show your picks with show_course_cards (usually 3-6 courses). Each card needs a short, specific reason \
tied to this student's preferences, and may include one short quote copied exactly from that course's comments.
- After the cards, add a few sentences of advice: tradeoffs between the picks, and anything worth checking.
- Students are usually planning Spring 2027. Prefer courses offered in an upcoming term, mention when and how \
often they meet, and say clearly if a course isn't in the Fall 2026 / Spring 2027 catalog.
- Some courses are new or haven't been reviewed (no_q_data); describe them from the catalog and say there are \
no Q reviews yet. Q ratings describe past offerings, and the instructor may have changed.
- Use the catalog's Gen Ed / Divisional Distribution / QRD fields for requirement questions; don't guess them.
- Students are undergraduates: skip courses whose level is "Primarily for Graduate Students" unless asked, \
and skip independent study, thesis and dissertation listings.
- Ratings are on a 1-5 scale. FAS-wide averages are around 4.2-4.3 overall. A rating from a handful of \
respondents is noisy; say so when it matters.
- Keep replies concise and warm, like a sharp upperclassman giving advice. Use plain text and short paragraphs."""

TOOLS = [
    {
        "name": "search_courses",
        "description": "Keyword search over courses (titles, catalog descriptions, Gen Ed categories, departments, "
                       "instructors, and Q report student comments), with optional filters. Returns brief summaries ranked by relevance. An empty "
                       "query with filters returns the best-reviewed matching courses.",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, e.g. 'machine learning python projects'"},
                "dept": {"type": "string", "description": "Department name or subject code fragment, e.g. 'Computer Science' or 'COMPSCI'"},
                "max_workload": {"type": "number", "description": "Max mean hours/week outside class"},
                "min_overall": {"type": "number", "description": "Min course overall rating (1-5)"},
                "min_recommend": {"type": "number", "description": "Min recommend score (1-5)"},
                "limit": {"type": "integer", "description": "Max results (default 8, max 15)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_course_details",
        "description": "Full data for one course: catalog info (terms offered, meeting times, description, Gen Ed, "
                       "syllabus) plus every Q report offering (ratings, workload, enrollment reasons) and student comments.",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Course code, e.g. 'COMPSCI 50'"}},
            "required": ["code"],
        },
    },
    {
        "name": "show_course_cards",
        "description": "Display recommended courses to the student as visual cards with ratings, workload and a "
                       "favorite button. Call once per reply with your final picks, in order of fit.",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "courses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "reason": {"type": "string", "description": "1-2 sentences on why it fits this student"},
                            "quote": {"type": "string", "description": "Optional short excerpt copied verbatim from a student comment"},
                        },
                        "required": ["code", "reason"],
                    },
                }
            },
            "required": ["courses"],
        },
    },
]


def _maybe_reload():
    """Pick up newly scraped reports while the scraper is still running."""
    global _report_count
    n = len(list(REPORTS_DIR.glob("*.json")))
    tags_mtime = TAGS_FILE.stat().st_mtime if TAGS_FILE.exists() else 0
    sections = CATALOG_DIR / "sections.jsonl"
    cat_mtime = max([sections.stat().st_mtime if sections.exists() else 0]
                    + [(CATALOG_DIR / "details").stat().st_mtime if (CATALOG_DIR / "details").exists() else 0])
    if n != _report_count or tags_mtime != db.tags_mtime or cat_mtime != getattr(db, "loaded_catalog_mtime", None):
        db.loaded_catalog_mtime = cat_mtime
        db.reload()
        _report_count = n


def _verified_quote(code, quote):
    """Only show a quote if it really appears in that course's comments."""
    c = db.lookup(code)
    if not quote or not c:
        return None
    needle = " ".join(quote.strip().strip('"“”').split()).lower().rstrip(".…")
    for comment in c["comments"]:
        if needle and needle in " ".join(comment["text"].split()).lower():
            return quote.strip().strip('"“”')
    return None


def run_tool(name, args, taken=frozenset()):
    """Returns (tool_result_content, ui_event_or_None). Courses in `taken` are never returned."""
    if not isinstance(args, dict):
        raise ValueError("tool input must be an object")
    if name == "search_courses":
        if not isinstance(args.get("query"), str):
            raise ValueError("query must be a string")
        limit = max(1, min(int(args.get("limit") or 8), 15))
        results = db.search(args["query"], dept=args.get("dept"), max_workload=args.get("max_workload"),
                            min_overall=args.get("min_overall"), min_recommend=args.get("min_recommend"), limit=limit,
                            exclude=taken)
        return json.dumps({"count": len(results), "results": results}), None
    if name == "get_course_details":
        detail = db.detail(str(args.get("code", "")), max_comments=8, compact=True)
        if not detail:
            return json.dumps({"error": f"No course found for {args.get('code')!r}. Try search_courses."}), None
        return json.dumps(detail), None
    if name == "show_course_cards":
        items = args.get("courses")
        if not isinstance(items, list):
            raise ValueError("courses must be a list")
        cards, missing, already = [], [], []
        for item in items:
            card = db.card(str(item.get("code", "")))
            if not card:
                missing.append(item.get("code"))
                continue
            if db.is_taken(card["code"], taken):
                already.append(card["code"])
                continue
            card["reason"] = item.get("reason", "")
            card["quote"] = _verified_quote(card["code"], item.get("quote"))
            cards.append(card)
        msg = f"Displayed {len(cards)} cards."
        if missing:
            msg += f" Not found (not shown): {missing}."
        if already:
            msg += f" Not shown because the student already took them: {already}."
        return msg, {"type": "cards", "cards": cards}
    raise ValueError(f"unknown tool {name}")


STATUS = {
    "search_courses": lambda a: f"Searching courses for “{a.get('query') or a.get('dept') or 'top courses'}”",
    "get_course_details": lambda a: f"Reading up on {a.get('code')}",
    "show_course_cards": lambda a: "Putting together your picks",
}


ROUTER_MODEL = "claude-haiku-4-5"
ROUTER_SCHEMA = {
    "type": "object",
    "properties": {"needs_q_data": {"type": "boolean"}},
    "required": ["needs_q_data"],
    "additionalProperties": False,
}
ROUTER_PROMPT = """You route questions for a Harvard course-advice chatbot that can search Q report data (past students' course ratings, workloads and reviews).

needs_q_data = true whenever the student wants course suggestions or information about particular courses or professors, even if phrased as a statement ("a chill science class", "something like CS50"): recommending, finding, comparing, or asking about ratings, workload, difficulty, schedules or what a course is like.
needs_q_data = false only for messages clearly answerable with no course lookups: greetings, thanks, general academic or study advice, how Harvard systems work (what a Gen Ed is, how to declare a concentration), or follow-ups fully answerable from courses already discussed in this conversation.
If in doubt, answer true.

Earlier in this conversation the student asked: {history}
New message: {message}"""


COURSE_WORDS = re.compile(r"\b(class|classes|course|courses|seminar|lecture|professor|prof|instructor|workload|"
                          r"recommend|suggest|take|taking|elective|easy|hard|[A-Z]{2,}\s?\d+[A-Z]?)\b", re.I)


def needs_q_data(message, prior_questions):
    """Cheap pre-check so simple messages skip retrieval entirely. Leans toward searching."""
    if COURSE_WORDS.search(message):
        return True, 0.0
    try:
        r = client.messages.create(
            model=ROUTER_MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": ROUTER_PROMPT.format(
                history=" | ".join(prior_questions[-3:]) or "(nothing yet)", message=message)}],
            output_config={"format": {"type": "json_schema", "schema": ROUTER_SCHEMA}},
        )
        cost = (r.usage.input_tokens * 1.0 + r.usage.output_tokens * 5.0) / 1e6  # Haiku 4.5 $/token
        if r.stop_reason != "end_turn":
            return True, cost
        text = next(b.text for b in r.content if b.type == "text")
        return bool(json.loads(text)["needs_q_data"]), cost
    except Exception as e:
        print(f"[router] failed, defaulting to search: {e}", flush=True)
        return True, 0.0


def user_turn(message, preferences, favorites, taken=()):
    parts = []
    prefs = {k: v for k, v in (preferences or {}).items() if v not in (None, "", [], False)}
    if prefs:
        parts.append("My preferences: " + json.dumps(prefs))
    if favorites:
        parts.append("Courses I've favorited so far: " + ", ".join(favorites))
    if taken:
        parts.append("Courses I've already taken (never recommend these): " + ", ".join(taken))
    parts.append(message)
    return "\n\n".join(parts)


def sse(event):
    return f"data: {json.dumps(event)}\n\n"


LAST_COST = 0.0
PREFETCH_LIMIT = 8  # retrieve-first is off by default: in A/B tests it added cost without saving a step


def prefetch(message, taken):
    """Retrieve-first RAG: search on the student's own words before the model's first step,
    so most answers skip a whole search round trip. The model can still search again."""
    results = db.search(message, limit=PREFETCH_LIMIT, exclude=frozenset(taken), undergrad=True)
    if not results:
        return None
    return ("Initial search results for this message (from search_courses on the message text). Use them if "
            "they fit; search again with better keywords or filters only if they don't:\n"
            + json.dumps({"count": len(results), "results": results}))


def chat_stream(session_id, message, preferences, favorites, taken=(), use_prefetch=False):
    _maybe_reload()
    messages = sessions.setdefault(session_id, [])
    prior = prior_questions.setdefault(session_id, [])
    yield sse({"type": "status", "text": "Thinking"})
    search, router_cost = needs_q_data(message, prior)
    prior.append(message[:300])
    turn = user_turn(message, preferences, favorites, taken)
    if search and use_prefetch:
        yield sse({"type": "status", "text": "Searching courses"})
        found = prefetch(message, taken)
        if found:
            turn += "\n\n" + found
    if not search:
        turn += "\n\n(No course lookup is needed for this message; reply directly in plain text.)"
    messages.append({"role": "user", "content": turn})
    usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    rounds = 0
    try:
        for rounds in range(1, 9):  # cap tool-use rounds per reply
            with client.beta.messages.stream(
                model=MODEL,
                max_tokens=64000,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
                # When the pre-check says no Q data is needed, answer directly without any retrieval.
                tool_choice={"type": "auto"} if search else {"type": "none"},
                output_config={"effort": "low"},  # chat-style work does well at low effort; cheapest
                cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield sse({"type": "text", "delta": event.delta.text})
                    elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                        yield sse({"type": "status", "text": "Looking through courses"})
                final = stream.get_final_message()

            messages.append({"role": "assistant", "content": final.content})
            u = final.usage
            usage["input"] += u.input_tokens or 0
            usage["output"] += u.output_tokens or 0
            usage["cache_write"] += u.cache_creation_input_tokens or 0
            usage["cache_read"] += u.cache_read_input_tokens or 0

            if final.stop_reason == "refusal":
                yield sse({"type": "error", "text": "Sorry, I can't help with that one. Try asking about courses."})
                break
            if final.stop_reason == "max_tokens":
                yield sse({"type": "error", "text": "That reply ran too long and was cut off."})
                break
            tool_uses = [b for b in final.content if b.type == "tool_use"]
            if final.stop_reason != "tool_use" or not tool_uses:
                break

            results = []
            for tu in tool_uses:
                args = tu.input if isinstance(tu.input, dict) else {}
                yield sse({"type": "status", "text": STATUS.get(tu.name, lambda a: "Working")(args)})
                try:
                    content, ui_event = run_tool(tu.name, tu.input, frozenset(taken))
                    if ui_event:
                        yield sse(ui_event)
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
                except Exception as e:  # bad or truncated tool input: report it so the model can retry
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                    "content": f"Invalid input: {e}"})
            messages.append({"role": "user", "content": results})
    except anthropic.AuthenticationError:
        yield sse({"type": "error", "text": "Missing or invalid ANTHROPIC_API_KEY. Add it to app/.env and restart."})
    except anthropic.RateLimitError:
        yield sse({"type": "error", "text": "Rate limited by the API. Wait a moment and try again."})
    except anthropic.APIStatusError as e:
        yield sse({"type": "error", "text": f"API error {e.status_code}: {e.message}"})
    except anthropic.APIConnectionError:
        yield sse({"type": "error", "text": "Couldn't reach the Claude API. Check your connection."})
    except Exception as e:  # keep the stream well-formed so the UI never hangs
        yield sse({"type": "error", "text": f"Something went wrong: {e}"})
    global LAST_COST
    cost = sum(usage[k] * PRICE[k] for k in PRICE) / 1e6 + router_cost
    LAST_COST = cost  # read by experiments/bench.py
    if any(usage.values()):
        print(f"[usage] search={search} rounds={rounds} {usage} ≈ ${cost:.4f}", flush=True)
        yield sse({"type": "usage", "searched": search})  # cost/tokens stay in the server log only
    yield sse({"type": "done"})


fast_sessions: dict[tuple, list] = {}  # (email, session id) -> plain-text turns for the fast pipeline


def fast_chat_stream(session_key, message, preferences, favorites, taken=()):
    """Production path: router -> server-side retrieval -> one streamed Sonnet call (see pipeline.py)."""
    _maybe_reload()
    history = fast_sessions.setdefault(session_key, [])
    prior = prior_questions.setdefault(session_key, [])
    if pipeline.is_follow_up(message, history):
        search, router_cost = True, 0.0  # compare the courses we just showed
    else:
        search, router_cost = needs_q_data(message, prior)
    prior.append(message[:300])
    try:
        cost, secs = yield from pipeline.stream_answer(client, db, history, message, preferences, favorites,
                                                       list(taken), search, sse, db.card, _verified_quote)
        print(f"[usage] fast search={search} {secs:.1f}s ≈ ${cost + router_cost:.4f}", flush=True)
    except anthropic.AuthenticationError:
        yield sse({"type": "error", "text": "Missing or invalid ANTHROPIC_API_KEY. Add it to app/.env and restart."})
    except anthropic.RateLimitError:
        yield sse({"type": "error", "text": "Lots of people are asking right now. Try again in a moment."})
    except anthropic.APIStatusError as e:
        yield sse({"type": "error", "text": f"API error {e.status_code}: {e.message}"})
    except anthropic.APIConnectionError:
        yield sse({"type": "error", "text": "Couldn't reach the Claude API. Check your connection."})
    except Exception as e:  # keep the stream well-formed so the UI never hangs
        yield sse({"type": "error", "text": f"Something went wrong: {e}"})
    yield sse({"type": "usage", "searched": search})
    yield sse({"type": "done"})


app = FastAPI()
SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET:
    import secrets
    SESSION_SECRET = secrets.token_urlsafe(48)
    print("WARNING: SESSION_SECRET missing from .env; using a temporary one (everyone is signed out on restart).")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="hua_session",
                   max_age=14 * 24 * 3600, same_site="lax", https_only=os.environ.get("HTTPS_ONLY") == "1")
STATIC = Path(__file__).parent / "static"


def current_user(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Sign in with your Harvard Google account.")
    return user


# ---- Auth ----
class GoogleLogin(BaseModel):
    credential: str


@app.get("/api/auth/config")
def auth_config():
    return {"google_client_id": GOOGLE_CLIENT_ID, "allowed_domains": sorted(ALLOWED_DOMAINS)}


@app.post("/api/auth/google")
def auth_google(body: GoogleLogin, request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "GOOGLE_CLIENT_ID isn't set on the server.")
    try:
        # Checks Google's signature, expiry, issuer and that the token was minted for this app.
        info = id_token.verify_oauth2_token(body.credential, google_requests.Request(), GOOGLE_CLIENT_ID,
                                            clock_skew_in_seconds=10)
    except ValueError:
        raise HTTPException(401, "Couldn't verify that Google sign-in. Please try again.")
    email = (info.get("email") or "").lower()
    domain = email.rsplit("@", 1)[-1]
    if not info.get("email_verified") or domain not in ALLOWED_DOMAINS or info.get("hd", "").lower() != domain:
        allowed = " or ".join("@" + d for d in sorted(ALLOWED_DOMAINS))
        raise HTTPException(403, f"Qurated is only for Harvard College students. Sign in with your {allowed} account.")
    store.touch_user(email, info.get("name") or email)
    request.session["user"] = {"email": email, "name": info.get("name") or email, "picture": info.get("picture"),
                               "signed_in": int(time.time())}
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    used = questions_today[(user["email"], date.today())]
    return {**user, "questions_left": max(DAILY_LIMIT - used, 0), "daily_limit": DAILY_LIMIT,
            "is_admin": user["email"] in ADMIN_EMAILS}


# ---- Feedback ----
FEEDBACK_FILE = Path(__file__).parent / "data" / "feedback.jsonl"
feedback_today: dict[tuple, int] = defaultdict(int)


class Feedback(BaseModel):
    kind: str = "other"
    message: str
    include_email: bool = False


@app.post("/api/feedback")
def feedback(body: Feedback, user=Depends(current_user)):
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Please write something first.")
    key = (user["email"], date.today())
    if feedback_today[key] >= 10:
        raise HTTPException(429, "Thanks! You've sent a lot of feedback today. Try again tomorrow.")
    feedback_today[key] += 1
    entry = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": body.kind if body.kind in ("bug", "idea", "other") else "other",
        "message": text[:3000],
        # Email only when the student opts in; the sign-in page promises we use it just for verification.
        "email": user["email"] if body.include_email else None,
    }
    FEEDBACK_FILE.parent.mkdir(exist_ok=True)
    with FEEDBACK_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[feedback] {entry['kind']}: {entry['message'][:80]}", flush=True)
    return {"ok": True}


# ---- App API (signed-in only) ----
class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    preferences: dict | None = None
    favorites: list[str] = []


@app.post("/api/chat")
def chat(req: ChatRequest, user=Depends(current_user)):
    key = (user["email"], date.today())
    if questions_today[key] >= DAILY_LIMIT:
        raise HTTPException(429, f"You've hit today's limit of {DAILY_LIMIT} questions. Come back tomorrow!")
    questions_today[key] += 1
    sid = req.session_id or uuid.uuid4().hex
    email = user["email"]
    prefs = store.prefs(email) or (req.preferences or {})
    favs = [f["code"] for f in store.favorites(email)][:30]
    taken = store.taken(email)
    return StreamingResponse(fast_chat_stream((email, sid), req.message[:2000], prefs, favs, taken),
                             media_type="text/event-stream", headers={"X-Session-Id": sid})


@app.get("/api/course/{code}")
def course(code: str, user=Depends(current_user)):
    _maybe_reload()
    c = db.detail(code, max_comments=40)
    return JSONResponse(c) if c else JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/cards")
def cards(codes: list[str], user=Depends(current_user)):
    _maybe_reload()
    return [c for c in (db.card(code) for code in codes[:30]) if c]


@app.get("/api/meta")
def meta(user=Depends(current_user)):
    _maybe_reload()
    terms = sorted({o["term"] for c in db.courses.values() for o in c["offerings"]})
    n_comments = sum(len(c["comments"]) for c in db.courses.values())
    return {"courses": len(db.courses), "terms": terms, "comments": n_comments, "departments": db.departments()}


# ---- Saved account data ----
PREF_KEYS = {"concentration", "year", "interests", "requirements", "max_workload_hrs", "style", "avoid"}


@app.get("/api/account")
def account(user=Depends(current_user)):
    e = user["email"]
    taken = [{"code": c, "title": (db.lookup(c) or {}).get("title", "")} for c in store.taken(e)]
    return {"favorites": store.favorites(e), "taken": taken, "prefs": store.prefs(e)}


@app.put("/api/account/favorites/{code}")
def add_favorite(code: str, card: dict, user=Depends(current_user)):
    c = db.lookup(code)
    if not c:
        raise HTTPException(404, "Unknown course")
    fresh = db.card(c["code"]) or {}
    # Keep the model's reason/quote from the card the student hearted, but trust our own data for the rest.
    fresh.update({k: str(card[k])[:600] for k in ("reason", "quote") if card.get(k)})
    store.add_favorite(user["email"], c["code"], fresh)
    return {"ok": True}


@app.delete("/api/account/favorites/{code}")
def remove_favorite(code: str, user=Depends(current_user)):
    store.remove_favorite(user["email"], code)
    return {"ok": True}


@app.put("/api/account/taken/{code}")
def add_taken(code: str, user=Depends(current_user)):
    c = db.lookup(code)
    if not c:
        raise HTTPException(404, "Unknown course")
    store.add_taken(user["email"], c["code"])
    return {"ok": True, "code": c["code"], "title": c["title"]}


@app.delete("/api/account/taken/{code}")
def remove_taken(code: str, user=Depends(current_user)):
    store.remove_taken(user["email"], code)
    return {"ok": True}


@app.put("/api/account/prefs")
def set_prefs(prefs: dict, user=Depends(current_user)):
    clean = {k: (v if isinstance(v, (int, float)) else str(v)[:300]) for k, v in prefs.items() if k in PREF_KEYS}
    store.set_prefs(user["email"], clean)
    return {"ok": True}


@app.get("/api/courses/suggest")
def suggest(q: str = "", user=Depends(current_user)):
    _maybe_reload()
    return db.suggest(q)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index(request: Request):
    # Static assets are public (no data); every Q-data endpoint above requires a signed-in Harvard account.
    page = "index.html" if request.session.get("user") else "login.html"
    return FileResponse(STATIC / page, headers={"Cache-Control": "no-store"})
