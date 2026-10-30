"""T1b: reference said anywhere vs in the last reply; and which notes hold the missing sentences (gated or not)."""
import json, re, sys, collections
sys.argv += []
exec(open(sys.argv[2]).read().split("NOTES = ")[0])  # reuse words()/STOP from t1
from app import chat
NOTES = [(k, s, words(s)) for k, s in chat._listed(list(chat.VERIFIED))]
def cov(ws, o): return len(ws & o) / len(ws) if ws else 1.0
META = re.compile(r"Vendor Guard (may|must|never|can't|explains|doesn't)")
rows = list(map(json.loads, open(sys.argv[1])))
holders = collections.Counter()
print(f"{'id':>3} {'pass':<4} {'judge':<5} {'any':>5} {'last':>5}  failing criteria")
agg = {True: [0, 0, 0], False: [0, 0, 0]}
for r in rows:
    p = all(v is not False for v in r["checks"].values())
    replies = [t["reply"] for t in r["turns"]]
    refs = [s for s in re.split(r"(?<=[.!?])\s+", r["reference"]) if s.strip() and not META.search(s)]
    anyw, lastw = words(" ".join(replies)), words(replies[-1])
    a = sum(cov(words(s), anyw) >= .5 for s in refs); l = sum(cov(words(s), lastw) >= .5 for s in refs)
    agg[p][0] += len(refs); agg[p][1] += a; agg[p][2] += l
    for s in refs:
        if cov(words(s), anyw) < .5:
            ws = words(s); best = max(cov(ws, n[2]) for n in NOTES)
            if best >= .5:
                for k, ns, nw in NOTES:
                    if cov(ws, nw) == best: holders["GATED " + k if k in chat.VERDICT_NOTES or k == "vg_verdicts" else k] += 1
            else: holders["(no note)"] += 1
    bad = [k for k, v in r["checks"].items() if v is False]
    if not p: print(f"{r['id']:>3} {'FAIL':<4} {r['judge']['score']:<5} {a:>2}/{len(refs):<2} {l:>2}/{len(refs):<2}  {bad}")
for p in (False, True):
    n, a, l = agg[p]
    print(("FAILING" if not p else "PASSING") + f": reference said anywhere {a}/{n} ({a/n:.0%}), in the last reply {l}/{n} ({l/n:.0%})")
print("missing sentences are held by:", holders.most_common())
