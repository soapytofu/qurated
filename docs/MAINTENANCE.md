# Qurated maintenance & scaling plan

How the Harvard Undergraduate Association (HUA) keeps Qurated running, current, affordable and handed off
cleanly for ~8,000 undergraduates.

*Last updated: October 2026. Numbers below are measured from the current system unless marked as estimates.*

---

## 1. Summary

| | Today (local pilot) | Target (all undergrads) |
|---|---|---|
| Users | 1 laptop, test accounts | ~8,000 eligible; expect 1,600–6,400 active per term |
| Data | Q reports for 2 terms (2,929 reports, 31k comments) + Fall 2026/Spring 2027 catalog | All 6 student-visible Q terms (~9,400 reports) + current catalog, refreshed every term |
| Updates | Manual scrape with one student's HarvardKey | Official data access from the Office of Undergraduate Education (OUE) and Harvard IT (HUIT); manual scrape only as fallback |
| Cost per answer | ≈ 1.1¢ | ≈ 1.1¢, or ~0.7–0.9¢ with caching |
| Yearly cost | ~$0 (pilot) | **~$500–3,700/yr** depending on adoption (see §5) |
| Hosting | Laptop | One small cloud server (~$10–25/mo) |
| Access point | `localhost` on one laptop | **Integrated into my.harvard.edu**, Harvard's official student portal, so students find it where they already plan courses (see §8) |

**Top three decisions for HUA leadership:** (1) get OUE's approval and official data access, (2) fund an
HUA-owned Anthropic account with a hard monthly spend limit, (3) name two maintainers so the project survives
graduations.

**Long-term goal:** make Qurated part of **my.harvard.edu**, so course advice sits next to course search, the
shopping cart and requirements tracking, under one official Harvard login (§8).

---

## 2. Roles

| Role | Responsibilities | Time |
|---|---|---|
| **Product owner** (HUA officer) | Priorities, OUE/HUIT relationship (including the my.harvard integration path), approves budget, reads feedback | ~1 h/month |
| **Tech lead** (student dev) | Deploys, fixes scraper breakages, reviews code, runs the per-term refresh | ~3 h/term + ~2 h/month |
| **Backup tech lead** | Trained on the runbook (§4); can do a refresh alone | ~2 h/term (shadowing) |
| **Budget owner** (HUA treasurer) | Owns the Anthropic and hosting billing, receives spend alerts | ~15 min/month |

**Every account is HUA-owned, never personal:** GitHub (org repo), Anthropic (org account), Google Cloud
(the OAuth client), hosting, and the domain. At least two officers have admin access to each.

---

## 3. Data: where it comes from and the path to official access

### 3.1 Today: manual scrape (fallback)
- **Q reports:** `qscrape/scrape.py` signs in with one person's HarvardKey and downloads reports. Students can
  see Fall 2023 onward (6 terms, ~9,400 listings).
- **Catalog:** `qscrape/catalog.py` reads public my.harvard pages, with no login.
- **Risks:**
  - It depends on an individual's credentials.
  - It breaks when the Q or my.harvard page layout changes. That has already happened once (the Fall 2025
    "Comment" vs "Comments" label).
  - It puts load on Harvard's sites.
  - It isn't formally sanctioned.

### 3.2 Goal: official access (ask OUE / the Q office and HUIT)
Ask for these, in priority order:
1. **Permission** to show Q ratings and student comments, plus summaries built from them, to signed-in
   undergraduates through Qurated. Ask about the AI-generated tags specifically.
2. **A Q results export or API**, each term: course ID, term, instructor(s), the numeric questions (overall,
   workload, recommend, instructor ratings, enrollment reasons), response counts and the "advice to future
   students" comments, as CSV or JSON.
3. **Course catalog API access.** HUIT's course API isn't open to students, but may be open to a recognized
   student organization.
