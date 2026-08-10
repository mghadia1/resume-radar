"""ResumeRadar - deterministic resume scoring with optional model-assisted rewrites."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import checks, competencies, discover, extract, keywords, suggest, tracker
from . import compare as compare_mod
from .checks import Report
from .competencies import CompetencyReport
from .extract import Document, ExtractionError
from .keywords import KeywordMatch
from .suggest import Suggestion

__version__ = "0.3.0"

__all__ = [
    "analyze", "Analysis", "Document", "ExtractionError", "Report",
    "checks", "competencies", "compare_mod", "discover", "extract", "keywords",
    "suggest", "tracker",
]


@dataclass
class Analysis:
    """One scored resume.

    Holds the real objects, and converts to JSON only when asked. An earlier version
    returned a dict with the ``Report`` smuggled in under a private key, which meant
    ``json.dumps`` on the documented "JSON-ready dict" raised ``TypeError`` unless the
    caller knew to delete it first.
    """

    resume: str
    pages: int
    words: int
    bullets: int
    report: Report
    competencies: CompetencyReport
    keyword_match: KeywordMatch | None = None
    suggestions: list[Suggestion] = field(default_factory=list)
    suggestions_error: str = ""

    @property
    def percent(self) -> float:
        return self.report.percent

    @property
    def score(self) -> int:
        return self.report.score

    def to_dict(self) -> dict:
        """A dict that ``json.dumps`` accepts without further work."""
        out: dict = {
            "resume": self.resume,
            "pages": self.pages,
            "words": self.words,
            "bullets": self.bullets,
            **self.report.to_dict(),
            "competencies": self.competencies.to_dict(),
        }
        if self.keyword_match is not None:
            out["keywords"] = self.keyword_match.to_dict()
        if self.suggestions or self.suggestions_error:
            out["suggestions"] = [s.to_dict() for s in self.suggestions]
        if self.suggestions_error:
            out["suggestions_error"] = self.suggestions_error
        return out


def analyze(path: str | Path, job_description: str = "", *,
            with_suggestions: bool = False, api_key: str | None = None) -> Analysis:
    """Score one resume.

    ``job_description`` enables keyword matching. ``with_suggestions`` adds a model
    pass; if no key is available the suggestion step is reported as unavailable
    rather than failing the run.
    """
    doc = extract.load(path)
    report = checks.run(doc)

    result = Analysis(
        resume=str(doc.path),
        pages=doc.pages,
        words=doc.words,
        bullets=len(doc.bullets),
        report=report,
        competencies=competencies.assess(doc.bullets),
    )

    if job_description.strip():
        result.keyword_match = keywords.compare(doc.text, job_description)

    if with_suggestions:
        targets = suggest.weak_bullets(doc, report)
        try:
            result.suggestions = suggest.rewrite(targets, api_key=api_key)
        except suggest.SuggestionUnavailable as exc:
            result.suggestions_error = str(exc)

    return result
