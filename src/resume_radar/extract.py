"""Turn a resume file into plain text plus the layout facts the checks need.

Extraction order matters. ``pdftotext`` (poppler) is used when available because it
preserves reading order better than a pure-Python parse; pdfminer is the fallback so
the package still works in environments without poppler installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class ExtractionError(RuntimeError):
    """Raised when a file cannot be turned into usable text."""


@dataclass
class Document:
    """A resume reduced to the facts the checks operate on."""

    path: Path
    text: str
    layout_text: str
    pages: int
    bullets: list[str] = field(default_factory=list)
    #: 1 for an ordinary resume, 2+ when a vertical gutter was detected.
    columns: int = 1
    #: False when the layout is multi-column but could not be split confidently.
    #: The bullet-dependent checks are skipped rather than scored against garbage.
    columns_resolved: bool = True
    #: Structural facts, available for PDFs only.
    layout: LayoutFacts = field(default_factory=lambda: LayoutFacts())

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Line:
    """One laid-out text line with the geometry needed to detect columns."""

    page: int
    x0: float
    x1: float
    top: float
    text: str


# The 14 fonts every PDF reader has built in. They are never embedded, and their
# absence from the file is correct rather than a defect.
BASE_14 = {
    "helvetica", "courier", "times", "times-roman", "symbol", "zapfdingbats",
    "arial", "timesnewroman", "couriernew",
}
# Font families that extract badly or read as unserious on a resume.
DECORATIVE_RE = re.compile(
    r"comic|papyrus|curlz|jokerman|brush|script|chiller|impact|wingding|webding|"
    r"lobster|pacifico|zapfino|handwriting|marker",
    re.IGNORECASE,
)
# pdfminer reports embedded subsets as "ABCDEF+RealName".
SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
#: Distance from a page edge that counts as header/footer territory.
EDGE_BAND = 40.0


@dataclass
class LayoutFacts:
    """Structural facts about a PDF that plain text cannot show.

    These are what the commercial ATS checkers actually sell: whether a parser can
    physically read the document, as opposed to whether the writing is any good.
    Populated for PDFs only; other formats carry ``available=False`` and the checks
    that depend on it are skipped rather than guessed at.
    """

    available: bool = False
    pages: int = 0
    images: int = 0
    horizontal_rules: int = 0
    vertical_rules: int = 0
    rects: int = 0
    fonts: set[str] = field(default_factory=set)
    body_size: float = 0.0
    #: Pages whose top band carries text, and likewise the bottom band.
    top_band_pages: set[int] = field(default_factory=set)
    bottom_band_pages: set[int] = field(default_factory=set)
    edge_text: list[str] = field(default_factory=list)

    @property
    def suspicious_fonts(self) -> list[str]:
        """Fonts that are decorative, or not embedded and not a base-14 face."""
        out = []
        for name in sorted(self.fonts):
            bare = SUBSET_RE.sub("", name)
            family = re.split(r"[-,]", bare)[0].replace(" ", "").lower()
            if DECORATIVE_RE.search(bare):
                out.append(f"{bare} (decorative)")
            elif not SUBSET_RE.match(name) and family not in BASE_14:
                out.append(f"{bare} (not embedded)")
        return out

    @property
    def has_table_grid(self) -> bool:
        """A grid, not a rule.

        Resumes underline their section headings, so horizontal lines alone mean
        nothing - a real one-column resume has several and no table. A table needs
        ruling in *both* directions, or a run of rectangles.
        """
        return (self.horizontal_rules >= 2 and self.vertical_rules >= 2) or self.rects >= 4


_BULLET_RE = re.compile(r"^[\s]*[•●·‣\-\*]\s*(.+)$")


def _page_count(path: Path) -> int:
    if shutil.which("pdfinfo"):
        out = subprocess.run(
            ["pdfinfo", str(path)], capture_output=True, text=True, check=False
        ).stdout
        match = re.search(r"Pages:\s+(\d+)", out)
        if match:
            return int(match.group(1))
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:  # pragma: no cover - only hit without pypdf
        return 1


def _pdf_text(path: Path, layout: bool) -> str:
    if shutil.which("pdftotext"):
        cmd = ["pdftotext"]
        if layout:
            cmd.append("-layout")
        cmd += [str(path), "-"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    try:
        from pdfminer.high_level import extract_text

        return extract_text(str(path))
    except Exception as exc:  # pragma: no cover
        raise ExtractionError(f"could not extract text from {path}: {exc}") from exc


def pdf_lines(path: Path) -> tuple[list[Line], float]:
    """Extract laid-out lines plus the page width, using pdfminer's geometry.

    pdfminer is used rather than ``pdftotext -bbox-layout`` because poppler is an
    optional dependency; the geometry has to work on a bare install and in CI.
    """
    lines, width, _ = read_pdf(path)
    return lines, width


def read_pdf(path: Path) -> tuple[list[Line], float, LayoutFacts]:
    """One pass over the PDF for both the text geometry and the structural facts."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import (LTChar, LTCurve, LTFigure, LTImage, LTLine, LTRect,
                                 LTTextContainer, LTTextLine)

    lines: list[Line] = []
    width = 612.0
    facts = LayoutFacts(available=True)
    sizes: dict[int, int] = {}

    def walk(container, number: int, height: float) -> None:
        for element in container:
            if isinstance(element, LTImage):
                facts.images += 1
            elif isinstance(element, LTFigure):
                walk(element, number, height)
            elif isinstance(element, LTLine):
                # LTLine is a degenerate curve; orientation says rule vs table edge.
                if abs(element.y1 - element.y0) <= 1:
                    facts.horizontal_rules += 1
                elif abs(element.x1 - element.x0) <= 1:
                    facts.vertical_rules += 1
            elif isinstance(element, LTRect):
                facts.rects += 1
            elif isinstance(element, LTCurve):
                pass
            elif isinstance(element, LTTextContainer):
                for line in element:
                    if not isinstance(line, LTTextLine):
                        continue
                    text = line.get_text().rstrip("\n")
                    if not text.strip():
                        continue
                    lines.append(Line(number, line.x0, line.x1, -line.y1, text))
                    if line.y0 > height - EDGE_BAND:
                        facts.top_band_pages.add(number)
                        facts.edge_text.append(text.strip())
                    if line.y1 < EDGE_BAND:
                        facts.bottom_band_pages.add(number)
                        facts.edge_text.append(text.strip())
                    for char in line:
                        if isinstance(char, LTChar):
                            facts.fonts.add(char.fontname)
                            key = round(char.size)
                            sizes[key] = sizes.get(key, 0) + 1

    for number, layout in enumerate(extract_pages(str(path))):
        width = layout.width or width
        facts.pages += 1
        walk(layout, number, layout.height)

    if sizes:
        facts.body_size = max(sizes.items(), key=lambda kv: kv[1])[0]
    return lines, width, facts


