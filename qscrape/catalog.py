"""Scrape the FAS course catalog from my.harvard (public pages, no login needed).

    uv run catalog.py list                      # pass 1: every section for each term (~10 min)
    uv run catalog.py details [--limit 3]       # pass 2: one detail page per course (resumable, ~1.5 h)

Output: data/catalog/sections.jsonl and data/catalog/details/<key>.json (+ raw/<key>.html.gz).
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

BASE = "https://my.harvard.edu"
TERMS = ["2026 Fall", "2027 Spring"]
OUT = Path(__file__).parent / "data" / "catalog"
SECTIONS = OUT / "sections.jsonl"
DETAILS = OUT / "details"
RAW = OUT / "raw"
DAYS = {"Sunday": "Su", "Monday": "M", "Tuesday": "T", "Wednesday": "W", "Thursday": "Th", "Friday": "F", "Saturday": "Sa"}
TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[ap]m\s*-\s*\d{1,2}:\d{2}\s*[ap]m", re.I)


def norm_code(s):
    return " ".join(s.split()).upper()


def text(el):
    return " ".join(el.get_text(" ", strip=True).split()) if el else None


def parse_meetings(el):
    meetings = []
    for group in el.select('[aria-label="Week Days"]'):
        days = [DAYS[d["aria-label"].split(",")[0]] for d in group.select("[aria-label]")
                if d["aria-label"].endswith("selected") and d["aria-label"].split(",")[0] in DAYS]
        m = TIME_RE.search(text(group.parent) or "")
        meetings.append({"days": days, "time": m.group(0).replace(" ", "") if m else None})
    return meetings


def instructors_of(el):
    return [text(a.select_one("span:last-child")) for a in el.select("a[href^='/instructor/']")
            if a.select_one("span:last-child")]


def parse_cards(hits_html, term):
    """One row per card. Multi-section courses (EXPOS 20, languages...) have no direct course link;
    those rows carry `sections_url` and are expanded into one row per section by expand_sections()."""
    soup = BeautifulSoup(hits_html, "html.parser")
    rows = []
    for card in soup.select(".course-card"):
        title_a = card.select_one("h2 a")
        code_el = card.select_one("h2 .badge") or card.select_one(".badge")
        if not title_a or not code_el:
            continue
        href = title_a.get("href") or ""
        sections_el = card.select_one("[data-url^='/course/sections/']")
        if not href.startswith("/course/") and not sections_el:
            continue
        meetings = parse_meetings(card)
        rows.append({
            "code": norm_code(code_el.get_text()),
            "title": text(title_a),
            "term": term,
            "url": href if href.startswith("/course/") else None,
            "sections_url": None if href.startswith("/course/") else sections_el["data-url"],
            "course_id": card.get("data-course-id"),
            "instructors": instructors_of(card),
            "dept": text(card.select_one("a[href*='Department%2FField'] span")),
            "meetings": meetings or ([{"days": [], "time": None, "tba": True}] if "To Be Announced" in card.get_text() else []),
        })
    return rows


def expand_sections(req, row):
    """Fetch a multi-section course's section list; return one row per section that has a course page."""
    for attempt in range(3):
        try:
            r = req.get(BASE + row["sections_url"])
            if r.ok:
                break
        except Exception:
            pass
        time.sleep(5 * (attempt + 1))
    else:
        return []
    soup = BeautifulSoup(r.json().get("html") or "", "html.parser")
    out = []
    for a in soup.select("a[href^='/course/']"):
        block = a
        for _ in range(6):  # climb to the section's row (the element that also holds the week days)
            if block.parent is None:
                break
            block = block.parent
            if block.select_one('[aria-label="Week Days"]') or block.select_one("a[href^='/instructor/']"):
                break
        topic_el = a.find_next_sibling("div")
        out.append({**row, "url": a["href"], "sections_url": None,
                    "section": text(a), "topic": text(topic_el),
                    "instructors": instructors_of(block) or row["instructors"],
                    "meetings": parse_meetings(block)})
    return out


