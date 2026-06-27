"""Detect recurring subscriptions from email receipts.

Source-agnostic: the engine works on normalised EmailRecord objects, so the same
detection logic serves a local Apple Mail (.emlx) scan, a portable .mbox export
(Gmail Takeout / Thunderbird), or a live IMAP connection.

The hard part isn't the source — it's that most subscriptions are billed through
aggregators (Apple, Stripe, PayPal) that name the real merchant in the receipt
subject or body rather than the sender. The rules in data/receipt_senders.json
encode who's an aggregator and where to find the merchant.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

RULES_PATH = Path(__file__).parent / "data" / "receipt_senders.json"

# Currency symbol/code → display symbol.
_CURRENCY = {"£": "£", "$": "$", "€": "€", "GBP": "£", "USD": "$", "EUR": "€"}


@dataclass
class EmailRecord:
    sender: str
    subject: str
    date: datetime
    body: str  # plaintext (HTML is stripped on ingest)


@dataclass
class Detection:
    merchant: str
    amount: Optional[float] = None
    currency: Optional[str] = None
    cadence: str = "unknown"          # monthly | yearly | unknown
    via: str = "direct"               # Apple | Stripe | PayPal | direct
    last_seen: Optional[datetime] = None
    occurrences: int = 1
    months_seen: set = field(default_factory=set)
    confidence: str = "medium"        # high | medium | low


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_rules() -> dict:
    with open(RULES_PATH) as f:
        return json.load(f)


def strip_html(text: str) -> str:
    if "<" not in text:
        return text
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&#39;", "'").replace("&pound;", "£"))
    return re.sub(r"\s+", " ", text).strip()


def _domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender or "")
    return m.group(1).lower() if m else (sender or "").lower()


def _matches(domain: str, key: str) -> bool:
    return domain == key or domain.endswith("." + key)


def extract_amount(body: str):
    """Return (amount, currency_symbol), preferring the gross paid amount.

    Receipts list several figures (subtotal, tax, total excluding tax). We want
    the amount actually charged, so prefer "amount paid"/"you paid", then a clean
    "Total" that isn't "Subtotal" or "Total excluding tax", then any amount.
    """
    paid = re.search(
        r"(?:amount paid|you paid|amount charged)\D{0,15}([£$€])\s?(\d+(?:[.,]\d{2})?)",
        body, re.IGNORECASE,
    )
    if paid:
        return _to_float(paid.group(2)), _CURRENCY.get(paid.group(1), paid.group(1))

    total = re.search(
        r"(?<!sub)\btotal\b(?!\s+excluding)\D{0,15}([£$€])\s?(\d+(?:[.,]\d{2})?)",
        body, re.IGNORECASE,
    )
    if total:
        return _to_float(total.group(2)), _CURRENCY.get(total.group(1), total.group(1))

    coded = re.search(r"([£$€])\s?(\d+(?:[.,]\d{2})?)\s*(GBP|USD|EUR)?", body)
    if coded:
        return _to_float(coded.group(2)), _CURRENCY.get(coded.group(1), coded.group(1))
    return None, None


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def detect_cadence(body: str, rules: dict) -> str:
    low = body.lower()
    if any(k in low for k in rules.get("yearly_keywords", [])):
        return "yearly"
    if any(k in low for k in rules.get("recurrence_keywords", [])):
        return "monthly"
    return "unknown"


# ---------------------------------------------------------------------------
# Merchant extraction per aggregator
# ---------------------------------------------------------------------------

def _merchant_subject_from(rec: EmailRecord) -> Optional[str]:
    # Stripe: "Your receipt from <Merchant> #1234-5678"
    m = re.search(r"from\s+(.+?)\s+#", rec.subject, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _merchant_subject_to(rec: EmailRecord) -> Optional[str]:
    # PayPal: "Receipt for your payment to <Merchant>..."
    m = re.search(r"payment to\s+(.+)", rec.subject, re.IGNORECASE)
    if m:
        return m.group(1).strip().rstrip(".").replace("...", "").strip()
    return None


def _merchant_apple_body(rec: EmailRecord) -> Optional[str]:
    # Apple invoices place the app name right after the "Apple Account:" line,
    # before the plan descriptor — anchor on that for a clean extraction.
    m = re.search(
        r"Apple Account:\s*\S+\s+(.+?)\s+"
        r"(?:Pro\b|Premium\b|Plus\b|Basic\b|Plan\b|\(Monthly\)|\(Yearly\)|Renews)",
        rec.body, re.IGNORECASE,
    )
    if m:
        name = re.sub(r"\s+", " ", m.group(1)).strip(" -")
        # Collapse an immediately repeated brand word ("X X" -> "X").
        name = re.sub(r"\b(\w+)\s+\1\b", r"\1", name)
        return name or None
    return None


def _merchant_google_body(rec: EmailRecord) -> Optional[str]:
    m = re.search(r"Google Play.*?\n?(.+?)\s+(?:Subscription|Monthly)", rec.body, re.IGNORECASE)
    return m.group(1).strip() if m else None


_MERCHANT_EXTRACTORS = {
    "subject_from": _merchant_subject_from,
    "subject_to": _merchant_subject_to,
    "apple_body": _merchant_apple_body,
    "body_google": _merchant_google_body,
}


# ---------------------------------------------------------------------------
# Classification + detection
# ---------------------------------------------------------------------------

def _classify(rec: EmailRecord, rules: dict):
    domain = _domain(rec.sender)
    for key in rules.get("ignore_senders", []):
        if _matches(domain, key):
            return ("ignore", None)
    for key, info in rules.get("aggregators", {}).items():
        if _matches(domain, key):
            return ("aggregator", info)
    for key, info in rules.get("merchants", {}).items():
        if _matches(domain, key):
            return ("merchant", info)
    return ("unknown", None)


def _looks_like_receipt(rec: EmailRecord, rules: dict) -> bool:
    blob = (rec.subject + " " + rec.body[:400]).lower()
    return any(k in blob for k in rules.get("receipt_keywords", []))


def _detect_one(rec: EmailRecord, rules: dict) -> Optional[Detection]:
    kind, info = _classify(rec, rules)
    if kind == "ignore":
        return None

    cadence = detect_cadence(rec.body, rules)
    amount, currency = extract_amount(rec.body)

    if kind == "aggregator":
        extractor = _MERCHANT_EXTRACTORS.get(info["merchant_from"])
        merchant = (extractor(rec) if extractor else None) or info["name"]
        via = info["via"]
        # App Store invoices are always subscriptions; Stripe/PayPal need a
        # recurrence signal to avoid catching one-off payments.
        if via != "App Store" and cadence == "unknown":
            if not re.search(r"subscription|renew|recurring|plan", rec.body, re.IGNORECASE):
                return None
        confidence = "high"
    elif kind == "merchant":
        merchant = info["name"]
        via = "direct"
        if cadence == "unknown" and not _looks_like_receipt(rec, rules):
            return None
        confidence = "high"
    else:  # unknown sender — only keep strong, recurring-looking receipts
        if not (_looks_like_receipt(rec, rules) and cadence != "unknown"):
            return None
        merchant = _domain(rec.sender).split(".")[0].title()
        via = "direct"
        confidence = "low"

    return Detection(
        merchant=merchant, amount=amount, currency=currency, cadence=cadence,
        via=via, last_seen=rec.date, occurrences=1,
        months_seen={rec.date.strftime("%Y-%m")} if rec.date else set(),
        confidence=confidence,
    )


def scan(records: List[EmailRecord], rules: Optional[dict] = None) -> List[Detection]:
    """Detect and de-duplicate recurring subscriptions across a set of emails."""
    rules = rules or load_rules()
    grouped: Dict[str, Detection] = {}

    for rec in records:
        det = _detect_one(rec, rules)
        if not det:
            continue
        key = f"{det.merchant.lower()}|{det.via}"
        if key in grouped:
            g = grouped[key]
            g.occurrences += 1
            g.months_seen |= det.months_seen
            if det.last_seen and (g.last_seen is None or det.last_seen > g.last_seen):
                g.last_seen = det.last_seen
                if det.amount is not None:
                    g.amount, g.currency = det.amount, det.currency
            if g.amount is None and det.amount is not None:
                g.amount, g.currency = det.amount, det.currency
            if g.cadence == "unknown":
                g.cadence = det.cadence
        else:
            grouped[key] = det

    # Confidence bump: seen across 2+ distinct months = confirmed recurring.
    for g in grouped.values():
        if len(g.months_seen) >= 2:
            g.confidence = "high"

    return sorted(grouped.values(), key=lambda d: -(d.amount or 0))


def monthly_total(detections: List[Detection]) -> Dict[str, float]:
    """Monthly spend grouped by currency (yearly plans amortised to /month)."""
    totals: Dict[str, float] = {}
    for d in detections:
        if d.amount is None:
            continue
        cur = d.currency or "?"
        amt = d.amount / 12 if d.cadence == "yearly" else d.amount
        totals[cur] = totals.get(cur, 0.0) + amt
    return totals


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _body_from_message(msg) -> str:
    """Extract a plaintext body from an email.message.Message."""
    if msg.is_multipart():
        plain, html = None, None
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                plain = _decode(part)
            elif ctype == "text/html" and html is None:
                html = _decode(part)
        return plain or strip_html(html or "")
    payload = _decode(msg)
    return payload if msg.get_content_type() == "text/plain" else strip_html(payload)


def _decode(part) -> str:
    try:
        raw = part.get_payload(decode=True)
        if raw is None:
            return str(part.get_payload())
        return raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return ""


def _record_from_message(msg) -> Optional[EmailRecord]:
    from email.utils import parsedate_to_datetime
    try:
        date = parsedate_to_datetime(msg.get("Date"))
        if date is not None and date.tzinfo is not None:
            date = date.replace(tzinfo=None)
    except Exception:
        date = None
    return EmailRecord(
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        date=date or datetime.now(),
        body=_body_from_message(msg),
    )


def read_mbox(path: str, since: Optional[datetime] = None) -> List[EmailRecord]:
    import mailbox
    records = []
    for msg in mailbox.mbox(path):
        rec = _record_from_message(msg)
        if rec and (since is None or rec.date >= since):
            records.append(rec)
    return records


def read_emlx_dir(path: Optional[str] = None, since: Optional[datetime] = None) -> List[EmailRecord]:
    """Walk an Apple Mail store for .emlx files (default ~/Library/Mail)."""
    import email
    base = Path(path) if path else (Path.home() / "Library" / "Mail")
    records = []
    if not base.exists():
        return records
    for emlx in base.rglob("*.emlx"):
        try:
            raw = emlx.read_bytes()
            # .emlx = "<byte-count>\n<rfc822 message>\n<plist>"; drop the count line.
            nl = raw.find(b"\n")
            msg = email.message_from_bytes(raw[nl + 1:] if nl != -1 else raw)
            rec = _record_from_message(msg)
            if rec and (since is None or rec.date >= since):
                records.append(rec)
        except Exception:
            continue
    return records


def read_imap(host: str, user: str, password: str,
              since: Optional[datetime] = None, mailbox_name: str = "INBOX") -> List[EmailRecord]:
    """Read messages over IMAP (read-only). Credentials are the caller's to supply
    — typically from env vars — and are never stored by this tool."""
    import email
    import imaplib

    records = []
    conn = imaplib.IMAP4_SSL(host)
    try:
        conn.login(user, password)
        conn.select(mailbox_name, readonly=True)
        criteria = "ALL"
        if since:
            criteria = f'(SINCE "{since.strftime("%d-%b-%Y")}")'
        _typ, data = conn.search(None, criteria)
        for num in data[0].split():
            _typ, msg_data = conn.fetch(num, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            rec = _record_from_message(msg)
            if rec:
                records.append(rec)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return records
