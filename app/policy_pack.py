"""Notes the chat shows word for word, looked up by key, never by similarity.

An entry is shown, or offered to the LLM, only when verified is True: a person has read its source page (test U16).
PayPal texts repeat their page; the only edit is "we/our" -> "PayPal", so they don't read as Vendor Guard speaking.
"""
from app.scam_check import MONEY_OUT_WARNING, POLICY

REGION = "us"  # the sandbox's region: Seller Protection, deadlines and help pages depend on it
PAYPAL_PAGES = f"https://www.paypal.com/{REGION}/"
HELP_URL = PAYPAL_PAGES + "cshelp/business"  # the "I don't have that information" link; loaded on 2026-10-22
SELLER_PROTECTION = PAYPAL_PAGES + "legalhub/paypal/seller-protection"  # the full terms, last updated 2026-01-26
SCAMS = PAYPAL_PAGES + "cshelp/article/what-are-common-scams-and-how-do-i-spot-them-help201"


def _vg(title: str, text: str, anchor: str) -> dict:
    """Vendor Guard's own notes, built from scam_check's texts so they can't drift."""
    return {"title": title, "text": text, "source_url": f"README.md#{anchor}", "retrieved_at": "2026-10-22", "verified": True}


def _pp(title: str, text: str, url: str) -> dict:
    # verified 2026-10-22: every sentence matched against its page's HTML by a script, then approved by the user
    return {"title": title, "text": text, "source_url": url, "retrieved_at": "2026-10-22", "verified": True}


def _partial(url: str) -> str:
    """For a note that quotes only part of PayPal's list of requirements: Vendor Guard's words, not PayPal's."""
    return f" This is not PayPal's full list: check every requirement on PayPal's page: {url}"


