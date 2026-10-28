import pytest

from app import output_guardrails, scam_check
from app.output_guardrails import Sentence

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
