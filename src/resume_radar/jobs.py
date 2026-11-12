"""Find postings, screen out the unwinnable ones, and rank the rest by fit.

Sources are limited to job-board APIs that publish openly and document the
endpoint: Greenhouse and Lever both serve per-company boards without auth. Scraping
LinkedIn or Indeed is prohibited by their terms and is deliberately not implemented.

The output is a *fit* score, not a probability. Ranking by fit says "this posting
asks for more of what you have" - it does not say "you are more likely to get an
interview", because that claim needs outcome data this tool does not have on day one.
Once ``tracker`` has enough labelled outcomes the two can be compared; until then the
honest word is fit. See ``docs/scoring.md``.
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

from . import keywords

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
LEVER_URL = "https://api.lever.co/v0/postings/{board}?mode=json"

USER_AGENT = "resume-radar/0.3 (personal job search; contact via GitHub mghadia1)"


# --------------------------------------------------------------------------- #
# Eligibility screening
# --------------------------------------------------------------------------- #

# On an F-1 visa these are hard blockers, not preferences. A posting requiring
# citizenship or an active clearance cannot be won regardless of technical fit, so
# filtering them out is worth more than any amount of ranking.
BLOCKERS: dict[str, list[str]] = {
    "citizenship": [
        r"must be a (?:u\.?s\.?|united states) citizen",
        r"(?:u\.?s\.?|united states) citizenship (?:is )?(?:required|mandatory)",
        r"(?:u\.?s\.?) citizens? only",
        r"restricted to (?:u\.?s\.?|united states) citizens",
    ],
    "clearance": [
        r"active (?:security )?clearance",
        r"(?:security )?clearance (?:is )?required",
        r"\bts/sci\b",
        r"\btop secret\b",
        r"ability to obtain (?:a )?(?:security )?clearance",
    ],
    "no_sponsorship": [
        r"(?:will|can|do) not (?:be able to )?sponsor",
        r"(?:unable|not able) to (?:provide |offer )?sponsor",
        r"no (?:visa )?sponsorship",
        r"does not (?:provide|offer) (?:visa )?sponsorship",
        r"without (?:the )?(?:need for )?(?:visa )?sponsorship",
        r"not eligible for (?:visa )?sponsorship",
        r"sponsorship is not available",
    ],
    "export_control": [
        r"\bitar\b",
        r"export[- ]control(?:led)? (?:data|information|regulations)",
    ],
}

# Phrases that mean the opposite. Checked first, because a posting saying "we are
# happy to sponsor" must not trip the no-sponsorship pattern in a nearby sentence.
SPONSORSHIP_FRIENDLY = [
    r"(?:will|do|can|happy to|able to) sponsor",
    r"sponsorship (?:is )?available",
    r"we sponsor",
    r"offer(?:s)? (?:visa )?sponsorship",
    r"open to (?:candidates requiring )?sponsorship",
]

COMPILED_BLOCKERS = {
    name: [re.compile(p, re.I) for p in patterns] for name, patterns in BLOCKERS.items()
}
COMPILED_FRIENDLY = [re.compile(p, re.I) for p in SPONSORSHIP_FRIENDLY]


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def strip_html(raw: str) -> str:
    """Turn posting HTML into plain text. Board APIs return escaped HTML bodies."""
    if not raw:
        return ""
    import html as html_mod

    parser = _Stripper()
    parser.feed(html_mod.unescape(raw))
    return parser.text()


@dataclass(eq=False)
class Posting:
    """One open role.

    ``eq=False`` with an explicit identity: two postings are the same posting when
    they are the same url on the same board, regardless of the fit and blockers later
    written onto them. Field-wise equality would make a posting unhashable (it carries
    a list and a KeywordMatch) and would say two identical roles at one company are
    the same opening, which they are not.
    """

    company: str
    title: str
    url: str
    description: str
    location: str = ""
    source: str = ""

    # filled by screen()
    blockers: list[str] = field(default_factory=list)
    sponsorship_friendly: bool = False
    # filled by rank()
    fit: float = 0.0
    match: keywords.KeywordMatch | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.source, self.company, self.url)

    def __eq__(self, other) -> bool:
        return isinstance(other, Posting) and self.identity == other.identity

    def __hash__(self) -> int:
        return hash(self.identity)

    @property
    def eligible(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict:
        out = {
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "location": self.location,
            "source": self.source,
            "eligible": self.eligible,
            "blockers": self.blockers,
            "sponsorship_friendly": self.sponsorship_friendly,
            "fit": round(self.fit, 1),
        }
        if self.match:
            out["title_matched"] = self.match.title_matched
            out["missing_skills"] = self.match.priority[:8]
            out["matched_skills"] = self.match.hard_matched[:8]
        return out


# --------------------------------------------------------------------------- #
# Parsing - pure functions so they can be tested without network
# --------------------------------------------------------------------------- #

def parse_greenhouse(payload: dict, company: str) -> list[Posting]:
    """Turn a Greenhouse ``/jobs?content=true`` payload into postings."""
    out = []
    for job in payload.get("jobs", []):
        out.append(Posting(
            company=company,
            title=job.get("title", ""),
            url=job.get("absolute_url", ""),
            description=strip_html(job.get("content", "")),
            location=(job.get("location") or {}).get("name", ""),
            source="greenhouse",
        ))
    return out


def parse_lever(payload: list, company: str) -> list[Posting]:
    """Turn a Lever ``/v0/postings`` payload into postings."""
    out = []
    for job in payload:
        body = job.get("descriptionPlain") or strip_html(job.get("description", ""))
        lists = " ".join(
            strip_html(section.get("content", "")) for section in job.get("lists", [])
        )
        out.append(Posting(
            company=company,
            title=job.get("text", ""),
            url=job.get("hostedUrl", ""),
            description=f"{body} {lists}".strip(),
            location=(job.get("categories") or {}).get("location", ""),
            source="lever",
        ))
    return out


def fetch(board: str, source: str, *, timeout: int = 20) -> list[Posting]:
    """Fetch one company board. ``source`` is ``greenhouse`` or ``lever``.

    Every network and parse failure becomes a ``RuntimeError`` so the caller can warn
    and move to the next board. Catching only ``URLError`` is not enough and was found
    the hard way against 29 live boards: a server that drops the connection mid-read
    raises ``http.client.RemoteDisconnected``, a read that stalls raises
    ``TimeoutError``, and neither is a ``URLError`` - so one flaky board aborted the
    whole scheduled run, which is exactly what this design promises never to do.
    """
    url = (GREENHOUSE_URL if source == "greenhouse" else LEVER_URL).format(board=board)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # OSError covers URLError, TimeoutError, ConnectionReset and ssl failures;
        # ValueError covers JSONDecodeError and a truncated body.
        raise RuntimeError(
            f"could not fetch {source}/{board}: {type(exc).__name__}: {exc}") from exc

    try:
        if source == "greenhouse":
            return parse_greenhouse(payload, board)
        return parse_lever(payload, board)
    except (AttributeError, TypeError, KeyError) as exc:
        # A board that answers 200 with a shape we do not expect is a bad board, not
        # a crash: an error page served as JSON should skip this company, not the run.
        raise RuntimeError(
            f"unexpected payload from {source}/{board}: {type(exc).__name__}: {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# Screening and ranking
# --------------------------------------------------------------------------- #

def screen(posting: Posting) -> Posting:
    """Flag hard eligibility blockers. Mutates and returns ``posting``."""
    text = f"{posting.title} {posting.description}"
    posting.sponsorship_friendly = any(p.search(text) for p in COMPILED_FRIENDLY)

    found = []
    for name, patterns in COMPILED_BLOCKERS.items():
        if name == "no_sponsorship" and posting.sponsorship_friendly:
            # An explicit offer to sponsor outranks boilerplate elsewhere in the post.
            continue
        if any(p.search(text) for p in patterns):
            found.append(name)
    posting.blockers = found
    return posting


def rank(resume_text: str, postings: list[Posting], *,
         include_ineligible: bool = False) -> list[Posting]:
    """Score each posting against the resume and sort by fit, best first."""
    scored = []
    for posting in postings:
        screen(posting)
        if posting.blockers and not include_ineligible:
            continue
        posting.match = keywords.compare(
            resume_text, f"{posting.title}\n{posting.description}"
        )
        posting.fit = posting.match.percent
        scored.append(posting)
    return sorted(scored, key=lambda p: -p.fit)


def load_boards(path: str) -> list[tuple[str, str]]:
    """Read a board list: one ``source:board`` per line, ``#`` for comments.

        greenhouse:figma
        lever:netflix
    """
    pairs = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            source, _, board = line.partition(":")
            source, board = source.strip().lower(), board.strip()
            if source in {"greenhouse", "lever"} and board:
                pairs.append((source, board))
    return pairs


# --------------------------------------------------------------------------- #
# Role level
# --------------------------------------------------------------------------- #

# Ranking a "Senior Staff Engineer" against a student resume wastes a slot on a
# posting that cannot be won. Level is read from the title, which is where boards put
# it consistently; description text is too noisy ("work with senior engineers").
LEVEL_PATTERNS: list[tuple[str, str]] = [
    # Order matters. "Senior Data Science Intern" is an intern posting, so intern wins
    # over senior. Senior is then checked before new_grad, so "Associate Director"
    # reads as senior rather than being caught by "associate".
    ("intern", r"\b(intern|internship|co-?op|summer analyst|industrial placement)\b"),
    ("senior", r"\b(senior|sr\.?|staff|principal|lead|manager|director|head of|"
               r"architect|vp|distinguished|fellow|iii|iv)\b"),
    # `grad\w*` rather than `grad\b`: "University Graduate" continues past "grad", so a
    # trailing word boundary fails there and the posting is silently dropped.
    ("new_grad", r"\b(?:(?:new|university|college)\s?grad\w*|graduate program|"
                 r"campus hire|early career|entry[- ]level|rotational program|"
                 r"apprentice|junior|jr\.?|trainee|emerging talent)\b|"
                 # "Associate" only counts next to a technical noun. Bare "associate"
                 # is a job family, not a level: across four real boards it matched
                 # 20-odd Stripe "Operations Associate" roles and almost nothing else.
                 r"\bassociate\s+(?:\w+\s+){0,2}"
                 r"(?:engineer|developer|scientist|programmer)\b|"
                 # "Software Engineer I" is the entry rung. Level II is usually two
                 # years in, so it stays "mid" rather than being claimed here.
                 r"\b(?:engineer|developer|analyst|scientist|programmer)\s+(?:i|1)\b",
     ),
]
COMPILED_LEVELS = [(name, re.compile(p, re.I)) for name, p in LEVEL_PATTERNS]

#: Levels a student can realistically win.
STUDENT_LEVELS = ("intern", "new_grad")


def classify_level(title: str) -> str:
    """Return ``intern``, ``new_grad``, ``senior``, or ``mid`` from a posting title."""
    for name, pattern in COMPILED_LEVELS:
        if pattern.search(title):
            return name
    return "mid"


def filter_levels(postings: list[Posting],
                  levels: tuple[str, ...] = STUDENT_LEVELS) -> list[Posting]:
    """Keep only postings whose title reads as one of ``levels``."""
    return [p for p in postings if classify_level(p.title) in levels]


@dataclass
class Slate:
    """A day's shortlist: new postings first, older ones backfilled to reach target."""

    new: list[Posting] = field(default_factory=list)
    backfill: list[Posting] = field(default_factory=list)
    filtered: list[Posting] = field(default_factory=list)
    #: Postings dropped for level, kept so the digest can show what was discarded.
    off_level: list[Posting] = field(default_factory=list)

    @property
    def wrong_level(self) -> int:
        return len(self.off_level)

    @property
    def picks(self) -> list[Posting]:
        return self.new + self.backfill

    @property
    def short(self) -> int:
        """How many slots could not be filled at all."""
        return max(0, self._target - len(self.picks))

    _target: int = 10

    def to_dict(self) -> dict:
        return {
            "target": self._target,
            "new": [p.to_dict() for p in self.new],
            "backfill": [p.to_dict() for p in self.backfill],
            "filtered_count": len(self.filtered),
            "wrong_level": self.wrong_level,
            "short": self.short,
        }


