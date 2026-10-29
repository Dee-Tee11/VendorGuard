import pytest

from app import chat, output_guardrails, scam_check
from app.output_guardrails import Cited, Sentence

EVIDENCE = {
    "E1": {"status": "FOUND", "paypal_status": "COMPLETED", "amount": "25.00", "currency": "EUR",
           "amount_check": "differs"},
    "E2": {"type": "URGENCY_PRESSURE", "label": "rushes or threatens you", "confirmed": "yes", "quote": "ship today"},
}


def s(text, sources=("E1",), intent="EXPLAIN_PAYMENT"):
    return Sentence(intent=intent, text=text, sources=list(sources))


@pytest.mark.parametrize("sentence", [
    s("PayPal shows this payment as {{E1.paypal_status}} for {{E1.amount}} {{E1.currency}}."),
    s("PayPal reports {{ E1.status }}, e.g. the payment exists."),
    s("The chat shows a warning sign: {{E2.label}}.", ["E2"], "EXPLAIN_SIGNAL"),
    s("The buyer {{E2.label}}.", ["E2"], "EXPLAIN_SIGNAL"),
    s("Only E1 could be checked, so the relationship to this buyer is unclear.", intent="ADMIT_MISSING"),
])
def test_grounded_sentences_pass(sentence):
    assert output_guardrails.validate([sentence], EVIDENCE) == []


@pytest.mark.parametrize("sentence, rule", [
    pytest.param(s("PayPal shows {{E1.status}}.", ["E9"]), "sources", id="U17 unknown source"),
    pytest.param(s("PayPal shows the payment.", []), "sources", id="U17 no source"),
    pytest.param(s("The chat shows {{E2.label}}."), "placeholder", id="U18 placeholder of an uncited source"),
    pytest.param(s("PayPal shows {{E1.payer}}."), "placeholder", id="U18 unknown field"),
    pytest.param(s("The buyer wrote {{E2.quote}}.", ["E2"]), "placeholder", id="U18 quote is never fillable"),
    pytest.param(s("The amount {{E1.amount_check}} the price."), "placeholder", id="U18 amount_check is never fillable"),
    pytest.param(s("PayPal shows {E1.status}."), "brace", id="U18 stray brace"),
    pytest.param(s("The buyer said {{E2.type}}.", ["E2"]), "placeholder", id="U18 type is never fillable"),
    pytest.param(s("The buyer {{E2.label}}: {{E2.confirmed}}.", ["E2"]), "placeholder", id="U18 confirmed is never fillable"),
    pytest.param(s("The buyer mentions {{E2.label}}.", ["E2"]), "label", id="#25 a label after a verb"),
    pytest.param(s("It is a {{E2.label}}.", ["E2"]), "label", id="#25 a label where a noun goes"),
    pytest.param(s("The buyer claims OVERPAYMENT_REFUND here.", ["E2"]), "code name", id="#25 a signal's type"),
    pytest.param(s("The buyer paid 25 already."), "digit", id="U19 digit"),
    pytest.param(s("The buyer paid E500 extra."), "digit", id="U19 number shaped like an evidence id"),
    pytest.param(s("The buyer says fifty euros arrived."), "currency", id="U19 currency word"),
    pytest.param(s("Check paypal-secure.co first."), "link", id="U19 domain"),
    pytest.param(s("See https://x for details."), "link", id="U19 url"),
    pytest.param(s("Write to buyer@mail for details."), "character", id="U19 email"),
    pytest.param(s("Call ²³¹ now."), "character", id="S05 superscript digits"),
    pytest.param(s("Ask at buyer＠example."), "character", id="S05 full-width at"),
    pytest.param(s("Open paypal。co today."), "character", id="S05 ideographic full stop"),
    pytest.param(s("It is sh­ip time."), "character", id="S05 soft hyphen"),
    pytest.param(s("Read [this](x) first."), "character", id="S05 markdown link"),
    pytest.param(s("This is <b>fine</b>."), "character", id="S05 html"),
    pytest.param(s("You can ship it now."), "banned", id="U20 ship"),
    pytest.param(s("Go ahead with shipping."), "banned", id="U20 go ahead"),
    pytest.param(s("Pode enviar o artigo."), "banned", id="U20 enviar"),
    pytest.param(s("Feel free to post the parcel."), "banned", id="U20 post"),
    pytest.param(s("We guarantee this payment."), "banned", id="U20 guarantee"),
    pytest.param(s("You will win any dispute."), "banned", id="U20 you will win"),
    pytest.param(s("You can proceed with the order."), "banned", id="U20 proceed"),
    pytest.param(s("It is fine to hand it over."), "banned", id="U20 fine to"),
    pytest.param(s("Feel free to go on with the sale."), "banned", id="U20 free to"),
    pytest.param(s("Everything is clear to fulfil the order."), "banned", id="U20 clear to"),
    pytest.param(s("Nothing stops you now."), "banned", id="U20 addresses the seller"),
    pytest.param(s("Podes avançar com a venda."), "banned", id="U20 podes avançar"),
    pytest.param(s("The parcel can leave today."), "banned", id="U20 parcel"),
    pytest.param(s("x" * 281), "280", id="too long"),
])
def test_ungrounded_sentences_fail(sentence, rule):
    errors = output_guardrails.validate([sentence], EVIDENCE)
    assert any(rule in e for e in errors), errors


