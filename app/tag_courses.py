"""Summarize what students say about each course into a few short tags.

    uv run tag_courses.py --sample 5     # tag 5 courses right away (fractions of a cent) and print them
    uv run tag_courses.py --all          # tag every untagged course via the Batches API (50% off)
    uv run tag_courses.py --resume ID    # reattach to a submitted batch (e.g. after a network drop)

Re-running --all only tags courses that don't have tags yet, so run it again after more scraping.
Results go to data/tags.json, which courses.py loads automatically.
"""
import argparse
import json
import time
from pathlib import Path

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from dotenv import load_dotenv

from courses import CourseDB

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
TAGS = ROOT / "data" / "tags.json"
MODEL = "claude-haiku-4-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "1-3 words, lowercase, e.g. 'engaging lectures'"},
                    "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                },
                "required": ["label", "sentiment"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string", "description": "One sentence on what students generally say"},
    },
    "required": ["tags", "summary"],
    "additionalProperties": False,
}

INSTRUCTIONS = """Below are Harvard Q report results for one course: ratings and students' advice to future students.
Write 3-5 short tags (1-3 words each, lowercase) capturing what students most consistently say about it: \
teaching quality, workload, difficulty, grading, assignments, vibe, who it suits. Prefer themes several students \
mention over one-off remarks. Mark each tag positive, negative or neutral from a prospective student's view. \
Then write a one-sentence summary. Base everything only on the data given."""


def course_prompt(c):
    o = c["offerings"][0]
    comments = sorted(c["comments"], key=lambda x: -len(x["text"]))[:15]
    lines = [
        f"{c['code']}: {c['title']} ({c['dept']})",
        f"Latest term {o['term']}; overall {o['course_overall']} (FAS avg {o['course_overall_fas_avg']}); "
        f"workload {o['workload_hrs_mean']} hrs/wk; recommend {o['recommend_mean']}; {o['responded']} responses",
        "Student comments:",
    ] + [f"- {x['text'][:300]}" for x in comments]
    return INSTRUCTIONS + "\n\n" + "\n".join(lines)


def params(c):
    return MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": course_prompt(c)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )


def load():
    return json.loads(TAGS.read_text()) if TAGS.exists() else {}


def save(tags):
    TAGS.parent.mkdir(exist_ok=True)
    TAGS.write_text(json.dumps(tags, indent=1))


def parse(msg):
    if msg.stop_reason != "end_turn":
        return None
    text = next((b.text for b in msg.content if b.type == "text"), "")
    data = json.loads(text)
    data["tags"] = data["tags"][:5]
    return data


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample", type=int)
    g.add_argument("--all", action="store_true")
    g.add_argument("--resume", metavar="BATCH_ID")
    args = ap.parse_args()

    client = anthropic.Anthropic()
    tags = load()

    if args.resume:
        batch = client.messages.batches.retrieve(args.resume)
        mapping_file = TAGS.parent / f"{batch.id}.json"
        if mapping_file.exists():
            by_id = json.loads(mapping_file.read_text())
        else:
            # Older batch without a saved mapping: rebuild the course list as it was at submission time.
            db = CourseDB(load_tags=False, before=batch.created_at.timestamp())
            prior = {k: v for k, v in tags.items() if v.get("tagged_at", 0) < batch.created_at.timestamp()}
            todo = [c for code, c in sorted(db.courses.items()) if code not in prior and c["comments"]]
            total = sum(vars(batch.request_counts).values())
            if len(todo) != total:
                raise SystemExit(f"Can't rebuild mapping: {len(todo)} courses vs {total} requests")
            by_id = {f"c{i}": c["code"] for i, c in enumerate(todo)}
        collect(client, batch.id, by_id, tags)
        return

    db = CourseDB(load_tags=False)
    todo = [c for code, c in sorted(db.courses.items()) if code not in tags and c["comments"]]
    print(f"{len(db.courses)} courses, {len(tags)} already tagged, {len(todo)} to tag")

    if args.sample:
        for c in todo[: args.sample]:
            msg = client.messages.create(**params(c))
            data = parse(msg)
            if data:
                tags[c["code"]] = {**data, "tagged_at": 0}
                print(f"{c['code']}: {[t['label'] for t in data['tags']]} | {data['summary']}")
        save(tags)
        return

    if not todo:
        return
    batch = client.messages.batches.create(requests=[Request(custom_id=f"c{i}", params=params(c)) for i, c in enumerate(todo)])
    by_id = {f"c{i}": c["code"] for i, c in enumerate(todo)}
    TAGS.parent.mkdir(exist_ok=True)
    (TAGS.parent / f"{batch.id}.json").write_text(json.dumps(by_id))
    print(f"Batch {batch.id} submitted; usually done within an hour.")
    collect(client, batch.id, by_id, tags)


def collect(client, batch_id, by_id, tags):
    while True:
        try:
            batch = client.messages.batches.retrieve(batch_id)
        except anthropic.APIConnectionError:
            print("  connection dropped; retrying…", flush=True)
            time.sleep(30)
            continue
        if batch.processing_status == "ended":
            break
        print(f"  {batch.request_counts.succeeded} done, {batch.request_counts.processing} processing…", flush=True)
        time.sleep(30)
    ok = 0
    now = time.time()
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            data = parse(result.result.message)
            if data:
                tags[by_id[result.custom_id]] = {**data, "tagged_at": now}
                ok += 1
    save(tags)
    print(f"Tagged {ok} courses; {len(tags)} total in {TAGS}")


if __name__ == "__main__":
    main()
