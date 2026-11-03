"""Load scraped Q reports into course records and search them."""
import json
import math
import re
from difflib import get_close_matches
from collections import Counter, defaultdict
from pathlib import Path

REPORTS_DIR = Path(__file__).parent.parent / "qscrape" / "data" / "reports"
TAGS_FILE = Path(__file__).parent / "data" / "tags.json"  # written by tag_courses.py
CATALOG_DIR = Path(__file__).parent.parent / "qscrape" / "data" / "catalog"  # written by qscrape/catalog.py
UPCOMING = ("2026 Fall", "2027 Spring")
DEFAULT_TERM = "2027 Spring"
DETAIL_FIELDS = ("description", "class_notes", "credits", "component", "grading", "gen_ed", "qrd", "divisional",
                 "level", "consent", "enrollment_cap", "syllabus_url")
NO_Q = {"course_overall": None, "course_overall_fas_avg": None, "workload_hrs_mean": None, "recommend_mean": None,
        "responded": None, "invited": None}

TERM_ORDER = {"Spring": 0, "Fall": 1}  # Fall comes after Spring within a calendar year


# (field, display label, report section, question prefix); all are 1–5 means.
EXTRA_Q_SCORES = (
    ('materials', 'Course materials', 'course_questions', 'course materials'),
    ('assignments', 'Assignments', 'course_questions', 'assignments ('),
    ('course_feedback', 'Feedback on coursework', 'course_questions', 'feedback you received'),
    ('sections', 'Section component', 'course_questions', 'section component'),
    ('accessibility', 'Instructor accessibility', 'instructors', 'is accessible outside'),
    ('enthusiasm', 'Generates enthusiasm', 'instructors', 'generates enthusiasm'),
    ('discussion', 'Encourages discussion and participation', 'instructors', 'facilitates discussion'),
    ('instructor_feedback', 'Useful instructor feedback', 'instructors', 'gives useful feedback'),
    ('timely_returns', 'Timely assignment returns', 'instructors', 'returns assignments'),
    ('open_mindedness', 'Students listen with an open mind', 'other_questions', 'in this course, most students listen'),
    ('expressing_views', 'Comfort expressing controversial views', 'other_questions', 'in this course (including sections), i feel comfortable'),
)


# Every numeric metric displayed from an individual Q report can order browse results.
Q_SORT_FIELDS = {
    "overall": "course_overall", "workload": "workload_hrs_mean", "recommend": "recommend_mean",
    "instructor": "instructor_score", "lectures": "effective_lectures",
    "reviews": "responded", "invited": "invited",
    **{field: field for field, *_ in EXTRA_Q_SCORES},
}
BROWSE_SORTS = frozenset(Q_SORT_FIELDS) | {"rating", "relevance", "code"}


