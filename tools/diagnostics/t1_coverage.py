"""T1: for every reference sentence, is it in some note (coverage), was that note shown (retrieval), was it said?"""
import json, re, sys, collections
from app import chat
STOP = set("""a an the and or but if then so to of in on at by for with from as is are was were be been it its this that
these those you your yours they their them he she we our i my me do does did not no can could may might will would should
must have has had there here what which who when where why how than also only just any all each every one before after
about into out up down over vendor guard paypal s t don doesn isn aren wasn weren won""".split())
def words(t):
    out = set()
    for w in re.findall(r"[a-z0-9]+", t.lower()):
        if w in STOP or len(w) < 3: continue
        for suf in ("ing", "ed", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 4: w = w[: -len(suf)]; break
        out.add(w)
    return out
NOTES = [(k, s, words(s)) for k, s in chat._listed(list(chat.VERIFIED))]
TITLES = {v["title"]: k for k, v in chat.VERIFIED.items()}
TH = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
def cov(ws, other): return len(ws & other) / len(ws) if ws else 1.0
rows = list(map(json.loads, open(sys.argv[1])))
tot = {True: collections.Counter(), False: collections.Counter()}
detail = []
for r in rows:
    passed = all(v is not False for v in r["checks"].values())
    replies = [t["reply"] for t in r["turns"] if t["route"] not in ("start_check", "check")]
    checks = [t["reply"] for t in r["turns"] if t["route"] == "check"]
    said_words = words(" ".join(replies + checks))
    shown = {k for t in replies for ti, k in TITLES.items() if ti in t}
    lines = []
    for s in [x for x in re.split(r"(?<=[.!?])\s+", r["reference"]) if x.strip()]:
        ws = words(s)
        k, ns, nw = max(NOTES, key=lambda n: cov(ws, n[2]))
        c = cov(ws, nw)
        said = cov(ws, said_words) >= TH
        if said: cat = "said"
        elif c < TH: cat = "COVERAGE"
        elif k in shown: cat = "PRESENTATION"
        else: cat = "RETRIEVAL"
        tot[passed][cat] += 1
        lines.append(f"    [{cat:<12}] note {c:.2f} said {cov(ws, said_words):.2f} | {s[:95]}\n{'':20}best: {k}: {ns[:90]}")
    detail.append((r["id"], passed, r["kind"], r["judge"] and r["judge"]["score"], sorted(shown), lines))
print(f"threshold {TH}")
for p in (False, True):
    n = sum(tot[p].values())
    print(("FAILING" if not p else "PASSING") + f" cases, {n} reference sentences:", {k: f"{v} ({v/n:.0%})" for k, v in tot[p].most_common()})
with open(sys.argv[3], "w") as f:
    for i, p, kind, score, shown, lines in detail:
        f.write(f"#{i} {'PASS' if p else 'FAIL'} judge={score} {kind} | shown: {shown}\n" + "\n".join(lines) + "\n\n")
