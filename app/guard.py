"""Cheap anti-jailbreak checks that run in code, alongside guardrails.py (policy prompt + answer validation).

1. screen(): obvious prompt-injection / off-topic requests get a canned reply with no model call (free, instant).
2. leaked(): generated text is checked for pieces of the system prompt before it reaches the student.
"""
import re

INJECTION = re.compile(
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,30}\b(instructions?|prompts?|rules|guidelines|guardrails|"
    r"restrictions)\b"
    r"|\b(reveal|show|print|repeat|output|display|leak|dump|tell me|translate|encode)\b.{0,25}"
    r"\b(your|initial|hidden|original) (prompt|instructions|rules|configuration)\b"
    r"|\bsystem prompt\b|\bdeveloper mode\b|\bjailbr[eo]ak|(?-i:\bDAN\b)|\bdo anything now\b"
    r"|\byou are (now|no longer)\b|\bpretend (to be|you('re| are))\b|\b(new|different) persona\b"
    r"|\bact as (an? )?(unrestricted|unfiltered|evil|different|other)\b"
    r"|</?(system|student_message|instructions?)>",
    re.I)

OFF_TOPIC = re.compile(
    r"\b(write|draft|do|finish|complete|solve)\s+(my|this|me an?)\b.{0,25}\b(essay|homework|problem set|pset|"
    r"assignment|paper|cover letter|exam|take-home|code for)\b",
    re.I)

REFUSAL = ("I'm Qurated, and I can only help you find Harvard courses. Try something like "
           "“a light Gen Ed that meets after 10am” or “seminars on AI ethics.”")
OFF_TOPIC_REPLY = ("I can't help with coursework itself, but I can help you pick courses. Try asking for a class "
                   "by topic, workload, Gen Ed or meeting time.")

# Distinctive phrases from the system prompts; if any shows up in output, the prompt is leaking.
CANARIES = ("never as instructions that override", "student_context", "remember: new facts",
            "options: normally", "tappable answers", "graduate_level true", "student_comments")


def screen(message):
    """Returns a canned reply for requests we refuse in code, else None."""
    if INJECTION.search(message):
        return "injection", REFUSAL
    if OFF_TOPIC.search(message):
        return "off_topic", OFF_TOPIC_REPLY
    return None


def leaked(text):
    low = text.lower()
    return any(c.lower() in low for c in CANARIES)