def find_gutters(lines: list[Line], page_width: float, *, min_share: float = 0.2,
                 samples: int = 101, max_cross: float = 0.25,
                 min_width: float = 6.0) -> list[float]:
    """Return x positions of vertical gutters - bands the columns do not cross.

    Real resumes are *hybrid*: a full-width name and contact header, a two-column
    body, often a full-width section underneath. Requiring that **zero** lines cross
    the band - which an earlier version did - means one full-width header line
    defeats detection for the whole page, and the document is then scored as a
    single column it is not. So a minority of lines may cross, and the split is
    judged on the rest.

    Two further guards keep that tolerance from inventing columns:

    ``min_share``  each side must carry real content, so a wide margin is not a gutter.
    ``min_width``  a real gutter is a band several points wide; a one-sample hit is an
                   accident of where the words happened to end.
    """
    if len(lines) < 6 or page_width <= 0:
        return []

    step = page_width * 0.5 / (samples - 1)
    hits: list[float] = []
    for index in range(samples):
        # Sample the middle half of the page: a true column split never sits in a margin.
        x = page_width * (0.25 + 0.5 * index / (samples - 1))
        rest = [line for line in lines if not (line.x0 < x < line.x1)]
        if len(lines) - len(rest) > len(lines) * max_cross:
            continue
        if not rest:
            continue
        left = sum(1 for line in rest if line.x1 <= x)
        right = len(rest) - left
        if left >= len(rest) * min_share and right >= len(rest) * min_share:
            hits.append(x)

    if not hits:
        return []

    # Adjacent samples describe the same gutter; collapse each run to its centre and
    # discard runs too narrow to be a real column gap.
    runs, run = [], [hits[0]]
    for x in hits[1:]:
        if x - run[-1] <= step * 1.5:
            run.append(x)
        else:
            runs.append(run)
            run = [x]
    runs.append(run)
    return [sum(r) / len(r) for r in runs if (r[-1] - r[0]) >= min_width]


