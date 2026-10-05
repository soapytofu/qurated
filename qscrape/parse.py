"""Parse an explorance Blue Q report page (my-harvard-bc.bluera.com) into structured data."""
import re
from bs4 import BeautifulSoup


def _num(s):
    if s is None:
        return None
    s = s.strip().rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def _table_rows(table):
    headers = [th.get_text(" ", strip=True) for th in table.select("thead th")]
    rows = []
    for tr in table.select("tbody tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        rows.append(cells)
    return headers, rows


def parse_report(html):
    soup = BeautifulSoup(html, "html.parser")
    blocks = []
    for blk in soup.select("div.report-block"):
        title_el = blk.select_one(".ReportBlockTitle, .FrequencyQuestionTitle, h3, h4")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        tables = [dict(zip(("headers", "rows"), _table_rows(t))) for t in blk.select("table")]
        if tables:
            blocks.append({"title": title, "tables": tables})

    out = {
        "response": {},
        "course_questions": {},
        "instructors": [],
        "workload": {},
        "recommend": {},
        "enroll_reasons": {},
        "other_questions": {},
        "comments": [],
    }

    current_instructor = None
    for b in blocks:
        title = b["title"]
        for t in b["tables"]:
            h, rows = t["headers"], t["rows"]
            if h[:2] == ["Raters", "Students"]:
                for r in rows:
                    out["response"][r[0].lower().replace(" ", "_")] = _num(r[1])
            elif "Course Mean" in h:
                for r in rows:
                    out["course_questions"][r[0]] = {"count": _num(r[1]), "mean": _num(r[-2]), "fas_mean": _num(r[-1]),
                                                     "dist": dict(zip(h[2:-2], (_num(x) for x in r[2:-2])))}
            elif "Instructor Mean" in h:
                current_instructor = {"name": None, "questions": {}}
                for r in rows:
                    current_instructor["questions"][r[0]] = {"count": _num(r[1]), "mean": _num(r[-2]), "fas_mean": _num(r[-1])}
                out["instructors"].append(current_instructor)
            elif len(h) == 1 and h[0].lower().startswith("comment"):  # "Comments" (Spring 2026) / "Comment" (Fall 2025)
                out["comments"].extend({"question": title, "text": r[0]} for r in rows if r and r[0])
            elif h == ["Statistics", "Value"]:
                stats = {r[0].lower().replace(" ", "_"): _num(r[1]) for r in rows}
                key = _classify(title)
                if key in ("workload", "recommend"):
                    out[key].update(stats)
                else:
                    out["other_questions"].setdefault(title, {}).update(stats)
            elif h[:2] == ["Options", "Score"]:
                dist = {r[0]: _num(r[3]) for r in rows}
                key = _classify(title)
                if key == "recommend":
                    out["recommend"]["dist"] = dist
                else:
                    out["other_questions"].setdefault(title, {})["dist"] = dist
            elif h == ["Options", "Count"]:
                if "reason" in title.lower():
                    out["enroll_reasons"] = {r[0]: _num(r[1]) for r in rows}

    # Instructor names come from "Instructor Feedback for <Name>" block titles, in order.
    names = [re.sub(r"^Instructor Feedback for\s+", "", b["title"]) for b in blocks
             if b["title"].startswith("Instructor Feedback for")]
    names += [h.get_text(" ", strip=True).replace("Instructor Feedback for", "").strip()
              for h in soup.find_all(["h3", "h4"]) if h.get_text(strip=True).startswith("Instructor Feedback for")]
    names = list(dict.fromkeys(names))
    for inst, name in zip(out["instructors"], names):
        inst["name"] = name

    term_el = soup.select_one("h2.Report_TOC")
    out["report_title"] = term_el.get_text(" ", strip=True) if term_el else None

    # Handy summary fields for the chatbot / filters.
    cq = out["course_questions"]
    overall = next((v for k, v in cq.items() if k.lower().startswith("evaluate the course overall")), None)
    out["summary"] = {
        "course_overall": overall and overall["mean"],
        "course_overall_fas": overall and overall["fas_mean"],
        "instructor_overall": [
            {"name": i["name"], "mean": next((q["mean"] for k, q in i["questions"].items()
                                               if k.lower().startswith("evaluate your instructor overall")), None)}
            for i in out["instructors"]],
        "workload_hrs_mean": out["workload"].get("mean"),
        "workload_hrs_median": out["workload"].get("median"),
        "recommend_mean": out["recommend"].get("mean"),
        "responded": out["response"].get("responded"),
        "invited": out["response"].get("invited"),
        "n_comments": len(out["comments"]),
    }
    return out


def _classify(title):
    t = title.lower()
    if "hours per week" in t:
        return "workload"
    if "recommend this course" in t:
        return "recommend"
    return None
