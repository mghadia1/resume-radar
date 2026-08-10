"""Append every analysis run to a CSV log, and record what came of it.

Deliberately CSV rather than a database: the file opens directly in Excel or
imports into Google Sheets, which is where an application tracker actually gets read.

The ``outcome`` column is the groundwork for turning fit into something better. Right
now the scorer ranks postings by keyword overlap and calls it fit, because there is no
evidence that a higher fit produces more interviews. Once enough rows carry a real
outcome, ``stats`` can say whether it does - and only then would a stronger word be
honest.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

FIELDS = [
    "id", "timestamp", "resume", "company", "role", "score", "max_score", "percent",
    "keyword_percent", "title_matched", "failed_checks", "missing_keywords", "outcome",
]

#: Ordered worst to best. A reply of any kind counts as a response; see `stats`.
OUTCOMES = ["applied", "no_reply", "rejected", "screen", "interview", "offer"]
#: Outcomes that mean a human engaged with the application.
RESPONDED = {"screen", "interview", "offer"}
#: Below this many labelled rows, any rate computed is noise.
MIN_ROWS_FOR_STATS = 20


class UnknownOutcome(ValueError):
    """Raised when an outcome value is not one of :data:`OUTCOMES`."""


def _next_id(path: Path) -> int:
    rows = read(path)
    ids = [int(r["id"]) for r in rows if r.get("id", "").isdigit()]
    return max(ids, default=0) + 1


def append(log_path: str | Path, *, resume: str, report, company: str = "",
           role: str = "", keyword_match=None, outcome: str = "applied") -> Path:
    """Append one row and return the log path. Creates the file with a header if new."""
    if outcome not in OUTCOMES:
        raise UnknownOutcome(f"{outcome!r} is not one of: {', '.join(OUTCOMES)}")

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()

    row = {
        "id": _next_id(path),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resume": resume,
        "company": company,
        "role": role,
        "score": report.score,
        "max_score": report.max_score,
        "percent": round(report.percent, 1),
        "keyword_percent": round(keyword_match.percent, 1) if keyword_match else "",
        "title_matched": bool(keyword_match.title_matched) if keyword_match else "",
        "failed_checks": "; ".join(c.key for c in report.failures),
        "missing_keywords": "; ".join(keyword_match.missing[:15]) if keyword_match else "",
        "outcome": outcome,
    }

    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
    return path


def read(log_path: str | Path) -> list[dict]:
    """Read the log back, oldest first. Returns an empty list if it does not exist."""
    path = Path(log_path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def set_outcome(log_path: str | Path, row_id: int | str, outcome: str) -> dict:
    """Update one row's outcome in place and return the updated row."""
    if outcome not in OUTCOMES:
        raise UnknownOutcome(f"{outcome!r} is not one of: {', '.join(OUTCOMES)}")

    path = Path(log_path)
    rows = read(path)
    if not rows:
        raise LookupError(f"no log at {path}")

    target = str(row_id)
    for row in rows:
        if row.get("id") == target:
            row["outcome"] = outcome
            break
    else:
        raise LookupError(f"no row with id {target} in {path}")

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return row


def _labelled(rows: list[dict]) -> list[dict]:
    """Rows whose outcome is known - "applied" means nothing has happened yet."""
    return [r for r in rows if r.get("outcome") and r["outcome"] != "applied"]


def stats(log_path: str | Path) -> dict:
    """Response rate by keyword-fit decile and by job-title match.

    Refuses to report a rate below :data:`MIN_ROWS_FOR_STATS` labelled rows. With five
    applications every rate is 0%, 20%, or 100%, and reporting that invites reading a
    pattern into noise - which is the exact mistake this column exists to avoid.
    """
    rows = read(log_path)
    labelled = _labelled(rows)
    out: dict = {
        "rows": len(rows),
        "labelled": len(labelled),
        "minimum": MIN_ROWS_FOR_STATS,
        "reportable": len(labelled) >= MIN_ROWS_FOR_STATS,
    }
    if not out["reportable"]:
        out["reason"] = (
            f"{len(labelled)} labelled outcome(s); need {MIN_ROWS_FOR_STATS} before a "
            "response rate means anything. Record outcomes with "
            "`resume-radar outcome <id> <value>` as replies arrive."
        )
        return out

    responded = [r for r in labelled if r["outcome"] in RESPONDED]
    out["response_rate"] = round(100.0 * len(responded) / len(labelled), 1)

    by_decile: dict[str, dict] = {}
    for row in labelled:
        try:
            fit = float(row.get("keyword_percent") or 0)
        except ValueError:
            continue
        bucket = f"{int(fit // 10) * 10}-{int(fit // 10) * 10 + 9}%"
        entry = by_decile.setdefault(bucket, {"n": 0, "responded": 0})
        entry["n"] += 1
        entry["responded"] += row["outcome"] in RESPONDED
    for entry in by_decile.values():
        entry["rate"] = round(100.0 * entry["responded"] / entry["n"], 1)
    out["by_fit_decile"] = dict(sorted(by_decile.items()))

    by_title: dict[str, dict] = {}
    for row in labelled:
        key = "title matched" if row.get("title_matched") == "True" else "no title match"
        entry = by_title.setdefault(key, {"n": 0, "responded": 0})
        entry["n"] += 1
        entry["responded"] += row["outcome"] in RESPONDED
    for entry in by_title.values():
        entry["rate"] = round(100.0 * entry["responded"] / entry["n"], 1)
    out["by_title_match"] = by_title
    return out
