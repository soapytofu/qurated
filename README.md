# Qurated

**The Harvard Undergraduate Association's course advisor.** Students describe what they want from a class
("a fun Gen Ed with a light workload", "something like CS50 but less work"), and Qurated recommends FAS courses.
Its answers draw on what past students said in their **Q reports** and on the **my.harvard catalog**.

- 🎓 **Grounded answers:** ratings, weekly workload and real student quotes from Q reports, plus terms offered, meeting times, Gen Ed / Divisional Distribution / QRD, descriptions and syllabus links from my.harvard. Every quote is checked word-for-word against the source.
- 🛡️ **Validated responses:** progress appears immediately; each course card is checked before it appears, and advice is shown only after the complete answer passes. Obvious jailbreak and homework requests are refused in code, with no AI call.
- 💬 **Conversational:** up to 6 picks per answer, plus a free "Show more like these" button. Qurated asks one clarifying question when a request is too vague, and remembers facts the student mentions ("junior in CS") for the rest of the chat.
- 🏷️ **Course tags:** each course is tagged with what students consistently say about it (e.g. "engaging lectures", "heavy reading").
- ❤️ **Student accounts:** favorites, preferences and courses already taken are saved per student, and courses they've taken are never recommended.
- 🔒 **Harvard-only:** sign-in requires a verified `@college.harvard.edu` Google account.

> **Data policy.** Q reports are meant for the Harvard community. This repo contains **code only**: no Q data, catalog
> data, student accounts or secrets. Run the scrapers yourself with your own HarvardKey, keep the data private, and
> get the relevant offices (e.g. the Office of Undergraduate Education, OUE) on board before opening Qurated to
> other students.

---

## How it works

```
 OFFLINE (refresh each term)                          ONLINE (each question)
 ───────────────────────────                          ──────────────────────
 Q reports ──scrape.py───► reports/*.json ─┐           Browser (HTML/CSS/JS)
 (your HarvardKey session)                 │             │ POST /api/chat  (signed-in only)
 my.harvard ─catalog.py──► catalog/*.json ─┼► CourseDB ◄─┤ FastAPI server
 (public pages)                            │  in-memory  │  1. pre-check: does this need a course search?
 Claude Haiku ─tag_courses.py► tags.json ──┘  BM25 index │  2. retrieval: filters + keyword search (~2 ms)
   (Batches API)                                          │  3. ONE Claude Sonnet call → structured JSON
 SQLite: favorites / courses taken / prefs ◄──────────────┤  4. send each validated card, then advice
```

1. **Scrape (offline).**
   - `qscrape/scrape.py` uses Playwright and your HarvardKey session to download every Q report, then parses its ratings, workload, recommend score, enrollment reasons and comments.
   - `qscrape/catalog.py` reads my.harvard's public course search one subject at a time, expands multi-section courses such as Expos and languages, and fetches each course's detail page.
   - Raw pages are saved gzipped, so a parser fix can be re-applied without downloading again.
2. **Tag (offline).** `app/tag_courses.py` has Claude Haiku summarize each course's comments into 3–5 sentiment-labeled tags plus a one-line summary, using the half-price Batches API.
3. **Index.** `app/courses.py` merges Q reports, catalog and tags into one record per course. A weighted BM25 keyword index covers titles, descriptions, Gen Eds, section topics, tags and comments, with filters for term, workload, Gen Ed, undergrad level and courses already taken.
4. **Answer (`app/pipeline.py`).**
   - A quick pre-check (a regex, or Claude Haiku) skips searching for questions like "what's a Gen Ed?".
   - The server picks the top 10 candidate courses itself, with no LLM involved. When undergraduate matches run thin, graduate courses fill the remaining slots and are labeled Graduate.
   - It then makes **one** Claude Sonnet 5.5 call, with thinking off and a JSON-schema output, that returns `{picks, advice, options, remember}`. The fixed instructions are prompt-cached.
   - As the response streams, each pick that passes the per-pick checks (course allowlist, taken courses, verified quote) is sent as a card. Advice, clarifying options and remembered facts are sent only after the complete response passes validation. Refused or incomplete responses show a generic error instead.
   - Follow-ups like "which of those is lightest?" reuse the courses already shown.

### Why one call instead of an agent loop
Historical measurements before response-validation guardrails (rebenchmark current latency before advertising these numbers):

I benchmarked several approaches (`app/experiments/bench.py`) on 8 fixed student questions, with Claude Opus as a
blind judge scoring fit, grounding and helpfulness from 1 to 5:

| Approach | First card | Full answer | Quality | Cost/answer |
|---|---|---|---|---|
| Agent loop (Claude calls search/detail/card tools) | 8.5s | 11.1s | 3.54 | ~3.8¢ |
| **Server-side retrieval + one Sonnet call, thinking off** | **~1.8s** | **~3.0s** | **3.79** | **~1.1¢** |

Turning thinking off was the biggest single win. Retrieving on the server removed 2–3 round trips to the model.
Hedged requests (a duplicate request is sent if no tokens arrive within 2.5s) and an IPv4-only HTTP transport
remove the remaining latency spikes.

