# Qurated

**A course advisor built for the Harvard Undergraduate Association.** Describe what you want from a class,
browse FAS courses, compare Q-guide ratings and workload, and save favorites or courses already taken.
Answers use locally imported Q reports and my.harvard catalog records, with checks on course recommendations
and quoted evidence. This is not an official Harvard University service.

**Live app:** [qurated-34-26-14-14.sslip.io](https://qurated-34-26-14-14.sslip.io)
(authorized Harvard College Google sign-in required).

This repository contains **code and synthetic test fixtures only**. Real Q reports, catalog snapshots,
student records, API keys, and saved HarvardKey sessions are excluded from Git. Keep Q data within its
authorized Harvard audience; coordinate a student rollout with the relevant university offices.

| What you want to do | Start here | Credentials needed |
|---|---|---|
| Click through the interface with example courses | [Local demo](#local-demo-no-credentials) | None |
| Run automated checks or contribute code | [Tests](#tests) | None |
| Use real courses and the AI chatbot | [Full local setup](#full-local-setup) | Google OAuth, a permitted Google account, and an AI provider key; HarvardKey for Q reports |

## Install

The commands below use a macOS/Linux shell. Install [Git](https://git-scm.com/downloads) and
[uv](https://docs.astral.sh/uv/getting-started/installation/) first. Use Python 3.12 to match CI;
uv downloads it if needed. Node.js 22 is needed only for the JavaScript tests, not to serve the app.

```bash
git clone https://github.com/soapytofu/qurated.git
cd qurated
uv sync --project app --locked --python 3.12
```

This creates `app/.venv` with the app and development dependencies. There is no frontend build or
`npm install` step. **Run all subsequent command blocks from the repository root** unless a block
explicitly changes directory; parenthesized blocks return you to the root when finished.

If uv selects a broken system/Conda Python (for example, pytest crashes importing `readline`), repeat
the sync command with `--python /absolute/path/to/python3.12` pointing to a working Python 3.12 installation.

## Local demo (no credentials)

After installing, run:

```bash
LLM_PROVIDER=anthropic SEARCH_QUERY_EXPANSION=0 HTTPS_ONLY=0 PYTHONPATH=app:tests \
  app/.venv/bin/uvicorn playtest_app:app --host 127.0.0.1 --port 8766
```

Open **http://127.0.0.1:8766**. You should see **Demo Student** and synthetic courses. Try Browse,
filters, course details, favorites, preferences, and a course question. Replies are mocked; this tests
the interface, not real AI quality or Harvard sign-in. Demo account data is temporary. Stop with `Ctrl+C`.

The demo bypasses authentication for local testing. Keep it bound to `127.0.0.1`; do not deploy it or
expose it through a public tunnel. For real users, run `server:app` as described below.

## Full local setup

### 1. Configure the app

```bash
cp -n app/.env.example app/.env
app/.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Do not overwrite an existing `.env`. Paste the generated value into `SESSION_SECRET`, then edit
`app/.env` with your Google client ID and **one** provider's API key. For example, using OpenAI:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=replace-with-your-key
OPENAI_MODEL=gpt-4.1-mini
GOOGLE_CLIENT_ID=replace-with-your-client-id.apps.googleusercontent.com
SESSION_SECRET=replace-with-the-generated-random-value
ALLOWED_EMAIL_DOMAINS=college.harvard.edu
SEARCH_QUERY_EXPANSION=0
HTTPS_ONLY=0
```

Alternatively, set `LLM_PROVIDER=anthropic` and fill in `ANTHROPIC_API_KEY`. There is no automatic provider
fallback. Model access and API billing belong to your provider account; the repository supplies no keys
or credits. Live chat sends the question, relevant context, and selected course excerpts to that provider.

Create a Google OAuth **Web application** client using the [sign-in walkthrough](docs/google-signin.md)
or [Google's setup guide](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid).
Add both `http://localhost` and `http://localhost:8765` as authorized JavaScript origins. Copy the **client
ID**, not the client secret. The app uses a JavaScript callback, so this setup needs no redirect URI.
Configure the consent screen and test-user access as applicable to your Google project.

By default, real sign-in requires a verified `@college.harvard.edu` address with a matching Google
Workspace domain claim. The Google account that owns your OAuth project can be different from the
account used to sign in to Qurated.

### 2. Load course data

A fresh clone has no real courses. Choose an authorized existing snapshot or import the data yourself.

**Existing snapshot:** place these files at the same repository-relative paths. Catalog-only browsing
works without Q reports; ratings and student comments require the reports. Tags are optional.

```text
qscrape/data/catalog/sections.jsonl
qscrape/data/catalog/details/*.json
qscrape/data/reports/*.json
app/data/tags.json
```

Copy only the course files you are authorized to use. Do not bundle someone else's `.env`,
`.browser-profile`, or student account database with a snapshot.

**Public catalog import:** install the scraper dependencies, then run both passes in order:

```bash
uv sync --project qscrape --locked --python 3.12
(
  cd qscrape
  uv run --locked catalog.py list
  uv run --locked catalog.py details
)
```

This reads public my.harvard pages without HarvardKey or paid AI calls. A full import can take hours;
the details pass skips files already downloaded and can be rerun. Its configured catalog terms are
currently **Fall 2026 and Spring 2027**. These are separate from historical Q-guide terms.

Browsing and recommendations default to **Spring 2027** and require a matching catalog offering before
ranking. Select another loaded term or **All catalog terms** to broaden availability. Q reports only
supply historical review evidence; selecting a Q-report term does not change the offering term. Saved
course details and taken-course lookup retain historical records. Update `DEFAULT_TERM` and the term
controls when refreshing the planning semester.

**Q-report import:** requires installed Google Chrome, your own authorized HarvardKey access, and Duo.
Install the scraper dependencies above, then:

```bash
(
  cd qscrape
  uv run --locked scrape.py login
  uv run --locked scrape.py index
  uv run --locked scrape.py reports
)
```

Complete sign-in in the Chrome window. Omitting `--terms` on `index` discovers every available Q term;
omitting `--term` on `reports` downloads **all indexed terms**, not just two semesters. Downloads are
resumable. If authentication expires, rerun `login`, then resume. Keep the saved session and reports private.

**Optional course tags:** `uv run --locked tag_courses.py --all` from `app/` generates summaries/tags
for previously untagged courses. It requires an **Anthropic** key and makes paid batch API calls even
if chat uses OpenAI. Skip this step for an initial setup; it does not refresh already tagged courses.

### 3. Start the real app

```bash
(
  cd app
  uv run --locked uvicorn server:app --reload --host 127.0.0.1 --port 8765
)
```

Open **http://localhost:8765**, sign in, and open **Browse**. Confirm the data coverage and course details
before trying a chat question. Browse does not call an AI provider. A normal AI-generated answer uses
your API account and may incur charges. Stop the server with `Ctrl+C`.

Course files are checked for changes on relevant requests, at most once every five seconds. Restarting
also rebuilds the index. Changes to `.env` require a restart.

### Configuration reference

Start with [app/.env.example](app/.env.example); values below describe the current code defaults.

| Variable | Default / purpose |
|---|---|
| `LLM_PROVIDER` | `anthropic`; choose `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Key for the selected chat provider; tagging always needs Anthropic |
| `OPENAI_MODEL` | `gpt-4.1-mini`; model used by the OpenAI adapter |
| `OPENAI_ROUTER_MODEL` | Optional routing model; defaults to `OPENAI_MODEL` |
| `GOOGLE_CLIENT_ID` | Your Google OAuth Web client ID |
| `SESSION_SECRET` | Set a strong, stable random value; otherwise restarts invalidate sessions |
| `ALLOWED_EMAIL_DOMAINS` | `college.harvard.edu`; comma-separated permitted Workspace domains |
| `SEARCH_QUERY_EXPANSION` | `0`: deterministic retrieval; `1` enables extra AI query rewrites and API usage |
| `DAILY_QUESTION_LIMIT` | `40` per student; daily counts persist in SQLite |
| `PER_MINUTE_QUESTION_LIMIT` | `6` per student |
| `DAILY_BUDGET_USD` | `20`; checks recorded estimated spend, **not an OpenAI spending cap** |
| `ADMIN_EMAILS` | Optional comma-separated addresses that can see the admin spend summary |
| `HTTPS_ONLY` | `0` for local HTTP; set `1` behind production HTTPS |
| `QURATED_DB_PATH` | Defaults to `app/data/qurated.db`; set in the process environment **before startup**, not just `.env` |

OpenAI costs are currently recorded as unavailable rather than estimated, so `DAILY_BUDGET_USD` cannot
limit OpenAI spend. Even tracked estimates are not a guaranteed billing cap. Monitor your provider
dashboard and configure provider-side spending controls separately.

## Tests

After the install step, run the default regression suite and JavaScript checks:

```bash
app/.venv/bin/python -m pytest tests -q
for source in app/static/*.js; do
  node --check "$source" || exit
done
node --test tests/frontend.test.cjs
```

These use synthetic fixtures, mocked model replies, and a temporary account database. No Google login,
private Q data, or paid model calls are required. Browser tests are skipped by default; enable them with:

```bash
app/.venv/bin/python -m playwright install chromium
RUN_BROWSER_TESTS=1 app/.venv/bin/python -m pytest tests/test_ui*.py -q
```

On Linux, use `playwright install --with-deps chromium` if browser system libraries are missing.
Alternatively, set `QURATED_TEST_BROWSER` to an installed Chrome executable. CI runs both suites via
[the workflow](.github/workflows/tests.yml). These checks do not validate live Google/Harvard login,
provider availability, or the quality of real AI answers.

## FAQ and troubleshooting

**Can I test without a Harvard account, API key, or course data?**

Yes: use the local demo or automated tests. For real data, you can import the public catalog without
HarvardKey, but the real app still requires permitted Google sign-in. Q reports require authorized access.

**Why is Browse empty, or why are ratings missing?**

A clone does not include course data. Check the paths above and import both catalog passes. Catalog-only
courses have no Q ratings; missing data does not mean a zero score or prove that a course is new.
Clear strict rating/workload filters when investigating missing results.

**Which Q guides are included, and how does sorting work?**

Every valid report loaded from `qscrape/data/reports/` is included. There is no fixed two-term limit or
bundled report count. Each course's metric averages all its loaded reports, including separate sections,
with equal weight per report; missing values are omitted per metric. Use the Q-guide term filter to
restrict the average to one historical term. It is separate from the catalog's offering-term filter.
All displayed Q metrics support ascending/descending sorting, with missing values last.

**Why does Google sign-in fail?**

Check `GOOGLE_CLIENT_ID`, restart after editing `.env`, and open exactly `http://localhost:8765` with
that origin registered in Google. Keep `HTTPS_ONLY=0` for local HTTP. A 403 usually means the verified
email or Workspace domain is not allowed. Adding `gmail.com` alone will not permit personal Gmail
accounts because the app also requires a matching Workspace `hd` claim. See the
[sign-in troubleshooting table](docs/google-signin.md#troubleshooting) for OAuth or administrator blocks.

**Why does the chatbot report a key error or usage limit?**

Check that `LLM_PROVIDER` matches the key you filled in, restart, and verify API access/billing with
that provider. App question limits and provider limits are separate; Browse still works without AI.
Daily app quotas reset at midnight in Boston when timezone data is available.

**Why did a request return no courses or ask for clarification?**

Hard filters intersect rather than silently relax. Inspect **Search interpreted as**, then clear a
conflicting term, rating, schedule, or workload constraint. Currently, “light,” “easy,” and “manageable”
impose at most six reported hours/week, or a stricter selected/saved cap. Unsupported years or
contradictory constraints can require clarification. AI wording can vary even with deterministic retrieval.

**How do I refresh data?**

Rerun the catalog listing and Q index/report commands for the supported terms. Detail/report downloads
skip existing files, and tagging skips already tagged courses, so rerunning does not guarantee that
changed upstream content is refreshed. Plan deliberate regeneration where needed. Adding a future
catalog term also requires updating term handling in the app, not just importing new files.

**What persists, and what should I back up?**

Favorites, taken courses, preferences, daily counts, and recorded spend live in SQLite at
`app/data/qurated.db` by default. New feedback and its email delivery queue live in
`app/data/feedback-mail.db`; older submissions remain in `app/data/feedback.jsonl`.
Feedback is emailed to sophiezeng@college.harvard.edu using the [mail relay](deploy/feedback-mail.md).
The submitter's email is included only when they opt in. Back up both databases privately using SQLite's backup tools or while the
server is stopped. Chat history is process-local, retains three exchanges, and expires after an idle
hour; it is lost on restart. Keep `.env`, reports, tags, and scraper sessions private too.

**Can I share the GitHub link as a live app?**

The link shares source code; it does not run the Python backend. A deployment needs a Python host,
persistent storage, environment secrets, and privately provisioned course data. Use one Uvicorn worker
for consistent follow-ups, HTTPS with `HTTPS_ONLY=1`, and register the deployed origin in Google OAuth.
Omit `--reload` in production. The [deployment files](deploy/) are VM-specific examples, not a one-click
host: review their paths and assumptions before running them. The demo is never a production entry point.

**How do I report a bug or contribute?**

Open an [issue](https://github.com/soapytofu/qurated/issues) with steps, expected/actual behavior, and
your browser or environment; use synthetic examples and redact private data. For code changes, create
a branch, run the relevant checks above, and open a pull request. Arrange sensitive security reports
privately with the maintainer; do not publish keys, session cookies, student records, or private Q comments.

## How it works and where to look

Python/FastAPI serves a plain JavaScript frontend. The app merges local catalog records, Q reports,
and optional tags into an in-memory BM25 index, applies course constraints, then asks the selected
provider for a structured answer. The pipeline checks recommendations and quoted evidence before
display; these checks do not guarantee that every free-text claim is correct. Browse and sorting use
the local index directly.

| Path | Purpose |
|---|---|
| `app/server.py`, `app/db.py`, `app/chat_state.py` | API, Google sign-in, account storage, conversation state |
| `app/courses.py`, `app/search_planner.py` | Course loading, metric aggregation, filters, retrieval |
| `app/pipeline.py`, `app/llm.py`, `app/*constraints.py` | Answer generation, provider adapter, request/evidence checks |
| `app/static/` | Sign-in, chat, browsing, account controls; no build step |
| `qscrape/` | Catalog and authorized Q-report import tools |
| `tests/`, `tests/playtest_app.py` | Regression suites and the local synthetic demo |
| `deploy/` | Example VM service and reverse-proxy configuration |

See [the maintenance plan](docs/MAINTENANCE.md) and [the earlier code review](docs/CODE_REVIEW.md) for
background and operating considerations. Their historical counts, estimates, and validation notes may
predate the current implementation; inspect your loaded data and current provider billing for live values.

## Credits

Built for the Harvard Undergraduate Association. Original contributor attribution is preserved in Git
history. Not an official Harvard University service. Course data belongs to its respective owners;
Q reports are provided by the Harvard FAS Office of Undergraduate Education.
