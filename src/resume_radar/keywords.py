"""Match a resume against a job description.

Not every missing word costs the same. A posting that says "PostgreSQL" three times
and "communication" once is telling you which one it cares about, so terms are
classified and weighted rather than counted flat:

* **hard skills** - named tools, languages, and techniques. Weighted heaviest,
  because they are the terms a keyword filter is actually configured with.
* **job title** - checked separately. A posting's own title appearing on the resume
  is the single strongest signal in this module, so it carries its own weight.
* **soft skills** - real but unfilterable; weighted low.
* **everything else** - ordinary posting vocabulary, weighted lowest.

The output is still a list of terms you are missing, ordered by how much the posting
leans on them, because that is the actionable part.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STOPWORDS = {
    "and", "or", "the", "a", "an", "of", "for", "with", "to", "in", "on", "at", "as",
    "is", "are", "be", "by", "from", "that", "this", "you", "your", "we", "our", "will",
    "have", "has", "such", "other", "including", "etc", "using", "use", "used", "able",
    "ability", "strong", "excellent", "good", "years", "year", "plus", "must", "should",
    "who", "what", "their", "them", "they", "not", "but", "can", "may", "any", "all",
    "into", "than", "then", "these", "those", "when", "while", "about", "across", "also",
    "more", "most", "some", "each", "well", "work", "working", "role", "team", "teams",
    "candidate", "candidates", "applicant", "position", "opportunity", "join", "help",
    "looking", "seeking", "required", "requirements", "preferred", "qualifications",
    "responsibilities", "including", "ideal", "great", "new", "one", "would", "like",
}

# Multi-word technologies that must be matched as a unit, not token by token.
PHRASES = [
    "machine learning", "deep learning", "computer vision", "natural language processing",
    "large language model", "reinforcement learning", "neural network", "data structures",
    "linear algebra", "object oriented", "object-oriented", "unit test", "unit testing",
    "version control", "continuous integration", "rest api", "distributed systems",
    "data pipeline", "model training", "software engineering", "problem solving",
    "test driven", "code review", "web development", "back end", "front end",
    "full stack", "cloud computing", "data analysis", "time series", "image processing",
]

# Named tools and techniques. A term in here is treated as a hard skill; anything
# else that looks technical (contains a digit, +, or #) is treated as one too.
HARD_SKILLS = {
    "python", "java", "javascript", "typescript", "c", "c++", "c#", "go", "rust", "ruby",
    "kotlin", "swift", "scala", "matlab", "r", "sql", "nosql", "html", "css", "bash",
    "shell", "perl", "php", "dart", "assembly", "verilog",
    "react", "angular", "vue", "svelte", "node", "nodejs", "express", "django", "flask",
    "fastapi", "spring", "rails", "next.js", "jquery", "bootstrap", "tailwind",
    "postgresql", "postgres", "mysql", "sqlite", "mongodb", "redis", "cassandra",
    "dynamodb", "elasticsearch", "snowflake", "bigquery", "kafka", "rabbitmq",
    "docker", "kubernetes", "terraform", "ansible", "jenkins", "github", "gitlab", "git",
    "aws", "azure", "gcp", "linux", "unix", "nginx", "graphql", "grpc", "rest",
    "pytorch", "tensorflow", "keras", "numpy", "scipy", "pandas", "sklearn",
    "scikit-learn", "matplotlib", "opencv", "cuda", "huggingface", "transformers",
    "jupyter", "spark", "hadoop", "airflow", "dbt", "tableau", "powerbi",
    "pytest", "junit", "selenium", "cypress", "jest", "mocha",
    "ros", "gazebo", "slam", "moveit", "simulink", "solidworks",
    "machine learning", "deep learning", "computer vision", "neural network",
    "natural language processing", "reinforcement learning", "data structures",
    "algorithms", "linear algebra", "statistics", "optimization", "regression",
    "classification", "segmentation", "clustering", "rest api", "microservices",
    "distributed systems", "data pipeline", "unit testing", "continuous integration",
    "version control", "object-oriented", "agile", "scrum", "ci/cd", "api", "apis",
    "backend", "frontend", "devops", "etl", "oauth", "websocket", "latency", "caching",
}

SOFT_SKILLS = {
    "communication", "communicate", "collaboration", "collaborative", "collaborate",
    "leadership", "lead", "mentoring", "mentor", "teamwork", "interpersonal",
    "organized", "organization", "adaptable", "adaptability", "curious", "curiosity",
    "motivated", "motivation", "independent", "independently", "initiative",
    "detail-oriented", "creative", "creativity", "analytical", "passionate", "passion",
    "proactive", "reliable", "flexible", "enthusiasm", "ownership", "accountability",
    "problem solving", "critical thinking", "time management", "fast-paced",
}

# The title weight is the best-evidenced number here: Jobscan's study of 2.5M+
# applications found a 10.6x higher interview rate when the resume title matched the
# posting's. The hard-skill weight models what an automated keyword filter screens on,
# which is not the same as what a recruiter values - their own recruiter survey found
# most weigh hard and soft skills equally. See docs/scoring.md. Both numbers are
# judgement calls, not measured constants.
WEIGHTS = {"hard": 3.0, "soft": 1.0, "other": 1.0}
TITLE_WEIGHT = 6.0

TOKEN_RE = re.compile(r"[a-z][a-z0-9+#./_-]*")
TECHNICAL_RE = re.compile(r"[0-9+#]")

# Postings label the title in a handful of predictable ways; otherwise the first
# non-empty line is the title far more often than not.
TITLE_LABEL_RE = re.compile(
    r"(?im)^\s*(?:job\s+)?title\s*[:\-]\s*(.+)$|^\s*position\s*[:\-]\s*(.+)$"
)
TITLE_NOISE_RE = re.compile(
    r"(?i)\b(intern(ship)?|co-?op|full[- ]time|part[- ]time|remote|hybrid|onsite|"
    r"20\d\d|summer|fall|spring|winter|[IVX]+|senior|junior|entry[- ]level|new grad)\b"
)


@dataclass
class KeywordMatch:
    matched: list[str]
    missing: list[str]
    percent: float
    hard_missing: list[str] = field(default_factory=list)
    soft_missing: list[str] = field(default_factory=list)
    hard_matched: list[str] = field(default_factory=list)
    title: str = ""
    title_matched: bool = False
    emphasis: dict[str, int] = field(default_factory=dict)

    @property
    def priority(self) -> list[str]:
        """Missing hard skills, most-emphasised in the posting first."""
        return sorted(self.hard_missing, key=lambda t: (-self.emphasis.get(t, 1), t))

    def to_dict(self) -> dict:
        return {
            "percent": round(self.percent, 1),
            "matched": self.matched,
            "missing": self.missing,
            "hard_matched": self.hard_matched,
            "hard_missing": self.hard_missing,
            "soft_missing": self.soft_missing,
            "priority": self.priority,
            "title": self.title,
            "title_matched": self.title_matched,
            "emphasis": self.emphasis,
        }


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9+#./_ -]", " ", text.lower())


def tokenize(text: str) -> set[str]:
    """Reduce text to comparable terms, keeping known multi-word phrases intact."""
    return set(_counted(text))


def _counted(text: str) -> dict[str, int]:
    """Same reduction as :func:`tokenize`, but keeping how often each term appears."""
    flat = _normalize(text)
    counts: dict[str, int] = {}

    for phrase in PHRASES:
        hits = flat.count(phrase)
        if hits:
            counts[phrase] = hits
            flat = flat.replace(phrase, " ")

    for token in TOKEN_RE.findall(flat):
        token = token.strip("./-_")
        # Keep known short skills (C, R, Go, C#) without admitting short noise.
        if (len(token) > 2 or token in HARD_SKILLS) and token not in STOPWORDS:
            counts[token] = counts.get(token, 0) + 1
    return counts


def classify(term: str) -> str:
    """Sort one term into ``hard``, ``soft``, or ``other``."""
    if term in HARD_SKILLS or TECHNICAL_RE.search(term):
        return "hard"
    if term in SOFT_SKILLS:
        return "soft"
    return "other"


def job_title(job_description: str) -> str:
    """Pull the posting's title out of a job description, best effort."""
    match = TITLE_LABEL_RE.search(job_description)
    if match:
        return (match.group(1) or match.group(2)).strip()
    for line in job_description.splitlines():
        line = line.strip(" \t-–—•*#")
        if len(line.split()) <= 12 and len(line) > 2:
            return line
        if line:
            return ""
    return ""


