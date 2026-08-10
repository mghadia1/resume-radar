"""Render the daily posting digest as markdown.

Markdown because the digest is read, not parsed: it renders in a terminal, an email,
a GitHub Actions summary, or a note-taking app without any of them needing to know
about this tool.

The filtered-out postings are always included, collapsed, with the reason each was
screened. A screener that silently drops things cannot be checked, and this one will
be wrong sometimes - a posting saying "we cannot sponsor H-1B, but F-1 CPT is fine"
trips the no-sponsorship pattern and should not.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

#: How many screened-out postings to name before summarising the rest. A real run
#: drops thousands; an unbounded list is as unreadable as no list at all.
SAMPLE = 12

BLOCKER_LABELS = {
    "citizenship": "requires US citizenship",
    "clearance": "requires a security clearance",
    "no_sponsorship": "states no visa sponsorship",
    "export_control": "ITAR / export-controlled",
}


def _blockers(posting) -> str:
    return ", ".join(BLOCKER_LABELS.get(b, b) for b in posting.blockers)


def _level_label(title: str) -> str:
    from .jobs import classify_level

    return classify_level(title)


def render(new_postings: list, *, already_seen: int = 0, filtered: list | None = None,
           on: date | None = None, boards_failed: list[str] | None = None,
           backfill: list | None = None, off_level: list | None = None,
           wrong_level: int = 0, short: int = 0, target: int = 0) -> str:
    """Build the markdown digest for one run.

    ``backfill`` holds postings carried over from an earlier run to reach ``target``.
    They are rendered in their own section and never counted as new - a slow day must
    read as a slow day, not as ten fresh openings.
    """
    filtered = filtered or []
    backfill = backfill or []
    off_level = off_level or []
    wrong_level = wrong_level or len(off_level)
    boards_failed = boards_failed or []
    when = (on or date.today()).isoformat()

    lines = [f"# Internship shortlist — {when}", ""]
    summary = [f"{len(new_postings)} new", f"{already_seen} already seen"]
    if backfill:
        summary.append(f"{len(backfill)} carried over")
    summary.append(f"{len(filtered)} filtered as ineligible")
    if wrong_level:
        summary.append(f"{wrong_level} wrong level")
    lines.append(", ".join(summary))

    if short and target:
        lines += ["", f"> Only {target - short} of {target} slots filled. Add more "
                      f"boards to `boards.txt`, or lower `--min-fit`.",
                  "> Internship postings are seasonal: most Summer roles open between "
                  "August and October, so a quiet run in late spring or midsummer is "
                  "the calendar, not your configuration."]

    if boards_failed:
        lines += ["", f"> {len(boards_failed)} board(s) could not be reached this run: "
                      f"{', '.join(boards_failed)}"]

    if not new_postings and not backfill:
        lines += ["", "Nothing new today."]
    else:
        lines.append("")
        for posting in new_postings:
            flag = "  [title match]" if posting.match and posting.match.title_matched else ""
            lines.append(f"## {posting.fit:.0f}% · {posting.company} — {posting.title}{flag}")
            lines.append(f"{posting.location or 'location not stated'} · {posting.url}")
            if posting.match:
                missing = ", ".join(posting.match.priority[:8])
                have = ", ".join(posting.match.hard_matched[:8])
                if missing:
                    lines.append(f"Missing: {missing}")
                if have:
                    lines.append(f"Have: {have}")
            if posting.sponsorship_friendly:
                lines.append("_Posting mentions sponsorship is available._")
            lines.append("")

    if backfill:
        lines += ["", "---", "",
                  "## Still open from earlier runs",
                  "",
                  "_Carried over to fill the shortlist. You have seen these before._",
                  ""]
        for posting in backfill:
            lines.append(f"- **{posting.fit:.0f}%** {posting.company} — "
                         f"{posting.title} · {posting.url}")

    if off_level:
        # Listed, not just counted: the level filter is a heuristic on the title, and a
        # role called plainly "Software Engineer" carries no level word and is dropped.
        # Capped, because a real run drops thousands - 29 boards produced 5,845 - and a
        # digest nobody scrolls through is as unauditable as a bare number.
        lines += [
            "",
            "<details>",
            f"<summary>{len(off_level)} dropped as the wrong level — widen with "
            "--levels if one of these looks right</summary>",
            "",
        ]
        for posting in off_level[:SAMPLE]:
            lines.append(f"- **{posting.company} — {posting.title}** · reads as "
                         f"`{_level_label(posting.title)}` · {posting.url}")
        if len(off_level) > SAMPLE:
            counts = Counter(_level_label(p.title) for p in off_level)
            breakdown = ", ".join(f"{n} {name}" for name, n in counts.most_common())
            lines.append(f"- _…and {len(off_level) - SAMPLE} more: {breakdown}_")
        lines += ["", "</details>"]

    if filtered:
        lines += [
            "",
            "<details>",
            f"<summary>{len(filtered)} filtered as ineligible — check these if the "
            "screener looks wrong</summary>",
            "",
        ]
        for posting in filtered[:SAMPLE]:
            lines.append(f"- **{posting.company} — {posting.title}** · {_blockers(posting)} "
                         f"· {posting.url}")
        if len(filtered) > SAMPLE:
            counts = Counter(b for p in filtered for b in p.blockers)
            breakdown = ", ".join(f"{n} {BLOCKER_LABELS.get(k, k)}"
                                  for k, n in counts.most_common())
            lines.append(f"- _…and {len(filtered) - SAMPLE} more: {breakdown}_")
        lines += ["", "</details>"]

    lines += [
        "",
        "---",
        "Fit is keyword overlap between your resume and the posting. It is not a "
        "probability of an interview and does not predict one.",
    ]
    return "\n".join(lines) + "\n"
