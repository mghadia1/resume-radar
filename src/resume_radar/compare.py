"""Diff two versions of a resume.

Keeping several drafts is normal; knowing what a rewrite actually cost you is not,
because a single percentage hides the trade. Tightening bullets to pass
``bullet_length`` can drop ``quantified`` if the numbers went out with the words, and
the total barely moves. This module reports per check, so the trade is visible.

Percentages are compared rather than raw points: a document where some checks are
inapplicable has a smaller denominator, so its point total is not comparable.
"""

from __future__ import annotations

from dataclasses import dataclass

LEFT, RIGHT, TIE = "left", "right", "tie"


@dataclass
class CheckDelta:
    key: str
    category: str
    left_points: int
    right_points: int
    left_message: str
    right_message: str

    @property
    def winner(self) -> str:
        if self.left_points > self.right_points:
            return LEFT
        if self.right_points > self.left_points:
            return RIGHT
        return TIE

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "category": self.category,
            "winner": self.winner,
            "left": {"points": self.left_points, "message": self.left_message},
            "right": {"points": self.right_points, "message": self.right_message},
        }


@dataclass
class Comparison:
    left_name: str
    right_name: str
    left_percent: float
    right_percent: float
    deltas: list[CheckDelta]
    categories: dict[str, dict]

    @property
    def winner(self) -> str:
        if self.left_percent > self.right_percent:
            return LEFT
        if self.right_percent > self.left_percent:
            return RIGHT
        return TIE

    @property
    def gains(self) -> list[CheckDelta]:
        """Checks the right-hand resume wins - what the newer draft fixed."""
        return [d for d in self.deltas if d.winner == RIGHT]

    @property
    def losses(self) -> list[CheckDelta]:
        """Checks the right-hand resume loses - what the rewrite cost."""
        return [d for d in self.deltas if d.winner == LEFT]

    def to_dict(self) -> dict:
        return {
            "left": {"name": self.left_name, "percent": round(self.left_percent, 1)},
            "right": {"name": self.right_name, "percent": round(self.right_percent, 1)},
            "winner": self.winner,
            "categories": self.categories,
            "changed": [d.to_dict() for d in self.deltas],
        }


def compare(left, right) -> Comparison:
    """Diff two :class:`~resume_radar.Analysis` results."""
    left_checks = {c.key: c for c in left.report.checks}
    right_checks = {c.key: c for c in right.report.checks}

    deltas = []
    for key in sorted(left_checks.keys() | right_checks.keys()):
        a, b = left_checks.get(key), right_checks.get(key)
        a_points = a.points if a else 0
        b_points = b.points if b else 0
        a_message = a.message if a else "check not run"
        b_message = b.message if b else "check not run"
        if a_points != b_points or a_message != b_message:
            deltas.append(CheckDelta(
                key=key,
                category=(a or b).category,
                left_points=a_points,
                right_points=b_points,
                left_message=a_message,
                right_message=b_message,
            ))

    left_cats = left.report.by_category()
    right_cats = right.report.by_category()
    categories = {}
    for name in left_cats:
        a_pct, b_pct = left_cats[name]["percent"], right_cats[name]["percent"]
        categories[name] = {
            "left": a_pct,
            "right": b_pct,
            "delta": round(b_pct - a_pct, 1),
            "winner": LEFT if a_pct > b_pct else RIGHT if b_pct > a_pct else TIE,
        }

    return Comparison(
        left_name=left.resume,
        right_name=right.resume,
        left_percent=left.report.percent,
        right_percent=right.report.percent,
        deltas=deltas,
        categories=categories,
    )
