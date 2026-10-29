"""Output guardrails for LLM-written sentences: the LLM chooses the words, the code supplies the facts."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

PLACEHOLDER = re.compile(r"\{\{\s*(E\d+)\.([a-z_]+)\s*\}\}")
# buyer text never goes into a sentence; amount_check is a bare "matches"/"differs" that read as "differs the";
# type and confirmed are codes ("PAYMENT_LINK", "yes") the model wrote into sentences (E07, 2026-10-25)
NOT_FILLABLE = {"quote", "amount_check", "type", "confirmed"}
# a label is a verb phrase for "the buyer …": anywhere else it read "which is a wants another address" (RECHECK #25).
# After ": " and "; " for the template's list, after "and " for a second one
MISPLACED_LABEL = re.compile(r"(?<!the buyer )(?<!and )(?<!: )(?<!; )\{\{\s*E\d+\.label\s*\}\}", re.I)
CODE_NAME = re.compile(r"\b[A-Z]+_[A-Z_]+\b")  # a signal's type, as "The buyer claims OVERPAYMENT_REFUND"
SOURCE_ID = re.compile(r"\bE\d+\b")
# anything outside plain ASCII and Latin letters: superscripts, homoglyphs, soft hyphens, full-width forms
BAD_CHAR = re.compile(r"[^\x20-\x7eÀ-ſ«»‘’“”–—…]|[<>\[\]@]")
CURRENCY = re.compile(r"[€$£]|\b(eur|usd|gbp|euros?|dollars?|pounds?|cents?)\b", re.I)
LINK = re.compile(r"://|\bwww\.|\b[\w-]+\.[a-z]{2,}\b", re.I)
# promises and advice that no sentence may give, in any wording or source
PROMISE = re.compile(r"\b(guarant|garant|lawyer|advogad|tax)\w*|\blegal advice\b", re.I)
# the instruction to the seller is policy text, so the LLM may not write one in any wording.
# Instructions address the seller, so sentences stay in the third person: no "you", no "podes".
BANNED = re.compile(
    r"\b(ship|send|post|mail|dispatch|deliver|release|proceed|fulfil|parcel|package"
    r"|envi|mand|expedi|despach|entreg|avanç|avanc|prossegu|encomend|pacote)\w*"
    r"|\b(you|your|yours|você|voce|tu|teu|tua|podes?|deves?)\b"
    r"|\b(go ahead|go on|hand (it )?over|(safe|ok|okay|fine|free|good|clear|ready) to)\b|" + PROMISE.pattern,
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
        if MISPLACED_LABEL.search(s.text):
            errors.append(f"{i}: a label must follow 'the buyer'")
        # only real evidence ids are skipped: "E500" is a number, not a source
        rest = SOURCE_ID.sub(lambda m: "" if m[0] in evidence else m[0], PLACEHOLDER.sub("", s.text))
        for rule, pattern in (("brace", r"[{}]"), ("character", BAD_CHAR), ("digit", r"\d"),
                              ("currency", CURRENCY), ("link", LINK), ("banned phrase", BANNED), ("code name", CODE_NAME)):
            if re.search(pattern, rest):
                errors.append(f"{i}: {rule} outside placeholders")
    return errors


def render(sentences: list[Sentence], evidence: dict[str, dict]) -> list[dict]:
    """Fill placeholders in one pass, so a filled value is never scanned again."""
    return [
        {"text": PLACEHOLDER.sub(lambda m: str(evidence[m[1]][m[2]]), s.text), "sources": s.sources}
        for s in sentences
    ]


# A chat follow-up's answer (app/chat.py): the seller's own question in the LLM's words, each sentence citing numbered
# sentences of verified notes. Here the facts are the notes, so the checks compare each sentence with what it cites.
class Cited(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    sources: list[int]  # numbers of the note sentences it relies on


class Answer(BaseModel):  # no maxItems (Groq's strict mode doesn't document it): 1 to 3 is checked in code
    model_config = ConfigDict(extra="forbid")
    sentences: list[Cited]


URL = re.compile(r"https?://\S*[\w/]")
NUMBER_WORD = r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|hundred|thousand|million"
# compared as whole tokens: "3" is not in "3-5 days", and "$25" is "$" and "25" on both sides
FACT = re.compile(rf"{URL.pattern}|{LINK.pattern}|\d+(?:[.,:-]\d+)*|{CURRENCY.pattern}|\b(?:{NUMBER_WORD})\b"
                  rf"|{CODE_NAME.pattern}", re.I)
VERDICT = re.compile(r"\b(?:VERIFIED(?:_WITH_WARNINGS)?|WAIT|SCAM_LIKELY)\b")  # case-sensitive: "wait" is a word
# the verb only: "proof of shipment" and "shipping company" are PayPal's own requirements (E07 2026-10-28, case 5)
SHIP = re.compile(r"\b(?:ship(?:s|ped)?|shipping (?:it|them|now|today|the)|dispatch\w*|hand (?:it )?over|go ahead"
                  r"|proceed\w*|send (?:it|the (?:item|order|parcel|package)))\b", re.I)
NEGATION = re.compile(r"\b(?:not|never|cannot|no|none|neither|nor|without)\b|n['’]t\b", re.I)
# a negation to the end of its clause: "not eligible for PayPal's Seller Protection program" negates protection too
NEGATED = re.compile(f"(?:{NEGATION.pattern})" + r"([^,.;:()]*)", re.I)
# a ship word is negated only by a negation just before it, with no comma between: "don't wait, ship" is not
NEGATED_SHIP = re.compile(f"(?:{NEGATION.pattern})" + r"\s+(?:\w+\s+){0,2}$", re.I)
# ponytail: a short list of words that are never English; the notes are English, so rules 3, 6 and 8 need English
# U+2010/2011 and non-breaking spaces: gpt-oss writes "4‑7" (E07 2026-10-28, case 4)
NOT_ENGLISH = re.compile(r"[^\x20-\x7e‘’“”–—…\u2010\u2011\u00a0\u202f]|[<>\[\]{}@]"
                         r"|\b(?:que|para|com|nao|voce|pode|podes|seu|sua|pagamento|comprador|vendedor|envio|enviar"
                         r"|el|los|las|usted|pago)\b", re.I)
# "you can …", not "it can take longer" (E07 2026-10-28, case 4)
PERMISSION = re.compile(r"\b(?:you (?:can|may|could)|(?:allowed|free|safe|fine|ok|okay) to)\s+\w+", re.I)
WORD = re.compile(r"[a-z]{4,}")
STOPWORDS = set("that this these those with from they them their there then than what when where which while because "
                "about your yours have been does doesn into only also even just will would should could must here more "
                "some such very matter matters means mean dont isnt arent yourself after before until other same each "
                "over like make sure really".split())
GIVEN = {"paypal", "payment", "buyer", "seller"}
COVERAGE = 2 / 3  # ponytail: a lexical stand-in for "the cited sentences say this"; tune it on E07's fallback rate


def _stem(word: str) -> str:
    return re.sub(r"(?:ing|ed|es|e|s)$", "", word)


def _words(text: str) -> set[str]:
    return {_stem(w) for w in WORD.findall(text.lower().replace("’", "").replace("'", "")) if w not in STOPWORDS}


def _negated(text: str) -> set[str]:
    """A sentence's words only in its negated clauses: "4-7 business days (… aren't business days)" negates neither."""
    return _words(" ".join(m[1] for m in NEGATED.finditer(text))) - _words(NEGATED.sub(" ", text)) - GIVEN


