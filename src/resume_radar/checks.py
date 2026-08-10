"""Deterministic resume checks.

Every check returns a pass/fail plus the evidence behind it, so a low score can
always be traced to a specific line rather than an opaque number. No network calls
and no model inference happen here - the same input always produces the same score.

Checks are grouped into four categories so the output says *what kind* of problem a
resume has, not just how many points it lost:

``ats``      the document parses and the facts can be found
``impact``   the bullets show outcomes rather than duties
``brevity``  the document is short enough to survive a skim
``style``    word choice and consistency
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import Document, Entry, group_entries

# Bullets between 15 and 30 words survive a recruiter's first scan; longer ones get
# skipped. See docs/scoring.md for where these thresholds come from.
BULLET_WORD_CEILING = 30
LONG_BULLET_SHARE = 0.25
QUANTIFIED_SHARE = 0.60
STRONG_VERB_SHARE = 0.80
VERB_VARIETY_SHARE = 0.60
# A one-page resume that says enough without becoming a wall of text.
WORD_COUNT_FLOOR = 400
WORD_COUNT_CEILING = 800

CATEGORIES = ("ats", "impact", "brevity", "style")

STRONG_VERBS = {
    "achieved", "actuated", "added", "analyzed", "architected", "automated", "benchmarked",
    "built", "calibrated", "chained", "classified", "collaborated", "compared", "created",
    "cut", "debugged", "delivered", "deployed", "designed", "detected", "developed",
    "diagnosed", "documented", "drove", "engineered", "evaluated", "executed", "extended",
    "fit", "generated", "implemented", "improved", "increased", "integrated", "launched",
    "led", "locked", "mapped", "measured", "migrated", "navigated", "optimized", "planned",
    "preserved", "profiled", "prototyped", "ran", "reached", "rebuilt", "reduced",
    "refactored", "resolve", "resolved", "recovered", "run", "scaled", "selected",
    "shipped", "simulated", "solved", "streamlined", "tested", "tracked", "trained",
    "traced", "tuned", "validated", "wired", "wrote", "document", "maintain", "maintained",
}

WEAK_PHRASES = [
    "team player", "hard working", "hardworking", "detail oriented", "detail-oriented",
    "go-getter", "think outside the box", "responsible for", "duties included",
    "worked on", "helped with", "familiar with", "exposure to", "assisted with",
    "participated in", "involved in", "tasked with", "various tasks", "some experience",
    "results-driven", "self-starter", "synergy", "value add", "thought leader",
]

PRONOUN_RE = re.compile(r"\b(I|me|my|mine|we|our|ours)\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*'?\d{2,4}",
    re.IGNORECASE,
)
# "Calculus I--III" and similar roman numerals must not read as the pronoun "I".
# Ranges and multi-letter numerals are never pronouns, so they go unconditionally.
ROMAN_RE = re.compile(r"\b(?:[IVX]{1,4}\s*[-–—]\s*[IVX]{1,4}|[IVX]{2,4})\b")
# A lone "I" is ambiguous: "Physics I" is a course, "I built" is a pronoun. Treat it
# as a course number unless a lowercase word follows it *on the same line*, which is
# what a sentence looks like. The same-line requirement matters: "Physics I" ending a
# line above a lowercase continuation would otherwise read as a pronoun.
# "and"/"or" are excluded so "Calculus I and Statistics" stays a course.
COURSE_I_RE = re.compile(r"\bI\b(?![ \t]+(?!and\b|or\b)[a-z])")

# Passive voice buries the actor, which on a resume is you. "was built by the team"
# costs you the credit the bullet exists to claim.
#
# Matching "-en" generically would catch adverbs ("is often", "is when"), so irregular
# participles are listed explicitly instead of pattern-matched.
IRREGULAR_PARTICIPLES = [
    "built", "rebuilt", "written", "rewritten", "driven", "given", "taken", "seen",
    "known", "shown", "drawn", "grown", "thrown", "chosen", "hidden", "broken",
    "done", "made", "led", "held", "kept", "sent", "spent", "put", "set", "run",
    "brought", "bought", "found", "told", "left", "won", "begun", "dealt",
]
PASSIVE_RE = re.compile(
    r"\b(?:was|were|been|being|is|are|am|be)\s+(?:\w+ly\s+)?"
    r"(\w+ed|" + "|".join(IRREGULAR_PARTICIPLES) + r")\b",
    re.IGNORECASE,
)
# Words ending in -ed that follow "be" as adjectives rather than forming a passive.
PASSIVE_ALLOW = {
    "based", "advanced", "involved", "interested", "excited", "motivated",
    "dedicated", "detailed", "related", "skilled", "experienced", "focused",
    "committed", "qualified", "limited", "required", "preferred", "expected",
}
# "rather than being tuned against it" is a passive gerund and reads correctly; only
# a passive main clause actually hides who did the work.
GERUND_CONTEXT_RE = re.compile(
    r"\b(?:than|without|instead of|after|before|despite|avoid|avoids|avoided|"
    r"by|for|of|from)\s+$"
)

# Canonical spellings of tools whose casing a recruiter notices. Only terms whose
# lowercase form is not an ordinary English word are listed, so "excel", "go",
# "rust", and "spark" are deliberately absent - flagging those would be noise.
CANONICAL_TERMS = {
    "javascript": "JavaScript", "typescript": "TypeScript", "github": "GitHub",
    "gitlab": "GitLab", "pytorch": "PyTorch", "tensorflow": "TensorFlow",
    "numpy": "NumPy", "scipy": "SciPy", "postgresql": "PostgreSQL", "mysql": "MySQL",
    "mongodb": "MongoDB", "sqlite": "SQLite", "kubernetes": "Kubernetes",
    "docker": "Docker", "linux": "Linux", "macos": "macOS", "ios": "iOS",
    "android": "Android", "django": "Django", "flask": "Flask", "fastapi": "FastAPI",
    "react": "React", "angular": "Angular", "kotlin": "Kotlin", "python": "Python",
    "java": "Java", "matlab": "MATLAB", "jupyter": "Jupyter", "bash": "Bash",
    "jenkins": "Jenkins", "terraform": "Terraform", "ansible": "Ansible",
    "redis": "Redis", "kafka": "Kafka", "hadoop": "Hadoop", "tableau": "Tableau",
    "figma": "Figma", "opencv": "OpenCV", "cuda": "CUDA", "keras": "Keras",
    "firebase": "Firebase", "nginx": "Nginx", "graphql": "GraphQL",
}
URL_OR_EMAIL_RE = re.compile(r"\S+@\S+|https?://\S+|\b[\w-]+\.(?:com|org|net|io|dev|edu)\S*")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")

# Distinct ways of writing a date. Mixing them is the kind of inconsistency a
# recruiter reads as carelessness and a parser reads as two different fields.
DATE_STYLES = {
    "Month YYYY": re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b"
    ),
    "Mon 'YY": re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*'\d{2}\b"
    ),
    # A four-digit year is required: "60/20/20 splits" and "20/20 trials" are ratios,
    # and an unanchored \d{1,2}/\d{2} pattern reads them as dates.
    "MM/YYYY": re.compile(r"\b(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}\b"),
    # Likewise the month must be 01-12, so an academic year like "2024-25" is not a date.
    "YYYY-MM": re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])\b"),
}


# Below this, a document has too little prose for the "contains nothing bad" checks
# to mean anything. They are marked not-applicable instead of quietly passing.
MIN_WORDS_FOR_LANGUAGE = 50


@dataclass
class Check:
    key: str
    passed: bool
    points: int
    max_points: int
    message: str
    evidence: list[str] = field(default_factory=list)
    category: str = "ats"
    applicable: bool = True


@dataclass
class Report:
    checks: list[Check]

    @property
    def score(self) -> int:
        return sum(c.points for c in self.checks)

    @property
    def max_score(self) -> int:
        return sum(c.max_points for c in self.checks)

    @property
    def percent(self) -> float:
        return 100.0 * self.score / self.max_score if self.max_score else 0.0

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.applicable]

    @property
    def skipped(self) -> list[Check]:
        return [c for c in self.checks if not c.applicable]

    def by_category(self) -> dict[str, dict]:
        """Score each category separately, so a weak area is visible at a glance."""
        out: dict[str, dict] = {}
        for name in CATEGORIES:
            group = [c for c in self.checks if c.category == name]
            earned = sum(c.points for c in group)
            possible = sum(c.max_points for c in group)
            out[name] = {
                "score": earned,
                "max_score": possible,
                "percent": round(100.0 * earned / possible, 1) if possible else 0.0,
                "failed": [c.key for c in group if not c.passed],
            }
        return out

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "max_score": self.max_score,
            "percent": round(self.percent, 1),
            "categories": self.by_category(),
            "checks": [
                {
                    "key": c.key,
                    "category": c.category,
                    "passed": c.passed,
                    "applicable": c.applicable,
                    "points": c.points,
                    "max_points": c.max_points,
                    "message": c.message,
                    "evidence": c.evidence,
                }
                for c in self.checks
            ],
        }


def _check(key: str, passed: bool, pts: int, msg: str, evidence=None,
           category: str = "ats") -> Check:
    return Check(key, passed, pts if passed else 0, pts, msg, evidence or [], category)


def _skip(key: str, msg: str, category: str = "ats") -> Check:
    """A check with nothing to inspect. Scores 0 out of 0, so it cannot inflate a total.

    An absence-based check is trivially satisfied by an empty document: a file
    containing one character has no pronouns, no filler, and no passive voice. Passing
    it points would mean an unreadable file scores the same as a careful one.
    """
    return Check(key, True, 0, 0, msg, [], category, applicable=False)


def find_passive(text: str) -> list[str]:
    """Return passive-voice fragments, skipping the common false positives.

    Two exclusions, both learned from real resumes. Adjectives that merely look like
    participles ("is detailed") are allowed. So is a passive gerund after a
    subordinating word - "evaluated once rather than being tuned against it" is
    deliberate, correct English, and rewriting it in the active voice would be worse.
    """
    found = []
    for match in PASSIVE_RE.finditer(text):
        if match.group(1).lower() in PASSIVE_ALLOW:
            continue
        preceding = text[max(0, match.start() - 30):match.start()].lower()
        if GERUND_CONTEXT_RE.search(preceding):
            continue
        found.append(match.group(0).strip())
    return found


def find_miscased(text: str) -> list[str]:
    """Return tools written with non-standard capitalisation, e.g. 'github' or 'Numpy'.

    URLs and email addresses are stripped first: ``github.com/mghadia1`` is correct
    lowercase and must not be reported.
    """
    stripped = URL_OR_EMAIL_RE.sub(" ", text)
    seen: dict[str, str] = {}
    for word in WORD_RE.findall(stripped):
        canonical = CANONICAL_TERMS.get(word.lower())
        # An all-caps word is a heading or an acronym styling choice, not a typo.
        if canonical and word != canonical and not word.isupper():
            seen[word] = canonical
    return [f"{wrong} should be {right}" for wrong, right in sorted(seen.items())]


# Verbs whose past tense is not formed with -ed.
PAST_IRREGULAR = {
    "built", "rebuilt", "ran", "led", "wrote", "rewrote", "drove", "made", "took",
    "kept", "sent", "spent", "won", "began", "brought", "bought", "found", "told",
    "left", "grew", "drew", "threw", "chose", "held", "sold", "taught", "met",
    "paid", "oversaw", "undertook", "gave", "became", "understood", "dealt",
}
# Verbs spelled the same in both tenses. Judging them would be guessing.
TENSE_AMBIGUOUS = {
    "fit", "put", "set", "cut", "hit", "cost", "read", "spread", "split", "shut",
    "let", "bet", "quit", "cast", "hurt", "upset",
}


def verb_tense(bullet: str) -> str:
    """Classify a bullet's opening verb as ``past``, ``present``, or ``ambiguous``."""
    words = bullet.split()
    if not words:
        return "ambiguous"
    opener = words[0].lower().strip("*:,.")
    if opener in TENSE_AMBIGUOUS:
        return "ambiguous"
    if opener in PAST_IRREGULAR or opener.endswith("ed"):
        return "past"
    return "present"


