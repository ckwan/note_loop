"""Checks that run on every draft and review, whatever model wrote the text.

They are plain rules, so they are fast, free, and behave the same on every model.
They catch common failures and are not a clinical safety control. A therapist still
reads and signs every note.
"""

import re

from .schemas import SECTIONS, Finding

SOURCE = "guardrail"

# Diagnoses and drugs. If the note names one the session notes never did, flag it.
DIAGNOSIS_TERMS = (
    "disorder", "ptsd", "bipolar", "schizophreni", "psychosis", "borderline",
    "adhd", "ocd", "major depressive", "personality disorder",
)
MEDICATION_TERMS = (
    "sertraline", "zoloft", "fluoxetine", "prozac", "escitalopram", "lexapro",
    "alprazolam", "xanax", "lorazepam", "ativan", "clonazepam", "klonopin",
    "bupropion", "wellbutrin", "lithium", "quetiapine", "seroquel", "trazodone",
)
DOSE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|ml|units?)\b", re.I)
QUOTE = re.compile(r'"([^"]{4,})"|“([^”]{4,})”')

RISK_TERMS = ("suicid", "self harm", "self-harm", "hurt myself", "harm others", "kill myself")
RISK_NOTED = ("suicid", "self-harm", "self harm", "risk", "safety plan", "harm", "crisis")

INJECTION = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier|your) (?:instructions|rules|prompt)"
    r"|disregard (?:all |any |the )?(?:previous|prior|above|your) "
    r"|(?:reveal|show|print|repeat) (?:your |the )?(?:system )?prompt"
    r"|you are now\b"
    r"|new instructions\s*:"
    r"|system prompt",
    re.I,
)
# Our own prompt delimiters. Text that contains them could close a tag early.
PROMPT_TAGS = re.compile(r"</?\s*(?:session_notes|note_sections|note|findings)\s*>", re.I)

def neutralize_tags(text: str) -> str:
    """Stop notes from closing or opening the tags that mark them as data."""
    return PROMPT_TAGS.sub("[tag removed]", text)


def looks_like_injection(raw_input: str) -> bool:
    return bool(INJECTION.search(raw_input)) or bool(PROMPT_TAGS.search(raw_input))


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _finding(section: str, kind: str, message: str) -> Finding:
    return Finding(section=section, kind=kind, message=message, source=SOURCE)


def check_note(raw_input: str, note: dict[str, str]) -> list[Finding]:
    """Rule based checks of a note against the session notes it came from."""
    raw = _norm(raw_input)
    found: list[Finding] = []

    for section in SECTIONS:
        text = note.get(section, "")
        lowered = _norm(text)
        unsupported: list[str] = []
        for term in DIAGNOSIS_TERMS + MEDICATION_TERMS:
            if term in lowered and term not in raw:
                unsupported.append(term)
        for dose in DOSE.findall(text):
            if _norm(dose) not in raw and dose.lower() not in unsupported:
                unsupported.append(dose.lower())
        for match in QUOTE.finditer(text):
            quoted = _norm(match.group(1) or match.group(2))
            if quoted not in raw:
                unsupported.append(f'a quote ("{quoted[:40]}")')
        if unsupported:
            found.append(
                _finding(
                    section,
                    "ungrounded",
                    f"Mentions {', '.join(unsupported)}, which the session notes do not.",
                )
            )

    if any(t in raw for t in RISK_TERMS):
        whole = _norm(" ".join(note.get(s, "") for s in SECTIONS))
        if not any(t in whole for t in RISK_NOTED):
            found.append(
                _finding(
                    "assessment",
                    "safety_undocumented",
                    "The session notes mention risk of harm, but the note does not.",
                )
            )

    if looks_like_injection(raw_input):
        found.append(
            _finding(
                "subjective",
                "other",
                "The session notes contain text that looks like instructions to the AI. "
                "It was treated as session content. Check the note against what was said.",
            )
        )
    return found


def merge(findings: list[Finding], extra: list[Finding]) -> list[Finding]:
    """Add guardrail findings, skipping a section and kind another reviewer already raised."""
    seen = {(f.section, f.kind) for f in findings}
    return findings + [f for f in extra if (f.section, f.kind) not in seen]