def _facts(text: str) -> set[str]:
    return {m[0].lower() for m in FACT.finditer(re.sub("[\u2010-\u2013]", "-", text))}  # gpt-oss writes "4‑7"


def _ships(text: str) -> bool:
    return any(not NEGATED_SHIP.search(text[:m.start()]) for m in SHIP.finditer(text))


def check_answer(sentences: list[Cited], listed: list[tuple[str, str]], titles: str, question: str,
                 case_note: str | None) -> list[str]:
    """Every reason the answer can't be shown. Empty list = grounded. listed is [(note key, sentence)], numbered from
    1; titles (the notes') and question (the seller's message) are words a sentence may reuse; case_note is the last
    check's own note, the only one that may name a verdict or say ship. The errors name rules, never the text."""
    if not 1 <= len(sentences) <= 3:
        return [f"{len(sentences)} sentences, need 1 to 3"]
    errors = []
    for i, s in enumerate(sentences):
        if len(s.text) > 240:
            errors.append(f"{i}: longer than 240 chars")
        if not s.sources or not all(1 <= n <= len(listed) for n in s.sources):
            errors.append(f"{i}: sources must be listed sentence numbers")
            continue
        cited = [listed[n - 1] for n in s.sources]
        source = " ".join(text for _, text in cited)
        if _facts(s.text) - _facts(source):
            errors.append(f"{i}: a number, link, currency or code not in its sources")
        # ponytail: a double negative ("no reason not to ship") passes; the judge's safe criterion watches it
        if case_note not in {key for key, _ in cited} and (
                VERDICT.search(s.text) or _ships(s.text)):
            errors.append(f"{i}: a verdict or shipping without the last check's note")
        if NOT_ENGLISH.search(URL.sub("", s.text)):
            errors.append(f"{i}: not plain English")
        if any(" ".join(m[0].lower().split()) not in source.lower() for m in PERMISSION.finditer(s.text)):
            errors.append(f"{i}: a permission not in its sources")
        if PROMISE.search(s.text):
            errors.append(f"{i}: banned phrase")
        # most words from the notes, and at most a third more from the seller's message: a sentence the seller wrote
        # ("F&F is covered") isn't grounded because the seller wrote it
        if (words := _words(URL.sub("", s.text)) - GIVEN) and (
                len(words & _words(f"{source} {titles}")) < len(words) / 2
                or len(words & _words(f"{source} {titles} {question}")) < COVERAGE * len(words)):
            errors.append(f"{i}: too few words from its sources")
        # "aren't covered" cited, "are covered" written: a word only in a cited sentence's negated clause may be written
        # only in a negated clause. ponytail: lexical; a synonym ("eligible" → "qualifies") passes
        if set().union(*(_negated(text) for _, text in cited)) & (_words(s.text) - _negated(s.text)):
            errors.append(f"{i}: drops its sources' negation")
    return errors