def tense_problems(entries: list[Entry]) -> list[str]:
    """Report entries whose bullets disagree about tense, or use the wrong one.

    Two distinct defects: an entry that mixes past and present reads as careless, and
    a role you have left written in the present tense reads as a role you still hold.
    A *current* role in the present tense is correct and is never flagged.
    """
    problems = []
    for entry in entries:
        tenses = {verb_tense(b) for b in entry.bullets} - {"ambiguous"}
        if not tenses:
            continue
        label = entry.header[:60]
        if len(tenses) > 1:
            problems.append(f"{label}: mixes past and present tense")
        elif tenses == {"present"} and not entry.current:
            problems.append(f"{label}: past role written in the present tense")
    return problems


# Under 9pt is a shrink-to-fit tell and reads badly in print; over 12pt is padding.
FONT_SIZE_FLOOR = 9
FONT_SIZE_CEILING = 12


def _edge_problem(facts) -> str:
    """Describe stranded header/footer content, or return "" when there is none.

    A single line low on the last page is the final bullet, not a running footer, and
    flagging it would be wrong on a perfectly good resume. Only two things count: a
    band that carries text on **every** page of a multi-page document, which is what a
    running header looks like; or contact details sitting in the band, which are the
    fields a parser discarding that region would actually cost you.
    """
    contact = [t for t in facts.edge_text if EMAIL_RE.search(t) or PHONE_RE.search(t)]
    if contact:
        return ("Contact details sit in the header/footer band - some parsers discard "
                "that region entirely")
    if facts.pages > 1:
        for band in (facts.top_band_pages, facts.bottom_band_pages):
            if len(band) == facts.pages:
                return (f"Text in the same edge band on all {facts.pages} pages - "
                        "reads as a running header/footer")
    return ""