def build_slate(resume_text: str, postings: list[Posting], *,
                is_new, target: int = 10, min_fit: float = 0.0,
                levels: tuple[str, ...] = STUDENT_LEVELS) -> Slate:
    """Assemble a shortlist of ``target`` postings.

    ``is_new`` is a callable taking a :class:`Posting` and returning whether it has not
    been reported before. New postings fill the slate first; if there are fewer than
    ``target``, the best still-open older ones backfill the rest. A daily digest that
    says "nothing new" on a slow day is not useful, but a backfilled entry must be
    labelled as such so it is never mistaken for a fresh posting.

    Repeated identities count once throughout the slate. The first copy supplies
    the metadata, and input order is preserved for postings with equal fit.
    """
    # Posting equality and hashing use (source, company, url), not scored fields.
    postings = list(dict.fromkeys(postings))
    of_level = filter_levels(postings, levels)
    kept = set(of_level)
    off_level = [p for p in postings if p not in kept]

    ranked = rank(resume_text, of_level)
    blocked = [p for p in of_level if p.blockers]

    fresh = [p for p in ranked if p.fit >= min_fit and is_new(p)]
    older = [p for p in ranked if p.fit >= min_fit and not is_new(p)]

    slate = Slate(filtered=blocked, off_level=off_level)
    slate._target = target
    slate.new = fresh[:target]
    slate.backfill = older[:max(0, target - len(slate.new))]
    return slate