def title_present(title: str, resume_text: str) -> bool:
    """True when the posting's title appears on the resume, ignoring seniority noise.

    "Software Engineer Intern - Summer 2027" and "Software Engineer" are the same
    claim as far as a recruiter scanning for the title is concerned.
    """
    core = TITLE_NOISE_RE.sub(" ", title.lower())
    core = re.sub(r"[^a-z ]", " ", core)
    words = [w for w in core.split() if len(w) > 2 and w not in STOPWORDS]
    if not words:
        return False
    haystack = re.sub(r"[^a-z ]", " ", resume_text.lower())
    haystack = " ".join(haystack.split())
    return " ".join(words) in haystack


def compare(resume_text: str, job_description: str) -> KeywordMatch:
    """Report which job-description terms the resume does and does not contain."""
    resume_terms = tokenize(resume_text)
    jd_counts = _counted(job_description)
    if not jd_counts:
        return KeywordMatch([], [], 0.0)

    matched = sorted(t for t in jd_counts if t in resume_terms)
    missing = sorted(t for t in jd_counts if t not in resume_terms)

    title = job_title(job_description)
    has_title = bool(title) and title_present(title, resume_text)

    earned = possible = 0.0
    for term in jd_counts:
        weight = WEIGHTS[classify(term)]
        possible += weight
        if term in resume_terms:
            earned += weight
    if title:
        possible += TITLE_WEIGHT
        if has_title:
            earned += TITLE_WEIGHT

    return KeywordMatch(
        matched=matched,
        missing=missing,
        percent=100.0 * earned / possible if possible else 0.0,
        hard_missing=[t for t in missing if classify(t) == "hard"],
        soft_missing=[t for t in missing if classify(t) == "soft"],
        hard_matched=[t for t in matched if classify(t) == "hard"],
        title=title,
        title_matched=has_title,
        emphasis={t: n for t, n in jd_counts.items() if n > 1},
    )
