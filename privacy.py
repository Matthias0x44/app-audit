"""Derive a transparent privacy grade from cited factual attributes.

The grade is never an opinion — it is computed from a fixed rubric over
verifiable facts (open-source status, tracker counts, ToS;DR grade, business
model, known incidents). The contributing facts are always shown alongside the
grade so the result is auditable rather than a black box.
"""

from typing import Dict, List, Tuple

# Each factor maps a factual value to a point contribution. Higher = more
# privacy-respecting. The rubric is intentionally simple and visible.
_BUSINESS_MODEL_POINTS = {
    "nonprofit": 2,
    "paid": 1,
    "freemium": 0,
    "ad-supported": -2,
    "data-broker": -3,
}

_DATA_COLLECTION_POINTS = {
    "minimal": 2,
    "moderate": 0,
    "extensive": -2,
}

_TOSDR_POINTS = {"A": 2, "B": 1, "C": 0, "D": -1, "E": -2}


def _tracker_points(n) -> int:
    if n is None:
        return 0
    if n == 0:
        return 1
    if n <= 3:
        return -1
    return -2


def score(entry: Dict) -> Tuple[str, int, List[str]]:
    """Return (letter_grade, points, human-readable reasons) for one app entry."""
    points = 0
    reasons: List[str] = []

    if entry.get("open_source"):
        points += 2
        reasons.append("+2  open source")
    else:
        reasons.append(" 0  closed source")

    if entry.get("e2e_encryption"):
        points += 2
        reasons.append("+2  end-to-end encryption")

    bm = entry.get("business_model")
    if bm in _BUSINESS_MODEL_POINTS:
        p = _BUSINESS_MODEL_POINTS[bm]
        points += p
        reasons.append(f"{p:+d}  business model: {bm}")

    dc = entry.get("data_collection")
    if dc in _DATA_COLLECTION_POINTS:
        p = _DATA_COLLECTION_POINTS[dc]
        points += p
        reasons.append(f"{p:+d}  data collection: {dc}")

    trackers = entry.get("trackers")
    if trackers is not None:
        p = _tracker_points(trackers)
        points += p
        reasons.append(f"{p:+d}  {trackers} tracker(s) [Exodus Privacy]")

    grade_tosdr = entry.get("tosdr_grade")
    if grade_tosdr in _TOSDR_POINTS:
        p = _TOSDR_POINTS[grade_tosdr]
        points += p
        reasons.append(f"{p:+d}  ToS;DR grade {grade_tosdr}")

    incidents = entry.get("notable_incidents") or []
    if incidents:
        p = max(-2, -len(incidents))
        points += p
        reasons.append(f"{p:+d}  {len(incidents)} notable incident(s)")

    return letter(points), points, reasons


def letter(points: int) -> str:
    if points >= 7:
        return "A"
    if points >= 5:
        return "B"
    if points >= 3:
        return "C"
    if points >= 1:
        return "D"
    return "F"


# Rich colour per grade, for display.
GRADE_COLOUR = {"A": "green", "B": "green", "C": "yellow", "D": "yellow", "F": "red"}
