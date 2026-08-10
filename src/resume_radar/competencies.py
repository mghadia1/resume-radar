"""Which transferable competencies your bullets actually evidence.

Recruiters screening students look for the same five signals regardless of the role:
analytical ability, communication, leadership, teamwork, and initiative. A resume can
be technically excellent and still show only one of them, which is the failure this
module is for - it is a *coverage* report, not a score.

Detection is lexical, which on a technical resume creates a specific and dangerous
error: the same verb means different things depending on its object. "Managed a team
of four" is leadership; "global Otsu managed 1/20" is an algorithm succeeding. The
first version of this module reported leadership on the second sentence.

So verbs are split in two. **Unambiguous** terms imply a person by themselves
("mentored", "collaborated"). **Ambiguous** terms only count when the bullet also
names something that makes the people-reading correct - a human object for leadership
and teamwork, an audience or a written artifact for communication.

Competencies that cannot be judged from bullets at all are reported as
``not_detectable`` rather than missing. Telling someone on a solo-project resume that
they "lack leadership" implies a fix that does not exist; telling them it cannot be
seen from their bullets is true and actionable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Terms that imply a person on their own.
UNAMBIGUOUS: dict[str, set[str]] = {
    "analytical": {
        "analyzed", "analysed", "evaluated", "measured", "benchmarked", "diagnosed",
        "modeled", "modelled", "profiled", "traced", "investigated", "quantified",
        "compared", "validated", "debugged", "tested", "assessed", "identified",
        "root cause", "baseline", "experiment",
    },
    "communication": {
        "presented", "documented", "explained", "published", "communicated",
        "briefed", "demonstrated", "documentation", "walkthrough", "write-up",
        "readme", "visualized", "visualised",
    },
    "leadership": {
        "mentored", "supervised", "spearheaded", "chaired", "coached", "delegated",
        "founded", "onboarded", "trained a team",
    },
    "teamwork": {
        "collaborated", "cross-functional", "team of", "peer review", "code review",
        "pair programming", "with the team", "alongside",
    },
    "initiative": {
        "built", "created", "designed", "launched", "initiated", "proposed",
        "prototyped", "automated", "shipped", "introduced", "started",
        "self-directed", "from scratch", "independently", "pitched",
    },
}

# Terms that need a qualifying object before they mean the competency. "Owned the
# retry logic" is code ownership, not people leadership.
AMBIGUOUS: dict[str, set[str]] = {
    "analytical": set(),
    "communication": {"wrote", "reported", "authored"},
    "leadership": {"led", "managed", "owned", "drove", "directed", "coordinated",
                   "organized", "organised", "headed"},
    "teamwork": {"supported", "contributed", "partnered", "paired", "reviewed",
                 "assisted", "coordinated"},
    "initiative": set(),
}

# What has to appear alongside an ambiguous term for it to count.
HUMAN_OBJECT_RE = re.compile(
    r"\b(?:team|teams|teammates?|engineers?|developers?|students?|interns?|peers?|"
    r"colleagues?|classmates?|group|club|chapter|cohort|staff|volunteers?|members?|"
    r"contributors?|analysts?|researchers?|people|others|"
    r"\d+\s+(?:people|members|engineers|students|developers|interns|volunteers))\b",
    re.IGNORECASE,
)
AUDIENCE_OR_ARTIFACT_RE = re.compile(
    r"\b(?:report|reports|paper|papers|documentation|docs?|readme|guide|guides|"
    r"blog|post|posts|article|presentation|talk|demo|slides?|thesis|proposal|"
    r"spec|specification|newsletter|summary|write-up|tutorial)\b",
    re.IGNORECASE,
)

QUALIFIERS = {
    "leadership": HUMAN_OBJECT_RE,
    "teamwork": HUMAN_OBJECT_RE,
    "communication": AUDIENCE_OR_ARTIFACT_RE,
}
# Competencies that simply cannot be read off a solo technical resume.
PEOPLE_COMPETENCIES = ("leadership", "teamwork")

ORDER = ("analytical", "communication", "leadership", "teamwork", "initiative")

EVIDENCED = "evidenced"
NOT_EVIDENCED = "not_evidenced"
NOT_DETECTABLE = "not_detectable"


def _mentions(term: str, text: str) -> bool:
    if " " in term or "-" in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


@dataclass
class Coverage:
    name: str
    status: str = NOT_EVIDENCED
    bullets: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)

    @property
    def covered(self) -> bool:
        return self.status == EVIDENCED

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "covered": self.covered,
            "count": len(self.bullets),
            "terms": self.terms,
            "example": self.bullets[0][:120] if self.bullets else "",
        }


@dataclass
class CompetencyReport:
    coverage: list[Coverage]

    @property
    def covered(self) -> list[str]:
        return [c.name for c in self.coverage if c.status == EVIDENCED]

    @property
    def missing(self) -> list[str]:
        return [c.name for c in self.coverage if c.status == NOT_EVIDENCED]

    @property
    def undetectable(self) -> list[str]:
        return [c.name for c in self.coverage if c.status == NOT_DETECTABLE]

    @property
    def percent(self) -> float:
        """Share of the competencies that *could* be judged which were evidenced."""
        judged = len(self.covered) + len(self.missing)
        return 100.0 * len(self.covered) / judged if judged else 0.0

    def to_dict(self) -> dict:
        return {
            "percent": round(self.percent, 1),
            "covered": self.covered,
            "missing": self.missing,
            "undetectable": self.undetectable,
            "detail": [c.to_dict() for c in self.coverage],
        }


def assess(bullets: list[str]) -> CompetencyReport:
    """Report which of the five competencies the bullets give evidence for."""
    lowered = [b.lower() for b in bullets]
    # If no bullet names a person at all, leadership and teamwork are not absent -
    # they are simply invisible to this method.
    any_humans = any(HUMAN_OBJECT_RE.search(b) for b in lowered)

    coverage = []
    for name in ORDER:
        qualifier = QUALIFIERS.get(name)
        hit_bullets: list[str] = []
        hit_terms: set[str] = set()

        for bullet, low in zip(bullets, lowered):
            found = {t for t in UNAMBIGUOUS[name] if _mentions(t, low)}
            if qualifier and qualifier.search(low):
                found |= {t for t in AMBIGUOUS[name] if _mentions(t, low)}
            if found:
                hit_bullets.append(bullet)
                hit_terms |= found

        if hit_bullets:
            status = EVIDENCED
        elif name in PEOPLE_COMPETENCIES and not any_humans:
            status = NOT_DETECTABLE
        else:
            status = NOT_EVIDENCED

        coverage.append(Coverage(name, status, hit_bullets, sorted(hit_terms)))
    return CompetencyReport(coverage)
