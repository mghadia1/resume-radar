"""Find resume files inside a folder the user has granted access to.

Scoring is heuristic and transparent: filename hints and document content each
contribute, and the caller can inspect ``reasons`` to see why a file was picked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "archive", ".pytest_cache"}

NAME_HINTS = ("resume", "cv", "curriculum")
CONTENT_MARKERS = (
    "education", "experience", "skills", "projects", "university", "college",
    "bachelor", "internship", "gpa",
)


@dataclass
class Candidate:
    path: Path
    confidence: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "confidence": round(self.confidence, 2),
            "reasons": self.reasons,
        }


def _score_name(path: Path) -> tuple[float, list[str]]:
    stem = path.stem.lower()
    if any(h in stem for h in NAME_HINTS):
        return 0.5, [f"filename contains a resume keyword ({path.name})"]
    return 0.0, []


def _score_content(path: Path) -> tuple[float, list[str]]:
    from .extract import ExtractionError, load

    try:
        doc = load(path)
    except ExtractionError:
        return 0.0, ["file could not be parsed"]

    low = doc.text.lower()
    hits = [m for m in CONTENT_MARKERS if m in low]
    reasons = []
    score = 0.0

    if len(hits) >= 3:
        score += 0.4
        reasons.append(f"contains resume sections: {', '.join(hits[:4])}")
    if re.search(r"[\w.+-]+@[\w-]+\.\w+", doc.text):
        score += 0.1
        reasons.append("contains an email address")
    if doc.pages <= 2:
        score += 0.05
        reasons.append(f"{doc.pages} page(s)")
    return score, reasons


def find(folder: str | Path, *, threshold: float = 0.5, limit: int = 50) -> list[Candidate]:
    """Return likely resumes under ``folder``, most confident first.

    ``threshold`` is the minimum confidence to include. A file whose *name* says
    resume passes on that alone; other files must look like one on the inside.
    """
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {folder}")

    found: list[Candidate] = []
    for path in sorted(root.rglob("*")):
        if len(found) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in SUPPORTED:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue

        score, reasons = _score_name(path)
        content_score, content_reasons = _score_content(path)
        score += content_score
        reasons += content_reasons

        if score >= threshold:
            found.append(Candidate(path, min(score, 1.0), reasons))

    return sorted(found, key=lambda c: -c.confidence)