def fetch_json(req, term, page, subject=None):
    url = (f"{BASE}/search/?q=&school=FAS&term={quote(term)}&sort=subject_catalog&page={page}&browseSchool=false"
           + (f"&Subject={quote(subject)}" if subject else ""))
    for attempt in range(4):
        try:
            r = req.get(url)
            if r.ok:
                return r.json()
            print(f"    HTTP {r.status}; retrying", flush=True)
        except Exception as e:
            print(f"    {e}; retrying", flush=True)
        time.sleep(10 * (attempt + 1))
    raise SystemExit(f"Giving up on {url}")


def subject_counts(facets_html):
    soup = BeautifulSoup(facets_html, "html.parser")
    out = {}
    for inp in soup.select('input[data-type="Subject"]'):
        m = re.search(r"(\d+)\s*$", text(inp.find_parent("label")) or "")
        out[inp["value"]] = int(m.group(1)) if m else None
    return out


def cmd_list(args):
    """Page through each Subject separately: small result sets page reliably, and each subject's count
    from my.harvard tells us whether we got everything (the full listing's sort order isn't stable)."""
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows = {}
    with sync_playwright() as p:
        req = p.request.new_context()
        for term in args.term or TERMS:
            first = fetch_json(req, term, 1)
            total = int(first.get("total_hits") or 0)
            subjects = subject_counts(first.get("facets") or "")
            got_term = 0
            for i, (subj, expected) in enumerate(sorted(subjects.items()), 1):
                rows = {}
                for attempt in range(3):
                    page = 1
                    while True:
                        j = fetch_json(req, term, page, subj)
                        batch = parse_cards(j.get("hits") or "", term)
                        if not batch:
                            break
                        for r in batch:
                            rows[r["url"] or r["sections_url"]] = r
                        page += 1
                        time.sleep(args.delay + random.random() * 0.4)
                    if expected is None or len(rows) >= expected:
                        break
                    print(f"  {subj}: {len(rows)}/{expected}, re-paging", flush=True)
                # Expand multi-section cards into their sections.
                cards_seen = len(rows)
                for key in [k for k, r in rows.items() if r.get("sections_url")]:
                    sections = expand_sections(req, rows.pop(key))
                    for sec in sections:
                        rows[sec["url"]] = sec
                    time.sleep(args.delay + random.random() * 0.4)
                all_rows.update(rows)
                got_term += len(rows)
                if expected and cards_seen < expected:
                    print(f"  note {term} {subj}: {cards_seen}/{expected} listings (rest have no course page)", flush=True)
                if i % 25 == 0:
                    print(f"  {term}: {i}/{len(subjects)} subjects, {got_term} sections", flush=True)
            print(f"{term}: {got_term} sections (my.harvard says {total})", flush=True)
        req.dispose()
    with SECTIONS.open("w") as f:
        for r in all_rows.values():
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {len(all_rows)} sections to {SECTIONS}")


def field(soup, label):
    """Value next to <strong>Label</strong> or <span>Label:</span>."""
    for el in soup.find_all(["strong", "span"]):
        if text(el) in (label, label + ":"):
            val = el.find_next_sibling("span")
            # Multi-valued fields (e.g. two Gen Ed categories) are separated by <br>.
            parts = [" ".join(t.split()) for t in val.stripped_strings] if val else []
            v = " / ".join(p for p in parts if p)
            return None if v in ("", "N/A") else v
    return None


