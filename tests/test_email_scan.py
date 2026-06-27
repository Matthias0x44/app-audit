"""Verify the subscription detection engine against representative receipt formats.

The bodies below mirror the real-world structure of Apple / Stripe / PayPal /
direct-merchant receipts (synthetic data, no real account details). Run directly:

    python3 tests/test_email_scan.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import email_scan as es

NOW = datetime(2026, 6, 27)


def rec(sender, subject, body, date=NOW):
    return es.EmailRecord(sender=sender, subject=subject, date=date, body=body)


SAMPLES = [
    # Apple App Store invoice — merchant is in the body, after "Apple Account:".
    rec("no_reply@email.apple.com", "Your invoice from Apple.",
        "Invoice 20 June 2026 Order ID: MQVS014ZW6 Apple Account: user@example.com "
        "Claude by Anthropic Claude Pro - Monthly (Monthly) Renews 21 July 2026 "
        "Subtotal £16.67 VAT £3.33 Total £20.00"),
    rec("no_reply@email.apple.com", "Your invoice from Apple.",
        "Invoice 11 June 2026 Apple Account: user@example.com "
        "Suno - AI Songs & Music Pro Plan (Monthly) Renews 11 July 2026 Total £10.00"),
    rec("no_reply@email.apple.com", "Your invoice from Apple.",
        "Invoice 10 June 2026 Apple Account: user@example.com "
        "X X Premium (Monthly) (Monthly) Renews 8 July 2026 Total £11.00"),
    # Stripe — merchant in subject ("from X"), amount labelled in body.
    rec("invoice+statements@stripe.com", "Your receipt from X #2588-9160-7665",
        "Receipt from X £8.00 Paid June 26, 2026 Total excluding tax £6.67 "
        "VAT United Kingdom (20%) £1.33 Total £8.00 Amount paid £8.00 subscription"),
    # PayPal — merchant in subject ("payment to ..."), amount as "£x GBP".
    rec("service@paypal.co.uk", "Receipt for your payment to Microsoft Payments U...",
        "You paid £5.59 GBP to Microsoft Payments U... Merchant Microsoft Payments "
        "Transaction date 2 Jun 2026 subscription monthly"),
    # Direct merchant with amount in body.
    rec("invoice+statements@vercel.com", "Your receipt from Vercel Inc. #2424-8656",
        "Receipt from Vercel Inc. $24.00 Paid June 21, 2026 Total excluding tax "
        "$20.00 VAT (20%) $4.00 Total $24.00 Amount paid $24.00 subscription monthly"),
    # Direct merchant, recurrence signal but NO amount in the email.
    rec("team@smarty.co.uk", "Your plan has been renewed",
        "You're all good for another month. Just to confirm, payment for your plan "
        "has gone through. Your plan will renew again on 27-07-2026."),
    # One-offs / noise that must be IGNORED.
    rec("orders@jdwetherspoon.co.uk", "J D Wetherspoon Order Confirmation: 8608",
        "Thank you for your order. The Half Moon. Order Total £14.20 Table Number: 3"),
    rec("noreply@uber.com", "Your Sunday morning trip with Uber",
        "Thanks for riding. Total £15.94 Trip fare £15.94"),
    rec("clairefox@substack.com", "World Cup Podcast of Ideas",
        "Listen now. The team give their first thoughts. Subscribe for more."),
]


def find(dets, needle, via=None):
    for d in dets:
        if needle.lower() in d.merchant.lower() and (via is None or d.via == via):
            return d
    return None


def main():
    dets = es.scan(SAMPLES)
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    # Expected detections.
    claude = find(dets, "Claude")
    check(claude is not None, "Claude not detected")
    if claude:
        check(claude.amount == 20.00, f"Claude amount {claude.amount} != 20.00")
        check(claude.currency == "£", f"Claude currency {claude.currency}")
        check(claude.via == "App Store", f"Claude via {claude.via}")
        check(claude.cadence == "monthly", f"Claude cadence {claude.cadence}")

    suno = find(dets, "Suno")
    check(suno is not None and suno.amount == 10.00, "Suno not detected at £10.00")

    # Both X charges must be detected separately — this is the duplicate finding.
    x_apple = find(dets, "X", via="App Store")
    x_stripe = find(dets, "X", via="Stripe")
    check(x_apple is not None and x_apple.amount == 11.00, "X via App Store (£11) not detected")
    check(x_stripe is not None and x_stripe.amount == 8.00, "X via Stripe (£8) not detected")

    ms = find(dets, "Microsoft")
    check(ms is not None and ms.amount == 5.59, "Microsoft not detected at £5.59")
    if ms:
        check(ms.via == "PayPal", f"Microsoft via {ms.via}")

    vercel = find(dets, "Vercel")
    check(vercel is not None and vercel.amount == 24.00, "Vercel not detected at $24.00")
    if vercel:
        check(vercel.currency == "$", f"Vercel currency {vercel.currency}")

    smarty = find(dets, "SMARTY")
    check(smarty is not None, "SMARTY not detected")
    if smarty:
        check(smarty.amount is None, f"SMARTY amount should be None, got {smarty.amount}")
        check(smarty.cadence == "monthly", f"SMARTY cadence {smarty.cadence}")

    # Must NOT detect one-offs / newsletters.
    check(find(dets, "Wetherspoon") is None and find(dets, "Half Moon") is None,
          "Wetherspoon order wrongly detected")
    check(find(dets, "Uber") is None, "Uber trip wrongly detected")
    check(find(dets, "substack") is None and find(dets, "clairefox") is None,
          "Substack newsletter wrongly detected")

    # mbox reader round-trip.
    import mailbox
    import tempfile
    from email.message import EmailMessage
    with tempfile.TemporaryDirectory() as td:
        mbox_path = str(Path(td) / "t.mbox")
        mb = mailbox.mbox(mbox_path)
        m = EmailMessage()
        m["From"] = "invoice+statements@vercel.com"
        m["Subject"] = "Your receipt from Vercel Inc. #1"
        m["Date"] = "Sun, 21 Jun 2026 21:21:10 +0000"
        m.set_content("Receipt from Vercel Inc. Amount paid $24.00 subscription monthly")
        mb.add(m); mb.flush(); mb.close()
        rr = es.read_mbox(mbox_path)
        check(len(rr) == 1 and rr[0].subject.startswith("Your receipt"),
              "mbox reader failed to read message")
        mbdets = es.scan(rr)
        check(find(mbdets, "Vercel") is not None, "mbox->scan failed to detect Vercel")

    totals = es.monthly_total(dets)

    print(f"Detections: {len(dets)}")
    for d in dets:
        amt = f"{d.currency}{d.amount:.2f}" if d.amount is not None else "—"
        print(f"  {d.merchant:32} {amt:>9}  {d.cadence:8} via {d.via:9} [{d.confidence}]")
    print("Monthly total:", ", ".join(f"{c}{v:.2f}" for c, v in totals.items()))

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  ✗", f)
        sys.exit(1)
    print("\nAll checks passed ✓")


if __name__ == "__main__":
    main()
