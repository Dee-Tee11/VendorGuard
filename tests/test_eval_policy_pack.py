"""Eval: every sentence of every PayPal note is still on its page today (E09). Opt-in, no Groq key:

    pytest -m eval -v -s tests/test_eval_policy_pack.py

Each page is fetched fresh. Pages are untrusted data: HTMLParser reads their visible text, and nothing runs them.
U25, the matcher on fixed strings, is offline and runs with the other tests.
"""
import difflib
import functools
import re
from html.parser import HTMLParser

import httpx
import pytest

from app import chat, policy_pack

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
PARTIAL = "This is not PayPal's full list"  # policy_pack._partial: Vendor Guard's words, not on the page
KEYS = [key for key in policy_pack.POLICY_PACK if key.startswith("pp_")]


class _Visible(HTMLParser):
    """A page's visible text, one block per line; no length filter, so short list items ("Click Submit.") stay."""
    SKIP, BLOCKS = ("script", "style", "noscript", "svg"), ("p", "li", "h1", "h2", "h3", "h4", "td", "tr")

    def __init__(self):
        super().__init__()
        self.skip, self.out = 0, []

    def handle_starttag(self, tag, attrs):
        self.skip += tag in self.SKIP
        if tag in self.BLOCKS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in self.BLOCKS:  # "<p>Physical Goods</p>The following…" is two lines
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data.replace("\n", " "))


def words(text: str) -> str:
    """Lowercase words only: punctuation, quotes and line breaks don't count, so joined list items still match."""
    return " ".join(re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.casefold().replace("’", "'").replace("‘", "'")))


def pattern(sentence: str) -> re.Pattern:
    """A note's sentence as a regex over a page's words. The only edit to PayPal's text is "we/our" -> "PayPal"
    (policy_pack's docstring), so undo it, with the verb that follows: "PayPal still recommends" <- "we still recommend"."""
    toks, out, after, i = words(sentence).split(), [], 9, 0
    while i < len(toks):
        t = toks[i]
        if t == "paypal's":
            out.append("(?:paypal's|our)")
        elif t == "paypal" and toks[i + 1:i + 2] == ["will"]:
            out.append("(?:paypal will|we will|we'll)")
            i, after = i + 1, -1
        elif t == "paypal":
            out.append("(?:paypal|we|us)")
            after = -1
        elif after < 2 and t == "doesn't":  # the next two words: the verb, or an adverb then the verb
            out.append("(?:doesn't|don't)")
        elif after < 2 and t.endswith("s"):
            out.append(re.escape(t[:-1]) + "s?")
        else:
            out.append(re.escape(t))
        after, i = after + 1, i + 1
    return re.compile(r"\b" + " ".join(out) + r"\b")


def found(sentence: str, text: str) -> bool:
    """Each part of the sentence, split where Vendor Guard joined a lead-in and its items (": ", "; "), on the page
    in order. Items the note skips may sit between them: whether a note says it quotes only part is policy_pack's."""
    pos = 0
    for part in re.split(r"[:;]\s", sentence):
        if not words(part):
            continue
        if not (m := pattern(part).search(text, pos)):
            return False
        pos = m.end()
    return True


# ponytail: reads the HTML the server sends, not what the page's script draws from its embedded data. On 2026-10-30
# pp_add_tracking's embedded data also held a variant ("Choose “All Transactions.”"); compare in a browser if in doubt
@functools.cache
def page(url: str) -> list[str]:
    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=30)
    r.raise_for_status()
    p = _Visible()
    p.feed(r.text)
    return [line for line in (" ".join(x.split()) for x in "".join(p.out).splitlines()) if line]


def test_u25_page_matcher_undoes_we_and_joins_but_not_a_changed_word():
    text = words("We intentionally generate two IDs.\nOur emails will always address you.\nWe'll ask you to upload "
                 "this.\nBut we don't return the fees.\nRequirements for Seller Protection:\nFor physical items:\n"
                 "Ship the item.\nGo to your Activity.\na shipping receipt\ntracking number - or a signature\n"
                 "Provide us with valid proof.\nYou must respond within the first 10 days.")
    for ok in ("PayPal intentionally generates two IDs.", "PayPal's emails will always address you.",
               "PayPal will ask you to upload this.", "But PayPal doesn't return the fees.",
               "Requirements for Seller Protection, for physical items: Ship the item.", "Go to your Activity.",
               "a shipping receipt; tracking number - or a signature.", "Provide PayPal with valid proof."):
        assert found(ok, text), ok
    for changed in ("You must respond within the first 20 days.", "PayPal never generates two IDs.",
                    "Go to your Wallet.", "Ship the item: Requirements for Seller Protection."):  # the order counts
        assert not found(changed, text), changed


@pytest.mark.eval
@pytest.mark.parametrize("key", KEYS)
def test_e09_every_paypal_sentence_is_on_its_page_today(key):
    lines = page(policy_pack.POLICY_PACK[key]["source_url"])
    text = words("\n".join(lines))
    missing = [s for _, s in chat._listed([key]) if not s.startswith(PARTIAL) and not found(s, text)]
    for s in missing:  # the closest line on the page, to tell drift from a sentence the matcher can't read
        best = max(lines, key=lambda line: difflib.SequenceMatcher(None, s.lower(), line.lower()).ratio())
        print(f"\n{key}\n  note: {s}\n  page: {best[:300]}")
    assert not missing, f"{len(missing)} sentence(s) of {key} not on its page today"