def parse_detail(html):
    soup = BeautifulSoup(html, "html.parser")
    desc = soup.select_one("#course-desc")
    if desc and desc.h3:
        desc.h3.extract()
    notes = soup.select_one(".course-page-class-notes")
    if notes and notes.h3:
        notes.h3.extract()
    links = {text(a): a.get("href") for a in soup.find_all("a") if text(a) in ("Syllabus", "Website", "Q Report")}
    enroll = field(soup, "Enrollment")
    cap = int(enroll.split("/")[1]) if enroll and re.match(r"^\d+/\d+$", enroll) else None
    return {
        "description": text(desc),
        "class_notes": text(notes),
        "credits": field(soup, "Credits"),
        "department": field(soup, "Department"),
        "component": field(soup, "Course Component"),
        "grading": field(soup, "Grading Basis"),
        "gen_ed": field(soup, "General Education"),
        "qrd": field(soup, "Quantitative Reasoning with Data"),
        "divisional": field(soup, "Divisional Distribution"),
        "level": field(soup, "Course Level"),
        "consent": field(soup, "Consent"),
        "enrollment_cap": cap,
        "course_id": field(soup, "Course ID"),
        "syllabus_url": links.get("Syllabus"),
        "website_url": links.get("Website"),
    }


def detail_key(row):
    return hashlib.sha1(f"{row['code']}|{row['title']}".encode()).hexdigest()[:16]


def cmd_details(args):
    DETAILS.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in SECTIONS.open()]
    # One page per course+title; Gen Ed, description etc. don't change between terms. Prefer the later term.
    unique = {}
    for r in sorted(rows, key=lambda r: r["term"] != "2027 Spring"):
        unique.setdefault(detail_key(r), r)
    todo = [(k, r) for k, r in unique.items() if not (DETAILS / f"{k}.json").exists()]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(rows)} sections, {len(unique)} unique courses, {len(todo)} to fetch", flush=True)
    failures = 0
    with sync_playwright() as p:
        req = p.request.new_context()
        for i, (k, r) in enumerate(todo, 1):
            try:
                resp = req.get(BASE + r["url"])
                html = resp.text()
                if not resp.ok or "course-desc" not in html and "General Education" not in html:
                    failures += 1
                    print(f"[{i}/{len(todo)}] {r['code']}: HTTP {resp.status}, no course content", flush=True)
                    if failures >= 5:
                        sys.exit("5 failures in a row; stopping. Rerun later to resume.")
                    time.sleep(10)
                    continue
                failures = 0
                (RAW / f"{k}.html.gz").write_bytes(gzip.compress(html.encode()))
                d = {"code": r["code"], "term": r["term"], "title": r["title"], "url": r["url"], **parse_detail(html)}
                (DETAILS / f"{k}.json").write_text(json.dumps(d))
                if args.limit or i % 50 == 0:
                    print(f"[{i}/{len(todo)}] {r['code']} {r['term']}: gen_ed={d['gen_ed']} div={d['divisional']} "
                          f"cap={d['enrollment_cap']} syllabus={'yes' if d['syllabus_url'] else 'no'}", flush=True)
            except Exception as e:
                print(f"[{i}/{len(todo)}] {r['code']}: ERROR {e}", flush=True)
                time.sleep(5)
            time.sleep(args.delay + random.random() * 0.5)
        req.dispose()


def cmd_reparse(args):
    """Re-run parse_detail over the saved raw pages (after a parser fix); no network."""
    n = 0
    for path in DETAILS.glob("*.json"):
        raw = RAW / f"{path.stem}.html.gz"
        if not raw.exists():
            continue
        d = json.loads(path.read_text())
        d.update(parse_detail(gzip.decompress(raw.read_bytes()).decode()))
        path.write_text(json.dumps(d))
        n += 1
    print(f"Re-parsed {n} detail pages")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("list"); a.add_argument("--term", nargs="*"); a.add_argument("--delay", type=float, default=0.8)
    a.set_defaults(fn=cmd_list)
    b = sub.add_parser("details"); b.add_argument("--limit", type=int); b.add_argument("--delay", type=float, default=1.0)
    b.set_defaults(fn=cmd_details)
    sub.add_parser("reparse").set_defaults(fn=cmd_reparse)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
