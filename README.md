# Qurated

**The Harvard Undergraduate Association's course advisor.** Students describe what they want from a class
("a fun Gen Ed with a light workload", "something like CS50 but less work"), and Qurated recommends FAS courses.
Its answers draw on what past students said in their **Q reports** and on the **my.harvard catalog**.

- 🎓 **Grounded answers:** ratings, weekly workload and real student quotes from Q reports, plus terms offered, meeting times, Gen Ed / Divisional Distribution / QRD, descriptions and syllabus links from my.harvard. Every quote is checked word-for-word against the source.
- ⚡ **Fast:** the first course card appears in about 2 seconds, and the full answer finishes in about 3.
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
 SQLite: favorites / courses taken / prefs ◄──────────────┤  4. stream: each card as it's written, then advice
```

1. **Scrape (offline).**
   - `qscrape/scrape.py` uses Playwright and your HarvardKey session to download every Q report, then parses its ratings, workload, recommend score, enrollment reasons and comments.
   - `qscrape/catalog.py` reads my.harvard's public course search one subject at a time, expands multi-section courses such as Expos and languages, and fetches each course's detail page.
   - Raw pages are saved gzipped, so a parser fix can be re-applied without downloading again.
2. **Tag (offline).** `app/tag_courses.py` has Claude Haiku summarize each course's comments into 3–5 sentiment-labeled tags plus a one-line summary, using the half-price Batches API.
3. **Index.** `app/courses.py` merges Q reports, catalog and tags into one record per course. A weighted BM25 keyword index covers titles, descriptions, Gen Eds, section topics, tags and comments, with filters for term, workload, Gen Ed, undergrad level and courses already taken.
4. **Answer (`app/pipeline.py`).**
   - A quick pre-check (a regex, or Claude Haiku) skips searching for questions like "what's a Gen Ed?".
   - The server picks the top 8 candidate courses itself, with no LLM involved.
   - It then makes **one** Claude Sonnet 5.5 call, with thinking off and a JSON-schema output, that returns `{picks, advice}`.
   - The server parses the JSON as it streams: each pick becomes a course card the moment it's complete, then the advice streams in.
   - Follow-ups like "which of those is lightest?" reuse the courses already shown.

### Why one call instead of an agent loop
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
- An [Anthropic API key](https://console.anthropic.com/)
- A Google OAuth Client ID for sign-in: [step-by-step guide](docs/google-signin.md), about 10 minutes, free

### 1. Configure
```bash
cd app
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, GOOGLE_CLIENT_ID, and a long random SESSION_SECRET, e.g.:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API access | required |
| `GOOGLE_CLIENT_ID` | Google sign-in ([guide](docs/google-signin.md)) | required |
| `SESSION_SECRET` | Signs login cookies | required |
| `ALLOWED_EMAIL_DOMAINS` | Comma-separated domains allowed to sign in | `college.harvard.edu` |
| `DAILY_QUESTION_LIMIT` | Questions per student per day | `40` |
| `PER_MINUTE_QUESTION_LIMIT` | Questions per student per minute | `6` |
| `DAILY_BUDGET_USD` | Total API spend per day; AI answers pause past it (Browse still works) | `20` |
| `ADMIN_EMAILS` | Comma-separated admin emails | (none) |
| `HTTPS_ONLY` | Set to `1` when served over HTTPS (secure cookies) | `0` |

### 2. Get the data
```bash
cd qscrape
# Q reports (needs your HarvardKey)
uv run scrape.py login                                       # sign in with HarvardKey + Duo in the Chrome window
uv run scrape.py index                                       # list every report for every term
uv run scrape.py reports --term "2026 Spring" "2025 Fall"    # download + parse (resumable)

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