POLICY_PACK = {
    "vg_verdicts": _vg(
        "Vendor Guard's verdicts",
        "Vendor Guard checks the payment in PayPal and gives one of four verdicts. "
        f"VERIFIED: {POLICY['P.VERIFIED']} VERIFIED_WITH_WARNINGS: {POLICY['P.VERIFIED_WARN']} "
        f"WAIT: the payment is not confirmed yet. SCAM_LIKELY: {POLICY['P.SCAM_LIKELY']} "
        f"When the chat asks you to send money, pay a fee or follow a payment link, the answer also says: {MONEY_OUT_WARNING}",
        "graph-1-scam_check",
    ),
    "vg_why_wait": _vg(
        "Why Vendor Guard says WAIT",
        "WAIT means Vendor Guard could not confirm a completed payment of your price. It comes with one of these: "
        + " ".join(text for key, text in POLICY.items() if key.startswith("P.WAIT"))
        + " A transaction ID that PayPal can't find is not proof of a scam: PayPal gives the buyer and the seller "
        "different transaction IDs for the same payment.",
        "graph-1-scam_check",
    ),
    "vg_how_to_check": _vg(
        "How to check a payment",
        "Say check. I'll ask for your price, for example 25.00 USD, and then for the buyer's chat: paste it exactly "
        "as written. Type cancel to stop at any time. Screenshots aren't supported in this chat yet.",
        "graph-3-chat",
    ),
    "pp_different_ids": _pp(
        "Why the buyer's and your transaction IDs differ",
        "PayPal intentionally generates two unique transaction IDs, one for the buyer and one for the seller. "
        "Even though they refer to the same transaction, the IDs are different to help facilitate backend processing. "
        "If your buyer gives you a transaction ID, you can find transaction information as follows: Go to your Activity. "
        "Enter the buyer’s transaction ID in the Search for Transactions box.",
        PAYPAL_PAGES + "cshelp/article/why-are-there-different-buyer-and-seller-transaction-ids-for-the-same-transaction-ts2265",
    ),
    "pp_payment_hold": _pp(
        "Why a payment is on hold",
        "If you have a new PayPal account, it can take time to build up a positive reputation as a seller. "
        "Log in to your account and click on Know your options under your PayPal balance to see if you can help "
        "release your payments on hold. Add Tracking. Use one of PayPal's approved shipping carriers, and PayPal will "
        "release the hold approximately 24 hours after the courier confirms delivery to the buyer’s address. "
        "In rare cases, after these steps are completed, PayPal may need to hold the payment for up to 21 days.",
        PAYPAL_PAGES + "cshelp/article/why-is-my-payment-on-hold-or-unavailable-pcc105",
    ),
    "pp_pending_echeck": _pp(
        "Pending eCheck payments",
        "An eCheck is an electronic payment funded by the sender's bank account. It usually takes between 4-7 business "
        "days to clear, but sometimes it may take longer (Saturday, Sunday, and Holidays aren’t considered business days). "
        "The payment may show as “pending” in your PayPal account. Don’t ship any items or provide any services until "
        "the eCheck clears because if you do and the eCheck doesn't clear, you won't get paid for the item/ services.",
        PAYPAL_PAGES + "cshelp/article/why-is-my-incoming-echeck-pending-help283",
    ),
    "pp_fake_emails": _pp(
        "Spotting fake PayPal emails",
        "PayPal's emails will always address you by your first and last names or by your business name. "
        "If there’s an urgent need for you to complete something on your account, you can find this information by "
        "logging into PayPal directly. Before you ship anything, log in to PayPal and check that you actually received "
        "a payment. If you received a payment, you’ll always see it in your PayPal activity.",
        PAYPAL_PAGES + "cshelp/article/how-do-i-spot-a-fake-fraudulent-or-phishing-paypal-email-or-website-help164",
    ),
    "pp_overpayment": _pp(
        "Overpayment scam",
        "A customer sends a PayPal payment that is more than the purchase price of the order, and then asks you to wire "
        "them the difference. Even if a payment appears in your account, it might not be safe to spend it yet. If the "
        "legitimate account holder reports unauthorized activity, the money can be withdrawn from your account. If a "
        "customer overpays you and asks you to wire them the difference, consider canceling the order—it's very likely "
        "to be fraudulent.",
        SCAMS,
    ),
    "pp_seller_protection": _pp(
        "What Seller Protection covers",
        "PayPal's Seller Protection applies to 2 types of complaints: Item not received issues filed through PayPal’s "
        "Resolution Center. Unauthorized payment issues. Requirements for Seller Protection, for physical items: "
        "Ship the item to the address on the Transaction Details page. Provide PayPal with valid proof of shipment or delivery."
        # skips "Respond to our requests for documentation and other information within the required timeframe."
        + _partial(PAYPAL_PAGES + "cshelp/article/what-is-paypal-seller-protection-help156"),
        PAYPAL_PAGES + "cshelp/article/what-is-paypal-seller-protection-help156",
    ),
    "pp_snad_not_covered": _pp(
        "Not covered: Significantly Not as Described",
        "The following items or transactions are not eligible for PayPal’s Seller Protection program: Items where the "
        "buyer claims (either with PayPal or their card issuer) that the item you sent isn’t what was ordered (referred "
        "to as a Significantly Not as Described claim). Item Not Received claims filed by your buyer directly with their "
        "card issuer.",
        SELLER_PROTECTION,
    ),
    "pp_ship_to_transaction_address": _pp(
        "Ship only to the Transaction Details address",
        "You must ship the item to the shipping address on the Transaction Details page in your PayPal account for the "
        "transaction. If you originally ship the item to the recipient’s shipping address on the Transaction Details page "
        "but the item is later redirected to a different address, you will not be eligible for PayPal’s Seller "
        "Protection program.",
        SELLER_PROTECTION,
    ),
    "pp_proof_of_delivery": _pp(
        "Proof of shipment or delivery",
        "You provide PayPal with valid proof of shipment or delivery. The following is required as proof of shipment or "
        "delivery for physical goods: Online or physical documentation from a shipping company that includes: An online "
        "and verifiable tracking number. An address for the recipient that matches the shipping address on the "
        "Transaction Details page."
        + _partial(SELLER_PROTECTION),  # skips "Date of shipment." and the items after the address
        SELLER_PROTECTION,
    ),
    "pp_add_tracking": _pp(
        "How to add tracking",
        "Go to your Activity. Find the purchase you want to update and click Get your money. If you sold a product, "
        "choose “Product” to add tracking information or print a shipping label. Click Submit. When you submit tracking "
        "information, your buyer will receive an email that includes the tracking number, order status, and shipping company.",
        PAYPAL_PAGES + "cshelp/article/how-do-i-add-tracking-information-to-my-payment-or-update-an-order-status-help107",
    ),
    "pp_buyer_shipping_label": _pp(
        "A buyer's shipping account or label",
        "If you use the buyer's shipping account, they can easily contact the shipping company and reroute the order to "
        "another address. Only use your shipping account. Do not accept shipping labels from your customers. Contact your "
        "shipping company and block buyers from rerouting packages.",
        SCAMS,
    ),
    "pp_friends_and_family": _pp(
        "Friends and Family or goods and services",
        "Choose “Sending to a friend” for the everyday exchange of money between you and your friends and family. "
        "Choose “Paying for an item or service” when buying goods or services. Personal payments aren't covered by "
        "PayPal Purchase Protection. PayPal covers eligible goods and services payments under PayPal Purchase Protection.",
        PAYPAL_PAGES + "cshelp/article/whats-the-difference-between-friends-and-family-or-goods-and-services-payments-help277",
    ),
    "pp_ff_no_seller_protection": _pp(
        "Friends and Family payments and Seller Protection",
        "The following items or transactions are not eligible for PayPal’s Seller Protection program: Personal payments "
        "including a payment sent using PayPal’s friends and family functionality.",
        SELLER_PROTECTION,
    ),
    "pp_dispute_vs_claim": _pp(
        "A dispute or a claim",
        "A buyer can open a dispute in their Resolution Center if their order doesn't arrive or isn’t as described. In a "
        "dispute, PayPal doesn’t get involved or decide the outcome. To ask PayPal to investigate the case and decide the "
        "outcome, the dispute must be escalated to a claim within 20 days. Either the buyer or seller can escalate a "
        "dispute to a claim.",
        PAYPAL_PAGES + "cshelp/article/whats-the-difference-between-a-dispute-and-a-claim-help238",
    ),
    "pp_chargeback": _pp(
        "What a chargeback is",
        "When a customer files a chargeback with their credit card issuer, it means that they’re disputing a charge and "
        "asking the card issuer for a refund. A chargeback isn’t the same as a PayPal claim. The chargeback process is "
        "initiated outside of PayPal, between the card issuer and their cardholder. In a dispute over a chargeback, the "
        "decision is ultimately made by the card issuer and PayPal doesn't decide the outcome.",
        PAYPAL_PAGES + "cshelp/article/what-is-a-chargeback-and-why-did-i-get-one-help607",
    ),
    "pp_dispute_deadline": _pp(
        "When to respond to a dispute",
        "You must respond within the first 10 days following a dispute creation. If you don’t respond within these 10 "
        "days and the buyer escalates it to a claim, PayPal will resolve the case in the buyer's favor. To resolve a "
        "dispute with the other party, you have 20 days to exchange messages using your Resolution Center. Disputes are "
        "closed automatically after 20 days unless you or the buyer escalate it.",
        PAYPAL_PAGES + "cshelp/article/i-am-a-seller---how-do-i-handle-a-dispute-or-problem-with-a-buyer-help1083",
    ),
    "pp_refund_in_paypal": _pp(
        "How to refund a buyer in PayPal",
        "Go to Activity. Select the payment you want to refund and click Issue a refund. You can send a full or partial "
        "refund within 180 days of the payment date. You don’t pay any fees to refund a payment for goods or services. "
        "But PayPal doesn't return the fees you paid when you got the payment.",
        PAYPAL_PAGES + "cshelp/article/how-do-i-issue-a-refund-help101",
    ),
}


def verified() -> dict[str, dict]:
    """The only entries the chat may show or offer to the LLM."""
    return {key: entry for key, entry in POLICY_PACK.items() if entry["verified"]}