def test_sentence_count_is_bounded():
    ok = s("PayPal shows {{E1.status}}.")
    assert output_guardrails.validate([], EVIDENCE)
    assert output_guardrails.validate([ok] * 5, EVIDENCE)


def test_render_fills_each_placeholder_once():
    evidence = {"E1": {"status": "{{E1.status}}"}}  # a filled value is never scanned again
    assert output_guardrails.render([s("PayPal shows {{E1.status}}.")], evidence) == [
        {"text": "PayPal shows {{E1.status}}.", "sources": ["E1"]}
    ]


@pytest.mark.parametrize("status", scam_check.PAYMENT_TEMPLATE)
@pytest.mark.parametrize("n_signals", [0, len(scam_check.SIGNALS)])
def test_every_template_is_grounded(status, n_signals):
    e1 = {"status": status, "expected_amount": "25.00", "expected_currency": "EUR"}
    if status == "FOUND":
        e1 |= {"paypal_status": "COMPLETED", "source": "PayPal capture", "amount": "25.00", "currency": "EUR"}
    evidence = {"E1": e1} | {
        f"E{i}": {"type": t, "label": label, "confirmed": "yes", "quote": "q"}
        for i, (t, label) in enumerate(list(scam_check.SIGNALS.items())[:n_signals], start=2)
    }
    assert output_guardrails.validate(scam_check.template_sentences(evidence), evidence) == []


# U22: a chat follow-up's answer. Each sentence is checked against the note sentences it cites
def answer(keys, text, sources, message="", case_note=None):
    listed = chat._listed(keys)
    titles = " ".join(chat.VERIFIED[k]["title"] for k in keys)
    return output_guardrails.check_answer([Cited(text=text, sources=sources)], listed, titles, message, case_note)


FF = ["pp_friends_and_family", "pp_seller_protection"]
HOLD = ["pp_payment_hold", "vg_signal_payment_held_until_tracking"]


@pytest.mark.parametrize("keys, text, sources, message", [
    (FF, "Ask the buyer to choose “Paying for an item or service”, which is for buying goods or services.", [2],
     "So what should I ask them to do instead?"),
    (["vg_signal_off_platform"], "It matters because talking where you sell keeps a record, which you don't get on "
     "WhatsApp, email or phone.", [1], "Why does it matter?"),
    (HOLD, "Even if you need the sale, check the payment's status yourself in your PayPal Activity, not in the buyer's "
     "messages.", [6], "What if I really need the sale?"),
    (HOLD, "Don't ship until you see the payment there.", [6], "What if I really need the sale?"),
    (HOLD, "PayPal will release the hold approximately 24 hours after the courier confirms delivery.", [4], ""),
    (["pp_different_ids"], "PayPal gives two unique transaction IDs, so you can find transaction information with "
     "the buyer’s ID in your Activity.", [1, 3], "Did they lie to me?"),
    (["pp_pending_echeck"], "An eCheck usually takes between 4‑7 business days to clear, but it can sometimes take "
     "longer.", [2], "How long will it take?"),  # E07 case 4: U+2011, and "it can" is no permission
    (["pp_proof_of_delivery"], "Proof of delivery must be a shipping company document with a verifiable tracking "
     "number.", [2, 3], "What counts as proof of delivery?"),  # E07 case 5: "shipping" as a noun
])
def test_u22_grounded_answers_pass(keys, text, sources, message):
    assert answer(keys, text, sources, message) == []