4. **HarvardKey sign-in (Harvard's own single sign-on, through HUIT)** in place of Google sign-in. This gives
   an official Harvard identity check and removes the Google "Testing / Publish" constraints.
5. **A named contact** to notify when report formats change.

**Draft request (1 paragraph):** *"The HUA built Qurated, a course advisor that helps undergraduates find courses
using Q report data and the course catalog. Access is limited to signed-in Harvard College students. We'd like
OUE's permission to display Q data this way, and a per-term export of Q results so we can stop scraping the Q
site. We're happy to share the code, follow any display or retention rules, and add OUE's preferred
disclaimers."*

### 3.3 "Training on the full Q dataset": what that should mean
Fine-tuning a model on Q reports is **not recommended**:
- Fine-tuning for current Claude models isn't offered through the standard Anthropic API.
- A fine-tuned model would bake in last year's ratings and could repeat them inaccurately.
- Retraining every term would be costly.

Qurated instead uses **retrieval**: Claude reads the relevant reports at answer time, so new data takes effect
immediately and every claim can be traced to a source. "Using the full dataset" means:

| Step | What | One-time effort / cost (estimate) |
|---|---|---|
| Ingest all 6 terms | Scrape the 4 older terms (~6,500 more listings) | ~3 h of unattended scraping; $0 |
| Recency-aware ratings | Weight recent terms more; show per-instructor history ("rated 4.6 under Prof. X, 3.9 under Prof. Y") | ~1 day of dev |
| Re-tag with full history | Regenerate tags from all terms' comments with Claude Haiku (Batches API) | ~$3–5 |
| *(Optional)* semantic search | Add embeddings so "like CS50 but less work" matches by meaning, not just keywords (e.g. Voyage AI) | < $1 to embed 30–60k comments; ~1–2 days of dev |

---

## 4. Runbook (each term)

| When | Task | Command / action | Time |
|---|---|---|---|
| Before course registration opens | Refresh catalog for the next term | `cd qscrape && uv run catalog.py list && uv run catalog.py details` | ~20 min list + ~2 h details (unattended) |
| After OUE releases Q reports for the finished term | Add new Q reports | `uv run scrape.py login && uv run scrape.py index && uv run scrape.py reports --term "<term>"` | ~1 h unattended |
| Right after | Tag newly reviewed courses | `cd app && uv run tag_courses.py --all` | ~10–60 min; ~$0.50–1 |
| Right after | Quality check | Run `app/experiments/bench.py` on the standard questions; compare to last term | ~15 min; ~$1 |
| Right after | Update the in-app date ranges and notices (e.g. the grading-policy notice) | edit `app/static/index.html` | ~15 min |
| Monthly during the term | Review feedback, spend and errors | `app/data/feedback.jsonl`, Anthropic Console, server logs | ~1 h |
| Each spring | Handoff training for next year's maintainers | walk through this doc | ~2 h |

**When a scraper breaks** (counts drop, fields go empty):
1. Compare against the saved raw pages in `qscrape/data/raw/` and `qscrape/data/catalog/raw/`.
2. Fix `parse.py` or `catalog.py`.
3. Re-parse the saved pages (`uv run catalog.py reparse` for the catalog), with no re-download.
4. Spot-check 3 pages by hand before trusting a full run.

---

## 5. Cost model

### 5.1 Measured unit costs
| Item | Cost |
|---|---|
| Course question (search + one Claude Sonnet 5.5 call, ~4,500 tokens in / ~250 out) | **≈ 1.1¢** |
| Follow-up or general question (no search) | ≈ 0.4¢ |
| Haiku pre-check (only when wording is ambiguous) | ≈ 0.04¢ |
| Direct filter search ("Browse courses") | **$0** (no AI) |
| Tagging new courses each term | ≈ $0.50–1 |
| Storage: all data | ≈ 165 MB (100 MB is optional raw backups) |
| Server memory | ≈ 210 MB per app process |

### 5.2 Yearly estimate (2 terms; ~70% of questions during registration weeks)
| Adoption | Active students | Questions/student/term | Questions/yr | Claude API | Hosting + domain | **Total/yr** |
|---|---|---|---|---|---|---|
| Low | 1,600 (20%) | 10 | 32k | ~$350 | ~$150 | **~$500** |
| Medium | 4,000 (50%) | 15 | 120k | ~$1,300 | ~$200 | **~$1,500** |
| High | 6,400 (80%) | 25 | 320k | ~$3,500 | ~$250 | **~$3,750** |

Budget the **medium scenario plus 20%** for year one (≈ **$1,800**), and set the Anthropic hard limit slightly
above the monthly share.

### 5.3 Ways to spend less without hurting quality
1. **Steer simple lookups to "Browse courses"** (free). Many questions are really filters.
2. **Cache common answers** for 24h, for generic questions with no personal preferences or taken courses
   (e.g. "easy Gen Ed"). Expected savings: 20–40% at peak.
3. **Keep answers short** (already capped at 3 cards and 1–2 sentences).
4. **Don't switch to a smaller model for answers:** Claude Haiku scored 2.67/5 vs Sonnet's 3.79 in our benchmark.

---

## 6. Usage limits (protecting the budget and the service)

| Limit | Recommended setting | Why |
|---|---|---|
| Per student per day | **25 AI questions** (currently 40) | Real students rarely need more than ~10; caps one person's worst case at ~28¢/day |
| Per student per minute | **6 questions** | Stops scripts and accidental loops |
| Global daily budget guard | e.g. **$60/day** | When reached, the chatbot pauses and points students to the free "Browse courses" until midnight |
| Anthropic monthly hard limit | ~1.2 × planned monthly spend | Last line of defense; set in the Anthropic Console |
| Direct search ("Browse courses") | Unlimited | Costs nothing |

Show remaining questions in the account menu (already done) and say clearly when a limit is hit, with the
free filter search offered as the fallback.

---

## 7. Scaling to 8,000 students

**Expected load:** even the high scenario peaks at roughly 20–25 questions per minute during registration week.
Search takes ~2 ms. Each answer spends ~3 s waiting on Claude, so one small server is enough.

**Changes needed before a public launch:**
| Change | Why | Effort |
|---|---|---|
| Move sessions' chat history, daily counters and rate limits from memory into SQLite (or Redis) | Restarts and deploys currently reset them | ~0.5 day |
| Raise the Anthropic rate-limit tier before registration week | The real ceiling is API tokens per minute (~100k input tokens/min at peak), not our server | Request in the Console |
| Use the async Claude client or more worker threads | Handle 40+ simultaneous streaming answers comfortably | ~0.5 day |
| Global budget guard + per-minute limit (§6) | Cost safety | ~0.5 day |
| Answer cache for generic questions | Cost and speed at peak | ~1 day |
| Error and uptime monitoring (e.g. a free uptime check + a log-based error alert) | Know about outages before students do | ~0.5 day |
| Nightly backup of `app/data/` (accounts, tags, feedback) | Student data safety | ~1 h |
| Hosting: one container with a persistent disk, HTTPS, custom domain | Public access | ~0.5–1 day |

**Not needed:** a bigger database, search engine or GPU. 4,700 courses fit in memory, and search time is
negligible.

---

## 8. Long-term goal: integration into my.harvard.edu

**Why:** students already plan courses in my.harvard (course search, the Crimson Cart, requirements). Putting
Qurated there means one place to explore, decide and enroll, one official login, and no separate site to find
or trust. Integration also makes Qurated an official, sustained service rather than a student side project that
depends on volunteers.

**A staged path:** each stage is useful on its own, and each builds the trust needed for the next.

| Stage | What it looks like | What it takes | Owner |
|---|---|---|---|
| **1. Standalone + sanctioned** | Qurated at its own domain, HarvardKey sign-in, OUE-approved data | Items in §3.2; privacy policy; security basics (§7) | HUA |
| **2. Linked from my.harvard** | A "Get course advice (Qurated)" link or tile on my.harvard course search and course pages, deep-linking to that course in Qurated | HUIT/Registrar agreement to add the link; stable URLs like `/course/COMPSCI%2050` | HUA + HUIT |
| **3. Embedded** | Qurated's course cards and chat appear inside my.harvard (an embedded panel), and "Add to Cart" hands the course back to my.harvard's own cart | HUIT technical review; embedding/CSP approval; Harvard accessibility (WCAG 2.1 AA) and security review; a my.harvard design pass | HUIT with HUA |
| **4. Official service** | Run on Harvard infrastructure with official data feeds (Q results + catalog API), owned or co-owned by OUE/HUIT, with HUA as product partner | Data-use and hosting agreements, a HUIT service owner, ongoing budget (could shift from HUA to the College) | OUE/HUIT + HUA |

**What to build now so integration is easy later:**
- **HarvardKey sign-in** (Harvard's single sign-on) instead of Google; my.harvard already uses it.
- **Stable course URLs and IDs:** store my.harvard's course ID (already scraped as `course_id`) on every course, so links and "Add to Cart" hand-offs can point at the exact class.
- **A clean API boundary:** keep `/api/browse`, `/api/course/{code}` and `/api/chat` documented and stable, so my.harvard could call them or embed a front end.
- **Accessibility:** meet WCAG 2.1 AA (keyboard navigation, focus states, color contrast, screen-reader labels). Recent UI work added visible focus rings and labeled buttons; a full audit is still needed.
- **Respect Harvard branding rules:** Qurated stays HUA-branded and is clearly marked "not an official Harvard service" until stage 4.
- **Official data instead of scraping** (§3.2). Integration requires it.

**Who to talk to:** HUIT (my.harvard product team / Student Information Systems), the Registrar's Office (course
data and the cart), and OUE (Q reports). Bring usage numbers and student feedback from stages 1–2. Adoption
data is the strongest argument for integration.

**Risks:** university timelines are long (often a year or more), embedded tools face strict security and
accessibility reviews, and HUIT may prefer to build its own version. In that case Qurated's benchmark, prompts
and data pipeline are still valuable to hand over.

---

## 9. Privacy & policy
- Store only what's needed: name/email (for sign-in), favorites, courses taken, preferences. Feedback email
  only if the student opts in.
- **Retention:** delete accounts inactive for 12 months. Clear chat history at restart (it isn't stored
  permanently today; keep it that way unless there's a reason).
- Publish a short privacy policy (also required to publish Google sign-in or to set up HarvardKey sign-in).
- Keep Q data out of the GitHub repo (already gitignored). Repo stays private until OUE agrees otherwise.
- Keep the grading-policy notice current. Review AI-generated tags that judge named instructors (consider
  limiting tags to course-level qualities).

---

## 10. Quality & monitoring
- **Benchmark each term:** `app/experiments/bench.py` (8 fixed questions, Claude Opus judge). Track first-card
  time, total time, cost and quality; investigate if quality drops below ~3.6 or median time exceeds ~5 s.
- **Feedback:** triage `feedback.jsonl` monthly; tag issues as bug / data / idea.
- **Spot checks:** each term, hand-verify 5 random courses' ratings, workload and meeting times against
  my.harvard and Q.

---

## 11. Handoff checklist (each spring)
- [ ] Two maintainers have admin on GitHub, Anthropic, Google Cloud, hosting and the domain
- [ ] `.env` values stored in the HUA password manager (never in the repo or chat)
- [ ] Next maintainer has done one full refresh (§4) with the outgoing lead
- [ ] Budget for next year approved; Anthropic spend limit updated
- [ ] OUE/HUIT contacts and agreements documented in this file
- [ ] Open issues and feedback themes summarized

---

## 12. Open questions for HUA & administration
1. Will OUE approve displaying Q comments and AI summaries? Under what conditions (disclaimers, retention, access)?
2. Can HUA get a per-term Q export or API, and catalog API access, instead of scraping?
3. Can Qurated use HarvardKey sign-in (Harvard's single sign-on)?
4. What's HUA's year-one budget ceiling, and who is the budget owner?
5. Daily AI question cap: 25? Lower during registration week?
6. Should Qurated cover all 6 available Q terms (more history) or only the newest 2 (more relevant)?
   *Recommendation: all 6, weighted toward recent terms.*
7. Who are the two maintainers for 2026–27?
8. Is HUIT / the my.harvard team open to linking Qurated from course search (stage 2), and what review would an
   embedded version (stage 3) require?
9. Would OUE or the College co-own Qurated long term (stage 4), and could funding shift from HUA to the College?
