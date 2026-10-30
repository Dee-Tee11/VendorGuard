import json, re, sys
exec(open(sys.argv[1]).read().split("NOTES = ")[0])
from app import chat
NOTES = [(k, s, words(s)) for k, s in chat._listed(list(chat.VERIFIED))]
META = re.compile(r"Vendor Guard (may|must|never|can't|explains|doesn't)|Vendor Guard can check|say check")
def cov(ws, o): return len(ws & o) / len(ws) if ws else 1.0
for path in sys.argv[2:]:
    for r in map(json.loads, open(path)):
        for s in re.split(r"(?<=[.!?])\s+", r["reference"]):
            if not s.strip() or META.search(s): continue
            ws = words(s); best = max(NOTES, key=lambda n: cov(ws, n[2]))
            if cov(ws, best[2]) < .5:
                print(f"{path.split('/')[-1][:4]} #{r['id']:<2} {r['kind'][:30]:<30} | {s}")
