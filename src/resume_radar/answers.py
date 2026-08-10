"""Prepare an application by hand, faster - without ever submitting anything.

This is the half of "auto-apply" that is safe to build. It reads a posting, prints the
questions it can see, prints your standard answers next to them, and drafts three
bullets from facts already on your resume. Then it stops.

It does not open a browser, fill a field, or submit a form, and it never will. Job
board terms prohibit automated submission; more importantly, work-authorisation
questions have to be answered correctly every single time, and a tool that fills them
unattended is a tool that will eventually get one wrong on your behalf.

The answers file is plain YAML-ish ``key: value`` text, parsed here rather than with
PyYAML so the package keeps its two-dependency footprint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ANSWERS = Path.home() / ".resume-radar" / "answers.yml"

TEMPLATE = """# ResumeRadar answer bank
# Your standard responses to the questions every application asks.
# Nothing here is ever submitted automatically - `resume-radar apply` prints it and
# stops. Check every answer before you paste it.

full_name: Your Name
email: you@example.edu
phone: (000) 000-0000
location: City, ST
work_authorization: F-1 student, eligible for CPT internships and OPT after graduation
requires_sponsorship: Yes, will require sponsorship in the future
citizenship: Not a US citizen
security_clearance: None
graduation_date: December 2027
degree: BS Computer Science
school: Arizona State University
gpa: 0.00/4.00
github: https://github.com/username
linkedin: https://linkedin.com/in/username
portfolio: https://example.com
availability: Available May through August 2027
notice_period: None, currently a student
salary_expectation: Open, happy to discuss the posted range
"""

# Questions an application form asks, and the answer key each maps to. Matching is by
# keyword because no two forms word these the same way.
QUESTION_PATTERNS: list[tuple[str, str]] = [
    (r"sponsor", "requires_sponsorship"),
    (r"work authoriz|authoriz(?:ed|ation) to work|legally authorized", "work_authorization"),
    (r"citizen", "citizenship"),
    (r"clearance", "security_clearance"),
    (r"graduat|expected degree|degree date", "graduation_date"),
    (r"\bgpa\b", "gpa"),
    (r"github", "github"),
    (r"linkedin", "linkedin"),
    (r"portfolio|personal (?:web)?site", "portfolio"),
    (r"available|start date|availability", "availability"),
    (r"notice period", "notice_period"),
    (r"salary|compensation expectation|desired pay", "salary_expectation"),
    (r"relocat", "location"),
]
COMPILED_QUESTIONS = [(re.compile(p, re.I), key) for p, key in QUESTION_PATTERNS]

# A line in a posting that reads like a question or a requirement. "?" is excluded
# from the question body so two questions in a row split rather than merging into one.
QUESTION_LINE_RE = re.compile(
    r"(?:^|(?<=[.;•\-?]))\s*([^.;•?]{12,160}\?)|"
    r"((?:must|should|required to|able to|willing to|eligible)[^.;•?]{5,140})",
    re.I,
)


class AnswersNotFound(FileNotFoundError):
    """Raised when no answer bank exists yet."""


def load(path: str | Path | None = None) -> dict[str, str]:
    """Read the answer bank. Supports ``key: value`` lines and ``#`` comments."""
    target = Path(path) if path is not None else DEFAULT_ANSWERS
    if not target.exists():
        raise AnswersNotFound(
            f"no answer bank at {target}. Create one with "
            "`resume-radar answers --init`."
        )

    answers: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip() if line.strip().startswith("#") else line
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip().strip('"\'')
        if key and value:
            answers[key] = value
    return answers


def write_template(path: str | Path | None = None) -> Path:
    """Create a starter answer bank. Never overwrites an existing one."""
    target = Path(path) if path is not None else DEFAULT_ANSWERS
    if target.exists():
        raise FileExistsError(f"{target} already exists; edit it rather than replacing it")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE, encoding="utf-8")
    return target


def find_questions(description: str) -> list[str]:
    """Pull question-like and requirement-like lines out of a posting."""
    found: list[str] = []
    for match in QUESTION_LINE_RE.finditer(description):
        text = (match.group(1) or match.group(2) or "").strip()
        text = re.sub(r"\s+", " ", text)
        if text and text not in found:
            found.append(text)
    return found[:25]


@dataclass
class PreparedAnswer:
    question: str
    key: str
    answer: str


@dataclass
class Preparation:
    """Everything needed to fill a form by hand, and nothing that fills it."""

    company: str
    title: str
    url: str
    answers: list[PreparedAnswer] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    pitch: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "answers": [{"question": a.question, "key": a.key, "answer": a.answer}
                        for a in self.answers],
            "unmatched_questions": self.unmatched,
            "pitch": self.pitch,
            "missing_skills": self.missing_skills,
            "blockers": self.blockers,
        }


def build_pitch(posting, doc, match, limit: int = 3) -> list[str]:
    """Pick the three resume bullets that best match this posting.

    Deliberately *selection*, not generation: every line returned is already on the
    resume, word for word. Writing new claims to fit a posting is how a resume stops
    being defensible in the interview it wins.
    """
    wanted = set(match.hard_matched) | set(match.matched) if match else set()
    scored = []
    for bullet in doc.bullets:
        terms = {t for t in wanted if t and t in bullet.lower()}
        if terms:
            scored.append((len(terms), -len(bullet.split()), bullet))
    scored.sort(reverse=True)
    return [bullet for _, _, bullet in scored[:limit]]


def prepare(posting, doc, match, bank: dict[str, str]) -> Preparation:
    """Assemble everything needed to fill this application by hand."""
    questions = find_questions(posting.description)

    prepared, unmatched = [], []
    used: set[str] = set()
    for question in questions:
        for pattern, key in COMPILED_QUESTIONS:
            if pattern.search(question) and key in bank and key not in used:
                prepared.append(PreparedAnswer(question, key, bank[key]))
                used.add(key)
                break
        else:
            unmatched.append(question)

    # Work authorisation is asked on essentially every form, whether or not the
    # posting text mentions it. Surface it even when nothing matched.
    for key in ("work_authorization", "requires_sponsorship"):
        if key not in used and key in bank:
            prepared.append(PreparedAnswer("(asked on most application forms)",
                                           key, bank[key]))

    return Preparation(
        company=posting.company,
        title=posting.title,
        url=posting.url,
        answers=prepared,
        unmatched=unmatched[:10],
        pitch=build_pitch(posting, doc, match),
        missing_skills=match.priority[:8] if match else [],
        blockers=list(posting.blockers),
    )
