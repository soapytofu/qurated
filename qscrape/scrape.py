"""Scrape Harvard FAS Q reports using your own HarvardKey session.

    uv run scrape.py login                      # opens Chrome; sign in with HarvardKey + Duo
    uv run scrape.py index                      # list every course report for every term
    uv run scrape.py reports [--limit 10]       # download + parse reports (resumable)

Data lands in ./data. Keep it private: Q data is for the Harvard community only.
"""
import argparse
import gzip
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from parse import parse_report

BASE = "https://qreports.fas.harvard.edu"
ROOT = Path(__file__).parent
PROFILE = ROOT / ".browser-profile"
STATE = ROOT / ".browser-profile" / "session-state.json"  # includes session cookies Chrome drops on close
DATA = ROOT / "data"
INDEX = DATA / "index.jsonl"
REPORTS = DATA / "reports"
RAW = DATA / "raw"  # gzipped report HTML, so parser fixes don't need a re-download


def report_key(row):
    # Link ids repeat across sections/terms; the report URL is the true identity.
    return hashlib.sha1(row["url"].encode()).hexdigest()[:16]


def open_context(p, headless=True):
    ctx = p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", headless=headless)
    if STATE.exists():
        ctx.add_cookies(json.loads(STATE.read_text())["cookies"])
    return ctx


def logged_in(resp_url, text):
    return "login.harvard.edu" not in resp_url and "Sign in with your account" not in text


def cmd_login(_):
    with sync_playwright() as p:
        ctx = open_context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE + "/browse/index")
        print("Sign in with HarvardKey in the Chrome window. Waiting up to 5 minutes...")
        page.wait_for_url(re.compile(r"qreports\.fas\.harvard\.edu/browse"), timeout=300_000)
        # Visit one report so the bluera.com session cookie gets set too.
        link = page.locator('a[href*="bluera.com"]').first
        if link.count():
            page.goto(link.get_attribute("href"))
            page.wait_for_load_state("domcontentloaded")
        ctx.storage_state(path=str(STATE))
        print("Logged in. Session saved to .browser-profile/")
        ctx.close()


def parse_index(html, term):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select("div.card.term"):
        dept_el = card.select_one(".card-header b")
        dept = dept_el.get_text(strip=True) if dept_el else None
        for a in card.select('a[href*="bluera.com"]'):
            text = " ".join(a.get_text(" ", strip=True).split())
            m = re.match(r"^(?P<code>.+?)-(?P<title>.+?)\s+(?P<section>\d{3})?\s*(?:\((?P<instr>.*)\))?$", text)
            g = m.groupdict() if m else {}
            rows.append({
                "id": a.get("id"),
                "term": term,
                "dept": dept,
                "code": (g.get("code") or "").strip() or None,
                "title": (g.get("title") or text).strip(),
                "section": g.get("section"),
                "instructors": [s.strip() for s in (g.get("instr") or "").split(",") if s.strip()],
                "url": a["href"],
                "link_text": text,
            })
    return rows


def list_terms(html):
    soup = BeautifulSoup(html, "html.parser")
    terms = []
    for a in soup.select('a[href*="calTerm="]'):
        m = re.search(r"calTerm=([^&]+)", a["href"])
        if m and "school=FAS" in a["href"]:
            terms.append(m.group(1).replace("%20", " "))
    return list(dict.fromkeys(terms))


def cmd_index(args):
    DATA.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = open_context(p)
        r = ctx.request.get(BASE + "/browse/index")
        html = r.text()
        if not logged_in(r.url, html):
            sys.exit("Not logged in. Run: uv run scrape.py login")
        terms = args.terms or list_terms(html)
        print("Terms:", terms)
        total = 0
        with INDEX.open("w") as f:
            for term in terms:
                r = ctx.request.get(f"{BASE}/browse/index?school=FAS&calTerm={quote(term)}")
                rows = parse_index(r.text(), term)
                for row in rows:
                    f.write(json.dumps(row) + "\n")
                total += len(rows)
                print(f"  {term}: {len(rows)} reports")
                time.sleep(1.5)
        print(f"Wrote {total} rows to {INDEX}")
        ctx.close()


def cmd_reports(args):
    REPORTS.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in INDEX.open()]
    if args.term:
        rows = [r for r in rows if r["term"] in args.term]
    # Cross-listed courses share one report URL: fetch once, keep every listing.
    by_url = {}
    for r in rows:
        by_url.setdefault(r["url"], []).append(r)
    def wanted(row):
        path = REPORTS / f"{report_key(row)}.json"
        if not path.exists():
            return True
        if args.refetch_no_comments:
            return not json.loads(path.read_text())["qreport"]["comments"]
        return False
    todo = [rs[0] | {"listings": [{"code": x["code"], "dept": x["dept"]} for x in rs]}
            for rs in by_url.values() if wanted(rs[0])]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(rows)} in index, {len(todo)} to fetch")
    with sync_playwright() as p:
        ctx = open_context(p)
        page = None
        failures = 0
        for i, row in enumerate(todo, 1):
            try:
                r = ctx.request.get(row["url"])
                html = r.text()
                if "report-block" not in html:
                    # Fall back to a real page load (handles any JS-based SSO hop).
                    page = page or ctx.new_page()
                    page.goto(row["url"], wait_until="networkidle")
                    html = page.content()
                if not logged_in(page.url if page else r.url, html) or "report-block" not in html:
                    failures += 1
                    print(f"[{i}/{len(todo)}] {row['link_text']}: no report content")
                    if failures >= 5:
                        sys.exit("5 failures in a row. Session probably expired: run `uv run scrape.py login`.")
                    continue
                failures = 0
                RAW.mkdir(parents=True, exist_ok=True)
                (RAW / f"{report_key(row)}.html.gz").write_bytes(gzip.compress(html.encode()))
                data = {**row, "qreport": parse_report(html)}
                (REPORTS / f"{report_key(row)}.json").write_text(json.dumps(data, indent=1))
                s = data["qreport"]["summary"]
                print(f"[{i}/{len(todo)}] {row['code']} {row['term']}: overall={s['course_overall']} "
                      f"workload={s['workload_hrs_mean']} rec={s['recommend_mean']} comments={s['n_comments']}")
            except Exception as e:
                print(f"[{i}/{len(todo)}] {row['link_text']}: ERROR {e}")
            time.sleep(args.delay + random.random())
        ctx.close()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    a = sub.add_parser("index"); a.add_argument("--terms", nargs="*"); a.set_defaults(fn=cmd_index)
    b = sub.add_parser("reports"); b.add_argument("--limit", type=int); b.add_argument("--term", nargs="*")
    b.add_argument("--delay", type=float, default=1.0)
    b.add_argument("--refetch-no-comments", action="store_true", help="re-download reports saved with no comments")
    b.set_defaults(fn=cmd_reports)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
