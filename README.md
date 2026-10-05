# Qurated: the HUA course advisor

A Harvard Undergraduate Association chatbot that recommends FAS courses using Q report data (ratings, workload, student comments) and the my.harvard course catalog (when courses meet, Gen Eds, descriptions, syllabi).
Only signed-in `@college.harvard.edu` Google accounts can use it.

## 1. Scrape Q reports (`qscrape/`)
Uses your own HarvardKey session. Keep the data private: Q reports are for the Harvard community.

```bash
cd qscrape
uv run scrape.py login                                    # sign in with HarvardKey + Duo in the Chrome window
uv run scrape.py index                                    # list every report for every term
uv run scrape.py reports --term "2026 Spring" "2025 Fall" # download + parse (resumable; rerun after re-login)
```

## 1b. Scrape the course catalog (`qscrape/catalog.py`)
Public my.harvard pages, no login needed. Re-run each term to refresh.

```bash
cd qscrape
uv run catalog.py list       # every FAS section for Fall 2026 + Spring 2027, one subject at a time (~15 min)
uv run catalog.py details    # one detail page per course: description, Gen Ed, QRD, syllabus link (~1.5 h, resumable)
```

## 2. Tag courses (`app/tag_courses.py`)
Summarizes each course's student comments into 3-5 tags with Claude Haiku via the Batches API (about $1 for ~1,500 courses).
Re-run after more scraping; it only tags new courses.

```bash
cd app
uv run tag_courses.py --sample 5   # quick check
uv run tag_courses.py --all
```

## 3. Set up Google sign-in (one time)
You'll create a free "OAuth Client ID" that tells Google which website is allowed to show the sign-in button. No billing or credit card is needed. Takes about 10 minutes.

**Which Google account to use:** a personal Gmail works best. Harvard-managed accounts may not be allowed to create Cloud projects. Which account *owns* the project doesn't affect who can *sign in* to hua.

**Step 1: Create a project**
1. Go to https://console.cloud.google.com/ and sign in. Accept the terms if this is your first time.
2. Click the project picker at the top left (it may say "Select a project") → **New project**.
3. Project name: `hua`. Leave Organization as "No organization" → **Create**.
4. Wait a few seconds, then make sure `hua` is selected in the project picker.

**Step 2: Set up the sign-in screen (the "consent screen")**
Google now calls this section **Google Auth Platform**. Older guides call it "OAuth consent screen".
1. Open ☰ menu → **APIs & Services → OAuth consent screen** (or search "Google Auth Platform" in the top search bar).
2. If you see **Get started**, click it and fill in:
   - **App information:** App name `hua`; User support email: your email.
   - **Audience:** choose **External**. Internal only appears for Workspace orgs and would block Harvard accounts.
   - **Contact information:** your email.
   - Check the agreement box → **Create**.
3. Go to **Audience** in the left sidebar. Under Publishing status you'll see **Testing**. Pick one:
   - **Recommended:** click **Publish app** → Confirm. hua only asks for name and email (non-sensitive), so Google does **not** require a review. Any Harvard student can sign in.
   - **Or** stay in Testing and add each person's email under **Test users** (up to 100). Anyone not listed sees "Access blocked".
4. Optional, under **Branding**: add a logo and app home page. Leave **Data access / Scopes** alone. hua needs only the default `openid`, `email` and `profile`.

**Step 3: Create the Client ID**
1. Left sidebar → **Clients** (or **APIs & Services → Credentials → + Create credentials → OAuth client ID**).
2. **Create client**:
   - Application type: **Web application**
   - Name: `hua web`
   - **Authorized JavaScript origins**: click **+ Add URI** and add both:
     - `http://localhost`
     - `http://localhost:8765`

     Type them exactly like this: `http`, not `https`; no trailing slash; no path. Google may reject `http://127.0.0.1` here, so use **localhost** in your browser.
   - **Authorized redirect URIs**: leave empty. hua uses the pop-up sign-in, which doesn't need one.
   - **Create**.
