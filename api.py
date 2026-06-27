#!/usr/bin/env python3
"""JSON API over the app-audit backend, for the Tauri desktop sidecar.

The rich CLI (audit.py) stays for terminal use. This entry point exposes the
same logic as machine-readable JSON so the Rust/Tauri layer can shell out and
render it natively:

    python3 api.py <command> [json-encoded-args]

Every response is a single JSON object on stdout:
    {"ok": true,  "data": ...}
    {"ok": false, "error": "..."}
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent / "data"
ALTERNATIVES_DB = DATA_DIR / "alternatives.json"
SAR_CONTACTS_DB = DATA_DIR / "sar_contacts.json"
CATEGORIES_DB = DATA_DIR / "categories.json"
SUBSCRIPTIONS_DB = DATA_DIR / "subscriptions.json"
PRIVACY_SCORES_DB = DATA_DIR / "privacy_scores.json"


# ---------------------------------------------------------------------------
# Shared helpers (kept tiny and self-contained for the sidecar)
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def _match(name: str, db: dict) -> Optional[str]:
    needle = name.lower().replace(" ", "").replace("-", "").replace(".", "")
    for key in db:
        if key == "_meta":
            continue
        hay = key.lower().replace(" ", "").replace("-", "").replace(".", "")
        if needle == hay or needle in hay or hay in needle:
            return key
    return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _privacy_grade(name: str, scores: dict):
    import privacy
    key = _match(name, scores)
    if not key:
        return None
    grade, points, _ = privacy.score(scores[key])
    return {"grade": grade, "points": points}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_scan(args):
    from scanner import scan_all

    apps = scan_all()
    alts = _load(ALTERNATIVES_DB)
    scores = {k: v for k, v in _load(PRIVACY_SCORES_DB).items() if k != "_meta"}
    out = []
    for a in apps:
        pg = _privacy_grade(a.name, scores)
        out.append({
            "name": a.name,
            "source": a.source,
            "bundle_id": a.bundle_id,
            "last_used": _iso(a.last_used),
            "days_since_used": a.days_since_used,
            "has_alternative": _match(a.name, alts) is not None,
            "privacy_grade": pg["grade"] if pg else None,
        })
    return out


def cmd_caches(args):
    from caches import get_cache_sizes
    sizes = get_cache_sizes()
    entries = [{"name": n, "size": s} for n, s in sizes.items()]
    return {"entries": entries, "total": sum(sizes.values())}


def cmd_orphaned_caches(args):
    from caches import find_orphaned_caches
    from scanner import scan_all
    bundle_ids = {a.bundle_id for a in scan_all() if a.bundle_id}
    orphaned = find_orphaned_caches(bundle_ids)
    return {
        "entries": [{"name": n, "size": s} for n, s in orphaned.items()],
        "total": sum(orphaned.values()),
    }


def cmd_clean(args):
    from caches import clear_caches
    names = args.get("names", [])
    if not names:
        raise ValueError("clean requires 'names'")
    cleared, freed, errors = clear_caches(names)
    return {"cleared": cleared, "freed": freed, "errors": errors}


def cmd_overlap(args):
    from scanner import scan_all
    apps = scan_all()
    categories = _load(CATEGORIES_DB)

    buckets: dict = {}
    for a in apps:
        name = a.name.lower()
        for category, keywords in categories.items():
            if any(kw in name or name in kw for kw in keywords):
                buckets.setdefault(category, []).append(a)
                break

    result = []
    for cat, items in buckets.items():
        if len(items) < 2:
            continue
        items_sorted = sorted(items, key=lambda a: a.last_used or datetime.min, reverse=True)
        result.append({
            "category": cat,
            "apps": [
                {"name": a.name, "last_used": _iso(a.last_used),
                 "keeper": (i == 0 and a.last_used is not None)}
                for i, a in enumerate(items_sorted)
            ],
        })
    result.sort(key=lambda c: -len(c["apps"]))
    return result


def cmd_subscriptions(args):
    from scanner import scan_all
    apps = scan_all()
    subs = _load(SUBSCRIPTIONS_DB)
    days = args.get("days", 90)
    now = datetime.now()

    rows = []
    monthly = 0.0
    wasted = 0.0
    for a in apps:
        key = _match(a.name, subs)
        if not key or key == "_meta":
            continue
        sub = subs[key]
        price = sub.get("price", 0.0)
        monthly += price
        idle = None if a.last_used is None else (now - a.last_used).days
        unused = a.last_used is None or (idle is not None and idle > days)
        if unused:
            wasted += price
        rows.append({
            "name": sub.get("display_name", a.name),
            "price": price,
            "last_used": _iso(a.last_used),
            "unused": unused,
            "has_free_tier": sub.get("has_free_tier", False),
            "cancel_url": sub.get("cancel_url", ""),
        })
    rows.sort(key=lambda r: (not r["unused"], -r["price"]))
    return {"rows": rows, "monthly_total": monthly, "wasted_total": wasted}


def cmd_privacy_all(args):
    import privacy
    from scanner import scan_all
    apps = scan_all()
    scores = {k: v for k, v in _load(PRIVACY_SCORES_DB).items() if k != "_meta"}
    rows = []
    for a in apps:
        key = _match(a.name, scores)
        if key:
            grade, points, _ = privacy.score(scores[key])
            rows.append({"name": a.name, "grade": grade, "points": points})
    rows.sort(key=lambda r: r["points"])
    return rows


def cmd_privacy_app(args):
    import privacy
    name = args.get("name")
    scores = {k: v for k, v in _load(PRIVACY_SCORES_DB).items() if k != "_meta"}
    key = _match(name, scores)
    if not key:
        raise ValueError(f"No privacy data for '{name}'")
    entry = scores[key]
    grade, points, reasons = privacy.score(entry)
    return {
        "name": entry.get("display_name", key),
        "grade": grade,
        "points": points,
        "reasons": reasons,
        "sources": entry.get("sources", []),
        "notes": entry.get("notes"),
    }


def cmd_alternatives(args):
    name = args.get("name")
    alts = _load(ALTERNATIVES_DB)
    scores = {k: v for k, v in _load(PRIVACY_SCORES_DB).items() if k != "_meta"}
    key = _match(name, alts)
    if not key:
        raise ValueError(f"No alternatives listed for '{name}'")
    entry = alts[key]
    orig = _privacy_grade(entry.get("display_name", key), scores)
    out_alts = []
    for alt in entry.get("alternatives", []):
        pg = _privacy_grade(alt["name"], scores)
        out_alts.append({**alt, "privacy_grade": pg["grade"] if pg else None})
    return {
        "display_name": entry.get("display_name", key),
        "category": entry.get("category"),
        "big_tech": entry.get("big_tech", False),
        "privacy_grade": orig["grade"] if orig else None,
        "alternatives": out_alts,
    }


def cmd_sar(args):
    from sar import generate_sar_email
    sar_db = _load(SAR_CONTACTS_DB)
    name = args["app_name"]
    rtype = args.get("request_type", "access")
    user_name = args.get("user_name", "")
    user_email = args.get("user_email", "")

    key = _match(name, sar_db)
    if key:
        c = sar_db[key]
        company = c.get("display_name", key)
        to_email = c.get("privacy_email") or c.get("dpo_email") or ""
        meta = {
            "deletion_url": c.get("deletion_url", ""),
            "deletion_difficulty": c.get("deletion_difficulty", "unknown"),
            "sar_form": c.get("sar_form", ""),
        }
    else:
        company, to_email, meta = name, "", {}

    body = generate_sar_email(company, user_name, user_email, rtype, to_email)
    return {"company": company, "to_email": to_email, "body": body, **meta}


def cmd_noncompliant(args):
    from scanner import scan_all
    apps = scan_all()
    sar_db = _load(SAR_CONTACTS_DB)
    out = []
    for a in apps:
        key = _match(a.name, sar_db)
        if key:
            if not sar_db[key].get("compliant", True):
                out.append({"name": a.name, "severity": "confirmed"})
        else:
            out.append({"name": a.name, "severity": "unknown"})
    return out


def cmd_export_dataset(args):
    from dataset import write_dataset
    sar_db = _load(SAR_CONTACTS_DB)
    if not sar_db:
        raise ValueError("no privacy-contact data to export")
    # Default to a findable, user-visible location rather than a cwd-relative dir.
    default_dir = Path.home() / "Downloads"
    out_dir = Path(args["output_dir"]) if args.get("output_dir") else (
        default_dir if default_dir.exists() else Path.home()
    )
    return write_dataset(sar_db, out_dir)


COMMANDS = {
    "scan": cmd_scan,
    "caches": cmd_caches,
    "orphaned_caches": cmd_orphaned_caches,
    "clean": cmd_clean,
    "overlap": cmd_overlap,
    "subscriptions": cmd_subscriptions,
    "privacy_all": cmd_privacy_all,
    "privacy_app": cmd_privacy_app,
    "alternatives": cmd_alternatives,
    "sar": cmd_sar,
    "noncompliant": cmd_noncompliant,
    "export_dataset": cmd_export_dataset,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(json.dumps({"ok": False, "error": f"unknown command; valid: {list(COMMANDS)}"}))
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"bad args JSON: {exc}"}))
        sys.exit(1)

    try:
        data = COMMANDS[cmd](args)
        print(json.dumps({"ok": True, "data": data}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