def date_styles(text: str) -> list[str]:
    """Return the names of every distinct date format present."""
    return sorted(name for name, pattern in DATE_STYLES.items() if pattern.search(text))


def run(doc: Document) -> Report:
    """Score a document against every deterministic check."""
    text, bullets = doc.text, doc.bullets
    checks: list[Check] = []

    # ---------------------------------------------------------------- ats: contact
    emails = EMAIL_RE.findall(text)
    checks.append(_check("email", bool(emails), 5,
                         f"Email parses: {emails[0]}" if emails else "No parseable email address",
                         emails[:1], "ats"))

    phones = PHONE_RE.findall(text)
    checks.append(_check("phone", bool(phones), 5,
                         "Phone number parses" if phones else "No parseable phone number",
                         category="ats"))

    checks.append(_check("linkedin", "linkedin.com" in text.lower(), 3,
                         "LinkedIn present" if "linkedin.com" in text.lower() else "No LinkedIn URL",
                         category="ats"))
    checks.append(_check("github", "github.com" in text.lower(), 3,
                         "GitHub present" if "github.com" in text.lower() else "No GitHub URL",
                         category="ats"))

    # -------------------------------------------------------------- ats: structure
    wanted = {"education": ["education"],
              "experience": ["experience", "employment", "work history"],
              "skills": ["skills", "technical skills", "technologies"],
              "projects": ["projects", "personal projects"]}
    missing = []
    for name, variants in wanted.items():
        if not any(re.search(rf"(?im)^\s*{v}\s*$", text) for v in variants):
            missing.append(name)
    checks.append(_check("sections", not missing, 8,
                         "All four standard sections present" if not missing
                         else f"Missing section header(s): {', '.join(missing)}", missing, "ats"))

    dates = DATE_RE.findall(text)
    checks.append(_check("dates", len(dates) >= 4, 6,
                         f"{len(dates)} parseable dates" if len(dates) >= 4
                         else f"Only {len(dates)} parseable dates - ATS may miss your timeline",
                         category="ats"))

    styles = date_styles(text)
    if not styles:
        checks.append(_skip("date_format", "No dates found - nothing to check", "ats"))
    else:
        checks.append(_check("date_format", len(styles) == 1, 4,
                             f"Consistent date format ({styles[0]})" if len(styles) == 1
                             else f"{len(styles)} different date formats mixed: {', '.join(styles)}",
                             styles if len(styles) > 1 else [], "ats"))

    checks.append(_check("text_layer", doc.words > 200, 6,
                         "Real text layer" if doc.words > 200
                         else "Little or no extractable text - is this a scanned image?",
                         category="ats"))

    enough_prose = doc.words >= MIN_WORDS_FOR_LANGUAGE
    if not enough_prose:
        checks.append(_skip("clean_extraction", "Too little text to check extraction", "ats"))
    else:
        broken = re.findall(r"\b[A-Z]\s[a-z]{2,}\b", text)
        checks.append(_check("clean_extraction", not broken, 6,
                             "No words split during extraction" if not broken
                             else f"{len(broken)} words split during extraction (font/kerning issue)",
                             broken[:5], "ats"))

    # ------------------------------------------------- ats: document structure
    # These are the checks the commercial scanners sell: whether a parser can
    # physically read the file, rather than whether the writing is good. They need
    # PDF geometry, so for .txt and .docx they are not applicable rather than passed.
    facts = doc.layout
    if not facts.available:
        for key in ("no_images", "no_tables", "no_header_footer_content",
                    "standard_fonts", "font_size_sane"):
            checks.append(_skip(key, f"{doc.path.suffix or 'this format'} has no page "
                                     "layout to inspect", "ats"))
    else:
        checks.append(_check("no_images", facts.images == 0, 5,
                             "No images" if not facts.images
                             else f"{facts.images} image(s) - text inside an image is "
                                  "invisible to every parser",
                             category="ats"))

        checks.append(_check("no_tables", not facts.has_table_grid, 5,
                             "No table grid detected" if not facts.has_table_grid
                             else "Table-like ruling detected - tables scramble "
                                  "reading order in some parsers",
                             [f"{facts.horizontal_rules} horizontal rule(s), "
                              f"{facts.vertical_rules} vertical, {facts.rects} box(es)"]
                             if facts.has_table_grid else [], "ats"))

        edge = _edge_problem(facts)
        checks.append(_check("no_header_footer_content", not edge, 4,
                             "Nothing stranded in the header/footer band" if not edge
                             else edge, facts.edge_text[:3] if edge else [], "ats"))

        odd = facts.suspicious_fonts
        checks.append(_check("standard_fonts", not odd, 4,
                             f"{len(facts.fonts)} font(s), all safe to extract"
                             if not odd else f"{len(odd)} risky font(s)",
                             odd[:5], "ats"))

        size = facts.body_size
        sane = FONT_SIZE_FLOOR <= size <= FONT_SIZE_CEILING
        checks.append(_check("font_size_sane", sane, 3,
                             f"Body text {size:.0f}pt" if sane
                             else f"Body text {size:.0f}pt - outside the "
                                  f"{FONT_SIZE_FLOOR}-{FONT_SIZE_CEILING}pt range",
                             category="ats"))

    # ------------------------------------------------------------------- brevity
    checks.append(_check("one_page", doc.pages == 1, 8,
                         "One page" if doc.pages == 1
                         else f"{doc.pages} pages - students should fit one",
                         category="brevity"))

    in_band = WORD_COUNT_FLOOR <= doc.words <= WORD_COUNT_CEILING
    checks.append(_check("word_count", in_band, 5,
                         f"{doc.words} words, inside the {WORD_COUNT_FLOOR}-{WORD_COUNT_CEILING} band"
                         if in_band else
                         f"{doc.words} words - thin, under {WORD_COUNT_FLOOR}"
                         if doc.words < WORD_COUNT_FLOOR else
                         f"{doc.words} words - dense, over {WORD_COUNT_CEILING}",
                         category="brevity"))

    # --------------------------------------------------------------------- style
    # Every check below is absence-based, so all of them are meaningless - and
    # trivially passed - when there is barely any text to inspect.
    if not enough_prose:
        reason = f"Under {MIN_WORDS_FOR_LANGUAGE} words - nothing to judge"
        for key in ("no_pronouns", "no_filler", "active_voice", "tech_casing"):
            checks.append(_skip(key, reason, "style"))
    else:
        scrubbed = COURSE_I_RE.sub(" ", ROMAN_RE.sub(" ", text))
        pronouns = PRONOUN_RE.findall(scrubbed)
        checks.append(_check("no_pronouns", not pronouns, 5,
                             "No first-person pronouns" if not pronouns
                             else f"{len(pronouns)} first-person pronoun(s)",
                             sorted(set(pronouns)), "style"))

        found_weak = [p for p in WEAK_PHRASES if p in text.lower()]
        checks.append(_check("no_filler", not found_weak, 5,
                             "No filler phrases" if not found_weak
                             else f"Filler phrases found: {', '.join(found_weak)}",
                             found_weak, "style"))

        passive = find_passive(text)
        checks.append(_check("active_voice", not passive, 5,
                             "No passive voice" if not passive
                             else f"{len(passive)} passive construction(s) - the actor should be you",
                             passive[:5], "style"))

        miscased = find_miscased(text)
        checks.append(_check("tech_casing", not miscased, 4,
                             "Tool names capitalised correctly" if not miscased
                             else f"{len(miscased)} tool name(s) miscapitalised",
                             miscased[:5], "style"))

    # -------------------------------------------------------------------- bullets
    if not doc.columns_resolved:
        # The columns could not be separated, so any "bullet" is spliced from two
        # unrelated lines. Scoring that produces confident nonsense; skipping it
        # reports the real problem, which is the layout.
        reason = (f"{doc.columns}-column layout - bullets cannot be read reliably. "
                  "A single-column resume parses correctly.")
        for key, category in [("quantified", "impact"), ("bullet_length", "brevity"),
                              ("long_bullets", "brevity"), ("action_verbs", "impact"),
                              ("verb_variety", "style"), ("tense_consistency", "style")]:
            checks.append(_skip(key, reason, category))
    elif bullets:
        quantified = [b for b in bullets if re.search(r"\d", b)]
        share = len(quantified) / len(bullets)
        checks.append(_check("quantified", share >= QUANTIFIED_SHARE, 10,
                             f"{share:.0%} of bullets contain a number",
                             [b[:80] for b in bullets if not re.search(r"\d", b)][:3], "impact"))

        lengths = [len(b.split()) for b in bullets]
        mean = sum(lengths) / len(lengths)
        checks.append(_check("bullet_length", mean <= BULLET_WORD_CEILING, 8,
                             f"Mean bullet {mean:.1f} words (ceiling {BULLET_WORD_CEILING})",
                             category="brevity"))

        long_ones = [b for b in bullets if len(b.split()) > BULLET_WORD_CEILING]
        share_long = len(long_ones) / len(bullets)
        checks.append(_check("long_bullets", share_long <= LONG_BULLET_SHARE, 6,
                             f"{len(long_ones)}/{len(bullets)} bullets over {BULLET_WORD_CEILING} words",
                             [b[:80] for b in long_ones][:3], "brevity"))

        openers = [b.split()[0].lower().strip("*:,") for b in bullets if b.split()]
        strong = [o for o in openers if o in STRONG_VERBS]
        checks.append(_check("action_verbs", len(strong) >= len(openers) * STRONG_VERB_SHARE, 8,
                             f"{len(strong)}/{len(openers)} bullets open with a strong verb",
                             sorted({o for o in openers if o not in STRONG_VERBS})[:5], "impact"))

        unique = len(set(openers))
        repeated = sorted({o for o in openers if openers.count(o) > 1})
        checks.append(_check("verb_variety", unique >= len(openers) * VERB_VARIETY_SHARE, 5,
                             f"{unique} distinct opening verbs across {len(openers)} bullets",
                             [f"{o} (x{openers.count(o)})" for o in repeated][:5], "style"))

        entries = [e for e in group_entries(doc.layout_text) if e.bullets]
        if not entries:
            checks.append(_skip("tense_consistency",
                                "No dated entries found - cannot judge tense", "style"))
        else:
            problems = tense_problems(entries)
            checks.append(_check("tense_consistency", not problems, 4,
                                 f"Tense consistent across {len(entries)} dated entries"
                                 if not problems
                                 else f"{len(problems)} entry/entries with a tense problem",
                                 problems[:3], "style"))
    else:
        for key, pts, category in [("quantified", 10, "impact"), ("bullet_length", 8, "brevity"),
                                   ("long_bullets", 6, "brevity"), ("action_verbs", 8, "impact"),
                                   ("verb_variety", 5, "style")]:
            checks.append(_check(key, False, pts, "No bullets detected", category=category))
        checks.append(_skip("tense_consistency", "No bullets detected", "style"))

    return Report(checks)