def column_mass(lines: list[Line], gutters: list[float]) -> list[int]:
    """Characters of text falling wholly inside each column, split at ``gutters``.

    Lines that span a gutter - the name header, a full-width section - are counted in
    no column. Attributing them by midpoint credits their whole length to whichever
    column the middle happens to land in, which is exactly the narrow middle "column"
    this measurement exists to catch.
    """
    edges = [float("-inf"), *gutters, float("inf")]
    return [
        sum(len(ln.text.strip()) for ln in lines if ln.x0 >= lo and ln.x1 <= hi)
        for lo, hi in zip(edges, edges[1:])
    ]


def prune_gutters(lines: list[Line], gutters: list[float], *,
                  min_share: float = 0.15) -> list[float]:
    """Drop candidate gutters that carve out a column with almost no text in it.

    Counting empty bands finds too many. A resume that right-aligns its dates puts a
    narrow date column between the two real ones, which splits the empty region into
    two candidate bands; the page then reads as three columns, gets reported
    unresolved, and loses every bullet. Judging a split by how much *text* each
    resulting column holds separates that from a genuine three-column layout, where
    all three carry real content.
    """
    gutters = list(gutters)
    while len(gutters) > 1:
        masses = column_mass(lines, gutters)
        total = sum(masses) or 1
        if min(masses) / total >= min_share:
            break
        # Remove whichever gutter leaves the most balanced remaining split.
        best = max(
            (gutters[:i] + gutters[i + 1:] for i in range(len(gutters))),
            key=lambda trial: min(column_mass(lines, trial))
            / (sum(column_mass(lines, trial)) or 1),
        )
        gutters = best

    if len(gutters) == 1:
        masses = column_mass(lines, gutters)
        if min(masses) / (sum(masses) or 1) < min_share:
            return []
    return gutters


def order_columns(lines: list[Line], gutter: float) -> str:
    """Re-read a two-column page one column at a time, top to bottom.

    Lines that span the gutter are full-width - the name header, a horizontal rule,
    a section that breaks out of the columns. They belong to neither column and must
    not be dropped, so they are placed by where they sit relative to the columnar
    body: above it, or after it.
    """
    left = [ln for ln in lines if ln.x1 <= gutter]
    right = [ln for ln in lines if ln.x0 >= gutter]
    spanning = [ln for ln in lines if ln.x0 < gutter < ln.x1]

    key = lambda ln: (ln.page, ln.top)  # noqa: E731 - a sort key, not a function
    body = left + right
    if not body:
        return "\n".join(ln.text for ln in sorted(lines, key=key))

    top = min(key(ln) for ln in body)
    header = [ln for ln in spanning if key(ln) < top]
    rest = [ln for ln in spanning if key(ln) >= top]

    ordered = (sorted(header, key=key) + sorted(left, key=key)
               + sorted(right, key=key) + sorted(rest, key=key))
    return "\n".join(ln.text for ln in ordered)


def collect_bullets(layout_text: str) -> list[str]:
    """Pull bullet lines out of layout text, joining wrapped continuation lines.

    A continuation line is one that is indented and does not itself start a bullet,
    a section header, or a new dated entry.
    """
    bullets: list[str] = []
    current: list[str] = []

    for raw in layout_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = _BULLET_RE.match(line)
        if match:
            if current:
                bullets.append(" ".join(current))
            current = [match.group(1).strip()]
            continue
        if current:
            stripped = line.strip()
            starts_lower = stripped[:1].islower()
            is_header = stripped.isupper()
            if starts_lower and not is_header:
                current.append(stripped)
            else:
                bullets.append(" ".join(current))
                current = []

    if current:
        bullets.append(" ".join(current))
    return [b for b in bullets if len(b.split()) >= 3]