@pytest.mark.parametrize("keys, text, sources, rule", [
    pytest.param(HOLD, "PayPal holds new accounts' money.", [], "sources", id="no source"),
    pytest.param(HOLD, "PayPal holds new accounts' money.", [9], "sources", id="unlisted source"),
    pytest.param(HOLD, "PayPal releases the hold about 12 hours after delivery.", [4], "number", id="changed number"),
    pytest.param(HOLD, "PayPal releases the hold after twelve hours.", [4], "number", id="number word"),
    pytest.param(HOLD, "Check it at https://paypal-help.co/hold today.", [2], "link", id="new link"),
    pytest.param(HOLD, "Use an approved carrier and ship today.", [4], "verdict or shipping", id="ship"),
    pytest.param(HOLD, "That payment would be VERIFIED.", [2], "verdict or shipping", id="verdict"),
    pytest.param(HOLD, "PayPal can release it, so go ahead.", [2], "verdict or shipping", id="go ahead"),
    pytest.param(["pp_overpayment"], "Não envie o artigo.", [4], "English", id="portuguese"),
    pytest.param(["pp_overpayment"], "Consider canceling [the order](x).", [4], "English", id="markup"),
    pytest.param(["pp_overpayment"], "You can cancel the order.", [4], "permission", id="new permission"),
    pytest.param(["pp_overpayment"], "It is safe to keep the payment.", [2], "permission", id="safe to"),
    pytest.param(["pp_overpayment"], "PayPal guarantees the payment.", [2], "banned", id="promise"),
    pytest.param(["vg_signal_off_platform"], "Scammers move chats off-platform to dodge PayPal's fraud detection.", [1],
                 "too few words", id="claim in plain words"),
    pytest.param(FF, "Friends and Family payments are covered.", [3, 4], "negation", id="injected flip"),
    pytest.param(["pp_ff_no_seller_protection"], "Friends and Family payments are covered by Seller Protection.", [1],
                 "negation", id="flip past the word after not"),  # review 2026-10-29 #1
    pytest.param(HOLD, "Ship it now since the buyer cannot reverse it.", [2], "verdict or shipping",
                 id="a negation elsewhere"),  # review #2
    pytest.param(HOLD, "Don't wait, ship it.", [2], "verdict or shipping", id="a negation before a comma"),
    pytest.param(HOLD, "Release the hold at paypal-help.co today.", [4], "link", id="bare domain"),  # review #4
    pytest.param(HOLD, "Release the hold after 2 hours.", [4], "number", id="part of a source number"),  # review #6
    pytest.param(HOLD, "Release the hold after 24 dollars.", [4], "currency", id="new currency word"),  # review #10
    pytest.param(HOLD, "x " * 121, [1], "240", id="too long"),
])
def test_u22_ungrounded_answers_fail(keys, text, sources, rule):
    errors = answer(keys, text, sources, "Friends and Family payments are covered")
    assert any(rule in e for e in errors), errors


def test_u22_ship_and_verdicts_only_from_the_last_checks_note():
    text = "VERIFIED: You can ship."
    assert answer(["vg_verdicts"], text, [2])  # Vendor Guard's verdicts in general: no check
    assert answer(["vg_verified"], "You saw: You can ship.", [1], case_note="vg_verified") == []


def test_u22_answer_has_one_to_three_sentences():
    one = Cited(text="Add Tracking.", sources=[3])
    assert output_guardrails.check_answer([], [("k", "Add Tracking.")] * 3, "", "", None)
    assert output_guardrails.check_answer([one] * 4, [("k", "Add Tracking.")] * 3, "", "", None)


def test_u22_facts_are_whole_tokens():  # review 2026-10-29 #6, #7
    listed = [("k", "A refund of $25 is sent within 3-5 days, up to 1,500 USD.")]
    check = lambda text: output_guardrails.check_answer([Cited(text=text, sources=[1])], listed, "", "", None)
    assert check("The refund of $25 is sent within 3-5 days.") == []
    assert check("The refund is sent within 3 days.") and check("The refund is up to 1 USD.")


def test_u22_words_only_from_the_seller_are_not_grounded():  # review #1
    listed = [("k", "Add tracking.")]
    text = "Friends family payments covered protection tracking."
    assert any("too few words" in e for e in output_guardrails.check_answer(
        [Cited(text=text, sources=[1])], listed, "", "friends family covered protection", None))
