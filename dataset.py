"""Build and write the public privacy-contact dataset.

Shared by the CLI (audit.py) and the desktop sidecar (api.py) so the exported
artifact is identical regardless of entry point.
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

CSV_FIELDS = [
    "company", "compliant", "has_privacy_email", "has_dpo_email",
    "has_sar_form", "contact_method", "has_deletion_url",
    "deletion_difficulty", "privacy_email", "dpo_email",
    "deletion_url", "sar_form", "notes",
]

_METHODOLOGY = (
    "Compiled from public privacy policies and DPA registrations. A company is "
    "flagged as a contact gap only when no email, DPO, or SAR web form is "
    "discoverable. Web-form-only companies are recorded separately, not flagged "
    "as non-compliant."
)


def build_records(sar_db: dict) -> List[Dict]:
    """Normalise the raw sar_contacts DB into flat, exportable records."""
    records = []
    for key, c in sorted(sar_db.items()):
        has_email = bool(c.get("privacy_email")) or bool(c.get("dpo_email"))
        has_form = bool(c.get("sar_form"))
        # A company reachable via a SAR web form is not "no contact", just not
        # reachable by email — keep that distinction so we don't overstate gaps.
        if has_email:
            method = "email"
        elif has_form:
            method = "web-form only"
        else:
            method = "none found"
        records.append({
            "company": c.get("display_name", key),
            "compliant": c.get("compliant", True),
            "has_privacy_email": bool(c.get("privacy_email")),
            "has_dpo_email": bool(c.get("dpo_email")),
            "has_sar_form": has_form,
            "contact_method": method,
            "has_deletion_url": bool(c.get("deletion_url")),
            "deletion_difficulty": c.get("deletion_difficulty", "unknown"),
            "privacy_email": c.get("privacy_email", ""),
            "dpo_email": c.get("dpo_email", ""),
            "deletion_url": c.get("deletion_url", ""),
            "sar_form": c.get("sar_form", ""),
            "notes": c.get("notes", ""),
        })
    return records


def summarize(records: List[Dict]) -> Dict[str, int]:
    return {
        "total": len(records),
        "non_compliant": sum(1 for r in records if not r["compliant"]),
        "no_discoverable_contact": sum(1 for r in records if r["contact_method"] == "none found"),
        "web_form_only": sum(1 for r in records if r["contact_method"] == "web-form only"),
    }


def _markdown(records: List[Dict], stamp: str, summary: Dict[str, int]) -> str:
    lines = [
        f"# Privacy Contact Dataset — {stamp}",
        "",
        f"- **Companies tracked:** {summary['total']}",
        f"- **Confirmed non-compliant:** {summary['non_compliant']}",
        f"- **No discoverable contact (no email, DPO, or web form):** {summary['no_discoverable_contact']}",
        f"- **Web-form-only (reachable, but no direct email):** {summary['web_form_only']}",
        "",
        f"_Methodology: {_METHODOLOGY}_",
        "",
        "## Flagged companies (non-compliant or no discoverable contact)",
        "",
        "| Company | Email | DPO | Web form | Deletion URL | Status |",
        "|---|:---:|:---:|:---:|:---:|---|",
    ]
    flagged = [r for r in records if not r["compliant"] or r["contact_method"] == "none found"]
    for r in sorted(flagged, key=lambda x: x["company"]):
        status = "Non-compliant" if not r["compliant"] else "No contact found"
        lines.append(
            f"| {r['company']} | {'✓' if r['has_privacy_email'] else '✗'} | "
            f"{'✓' if r['has_dpo_email'] else '✗'} | "
            f"{'✓' if r['has_sar_form'] else '✗'} | "
            f"{'✓' if r['has_deletion_url'] else '✗'} | {status} |"
        )
    if not flagged:
        lines.append("| _none_ | | | | | |")

    web_form_only = [r for r in records if r["contact_method"] == "web-form only"]
    if web_form_only:
        lines += [
            "",
            "## Web-form-only companies",
            "",
            "_Reachable for SAR/erasure, but only via a web form — no direct privacy email._",
            "",
            "| Company | Web form |",
            "|---|---|",
        ]
        for r in sorted(web_form_only, key=lambda x: x["company"]):
            lines.append(f"| {r['company']} | {r['sar_form'] or '—'} |")

    return "\n".join(lines) + "\n"


def write_dataset(sar_db: dict, output_dir: Path) -> Dict:
    """Write CSV + JSON + markdown to output_dir. Returns summary and paths."""
    records = build_records(sar_db)
    summary = summarize(records)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")

    json_path = output_dir / f"privacy-contacts-{stamp}.json"
    json_path.write_text(json.dumps({
        "generated": stamp,
        "methodology": _METHODOLOGY,
        **summary,
        "records": records,
    }, indent=2))

    csv_path = output_dir / f"privacy-contacts-{stamp}.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(records)

    md_path = output_dir / f"privacy-contacts-{stamp}.md"
    md_path.write_text(_markdown(records, stamp, summary))

    return {
        "stamp": stamp,
        "summary": summary,
        "paths": {"json": str(json_path), "csv": str(csv_path), "md": str(md_path)},
    }