def additional_scores(qreport):
    scores = {}
    for field, _, section, prefix in EXTRA_Q_SCORES:
        if section == "instructors":
            continue
        scores[field] = None
        for label, answer in qreport.get(section, {}).items():
            if label.lower().startswith(prefix):
                try:
                    value = float(answer["mean"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(value) and 1 <= value <= 5:
                    scores[field] = value
    return scores


def instructor_scores(qreport):
    """Response-weighted scores across instructors in one report, never across terms."""
    scores = {}
    for field, prefix in (("instructor_score", "evaluate your instructor overall"),
                          ("effective_lectures", "gives effective lectures or presentations"),
                          *((field, prefix) for field, _, section, prefix in EXTRA_Q_SCORES if section == "instructors")):
        weighted = count = 0.0
        for instructor in qreport.get("instructors", []):
            for label, answer in instructor.get("questions", {}).items():
                if not label.lower().startswith(prefix):
                    continue
                try:
                    mean, n = float(answer["mean"]), float(answer["count"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(mean) and math.isfinite(n) and 1 <= mean <= 5 and n > 0:
                    weighted += mean * n
                    count += n
        scores[field] = weighted / count if count else None
    return scores

NOT_A_CLASS = re.compile(r"\b(tutorial|supervised|reading and research|directed (reading|research|writing)|"
                         r"independent (study|research)|dissertation|thesis|senior project)\b", re.I)
NON_CLASS_COMPONENTS = {"Reading and Research", "Tutorial", "Thesis Research", "Doctoral Dissertation"}
GEN_EDS = ["Aesthetics and Culture", "Ethics and Civics", "Histories, Societies, Individuals",
           "Science and Technology in Society"]
DIVISIONS = ["Arts and Humanities", "Social Sciences", "Science & Engineering & Applied Science"]
DAY_CODES = ["M", "T", "W", "Th", "F"]
# Verified renumberings; never infer equivalence by stripping a trailing zero.
# Harvard lists CS1240 (formerly CS124) and CS1520 (formerly CS152):
# https://csadvising.seas.harvard.edu/concentration/courses/tags/
CODE_EQUIVALENTS = ({"COMPSCI124", "COMPSCI1240"}, {"COMPSCI152", "COMPSCI1520"})


def normalized_code(code):
    value = re.sub(r"\s+", "", code.upper())
    return re.sub(r"^CS(?=\d)", "COMPSCI", value)

TIME_START = re.compile(r"^(\d{1,2}):(\d{2})(am|pm)", re.I)


def start_minutes(t):
    m = TIME_START.match(t or "")
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    return (h % 12 + (12 if ap == "pm" else 0)) * 60 + mi


STOPWORDS = set("""a an and are as at be but by for from has have i if in into is it its of on or
so that the their there this to was were will with you your class course courses really very
classes want need looking look something some any me my good best take taking like would should could can
recommend recommendations suggest suggestions find what which do does about please help""".split())


def term_rank(term):
    year, season = term.split()
    return int(year) * 2 + TERM_ORDER.get(season, 0)


def planning_filters(filters=None):
    """Default to spring; 'any' means the loaded catalog terms, never Q history."""
    result = dict(filters or {})
    result["term"] = result.get("term") or DEFAULT_TERM
    return result


def tokenize(text):
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOPWORDS and len(t) > 1]


class CourseDB:
    def __init__(self, reports_dir=REPORTS_DIR, load_tags=True, before=None):
        self.reports_dir = reports_dir
        self.load_tags = load_tags
        self.before = before  # only use report files written before this unix time
        self.courses = {}
        self.reload()

    def reload(self):
        grouped = defaultdict(list)
        for path in sorted(self.reports_dir.glob("*.json")):
            if self.before and path.stat().st_mtime > self.before:
                continue
            try:
                r = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue  # file mid-write by the scraper
            if r.get("code"):
                grouped[r["code"]].append(r)

        courses = {}
        for code, reports in grouped.items():
            reports.sort(key=lambda r: (term_rank(r["term"]), r.get("section") or ""), reverse=True)
            latest = reports[0]
            offerings = []
            comments = []
            for r in reports:
                q = r["qreport"]
                s = q["summary"]
                instructors = [i["name"] for i in q["instructors"] if i.get("name")] or r["instructors"]
                offerings.append({
                    **instructor_scores(q), **additional_scores(q),
                    "term": r["term"],
                    "section": r.get("section"),
                    "instructors": instructors,
                    "course_overall": s["course_overall"],
                    "course_overall_fas_avg": s["course_overall_fas"],
                    "instructor_overall": s["instructor_overall"],
                    "workload_hrs_mean": s["workload_hrs_mean"],
                    "workload_hrs_median": s["workload_hrs_median"],
                    "recommend_mean": s["recommend_mean"],
                    "responded": s["responded"],
                    "invited": s["invited"],
                    "enroll_reasons": {k: v for k, v in q["enroll_reasons"].items() if v},
                    "course_questions": {k: v["mean"] for k, v in q["course_questions"].items()},
                })
                comments += [{"term": r["term"], "text": c["text"]} for c in q["comments"]]
            listings = {l["code"] for r in reports for l in r.get("listings", [])} - {code}
            courses[code] = {
                "code": code,
                "title": latest["title"],
                "dept": latest["dept"],
                "also_listed_as": sorted(listings),
                "latest_term": latest["term"],
                "instructors": offerings[0]["instructors"],
                "offerings": offerings,
                "comments": comments,
            }
        self._merge_catalog(courses)
        tags = {}
        if self.load_tags and TAGS_FILE.exists():
            try:
                tags = json.loads(TAGS_FILE.read_text())
            except json.JSONDecodeError:
                pass
        for code, c in courses.items():
            t = tags.get(code, {})
            c["tags"] = t.get("tags", [])
            c["summary"] = t.get("summary")
        self.courses = courses
        self.tags_mtime = TAGS_FILE.stat().st_mtime if TAGS_FILE.exists() else 0
        self._build_index()

    def _merge_catalog(self, courses):
        """Attach my.harvard catalog data; catalog-only courses (no Q reports yet) become searchable too."""
        sections_file = CATALOG_DIR / "sections.jsonl"
        if not sections_file.exists():
            self.catalog_mtime = 0
            return
        self.catalog_mtime = sections_file.stat().st_mtime
        by_code = defaultdict(list)
        for line in sections_file.open():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            by_code[row["code"]].append(row)
        details = defaultdict(dict)  # code -> merged course-level fields (first non-empty wins)
        for path in sorted((CATALOG_DIR / "details").glob("*.json")):
            try:
                d = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            for k in DETAIL_FIELDS:
                if d.get(k) and not details[d["code"]].get(k):
                    details[d["code"]][k] = d[k]
        for code, rows in by_code.items():
            offered = {}
            for r in rows:
                offered.setdefault(r["term"], []).append(
                    {"title": r["title"], "topic": r.get("topic") if r.get("topic") not in (None, "TBA") else None,
                     "section": r.get("section"), "instructors": r["instructors"], "meetings": r["meetings"]})
            terms = sorted(offered, key=term_rank)
            cat = {"offered": [{"term": t, "sections": offered[t]} for t in terms], **details.get(code, {})}
            if code not in courses:
                first = rows[0]
                courses[code] = {"code": code, "title": first["title"], "dept": first.get("dept") or "",
                                 "also_listed_as": [], "latest_term": None, "instructors": first["instructors"],
                                 "offerings": [], "comments": []}
            courses[code]["catalog"] = cat

    def _build_index(self):
        self._identity_cache = {}
        self.doc_tf = {}
        df = Counter()
        lengths = []
        for code, c in self.courses.items():
            cat = c.get("catalog") or {}
            topics = " ".join(sec.get("topic") or "" for o in cat.get("offered", []) for sec in o["sections"])
            tokens = (tokenize(c["code"] + " " + c["title"]) * 4
                      + tokenize((cat.get("description") or "") + " " + (cat.get("gen_ed") or "")) * 2
                      + tokenize(topics) * 2
                      + tokenize(" ".join(t["label"] for t in c["tags"]) + " " + (c["summary"] or "")) * 3
                      + tokenize(c["dept"] + " " + " ".join(c["also_listed_as"])) * 2
                      + tokenize(" ".join(c["instructors"])) * 2
                      + tokenize(" ".join(x["text"] for x in c["comments"])))
            tf = Counter(tokens)
            self.doc_tf[code] = (tf, len(tokens))
            df.update(tf.keys())
            lengths.append(len(tokens))
        n = max(len(self.courses), 1)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.avg_len = (sum(lengths) / n) if lengths else 1

    def _bm25(self, code, q_tokens, k1=1.4, b=0.75):
        tf, length = self.doc_tf[code]
        score = 0.0
        for t in q_tokens:
            f = tf.get(t)
            if f:
                score += self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * length / self.avg_len))
        return score

    @staticmethod
    def _q(c, q_term=None):
        """Arithmetic mean of each available report metric in the selected Q scope.

        Each report contributes once, including multiple sections in a term. Missing
        values are omitted per metric rather than treated as zero or borrowed from
        another term. This is a mean of report means, not a pooled student mean.
        """
        reports = [o for o in c["offerings"] if not q_term or o.get("term") == q_term]
        result = dict(NO_Q)
        for field in {*Q_SORT_FIELDS.values(), "course_overall_fas_avg"}:
            values = [o.get(field) for o in reports]
            values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
            result[field] = sum(values) / len(values) if values else None
        terms = sorted({o["term"] for o in reports if o.get("term")}, key=term_rank)
        result.update(term=q_term or (terms[0] if len(terms) == 1 else "All terms"),
                      report_count=len(reports), terms=terms)
        return result

    def q_terms(self):
        return sorted({o["term"] for c in self.courses.values() for o in c["offerings"] if o.get("term")}, key=term_rank, reverse=True)

    @staticmethod
    def _next(c, filters=None):
        """(term, first section) of the next upcoming offering, or (None, None)."""
        filters = planning_filters(filters)
        for o in (c.get("catalog") or {}).get("offered", []):
            if o["term"] in UPCOMING and (filters["term"] == "any" or o["term"] == filters["term"]):
                if not o["sections"] and not (filters.get("avoid_days") or filters.get("earliest_start")):
                    return o["term"], None
                for section in o["sections"]:
                    if CourseDB.section_fits(section, filters):
                        return o["term"], section
        return None, None

    @staticmethod
    def _meets(section):
        if not section:
            return None
        parts = [f"{'/'.join(m['days'])} {m['time']}" for m in section.get("meetings", []) if m.get("days") and m.get("time")]
        return "; ".join(parts) or "time TBA"

    def _group_sections(self, sections, cap=60):
        """Collapse sections sharing a topic + instructors into one entry with all their times (token-cheap)."""
        groups = {}
        for s in sections:
            key = (s.get("topic") or "", tuple(s["instructors"]))
            groups.setdefault(key, []).append(self._meets(s))
        out = []
        for (topic, instr), times in list(groups.items())[:cap]:
            g = {"instructors": list(instr), "meets": sorted(set(times))}
            if topic:
                g["topic"] = topic
            out.append(g)
        return out

    @staticmethod
    def is_class(c):
        """False for research/tutorial/thesis/independent-study listings."""
        if ((c.get("catalog") or {}).get("component") or "") in NON_CLASS_COMPONENTS:
            return False
        return not NOT_A_CLASS.search(c["title"])

    @staticmethod
    def is_grad_level(c):
        level = ((c.get("catalog") or {}).get("level") or "").lower()
        return "primarily for graduate" in level or level == "graduate course"

    @classmethod
    def is_undergrad_class(cls, c):
        """Excludes graduate-level courses and research/tutorial/thesis listings."""
        return cls.is_class(c) and not cls.is_grad_level(c)

    # ---------------------------------------------------------------- structured filters
    # Keys (all optional): term, subject, min_overall, max_workload, min_recommend, gen_ed, divisional, qrd,
    # avoid_days [..], earliest_start (minutes), small_class, has_reviews, grading ("letter"|"satisfactory"),
    # format ("Lecture"|"Seminar"), include_grad
    def _term_sections(self, c, term):
        offered = (c.get("catalog") or {}).get("offered", [])
        return [s for o in offered if o["term"] in UPCOMING and (term == "any" or o["term"] == term)
                for s in o["sections"]]

    def matches(self, c, f):
        f = planning_filters(f)
        cat = c.get("catalog") or {}
        o = self._q(c, f.get("q_term"))
        if f.get("q_term") and not o["report_count"]:
            return False
        if not self.is_class(c) or (not f.get("include_grad") and self.is_grad_level(c)):
            return False
        term = f.get("term")
        if not any(x["term"] in UPCOMING and (term == "any" or x["term"] == term)
                   for x in cat.get("offered", [])):
            return False
        if f.get("subject") and c["code"].split()[0] != f["subject"] and \
                not any(x.split()[0] == f["subject"] for x in c["also_listed_as"]):
            return False
        for key, field, cmp in (("min_overall", "course_overall", "ge"), ("min_recommend", "recommend_mean", "ge"),
                                ("min_instructor", "instructor_score", "ge"),
                                ("min_lectures", "effective_lectures", "ge"),
                                *(("min_" + field, field, "ge") for field, *_ in EXTRA_Q_SCORES),
                                ("workload_under", "workload_hrs_mean", "lt"),
                                ("max_workload", "workload_hrs_mean", "le")):
            if f.get(key):
                v = o.get(field)
                if v is None or (v < f[key] if cmp == "ge" else v >= f[key] if cmp == "lt" else v > f[key]):
                    return False
        if f.get("has_reviews") and not c["offerings"]:
            return False
        if f.get("any_gen_ed") and not cat.get("gen_ed"):
            return False
        if f.get("gen_ed") and f["gen_ed"] not in (cat.get("gen_ed") or ""):
            return False
        if f.get("divisional") and f["divisional"] not in (cat.get("divisional") or ""):
            return False
        if f.get("qrd") and cat.get("qrd") != "Yes":
            return False
        if f.get("small_class") and not (cat.get("enrollment_cap") and cat["enrollment_cap"] <= 20):
            return False
        if f.get("grading"):
            g = (cat.get("grading") or "").lower()
            if not g or (f["grading"] == "letter" and "letter" not in g) or (
                    f["grading"] == "satisfactory" and not any(x in g for x in ("sat/unsat", "satisfactory", "pass/fail"))):
                return False
        if f.get("format") and cat.get("component") != f["format"]:
            return False
        if f.get("avoid_days") or f.get("earliest_start"):
            if not any(self.section_fits(sec, f) for sec in self._term_sections(c, term)):
                return False
        return True

    @staticmethod
    def section_fits(section, filters=None):
        f = filters or {}
        avoid, earliest = set(f.get("avoid_days") or []), f.get("earliest_start")
        if not avoid and not earliest:
            return True
        meetings = section.get("meetings", [])
        if not meetings or any(not m.get("days") for m in meetings):
            return False
        return all(not (set(m["days"]) & avoid) and
                   (not earliest or (start_minutes(m.get("time")) or 0) >= earliest) for m in meetings)

    def browse(self, f=None, query="", sort="overall", offset=0, limit=24, exclude=frozenset(), direction=None):
        """Direct search, no LLM: every course matching the filters, ranked by `sort` (or by keyword relevance)."""
        # A complete known code/alias is an exact lookup, not a fuzzy keyword query.
        exact = self.lookup(query.strip()) if query.strip() else None
        if exact:
            matching = self.filtered_lookup(query.strip(), f, exclude)
            return {"total": int(matching is not None), "cards": [self.card(matching["code"], filters=f)]
                    if matching and offset == 0 and limit > 0 else []}
        sort = sort if sort in BROWSE_SORTS else "overall"
        direction = direction if direction in {"asc", "desc"} else ("asc" if sort == "workload" else "desc")
        q_tokens = tokenize(query)
        rows = []
        for code, c in self.courses.items():
            if exclude and self.is_taken(code, exclude):
                continue
            if not self.matches(c, f):
                continue
            rel = self._bm25(code, q_tokens) if q_tokens else 0.0
            if q_tokens and rel == 0:
                continue
            o = self._q(c, (f or {}).get("q_term"))
            n = o.get("responded") or 0
            # Shrink small-sample ratings toward the FAS average so 5.0-from-3-students doesn't top the list.
            bayes = ((o.get("course_overall") or 0) * n + 4.25 * 8) / (n + 8) if o.get("course_overall") else 0
            if sort in Q_SORT_FIELDS:
                value = o.get(Q_SORT_FIELDS[sort])
            elif sort == "code":
                value = 0
            elif sort == "relevance" and q_tokens:
                value = rel
            else:
                value = bayes if o.get("course_overall") is not None else None
            missing = not isinstance(value, (int, float)) or not math.isfinite(value)
            # Missing metrics are always last, including ascending order. Code breaks ties
            # deterministically so pagination cannot repeat or skip equally scored courses.
            key = 0 if missing else (value if direction == "asc" else -value)
            rows.append((missing, key, code))
        rows.sort()
        return {"total": len(rows), "cards": [self.card(code, filters=f) for _, _, code in rows[offset:offset + limit]]}

    def facets(self):
        subjects = {}
        for c in self.courses.values():
            if not self.is_undergrad_class(c):
                continue
            sub = c["code"].split()[0]
            name, n = subjects.get(sub, (c["dept"] or sub, 0))
            subjects[sub] = (name, n + 1)
        return {"subjects": [{"code": k, "name": v[0], "count": v[1]} for k, v in sorted(subjects.items())],
                "terms": list(UPCOMING), "default_term": DEFAULT_TERM,
                "q_terms": self.q_terms(), "gen_eds": GEN_EDS, "divisions": DIVISIONS, "days": DAY_CODES}

    def is_taken(self, code, taken):
        """True if the course, or any code it's cross-listed as, is in the student's taken set."""
        if not taken:
            return False
        keys = self.identity_keys(code)
        return any(keys & self.identity_keys(t) for t in taken)

    def identity_keys(self, code):
        """Known equivalences for exclusions/deduplication, even when both records exist."""
        norm = normalized_code(code)
        if norm in self._identity_cache:
            return self._identity_cache[norm]
        course = self.lookup(code)
        keys = {norm}
        if course:
            keys.update(normalized_code(alias) for alias in [course["code"], *course["also_listed_as"]])
        for group in CODE_EQUIVALENTS:
            if keys & group:
                keys.update(group)
        keys = frozenset(keys)
        if course:
            self._identity_cache[norm] = keys
        return keys

    def search(self, query="", dept=None, max_workload=None, min_overall=None, min_recommend=None,
               term=None, limit=12, exclude=frozenset(), undergrad=False, filters=None):
        include_grad = not undergrad and not filters
        filters = planning_filters(filters)
        if term:
            filters["term"] = term
        if include_grad:
            filters["include_grad"] = True
        q_tokens = tokenize(query)
        results = []
        for code, c in self.courses.items():
            if exclude and self.is_taken(code, exclude):
                continue
            if undergrad and not self.is_undergrad_class(c):
                continue
            if not self.matches(c, filters):
                continue
            o = self._q(c, (filters or {}).get("q_term"))
            if dept and dept.lower() not in (c["dept"] + " " + code + " " + " ".join(c["also_listed_as"])).lower():
                continue
            if max_workload is not None and (o["workload_hrs_mean"] is None or o["workload_hrs_mean"] > max_workload):
                continue
            if min_overall is not None and (o["course_overall"] is None or o["course_overall"] < min_overall):
                continue
            if min_recommend is not None and (o["recommend_mean"] is None or o["recommend_mean"] < min_recommend):
                continue
            relevance = self._bm25(code, q_tokens) if q_tokens else 0.0
            if q_tokens and relevance == 0:
                continue
            # Small quality prior, weighted by how many students responded.
            n = o["responded"] or 0
            quality = ((o["recommend_mean"] or 3) - 3) * min(n, 30) / 30
            results.append((relevance + quality, code))
        results.sort(key=lambda row: (-row[0], row[1]))
        return [self.brief(code, filters=filters) for _, code in results[:limit]]

    def brief(self, code, filters=None):
        c = self.courses[code]
        o = self._q(c, (filters or {}).get("q_term"))
        cat = c.get("catalog") or {}
        term, sec = self._next(c, filters)
        q_term = (filters or {}).get("q_term")
        comments = [x for x in c["comments"] if not q_term or x.get("term") == q_term]
        return {
            "code": code,
            "title": c["title"],
            "dept": c["dept"],
            "latest_term": c["latest_term"],
            "instructors": c["instructors"],
            "overall": o["course_overall"],
            "instructor_score": o.get("instructor_score"),
            "effective_lectures": o.get("effective_lectures"),
            **{field: o.get(field) for field, *_ in EXTRA_Q_SCORES},
            "q_term": o.get("term"),
            "q_report_count": o["report_count"],
            "q_aggregation": "Equal-weight mean of available report scores; missing values omitted per metric.",
            "q_terms": o["terms"],
            "workload_hrs": o["workload_hrs_mean"],
            "recommend": o["recommend_mean"],
            "average_responses_per_report": o["responded"],
            "average_students_invited_per_report": o.get("invited"),
            "tags": [t["label"] for t in c["tags"]] if not q_term else [],
            "student_consensus": c["summary"] if not q_term else None,
            "sample_comment": None if c["summary"] and not q_term else (comments[0]["text"][:160] if comments else None),
            "offered": [x["term"] for x in cat.get("offered", []) if x["term"] in UPCOMING] or "not in Fall 2026 / Spring 2027 catalog",
            "next_meets": f"{term}: {self._meets(sec) or 'time TBA'}" if term else None,
            "gen_ed": cat.get("gen_ed"),
            "qrd": cat.get("qrd"),
            "component": cat.get("component"),
            "enrollment_cap": cat.get("enrollment_cap"),
            "divisional": cat.get("divisional") if cat.get("divisional") != "None" else None,
            "level": cat.get("level"),
            "description": (cat.get("description") or "")[:200] or None,
            "no_q_data": not c["offerings"],
        }

    def detail(self, code, max_comments=15, compact=False, filters=None):
        c = self.lookup(code)
        if not c:
            return None
        if not compact:
            return {**c, "comments": c["comments"][:max_comments], "total_comments": len(c["comments"])}
        q_term = (filters or {}).get("q_term")
        offerings = [o for o in c["offerings"] if not q_term or o.get("term") == q_term]
        comments = [x for x in c["comments"] if not q_term or x.get("term") == q_term]
        # Trimmed view for the model: fewer, shorter comments and only the headline numbers (saves tokens).
        keep = ("term", "section", "instructors", "course_overall", "course_overall_fas_avg", "workload_hrs_mean",
                "recommend_mean", "responded", "enroll_reasons", "instructor_score", "effective_lectures", *(field for field, *_ in EXTRA_Q_SCORES))
        cat = dict(c.get("catalog") or {})
        if filters:
            cat["offered"] = [{**o, "sections": [sec for sec in o["sections"] if self.section_fits(sec, filters)]}
                              for o in cat.get("offered", [])
                              if not filters.get("term") or filters["term"] == "any" or o["term"] == filters["term"]]
        if cat.get("description"):
            cat["description"] = cat["description"][:700]
        if cat.get("class_notes"):
            cat["class_notes"] = cat["class_notes"][:300]
        cat["offered"] = [{"term": o["term"], "n_sections": len(o["sections"]),
                           "sections": self._group_sections(o["sections"])} for o in cat.get("offered", [])]
        return {
            "code": c["code"], "title": c["title"], "dept": c["dept"], "also_listed_as": c["also_listed_as"],
            "catalog": cat or None,
            "tags": c["tags"] if not q_term else [], "student_consensus": c["summary"] if not q_term else None,
            "offerings": [{k: o.get(k) for k in keep} for o in offerings],
            # Prefer recent reviews, then substantive comments; instructors may change between terms.
            "comments": [x["text"][:350] for x in sorted(comments, key=lambda x: (-term_rank(x["term"]), -len(x["text"])))[:max_comments]],
            "total_comments": len(comments),
        }

    def lookup(self, code):
        if code in self.courses:
            return self.courses[code]
        norm = normalized_code(code)
        direct = [c for k, c in self.courses.items() if normalized_code(k) == norm]
        if len(direct) == 1:
            return direct[0]
        equivalents = next((group for group in CODE_EQUIVALENTS if norm in group), {norm})
        matches = [c for k, c in self.courses.items()
                   if {normalized_code(k), *(normalized_code(x) for x in c['also_listed_as'])} & equivalents]
        return matches[0] if len(matches) == 1 else None

    def filtered_lookup(self, code, filters=None, exclude=frozenset()):
        """Prefer the named record; allow one known equivalent if only it satisfies the filters."""
        direct = self.lookup(code)
        if not direct:
            return None
        if self.matches(direct, filters) and not self.is_taken(direct["code"], exclude):
            return direct
        keys = self.identity_keys(direct["code"])
        alternatives = [c for c in self.courses.values()
                        if keys & self.identity_keys(c["code"]) and self.matches(c, filters)
                        and not self.is_taken(c["code"], exclude)]
        return alternatives[0] if len(alternatives) == 1 else None

    def code_suggestions(self, code):
        """Suggest nearby codes within the same subject, without resolving a typo."""
        norm = normalized_code(code)
        subject = re.match(r"[^0-9]+", norm)
        if not subject:
            return []
        options = {normalized_code(k): k for k in self.courses
                   if re.match(r"[^0-9]+", normalized_code(k)).group() == subject.group()}
        return [options[k] for k in get_close_matches(norm, options, n=3, cutoff=0.8)]

    def card(self, code, filters=None):
        """Data the UI needs to render a course panel."""
        c = self.lookup(code)
        if not c:
            return None
        o = self._q(c, (filters or {}).get("q_term"))
        cat = c.get("catalog") or {}
        term, sec = self._next(c, filters)
        return {
            "code": c["code"],
            "title": c["title"],
            "dept": c["dept"],
            "term": term,
            "next_offered": f"{term} · {self._meets(sec) or 'time TBA'}" if term else None,
            "availability": (f"Offered in {term}" if term else
                             "Not in the loaded catalog" if planning_filters(filters)["term"] == "any" else
                             f"Not offered in {planning_filters(filters)['term']} in the loaded catalog"),
            "instructors": (sec or {}).get("instructors") or c["instructors"],
            "gen_ed": cat.get("gen_ed"),
            "syllabus_url": cat.get("syllabus_url"),
            "has_q": bool(c["offerings"]),
            "grad_level": self.is_grad_level(c),
            "instructor_score": o.get("instructor_score"),
            "effective_lectures": o.get("effective_lectures"),
            **{field: o.get(field) for field, *_ in EXTRA_Q_SCORES},
            "q_term": o.get("term"),
            "q_report_count": o["report_count"],
            "q_terms": o["terms"],
            "overall": o["course_overall"],
            "overall_fas_avg": o["course_overall_fas_avg"],
            "workload_hrs": o["workload_hrs_mean"],
            "recommend": o["recommend_mean"],
            "responded": o["responded"],
            "invited": o.get("invited"),
            "n_terms": len({x["term"] for x in c["offerings"]}),
            "tags": c["tags"] if not (filters or {}).get("q_term") else [],
            "summary": c["summary"] if not (filters or {}).get("q_term") else None,
        }

    def suggest(self, q, limit=8):
        """Autocomplete for the 'courses I've taken' picker: code prefix first, then title words."""
        q = " ".join(q.upper().split())
        if len(q) < 2:
            return []
        qn = normalized_code(q)
        if qn == "CS":
            qn = "COMPSCI"
        exact = self.lookup(q)
        words = tokenize(q)
        hits = []
        for code, c in self.courses.items():
            if exact is c:
                rank = -1
            elif any(normalized_code(alias).startswith(qn) for alias in [code, *c['also_listed_as']]):
                rank = 0
            elif words and all(w in tokenize(c["title"]) for w in words):
                rank = 1
            else:
                continue
            hits.append((rank, len(code), code))
        hits.sort()
        return [{"code": code, "title": self.courses[code]["title"]} for _, _, code in hits[:limit]]

    def departments(self):
        return sorted({c["dept"] for c in self.courses.values() if c["dept"]})