---

## Setup

### Prerequisites
- macOS or Linux with [uv](https://docs.astral.sh/uv/) (installs the right Python automatically)
- Google Chrome (only for the Q report scraper's HarvardKey login)
- An [OpenAI API key](https://platform.openai.com/api-keys) or [Anthropic API key](https://console.anthropic.com/)
- A Google OAuth Client ID for sign-in: [step-by-step guide](docs/google-signin.md), about 10 minutes, free

### 1. Configure
```bash
cd app
cp .env.example .env
# Fill in your selected AI provider key, GOOGLE_CLIENT_ID, and a long random SESSION_SECRET, e.g.:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

| Variable | Purpose | Default |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` or `openai`; explicit selection, no automatic fallback | `anthropic` |
| `ANTHROPIC_API_KEY` | Claude API access | required for Anthropic chat or batch tagging |
| `OPENAI_API_KEY` | OpenAI API access | required for OpenAI chat |
| `OPENAI_MODEL` | Responses API model supporting structured outputs | `gpt-4.1-mini` |
| `OPENAI_ROUTER_MODEL` | Optional separate routing/query-expansion model | same as `OPENAI_MODEL` |
| `SEARCH_QUERY_EXPANSION` | Optional AI query rewrites; `0` uses deterministic topic expansion only, `1` opts in to AI rewrites | `0` |
| `GOOGLE_CLIENT_ID` | Google sign-in ([guide](docs/google-signin.md)) | required |
| `SESSION_SECRET` | Signs login cookies | required |
| `ALLOWED_EMAIL_DOMAINS` | Comma-separated domains allowed to sign in | `college.harvard.edu` |
| `DAILY_QUESTION_LIMIT` | Questions per student per day | `40` |
| `PER_MINUTE_QUESTION_LIMIT` | Questions per student per minute | `6` |
| `DAILY_BUDGET_USD` | Total API spend per day; AI answers pause past it (Browse still works) | `20` |
| `ADMIN_EMAILS` | Comma-separated admin emails | (none) |
| `HTTPS_ONLY` | Set to `1` when served over HTTPS (secure cookies) | `0` |

To use GPT, set these values in `app/.env` and restart the server:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4.1-mini
```

To switch back, set `LLM_PROVIDER=anthropic` and supply `ANTHROPIC_API_KEY`.
You can keep both keys locally; chat sends requests only to the selected provider.
Both providers use the same retrieval, privacy checks, and validated course cards.
For course discovery, a bounded model call expands conversational interests into up to two
additional keyword queries. The original query remains, and results are interleaved.
The expansion cannot change hard filters or course identities; timeouts or malformed output
fall back to keyword search. This adds latency and API usage. Exact course lookups skip expansion.
The search still uses locally imported catalog data, not live web browsing or vector search.
OpenAI uses the [Responses API](https://developers.openai.com/api/docs/guides/streaming-responses)
with strict structured output and `store=False`. OpenAI costs are not estimated in the
server log; check your provider dashboard for actual usage. The offline
`tag_courses.py` batch tool and legacy experiments still use Anthropic.

### 2. Get the data
```bash
cd qscrape
# Q reports (needs your HarvardKey)
uv run scrape.py login                                       # sign in with HarvardKey + Duo in the Chrome window
uv run scrape.py index                                       # list every report for every term
uv run scrape.py reports                                   # all indexed terms; resumable

# Course catalog (public, no login)
uv run catalog.py list                                       # every FAS section, one subject at a time (~20 min)
uv run catalog.py details                                    # one detail page per course (~2 h, resumable)

# Tags (about $1 for ~1,600 courses via the Batches API)
cd ../app
uv run tag_courses.py --all
```
Everything is resumable. If your HarvardKey session expires, run `uv run scrape.py login` again and re-run the same command.

### 3. Run
```bash
cd app
uv run uvicorn server:app --reload --host 127.0.0.1 --port 8765
```
Open **http://localhost:8765** (use `localhost`, which must match your Google OAuth origin) and sign in.
The server picks up newly scraped data and tags automatically, with no restart needed.

### Refreshing each term
```bash
cd qscrape && uv run catalog.py list && uv run catalog.py details      # new catalog
uv run scrape.py login && uv run scrape.py index && uv run scrape.py reports --term "<new term>"
cd ../app && uv run tag_courses.py --all                                # tag newly reviewed courses
```

---

## Project structure
```
qscrape/               offline data collection
  scrape.py            Q reports: HarvardKey login, index, download (Playwright)
  parse.py             Q report HTML → structured JSON
  catalog.py           my.harvard catalog: sections + course details
app/                   the web app
  server.py            FastAPI: Google auth, accounts API, chat endpoint (SSE), feedback
  pipeline.py          fast answer pipeline: plan → retrieve → one streamed Claude call
  courses.py           CourseDB: merges Q + catalog + tags; BM25 search and filters
  db.py                SQLite store for favorites, courses taken, preferences
  tag_courses.py       Claude Haiku tagging via the Batches API
  experiments/bench.py latency / cost / quality benchmark with an LLM judge
  static/              frontend: sign-in page, chat UI, course cards, account drawers
docs/google-signin.md  Google OAuth setup walkthrough
```

Local-only, gitignored folders: `qscrape/data/` (scraped data), `app/data/` (tags, student accounts,
feedback), `.env`, and `.browser-profile/` (your saved HarvardKey session).

## Operating notes
See **[docs/MAINTENANCE.md](docs/MAINTENANCE.md)** for the full maintenance and scaling plan: roles, the per-term
refresh runbook, cost model, usage limits, and the path to official data access.

- **Costs:** about 1¢ per answer (Claude Sonnet 5.5) plus about 0.04¢ for the occasional Haiku pre-check. Cost is logged on the server, never shown to students. Set a monthly spend limit in the Anthropic Console as well.
- **Feedback:** students send it from the speech-bubble button. Read it with `cat app/data/feedback.jsonl`. Emails are only saved if the student opts in.
- **Back up** `app/data/` (student accounts) if you move machines.
- **Slow responses (15–20s) on some Wi-Fi networks:** the server already forces IPv4 for API calls, because some networks' IPv6 routes hang for about 16s. If you see this with another HTTP client, check `curl -6 https://api.anthropic.com`.

## Credits
Built for the Harvard Undergraduate Association. Not an official Harvard University service.
Course data © its respective owners. Q reports courtesy of the Harvard FAS Office of Undergraduate Education.


## Review, guardrails, and tests

See [the code review](docs/CODE_REVIEW.md) for prioritized findings, fixes, limitations and a live safety-evaluation checklist.

```bash
uv sync --project app --locked
app/.venv/bin/python -m pytest tests -q
node --check app/static/app.js
node --test tests/frontend.test.cjs
```

Tests use synthetic course records, mocked model replies, and a temporary account database. They require no
Google credentials, private Q reports, browser installation, or paid model calls. CI runs these same checks.
Use `uv sync --project app --locked --no-dev` for a production environment.

Daily chat quotas are stored in SQLite. Conversation context is process-local, limited to three exchanges,
and expires after one idle hour (removed on the next chat acquisition). At most 1,000 conversations are kept
per process. Use one worker for consistent follow-ups, or introduce shared conversation storage before scaling
across workers. `QURATED_DB_PATH` optionally overrides the account database path.

### Running on another computer

Git does not include the local course catalog, Q reports, accounts, or secrets. Each checkout
needs its own `app/.env` and course data. Import the public catalog using the commands above,
or copy `qscrape/data/catalog/sections.jsonl` and `qscrape/data/catalog/details/` from a prepared
checkout into the same paths in the new checkout. A public-catalog ZIP with those paths can
be extracted from the repository root. Restart the app or allow its automatic data reload.

A partial catalog snapshot may lack requirement details for some courses. Q reports require
a separate authorized HarvardKey import; omitting `--term` imports all indexed semesters.
Do not transfer `.env`, `.browser-profile`, or `app/data/qurated.db` as part of catalog setup.
No Q reviews in the loaded data does not establish that a course is new.

### Predictable course retrieval

By default, course search uses fixed topic synonyms, catalog filters, and BM25 keyword ranking.
For example, CS + design also searches human-computer interaction and user interfaces. Known
algorithm/circuit/compiler design requests keep their specific meaning. Topic families are versioned
in `app/search_planner.py`; unfamiliar topics retain their literal keywords. AI-written answers can
still vary, but candidate retrieval is repeatable for the same loaded data, request, history,
preferences, and filters. Equal-ranked courses use ascending course code as a stable tie-breaker.

`SEARCH_QUERY_EXPANSION=1` explicitly enables additional AI query rewrites and makes candidate
retrieval non-deterministic. Existing installations with that setting should change it to `0` and
restart to use deterministic retrieval. No AI rewrite is required for the default search path.

Subject, Gen Ed, term, and workload constraints are enforced before ranking, including for explicitly
named courses. A minimum Q score excludes missing values. A failed topic search does not silently
fall back to unrelated courses, and workload limits are not silently relaxed. “Light”, “easy”, and
“manageable” currently mean at most six reported hours/week, or a stricter saved/selected cap.

Chat recognizes explicit constraints such as “Spring 2027”, “under 5 hours”, “at most 6 hours per
week”, and “effective lectures rated at least 4.5”. “Under” is strict; “at most” includes the
boundary. Numerical ratings average every available loaded Q report, including separate sections. Each report contributes equally; missing scores are omitted per metric. The Q-guide term filter limits these averages to one historical term and is separate from the upcoming catalog term filter. All displayed Q metrics support ascending and descending sorting, with unavailable values last. Requested
thresholds intersect selected filters instead of weakening them. Unsupported years, multiple terms,
out-of-range scores, and conflicting selected terms get a clarification before answer generation.
Every course search displays an expandable **Search interpreted as** summary with its effective
constraints and keyword queries. This makes interpretation inspectable; unsupported phrasing still
needs rewording or the explicit Filters controls.
