"""Output guardrails for LLM-written sentences: the LLM chooses the words, the code supplies the facts."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

PLACEHOLDER = re.compile(r"\{\{\s*(E\d+)\.([a-z_]+)\s*\}\}")
# buyer text never goes into a sentence; amount_check is a bare "matches"/"differs" that read as "differs the"
NOT_FILLABLE = {"quote", "amount_check"}
SOURCE_ID = re.compile(r"\bE\d+\b")
# anything outside plain ASCII and Latin letters: superscripts, homoglyphs, soft hyphens, full-width forms
BAD_CHAR = re.compile(r"[^\x20-\x7eÀ-ſ«»‘’“”–—…]|[<>\[\]@]")
CURRENCY = re.compile(r"[€$£]|\b(eur|usd|gbp|euros?|dollars?|pounds?|cents?)\b", re.I)
LINK = re.compile(r"://|\bwww\.|\b[\w-]+\.[a-z]{2,}\b", re.I)
# the instruction to the seller is policy text, so the LLM may not write one in any wording.
# Instructions address the seller, so sentences stay in the third person: no "you", no "podes".
BANNED = re.compile(
    r"\b(ship|send|post|mail|dispatch|deliver|release|proceed|fulfil|parcel|package"
    r"|envi|mand|expedi|despach|entreg|avanç|avanc|prossegu|encomend|pacote)\w*"
    r"|\b(you|your|yours|você|voce|tu|teu|tua|podes?|deves?)\b"
    r"|\b(go ahead|go on|hand (it )?over|(safe|ok|okay|fine|free|good|clear|ready) to|legal advice)\b"
    r"|\b(guarant|garant|lawyer|advogad|tax)\w*",
    re.I,
)


class Sentence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["EXPLAIN_PAYMENT", "EXPLAIN_SIGNAL", "ADMIT_MISSING"]
    text: str
    sources: list[str]


def validate(sentences: list[Sentence], evidence: dict[str, dict], most: int = 4) -> list[str]:
    """Every reason the sentences can't be shown. Empty list = grounded."""
    if not 1 <= len(sentences) <= most:
        return [f"{len(sentences)} sentences, need 1 to {most}"]
    errors = []
    for i, s in enumerate(sentences):
        if len(s.text) > 280:
            errors.append(f"{i}: longer than 280 chars")
        if not s.sources or not set(s.sources) <= set(evidence):
            errors.append(f"{i}: sources must be existing evidence ids")
        for src, field in PLACEHOLDER.findall(s.text):
            if src not in s.sources or field in NOT_FILLABLE or field not in evidence.get(src, {}):
                errors.append(f"{i}: placeholder {src}.{field} not allowed")
        # only real evidence ids are skipped: "E500" is a number, not a source
        rest = SOURCE_ID.sub(lambda m: "" if m[0] in evidence else m[0], PLACEHOLDER.sub("", s.text))
        for rule, pattern in (("brace", r"[{}]"), ("character", BAD_CHAR), ("digit", r"\d"),
                              ("currency", CURRENCY), ("link", LINK), ("banned phrase", BANNED)):
            if re.search(pattern, rest):
                errors.append(f"{i}: {rule} outside placeholders")
    return errors


def render(sentences: list[Sentence], evidence: dict[str, dict]) -> list[dict]:
    """Fill placeholders in one pass, so a filled value is never scanned again."""
    return [
        {"text": PLACEHOLDER.sub(lambda m: str(evidence[m[1]][m[2]]), s.text), "sources": s.sources}
        for s in sentences
    ]