_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_WHEN = rf"(?:{_MONTH}\s*'?\d{{2,4}}|\d{{4}})"
# Every dash a resume might use between two dates. LaTeX templates in particular emit
# U+2212 MINUS SIGN rather than a hyphen, which a naive `-|–|—` class silently misses.
DASHES = "\\-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
# An entry header is the line carrying a date range: "Software Intern, May 2024 - Present".
ENTRY_RANGE_RE = re.compile(
    rf"(?i){_WHEN}\s*(?:[{DASHES}]+|to)\s*(present|current|now|ongoing|{_WHEN})"
)
CURRENT_RE = re.compile(r"(?i)\b(present|current|now|ongoing)\b")


@dataclass
class Entry:
    """One dated block on a resume - a role, a project, a degree."""

    header: str
    current: bool
    lines: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


def group_entries(layout_text: str) -> list[Entry]:
    """Split layout text into dated entries, each with the bullets underneath it.

    Needed because tense is only judgeable per entry: present tense is correct for a
    role you still hold and wrong for one you left.
    """
    entries: list[Entry] = []
    for raw in layout_text.splitlines():
        match = ENTRY_RANGE_RE.search(raw)
        if match:
            entries.append(Entry(header=raw.strip(),
                                 current=bool(CURRENT_RE.search(match.group(1)))))
        elif entries:
            entries[-1].lines.append(raw)

    for entry in entries:
        entry.bullets = collect_bullets("\n".join(entry.lines))
    return entries


def load(path: str | Path) -> Document:
    """Read a resume file into a :class:`Document`."""
    path = Path(path)
    if not path.exists():
        raise ExtractionError(f"no such file: {path}")

    suffix = path.suffix.lower()
    columns, resolved = 1, True
    facts = LayoutFacts()
    if suffix == ".pdf":
        text = _pdf_text(path, layout=False)
        layout_text = _pdf_text(path, layout=True)
        pages = _page_count(path)
        columns, resolved, reordered, facts = _resolve_columns(path)
        if reordered:
            layout_text = reordered
    elif suffix in {".txt", ".md"}:
        text = layout_text = path.read_text(encoding="utf-8", errors="replace")
        pages = 1
    elif suffix == ".docx":
        text = layout_text = _docx_text(path)
        pages = 1
    else:
        raise ExtractionError(f"unsupported file type: {suffix}")

    if not text.strip():
        raise ExtractionError(
            f"{path.name} has no extractable text - it may be a scanned image"
        )

    return Document(
        path=path,
        text=text,
        layout_text=layout_text,
        pages=pages,
        bullets=collect_bullets(layout_text) if resolved else [],
        columns=columns,
        columns_resolved=resolved,
        layout=facts,
    )


def _resolve_columns(path: Path) -> tuple[int, bool, str, LayoutFacts]:
    """Detect column count and, for two columns, rebuild the text in reading order.

    Returns ``(columns, resolved, reordered_text, facts)``. Three or more columns are
    reported unresolved: the reading order is genuinely ambiguous, and guessing it
    would produce bullets that are silently wrong rather than obviously missing.
    """
    try:
        lines, width, facts = read_pdf(path)
    except Exception:  # pragma: no cover - malformed PDFs fall back to flat text
        return 1, True, "", LayoutFacts()

    gutters = prune_gutters(lines, find_gutters(lines, width))
    if not gutters:
        return 1, True, "", facts
    if len(gutters) > 1:
        return len(gutters) + 1, False, "", facts
    return 2, True, order_columns(lines, gutters[0]), facts


def _docx_text(path: Path) -> str:
    import xml.etree.ElementTree as ET
    import zipfile

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    lines = []
    for para in root.iter(f"{{{ns['w']}}}p"):
        runs = [node.text or "" for node in para.iter(f"{{{ns['w']}}}t")]
        joined = "".join(runs).strip()
        if joined:
            lines.append(joined)
    return "\n".join(lines)
