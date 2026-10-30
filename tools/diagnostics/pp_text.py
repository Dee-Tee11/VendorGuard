"""Visible text of a saved PayPal page, one block per line; optional regex filter. Usage: pp_text.py FILE [REGEX]"""
import html, re, sys
from html.parser import HTMLParser
class P(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.out = []
    def handle_starttag(self, tag, a):
        if tag in ("script", "style", "noscript", "svg"): self.skip += 1
        if tag in ("p", "li", "h1", "h2", "h3", "h4", "td", "tr"): self.out.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip: self.skip -= 1
    def handle_data(self, d):
        if not self.skip: self.out.append(d.replace("\n", " "))
p = P(); p.feed(open(sys.argv[1], encoding="utf-8", errors="replace").read())
lines = [" ".join(l.split()) for l in "".join(p.out).splitlines()]
lines = [l for l in lines if len(l) > 25]
rx = re.compile(sys.argv[2], re.I) if len(sys.argv) > 2 else None
for l in dict.fromkeys(lines):
    if not rx or rx.search(l): print("-", l)
