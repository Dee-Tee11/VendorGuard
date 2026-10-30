"""T2: which saved conversations would the planned code triggers escalate, against the judge's pass/fail."""
import json, re, sys, collections
from app import chat
SHIP_Q = re.compile(r"(?i)\b(can|could|should|may)\b[^.?]{0,40}\b(ship|send|post|dispatch)\b[^.]*\?")
SHIPPED = re.compile(r"(?i)\b(already (shipped|sent|posted)|i (shipped|posted|sent) (it|the item)|posted the item)")
NOT_PAID = re.compile(r"(?i)\b(nothing (shows|in)|isn't in|not in my|no payment|haven't received|wasn't paid)")
def triggers(r):
    hits, last_verdict = [], None
    for t in r["turns"]:
        say, reply = t["say"], t["reply"]
        if chat.INSTRUCTION.search(say): hits.append("injection")
        if SHIPPED.search(say) and NOT_PAID.search(say): hits.append("shipped_not_paid")
        if t["route"] == "dispute": hits.append("dispute")
        if reply.startswith(("I don't have that information", "I don't have a checked note")): hits.append("no_note")
        if t["route"] == "check" and (m := re.match(r"(VERIFIED_WITH_WARNINGS|VERIFIED|WAIT|SCAM_LIKELY):", reply)):
            last_verdict = m[1]
            signs = re.search(r"Warning signs in the chat: (.*)", reply)
            if last_verdict == "VERIFIED_WITH_WARNINGS" and signs and signs[1].count(";") >= 1: hits.append("real_payment_2_signs")
        elif last_verdict in ("WAIT", "SCAM_LIKELY") and SHIP_Q.search(say): hits.append("insists_after_" + last_verdict)
    return hits
tot = collections.Counter(); why = collections.Counter()
for path in sys.argv[1:]:
    for r in map(json.loads, open(path)):
        p = all(v is not False for v in r["checks"].values())
        h = triggers(r); esc = bool(h)
        tot[(p, esc)] += 1
        for x in set(h): why[x] += 1
        if esc: print(f"  {'pass' if p else 'FAIL'} {path.split('/')[-1][:5]} #{r['id']:<2} {r['kind'][:42]:<42} {sorted(set(h))}")
n = sum(tot.values())
print(f"\n{n} conversations: escalate {tot[(True,True)]+tot[(False,True)]} ({(tot[(True,True)]+tot[(False,True)])/n:.0%})")
print(f"  failing: {tot[(False,True)]} escalate, {tot[(False,False)]} don't | passing: {tot[(True,True)]} escalate, {tot[(True,False)]} don't")
print("by trigger:", why.most_common())