3. A dialog shows your **Client ID**, which looks like `1234567890-abc123def456.apps.googleusercontent.com`. Copy it.
   - You can ignore the **Client secret**. hua doesn't use it.
   - You can always find the ID again under **Clients**.

**Step 4: Give it to hua**
1. Open `app/.env` (for example, `open -e ~/Downloads/"hua chatbot"/app/.env`).
2. Set `GOOGLE_CLIENT_ID=1234567890-abc123def456.apps.googleusercontent.com`. No quotes or spaces. Save.
3. Restart the server. `.env` is only read at startup; `--reload` doesn't watch it. Press Ctrl+C in the server terminal, then:
   ```bash
   cd ~/Downloads/"hua chatbot"/app && uv run uvicorn server:app --reload --host 127.0.0.1 --port 8765
   ```
4. Open **http://localhost:8765** (not 127.0.0.1). You should see a white **Continue with Google** button.
   New origins can take 5 minutes (occasionally longer) to start working.

**Step 5: Test it**
- Sign in with your `@college.harvard.edu` account. You should land on the hua home page with your initials or photo at the top right.
- Sign in with a personal Gmail. You should see "hua is only for Harvard students…". That means the gate works.

**Troubleshooting**
| What you see | Fix |
|---|---|
| "Google sign-in isn't configured yet" | `GOOGLE_CLIENT_ID` is empty or the server wasn't restarted. |
| No button, or "The given origin is not allowed for the given client ID" | The address bar must exactly match an Authorized JavaScript origin, including the port. Use `http://localhost:8765`, check for typos, and wait 5 minutes after editing. |
| "Access blocked: hua has not completed the Google verification process" | The app is still in Testing. Publish it (Step 2.3) or add that email as a test user. |
| "Access blocked … your administrator" with the Harvard account | Harvard's Google admins limit some third-party apps. Basic name/email sign-in normally works (Veritasty uses it). If it's blocked, contact HUIT. |
| Pop-up opens and closes instantly | Allow pop-ups for localhost, and make sure third-party cookies aren't blocked for `accounts.google.com`. |
| "hua is only for Harvard students" with a Harvard account | Only `@college.harvard.edu` is allowed by default. Add others, e.g. `ALLOWED_EMAIL_DOMAINS=college.harvard.edu,g.harvard.edu`, then restart. |

**Going live later:** in **Clients → hua web**, add your real site (e.g. `https://hua.example.org`) as another Authorized JavaScript origin. In `.env`, set `HTTPS_ONLY=1` so login cookies are only sent over HTTPS.

Sign-in verifies Google's signed ID token on the server and only accepts verified accounts whose email domain
(and Google Workspace `hd` claim) is in `ALLOWED_EMAIL_DOMAINS` (default `college.harvard.edu`).

## 4. Run the app (`app/`)
```bash
cd app
cp .env.example .env        # first time: fill in ANTHROPIC_API_KEY, SESSION_SECRET, GOOGLE_CLIENT_ID
uv run uvicorn server:app --reload --host 127.0.0.1 --port 8765
```
Open http://localhost:8765. The app picks up newly scraped reports and new tags automatically.

### Cost controls
- Chat uses Claude Sonnet 5.5 at low effort; a Claude Haiku pre-check skips Q-data search when a message doesn't need it.
- Each reply shows its cost. Typical: ~$0.01 (no search) to ~$0.02-0.03 (search + cards).
- `DAILY_QUESTION_LIMIT` (default 40) caps questions per student per day. Also set a monthly spend limit in the Anthropic Console.

## Reading feedback
Students can send feedback from the speech-bubble button in the top bar. Each message is one line in `app/data/feedback.jsonl` (time, type, message, and email only if they ticked "include my email").

```bash
cat ~/Downloads/"hua chatbot"/app/data/feedback.jsonl
```

## Student accounts
Favorites, preferences and courses taken are saved per student in `app/data/qurated.db` (SQLite).
Courses marked as taken (from any card, the course page, or "Courses I've taken" in the account menu) are never recommended.
Back up `app/data/` if you move servers; it's gitignored.
