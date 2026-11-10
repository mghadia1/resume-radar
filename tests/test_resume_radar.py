"""Tests for ResumeRadar.

Fixtures are generated, not copied from a real resume: the PDF is assembled byte by
byte below and the DOCX is a zip built in memory, so the suite has no dependency on
personal files and none on poppler being installed.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from resume_radar import (
    analyze, checks, cli, competencies, discover, extract, keywords, suggest, tracker,
)
from resume_radar import compare as compare_mod
from resume_radar.extract import ExtractionError, collect_bullets

# 21 text checks plus the five ATS document-structure checks (no_images, no_tables,
# no_header_footer_content, standard_fonts, font_size_sane).
MAX_SCORE = 140

# --------------------------------------------------------------------------- fixtures


def _escape(text: str) -> str:
    out = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    # WinAnsi code point for the bullet glyph; written as an octal escape so the file
    # stays pure ASCII.
    return out.replace("•", r"\225")


def write_pdf(path: Path, lines: list[str], pages: int = 1) -> Path:
    """Write a minimal but valid text PDF that pdfminer can read back."""
    per_page = []
    for _ in range(pages):
        body = "\n".join(f"({_escape(line)}) Tj T*" for line in lines)
        per_page.append(f"BT /F1 10 Tf 12 TL 1 0 0 1 40 750 Tm\n{body}\nET")

    objects: list[bytes] = []
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    for i, stream in enumerate(per_page):
        content_ref = 5 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_ref} 0 R >>".encode()
        )
        raw = stream.encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(raw), raw))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )

    path.write_bytes(bytes(out))
    return path


def write_columns_pdf(path: Path, columns: list[list[str]],
                      xs: list[int] | None = None) -> Path:
    """Write a PDF whose text sits in N side-by-side columns.

    Each column is emitted as its own text object at a fixed x, which is exactly what
    a two-column resume template produces and what naive top-to-bottom extraction
    interleaves into nonsense.
    """
    xs = xs or [40, 330]
    blocks = []
    for lines, x in zip(columns, xs):
        body = "\n".join(f"({_escape(line)}) Tj T*" for line in lines)
        blocks.append(f"BT /F1 10 Tf 12 TL 1 0 0 1 {x} 750 Tm\n{body}\nET")
    stream = "\n".join(blocks)

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
    ]
    raw = stream.encode("latin-1")
    objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(raw), raw))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_at)
    path.write_bytes(bytes(out))
    return path


def write_blocks_pdf(path: Path, blocks: list[tuple[int, int, list[str]]]) -> Path:
    """Write a PDF from positioned text blocks: ``(x, y, lines)``.

    More general than `write_columns_pdf`, so a *hybrid* page can be built - full-width
    header, two-column body, full-width footer - which is what a real two-column resume
    actually looks like.
    """
    stream = "\n".join(
        f"BT /F1 10 Tf 12 TL 1 0 0 1 {x} {y} Tm\n"
        + "\n".join(f"({_escape(line)}) Tj T*" for line in lines) + "\nET"
        for x, y, lines in blocks
    )
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
    ]
    raw = stream.encode("latin-1")
    objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(raw), raw))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_at)
    path.write_bytes(bytes(out))
    return path


def write_docx(path: Path, lines: list[str]) -> Path:
    """Write a minimal .docx containing one paragraph per line."""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paras = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{line}</w:t></w:r></w:p>' for line in lines
    )
    document = f'<?xml version="1.0"?><w:document xmlns:w="{ns}"><w:body>{paras}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
    return path


GOOD_LINES = [
    "MAYANK GHADIA",
    "test@example.com | (480) 555-0123 | linkedin.com/in/example | github.com/example",
    "EDUCATION",
    "Arizona State University, BS Computer Science, Jan 2024 - Dec 2027",
    "Coursework: Calculus I-III, Data Structures",
    "EXPERIENCE",
    "Software Intern, May 2024 - Jul 2024",
    "• Built a data pipeline that cut ingest time by 40 percent across 3 services",
    "• Automated 12 nightly reports, removing 5 hours of manual work each week",
    "EDUCATION",
    "PROJECTS",
    "• Designed a 4-layer classifier reaching 91 percent accuracy on 2000 samples",
    "• Deployed 2 containers to CI, cutting build time from 9 to 4 minutes",
    "SKILLS",
    "Python, Java, JavaScript, machine learning, Docker, Feb 2023, Mar 2023",
]


@pytest.fixture
def good_pdf(tmp_path: Path) -> Path:
    return write_pdf(tmp_path / "Mayank-Ghadia-Resume.pdf", GOOD_LINES)


# ---------------------------------------------------------------------------- extract


def test_collect_bullets_joins_wrapped_lines():
    layout = (
        "EXPERIENCE\n"
        "• Built a service that indexed\n"
        "    every document in the archive\n"
        "• Automated the nightly report\n"
    )
    bullets = collect_bullets(layout)
    assert bullets == [
        "Built a service that indexed every document in the archive",
        "Automated the nightly report",
    ]


def test_collect_bullets_stops_at_a_new_section_header():
    layout = "• Built a service that indexed documents\nSKILLS\nPython, Java\n"
    assert collect_bullets(layout) == ["Built a service that indexed documents"]


def test_load_pdf_reads_text_and_bullets(good_pdf: Path):
    doc = extract.load(good_pdf)
    assert doc.pages == 1
    assert "test@example.com" in doc.text
    assert len(doc.bullets) == 4
    assert doc.bullets[0].startswith("Built a data pipeline")


def test_load_counts_pages(tmp_path: Path):
    doc = extract.load(write_pdf(tmp_path / "two.pdf", GOOD_LINES, pages=2))
    assert doc.pages == 2


def test_load_txt(tmp_path: Path):
    path = tmp_path / "resume.txt"
    path.write_text("\n".join(GOOD_LINES), encoding="utf-8")
    doc = extract.load(path)
    assert doc.pages == 1
    assert len(doc.bullets) == 4


def test_load_docx(tmp_path: Path):
    doc = extract.load(write_docx(tmp_path / "resume.docx", GOOD_LINES))
    assert "Arizona State University" in doc.text
    assert len(doc.bullets) == 4


@pytest.fixture(params=["not_zip", "missing_document", "invalid_xml"])
def malformed_docx(request) -> bytes:
    """Invalid documents made entirely in memory, without personal data."""
    if request.param == "not_zip":
        return b"not a DOCX file"
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        if request.param == "missing_document":
            archive.writestr("other.xml", "<other/>")
        else:
            archive.writestr("word/document.xml", "<broken")
    return payload.getvalue()


def test_load_malformed_docx_raises_extraction_error(tmp_path: Path, malformed_docx: bytes):
    path = tmp_path / "resume.docx"
    path.write_bytes(malformed_docx)
    with pytest.raises(ExtractionError, match="could not extract text") as error:
        extract.load(path)
    assert error.value.__cause__ is not None


def test_load_missing_file_raises(tmp_path: Path):
    with pytest.raises(ExtractionError, match="no such file"):
        extract.load(tmp_path / "absent.pdf")


def test_load_unsupported_extension_raises(tmp_path: Path):
    path = tmp_path / "resume.rtf"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(ExtractionError, match="unsupported file type"):
        extract.load(path)


def test_load_empty_file_raises(tmp_path: Path):
    path = tmp_path / "blank.txt"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(ExtractionError, match="no extractable text"):
        extract.load(path)


# ---------------------------------------------------------------------- compare (A3)


def _write(tmp_path: Path, name: str, lines: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_compare_reports_gains_and_losses(tmp_path: Path):
    """The point of the command: a rewrite that fixes one check can break another."""
    wordy = GOOD_LINES[:7] + [
        "- Built a pipeline with 40 stages " + " ".join(f"word{i}" for i in range(40)),
    ]
    terse = GOOD_LINES[:7] + ["- Built a pipeline for the analytics team"]

    result = compare_mod.compare(
        analyze(_write(tmp_path, "a.txt", wordy)),
        analyze(_write(tmp_path, "b.txt", terse)),
    )
    keys_gained = {d.key for d in result.gains}
    keys_lost = {d.key for d in result.losses}
    assert "bullet_length" in keys_gained          # B got shorter
    assert "quantified" in keys_lost               # and lost its number
    assert result.winner in {"left", "right", "tie"}


def test_compare_names_a_category_winner(tmp_path: Path):
    result = compare_mod.compare(
        analyze(_write(tmp_path, "a.txt", GOOD_LINES)),
        analyze(_write(tmp_path, "b.txt", GOOD_LINES + ["I worked on my own project"])),
    )
    assert result.categories["style"]["winner"] == "left"
    assert result.categories["style"]["delta"] < 0


def test_compare_of_identical_resumes_has_no_deltas(tmp_path: Path):
    a = _write(tmp_path, "a.txt", GOOD_LINES)
    b = _write(tmp_path, "b.txt", GOOD_LINES)
    result = compare_mod.compare(analyze(a), analyze(b))
    assert result.deltas == []
    assert result.winner == "tie"
    assert all(c["delta"] == 0 for c in result.categories.values())


def test_compare_is_json_serialisable(tmp_path: Path):
    result = compare_mod.compare(
        analyze(_write(tmp_path, "a.txt", GOOD_LINES)),
        analyze(_write(tmp_path, "b.txt", GOOD_LINES[:8])),
    )
    payload = json.dumps(result.to_dict())
    assert "changed" in payload


def test_compare_cli_runs(tmp_path: Path, capsys):
    a = _write(tmp_path, "a.txt", GOOD_LINES)
    b = _write(tmp_path, "b.txt", GOOD_LINES[:8])
    assert cli.main(["compare", str(a), str(b)]) == 0
    assert "overall:" in capsys.readouterr().out


# --------------------------------------------------------- ATS document structure


def write_drawn_pdf(path: Path, text_lines: list[str], ops: str = "",
                    font: str = "Helvetica", size: int = 10) -> Path:
    """A PDF with text plus raw content-stream drawing operators.

    Needed because tables and rules are *drawn*, not written: they only exist as path
    operators, so they cannot be produced by the text-only builders above.
    """
    body = "\n".join(f"({_escape(line)}) Tj T*" for line in text_lines)
    stream = f"BT /F1 {size} Tf {size + 2} TL 1 0 0 1 40 700 Tm\n{body}\nET\n{ops}"
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        f"<< /Type /Font /Subtype /Type1 /BaseFont /{font} "
        f"/Encoding /WinAnsiEncoding >>".encode(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
    ]
    raw = stream.encode("latin-1")
    objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(raw), raw))
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_at)
    path.write_bytes(bytes(out))
    return path


SECTION_RULES = "\n".join(f"q 1 w 40 {650 - i * 20} m 500 {650 - i * 20} l S Q"
                          for i in range(4))
TABLE_GRID = "\n".join(
    [f"q 1 w 40 {600 - i * 30} m 500 {600 - i * 30} l S Q" for i in range(3)]
    + [f"q 1 w {40 + i * 150} 480 m {40 + i * 150} 600 l S Q" for i in range(3)])
TABLE_BOXES = "\n".join(f"q 1 w 40 {500 - i * 30} 200 20 re S Q" for i in range(5))


def test_section_rules_are_not_a_table(tmp_path: Path):
    """The real resume has four horizontal rules under its section headings."""
    doc = extract.load(write_drawn_pdf(tmp_path / "rules.pdf", GOOD_LINES, SECTION_RULES))
    assert doc.layout.horizontal_rules == 4
    assert doc.layout.vertical_rules == 0
    assert doc.layout.has_table_grid is False
    assert {c.key: c for c in checks.run(doc).checks}["no_tables"].passed


def test_ruling_in_both_directions_is_a_table(tmp_path: Path):
    doc = extract.load(write_drawn_pdf(tmp_path / "grid.pdf", GOOD_LINES, TABLE_GRID))
    assert doc.layout.has_table_grid is True
    check = {c.key: c for c in checks.run(doc).checks}["no_tables"]
    assert not check.passed
    assert "reading order" in check.message


def test_a_run_of_boxes_is_a_table(tmp_path: Path):
    doc = extract.load(write_drawn_pdf(tmp_path / "boxes.pdf", GOOD_LINES, TABLE_BOXES))
    assert doc.layout.rects == 5
    assert doc.layout.has_table_grid is True


def test_tiny_body_text_is_flagged(tmp_path: Path):
    doc = extract.load(write_drawn_pdf(tmp_path / "tiny.pdf", GOOD_LINES, size=7))
    check = {c.key: c for c in checks.run(doc).checks}["font_size_sane"]
    assert not check.passed
    assert "7pt" in check.message


def test_normal_body_text_passes(tmp_path: Path):
    doc = extract.load(write_drawn_pdf(tmp_path / "ok.pdf", GOOD_LINES, size=10))
    assert {c.key: c for c in checks.run(doc).checks}["font_size_sane"].passed


@pytest.mark.parametrize(
    "fonts,expected",
    [
        # A LaTeX resume: subset-embedded, and correct. Flagging this would make the
        # check useless, since it is what the real resume actually contains.
        ({"IWZWNL+NimbusRomNo9L-Regu", "IYELBM+CMSY10"}, []),
        ({"Helvetica", "Times-Roman"}, []),                      # base-14, never embedded
        ({"ABCDEF+Papyrus"}, ["Papyrus (decorative)"]),
        ({"BrushScriptMT"}, ["BrushScriptMT (decorative)"]),
        ({"FooBarDisplay"}, ["FooBarDisplay (not embedded)"]),
    ],
)
def test_suspicious_font_detection(fonts: set, expected: list):
    assert extract.LayoutFacts(fonts=fonts).suspicious_fonts == expected


def test_a_single_low_line_is_not_a_running_footer():
    """The real resume has one line in the bottom band: the last bullet, on one page."""
    facts = extract.LayoutFacts(available=True, pages=1, bottom_band_pages={0},
                                edge_text=["cutting build time from 9 to 4 minutes"])
    assert checks._edge_problem(facts) == ""


def test_contact_details_in_the_edge_band_are_flagged():
    facts = extract.LayoutFacts(available=True, pages=1, top_band_pages={0},
                                edge_text=["jane@example.com | (480) 555-0123"])
    assert "Contact details" in checks._edge_problem(facts)


def test_text_in_the_same_band_on_every_page_is_a_running_header():
    facts = extract.LayoutFacts(available=True, pages=2, top_band_pages={0, 1},
                                edge_text=["Jane Doe - page 1", "Jane Doe - page 2"])
    assert "running header" in checks._edge_problem(facts)


def test_a_band_on_only_one_of_two_pages_is_not_a_header():
    facts = extract.LayoutFacts(available=True, pages=2, top_band_pages={0},
                                edge_text=["a heading that happens to sit high"])
    assert checks._edge_problem(facts) == ""


def test_structure_checks_are_not_applicable_without_page_layout(tmp_path: Path):
    """A .txt file has no geometry; guessing would be worse than skipping."""
    by_key = _score(tmp_path, GOOD_LINES)
    for key in ("no_images", "no_tables", "no_header_footer_content",
                "standard_fonts", "font_size_sane"):
        assert by_key[key].applicable is False
        assert by_key[key].max_points == 0


def test_a_latex_style_resume_passes_every_structure_check(tmp_path: Path):
    """The regression that matters: these must not fire on a normal LaTeX resume.

    Checked against the real one during development - subset-embedded Nimbus Roman
    plus CMSY10 for the bullet glyph, 9pt body, four section rules and no table - and
    reproduced here with a generated file so the suite still needs no personal data.
    """
    doc = extract.load(write_drawn_pdf(tmp_path / "latex.pdf", GOOD_LINES,
                                       SECTION_RULES, size=10))
    by_key = {c.key: c for c in checks.run(doc).checks}
    for key in ("no_images", "no_tables", "no_header_footer_content",
                "standard_fonts", "font_size_sane"):
        assert by_key[key].applicable and by_key[key].passed, by_key[key].message


# ------------------------------------------------------------------- multi-column (A1)

LEFT_COLUMN = [
    "EXPERIENCE",
    "Software Intern, May 2024 - Jul 2024",
    "- Built a data pipeline that cut ingest",
    "- Automated 12 nightly reports each week",
    "- Deployed 2 containers to CI",
]
RIGHT_COLUMN = [
    "SKILLS",
    "Python, Java, Docker",
    "EDUCATION",
    "Arizona State University",
    "BS Computer Science, Dec 2027",
]


def test_two_column_pdf_is_detected_and_read_column_by_column(tmp_path: Path):
    doc = extract.load(write_columns_pdf(tmp_path / "two-col.pdf",
                                         [LEFT_COLUMN, RIGHT_COLUMN]))
    assert doc.columns == 2
    assert doc.columns_resolved is True
    # Read in column order the left column finishes before the right one starts.
    order = doc.layout_text.index("SKILLS") > doc.layout_text.index("Deployed 2 containers")
    assert order
    assert len(doc.bullets) == 3
    assert doc.bullets[0].startswith("Built a data pipeline")


def test_single_column_pdf_is_not_misdetected(good_pdf: Path):
    doc = extract.load(good_pdf)
    assert doc.columns == 1
    assert doc.columns_resolved is True


def test_three_columns_are_reported_unresolved(tmp_path: Path):
    third = ["AWARDS", "Deans List 2025", "Hackathon winner", "Scholarship", "Merit"]
    doc = extract.load(write_columns_pdf(
        tmp_path / "three-col.pdf", [LEFT_COLUMN, RIGHT_COLUMN, third],
        xs=[40, 240, 440]))
    assert doc.columns_resolved is False
    assert doc.bullets == []


def test_unresolved_columns_skip_the_bullet_checks_instead_of_scoring_garbage(tmp_path: Path):
    third = ["AWARDS", "Deans List 2025", "Hackathon winner", "Scholarship", "Merit"]
    doc = extract.load(write_columns_pdf(
        tmp_path / "three-col.pdf", [LEFT_COLUMN, RIGHT_COLUMN, third],
        xs=[40, 240, 440]))
    by_key = {c.key: c for c in checks.run(doc).checks}
    for key in ("quantified", "bullet_length", "long_bullets", "action_verbs",
                "verb_variety", "tense_consistency"):
        assert by_key[key].applicable is False
        assert by_key[key].max_points == 0
    assert "column" in by_key["quantified"].message


HYBRID_HEADER = [
    "JORDAN REYES  jordan@example.edu  (602) 555-0114  linkedin.com/in/j  github.com/j",
    "EDUCATION   State University, BS Computer Science, Aug 2023 - May 2027",
]
HYBRID_LEFT = ["SKILLS", "Python, Docker,", "PostgreSQL, React,", "Redis, FastAPI", "Git, Linux"]
HYBRID_RIGHT = [
    "EXPERIENCE",
    "Intern, May 2026 - Aug 2026",
    "- Built a REST API in FastAPI serving 2000 requests",
    "- Automated 9 release checks removing 6 hours",
    "- Profiled 5 queries cutting runtime to 8 minutes",
]
HYBRID_FOOTER = ["ADDITIONAL   Available May through August 2027 for a full-time internship"]


@pytest.fixture
def hybrid_pdf(tmp_path: Path) -> Path:
    """A realistic two-column resume: full-width header, columns, full-width footer."""
    return write_blocks_pdf(tmp_path / "hybrid.pdf", [
        (40, 750, HYBRID_HEADER),
        (40, 700, HYBRID_LEFT),
        (330, 700, HYBRID_RIGHT),
        (40, 600, HYBRID_FOOTER),
    ])


def test_hybrid_two_column_resume_is_detected(hybrid_pdf: Path):
    """Regression: one full-width header line used to defeat detection entirely.

    `find_gutters` required a band that *zero* lines crossed, so a hybrid page - which
    is what almost every real two-column resume is - scored as a single column.
    """
    doc = extract.load(hybrid_pdf)
    assert doc.columns == 2
    assert doc.columns_resolved is True


def test_hybrid_layout_finds_every_bullet(hybrid_pdf: Path):
    doc = extract.load(hybrid_pdf)
    assert len(doc.bullets) == 3
    assert doc.bullets[0].startswith("Built a REST API")


def test_hybrid_layout_keeps_the_full_width_header_and_footer(hybrid_pdf: Path):
    """Spanning lines belong to neither column and must not be dropped."""
    doc = extract.load(hybrid_pdf)
    assert "jordan@example.edu" in doc.layout_text
    assert "ADDITIONAL" in doc.layout_text
    # The header is read before the columnar body it sits above.
    assert doc.layout_text.index("JORDAN REYES") < doc.layout_text.index("SKILLS")


def test_hybrid_columns_are_read_one_at_a_time(hybrid_pdf: Path):
    doc = extract.load(hybrid_pdf)
    assert doc.layout_text.index("Git, Linux") < doc.layout_text.index("EXPERIENCE")


def test_interleaved_layout_text_loses_bullets(tmp_path: Path):
    """Why re-reading by column matters, in a form this suite can actually run.

    With poppler installed - as it is in the Dockerfile - `pdftotext -layout` emits a
    hybrid page one *physical* line at a time, so a right-column bullet is preceded by
    left-column text and the `^`-anchored bullet pattern never matches. This test
    pins the failure mode; `order_columns` is what prevents it reaching the checks.
    """
    interleaved = (
        "JORDAN REYES  jordan@example.edu\n"
        "   SKILLS                        EXPERIENCE\n"
        "Python, Docker, PostgreSQL       • Built a REST API serving 2000 requests\n"
        "Redis, FastAPI                   • Automated 9 release checks each sprint\n"
    )
    assert collect_bullets(interleaved) == []


def test_order_columns_repairs_interleaved_reading_order(hybrid_pdf: Path):
    """The repair does not depend on which extractor produced the text."""
    lines, width = extract.pdf_lines(hybrid_pdf)
    gutters = extract.find_gutters(lines, width)
    assert len(gutters) == 1
    rebuilt = extract.order_columns(lines, gutters[0])
    assert len(collect_bullets(rebuilt)) == 3


def test_the_real_single_column_resume_is_not_misdetected(good_pdf: Path):
    """The regression that matters most: a false positive scrambles a good document."""
    lines, width = extract.pdf_lines(good_pdf)
    assert extract.find_gutters(lines, width) == []


def test_a_narrow_accidental_gap_is_not_a_gutter():
    """A one-sample hit is where the words happened to end, not a column boundary."""
    lines = [extract.Line(0, 40.0, 300.0, float(i), "left") for i in range(8)]
    lines += [extract.Line(0, 302.0, 560.0, float(i), "right") for i in range(8)]
    # A 2pt gap is narrower than min_width and must be rejected.
    assert extract.find_gutters(lines, 612.0, min_width=6.0) == []


def test_find_gutters_tolerates_a_minority_of_spanning_lines():
    lines = [extract.Line(0, 40.0, 260.0, float(i), "left") for i in range(8)]
    lines += [extract.Line(0, 340.0, 560.0, float(i), "right") for i in range(8)]
    lines += [extract.Line(0, 40.0, 560.0, -1.0, "full width header")]
    gutters = extract.find_gutters(lines, 612.0)
    assert len(gutters) == 1
    assert 260 < gutters[0] < 340


def test_find_gutters_rejects_a_page_of_full_width_lines():
    """Too many crossings means there is no column structure to find."""
    lines = [extract.Line(0, 40.0, 560.0, float(i), "full width") for i in range(10)]
    assert extract.find_gutters(lines, 612.0) == []


@pytest.fixture
def date_column_pdf(tmp_path: Path) -> Path:
    """Two columns with right-aligned dates between them - a very common layout."""
    return write_blocks_pdf(tmp_path / "dates.pdf", [
        (40, 750, HYBRID_HEADER[:1]),
        (40, 700, ["EXPERIENCE", "Northwind Systems", "Cobalt Analytics",
                   "State University"]),
        (250, 700, ["2026", "2025", "2024", "2023"]),
        (350, 700, ["PROJECTS",
                    "- Built a REST API serving 2000 requests each second",
                    "- Automated 9 release checks removing 6 hours of work"]),
    ])


def test_a_narrow_date_column_does_not_read_as_a_third_column(date_column_pdf: Path):
    """Regression: the empty region split into two bands, so a two-column resume was
    reported unresolved and lost every bullet."""
    lines, width = extract.pdf_lines(date_column_pdf)
    assert len(extract.find_gutters(lines, width)) == 2  # two raw candidates
    assert len(extract.prune_gutters(lines, extract.find_gutters(lines, width))) == 1

    doc = extract.load(date_column_pdf)
    assert doc.columns == 2
    assert doc.columns_resolved is True
    assert len(doc.bullets) == 2


def test_pruning_keeps_a_genuine_three_column_layout_unresolved(tmp_path: Path):
    """The discrimination that matters: three real columns all carry text."""
    path = write_blocks_pdf(tmp_path / "three.pdf", [
        (40, 750, HYBRID_HEADER[:1]),
        (40, 700, ["SKILLS", "Python", "Docker", "Redis", "Linux"]),
        (240, 700, ["EDUCATION", "State University", "BS CS 2027", "GPA 3.9", "Deans List"]),
        (440, 700, ["AWARDS", "Hackathon", "Scholarship", "Merit", "Finalist"]),
    ])
    lines, width = extract.pdf_lines(path)
    assert len(extract.prune_gutters(lines, extract.find_gutters(lines, width))) == 2
    assert extract.load(path).columns_resolved is False


def test_column_mass_ignores_lines_that_span_a_gutter():
    """A full-width header belongs to no column; counting it by midpoint inflates
    whichever column the middle lands in - the exact case pruning must detect."""
    lines = [
        extract.Line(0, 40.0, 560.0, 0.0, "A FULL WIDTH HEADER LINE WITH LOTS OF TEXT"),
        extract.Line(0, 40.0, 200.0, 1.0, "left side"),
        extract.Line(0, 400.0, 560.0, 2.0, "right side"),
    ]
    assert extract.column_mass(lines, [300.0]) == [len("left side"), len("right side")]


def test_prune_rejects_a_single_gutter_with_an_empty_side():
    lines = [extract.Line(0, 40.0, 200.0, float(i), "left content here") for i in range(8)]
    lines.append(extract.Line(0, 400.0, 420.0, 9.0, "x"))
    assert extract.prune_gutters(lines, [300.0]) == []


def test_find_gutters_ignores_a_wide_margin():
    """A margin crosses no text but has nothing on one side - not a column boundary."""
    lines = [extract.Line(0, 300.0, 500.0, float(i), f"line {i}") for i in range(10)]
    assert extract.find_gutters(lines, 612.0) == []


def test_find_gutters_needs_content_on_both_sides():
    lines = [extract.Line(0, 40.0, 200.0, float(i), "left") for i in range(9)]
    lines.append(extract.Line(0, 400.0, 500.0, 9.0, "lonely"))
    assert extract.find_gutters(lines, 612.0) == []


# ------------------------------------------------------------------------- tense (A2)


@pytest.mark.parametrize(
    "bullet,tense",
    [
        ("Built the parser", "past"), ("Automated the report", "past"),
        ("Led the migration", "past"), ("Wrote the docs", "past"),
        ("Build the parser", "present"), ("Maintain the cluster", "present"),
        ("Fit a logistic model", "ambiguous"), ("Read the manifest", "ambiguous"),
    ],
)
def test_verb_tense_classification(bullet: str, tense: str):
    assert checks.verb_tense(bullet) == tense


def test_group_entries_splits_on_date_ranges():
    layout = "\n".join([
        "Software Intern, May 2024 - Jul 2024",
        "- Built the ingest pipeline for the team",
        "Support Analyst, Aug 2025 - Present",
        "- Resolve tickets for students each day",
    ])
    entries = extract.group_entries(layout)
    assert [e.current for e in entries] == [False, True]
    assert entries[0].bullets == ["Built the ingest pipeline for the team"]
    assert entries[1].bullets == ["Resolve tickets for students each day"]


@pytest.mark.parametrize(
    "dash",
    ["-", "–", "—", "−", "to"],  # hyphen, en, em, MINUS SIGN, word
)
def test_entry_ranges_accept_every_dash_a_resume_uses(dash: str):
    """Regression: a real LaTeX resume wrote "June 2026 - Present" with U+2212, and
    the entry parser found zero entries."""
    layout = f"Support Analyst, June 2026 {dash} Present\n- Resolve tickets for students"
    entries = extract.group_entries(layout)
    assert len(entries) == 1 and entries[0].current is True


def test_mixed_tense_within_one_entry_is_flagged(tmp_path: Path):
    lines = GOOD_LINES[:7] + [
        "- Built the ingest pipeline for the analytics team",
        "- Maintain the nightly report job for the same team",
    ]
    by_key = _score(tmp_path, lines)
    assert not by_key["tense_consistency"].passed
    assert "mixes past and present" in by_key["tense_consistency"].evidence[0]


def test_a_finished_role_in_present_tense_is_flagged(tmp_path: Path):
    lines = GOOD_LINES[:7] + ["- Maintain the nightly report job for the team"]
    by_key = _score(tmp_path, lines)
    assert not by_key["tense_consistency"].passed
    assert "present tense" in by_key["tense_consistency"].evidence[0]


def test_a_current_role_in_present_tense_is_not_flagged(tmp_path: Path):
    lines = GOOD_LINES[:6] + [
        "Support Analyst, Aug 2025 - Present",
        "- Resolve tickets for students across the campus network",
        "- Maintain the incident log for the support team",
    ]
    assert _score(tmp_path, lines)["tense_consistency"].passed


def test_consistent_past_tense_passes(tmp_path: Path):
    assert _score(tmp_path, GOOD_LINES)["tense_consistency"].passed


def test_tense_check_is_skipped_when_no_dated_entries(tmp_path: Path):
    lines = ["PROJECTS", "- Built a parser that handled 40 formats", PADDING]
    assert _score(tmp_path, lines)["tense_consistency"].applicable is False


# ----------------------------------------------------------------------------- checks


def _score(tmp_path: Path, lines: list[str], name: str = "r.txt") -> dict:
    path = tmp_path / name
    path.write_text("\n".join(lines), encoding="utf-8")
    return {c.key: c for c in checks.run(extract.load(path)).checks}


def test_missing_email_fails_the_email_check(tmp_path: Path):
    lines = [line for line in GOOD_LINES if "test@example.com" not in line]
    by_key = _score(tmp_path, lines)
    assert not by_key["email"].passed
    assert by_key["email"].points == 0


def test_present_email_passes_and_reports_evidence(tmp_path: Path):
    by_key = _score(tmp_path, GOOD_LINES)
    assert by_key["email"].passed
    assert by_key["email"].evidence == ["test@example.com"]


def test_long_bullets_are_flagged(tmp_path: Path):
    long_bullet = "• Built " + " ".join(f"thing{i}" for i in range(45))
    by_key = _score(tmp_path, GOOD_LINES[:7] + [long_bullet])
    assert not by_key["long_bullets"].passed
    assert not by_key["bullet_length"].passed
    assert by_key["long_bullets"].evidence


def test_first_person_pronouns_are_flagged(tmp_path: Path):
    by_key = _score(tmp_path, GOOD_LINES + ["I led my team through the migration"])
    assert not by_key["no_pronouns"].passed
    assert set(by_key["no_pronouns"].evidence) == {"I", "my"}


# The language checks are skipped as inapplicable below MIN_WORDS_FOR_LANGUAGE, so a
# one-line probe would pass vacuously. Pad it out to a document worth judging.
PADDING = " ".join(f"detail{i}" for i in range(60))


@pytest.mark.parametrize(
    "line",
    [
        "Coursework: Calculus I-III and Physics I",
        "Coursework: Calculus I and Statistics II",
        "Completed Physics I, Physics II, and Linear Algebra",
    ],
)
def test_roman_numeral_coursework_does_not_trip_the_pronoun_check(tmp_path: Path, line: str):
    check = _score(tmp_path, [line, PADDING])["no_pronouns"]
    assert check.applicable and check.passed


def test_a_lone_pronoun_i_is_still_caught(tmp_path: Path):
    by_key = _score(tmp_path, ["I built the parser after taking Calculus I-III", PADDING])
    assert not by_key["no_pronouns"].passed
    assert by_key["no_pronouns"].evidence == ["I"]


def test_a_course_number_ending_a_line_is_not_a_pronoun(tmp_path: Path):
    """A pronoun's verb sits on its own line; a trailing course number does not."""
    by_key = _score(tmp_path, ["Coursework: Physics I", "advanced topics follow", PADDING])
    assert by_key["no_pronouns"].passed


def test_filler_phrases_are_flagged(tmp_path: Path):
    by_key = _score(tmp_path, GOOD_LINES + ["Responsible for the nightly build"])
    assert not by_key["no_filler"].passed
    assert "responsible for" in by_key["no_filler"].evidence


def test_missing_sections_are_named(tmp_path: Path):
    by_key = _score(tmp_path, ["Jane Doe", "jane@example.com"])
    assert not by_key["sections"].passed
    assert set(by_key["sections"].evidence) == {
        "education", "experience", "skills", "projects"
    }


def test_no_bullets_zeroes_the_bullet_checks(tmp_path: Path):
    by_key = _score(tmp_path, ["Jane Doe", "jane@example.com", "EDUCATION"])
    for key in ("quantified", "bullet_length", "long_bullets", "action_verbs", "verb_variety"):
        assert not by_key[key].passed
        assert by_key[key].message == "No bullets detected"


def test_report_totals_and_percent(good_pdf: Path):
    report = checks.run(extract.load(good_pdf))
    assert report.max_score == MAX_SCORE
    assert 0 <= report.score <= report.max_score
    assert report.to_dict()["percent"] == pytest.approx(report.percent, abs=0.05)
    assert all(not c.passed for c in report.failures)


def test_every_check_declares_a_known_category(good_pdf: Path):
    report = checks.run(extract.load(good_pdf))
    assert {c.category for c in report.checks} <= set(checks.CATEGORIES)
    # Categories must partition the score, or the per-category percentages lie.
    cats = report.by_category()
    assert sum(c["max_score"] for c in cats.values()) == report.max_score
    assert sum(c["score"] for c in cats.values()) == report.score


def test_category_weights_match_the_documented_table(good_pdf: Path):
    """docs/scoring.md publishes these totals; drift makes the documentation wrong."""
    cats = checks.run(extract.load(good_pdf)).by_category()
    assert {name: c["max_score"] for name, c in cats.items()} == {
        "ats": 67, "impact": 18, "brevity": 27, "style": 28
    }


def test_category_breakdown_localises_a_weakness(tmp_path: Path):
    """A resume that is clean but duty-listing should lose points in impact, not ats."""
    lines = GOOD_LINES[:7] + [
        "- Responsible for the nightly build process across the whole platform",
        "- Worked on the reporting service with the rest of the group",
    ]
    path = tmp_path / "duties.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    cats = checks.run(extract.load(path)).by_category()
    assert "quantified" in cats["impact"]["failed"]
    assert cats["impact"]["percent"] < cats["ats"]["percent"]


# ------------------------------------------------------------------ passive voice


def test_passive_voice_is_flagged(tmp_path: Path):
    by_key = _score(tmp_path, GOOD_LINES + ["The service was rebuilt by the platform group"])
    assert not by_key["active_voice"].passed
    assert any("was rebuilt" in e for e in by_key["active_voice"].evidence)


def test_active_voice_passes(tmp_path: Path):
    assert _score(tmp_path, GOOD_LINES)["active_voice"].passed


@pytest.mark.parametrize(
    "phrase",
    [
        "the model is detailed and was based on a survey",
        "candidates are required to be motivated",
        # A passive gerund after a subordinator is correct English, not a defect.
        "evaluated once rather than being tuned against it",
        "shipped without being reviewed twice",
    ],
)
def test_passive_allowlist_avoids_false_positives(phrase: str):
    assert checks.find_passive(phrase) == []


def test_passive_still_caught_in_a_main_clause():
    assert checks.find_passive("The parser was written by an intern") == ["was written"]


# --------------------------------------------------------------------- tech casing


def test_miscapitalised_tools_are_flagged(tmp_path: Path):
    by_key = _score(tmp_path, GOOD_LINES + ["Skills: javascript, Pytorch, postgresql"])
    assert not by_key["tech_casing"].passed
    joined = " ".join(by_key["tech_casing"].evidence)
    assert "javascript should be JavaScript" in joined
    assert "Pytorch should be PyTorch" in joined


def test_urls_are_not_treated_as_miscapitalisation():
    # github.com is correct lowercase; only the bare word should be flagged.
    assert checks.find_miscased("github.com/mghadia1 and mail@github.com") == []
    assert checks.find_miscased("Hosted on github") == ["github should be GitHub"]


def test_all_caps_headings_are_not_flagged():
    assert checks.find_miscased("SKILLS: PYTHON, JAVA") == []


# ---------------------------------------------------------------------- word count


@pytest.mark.parametrize(
    "count,passes",
    [(120, False), (500, True), (1200, False)],
)
def test_word_count_band(tmp_path: Path, count: int, passes: bool):
    filler = " ".join(f"word{i}" for i in range(count))
    by_key = _score(tmp_path, ["EDUCATION", filler])
    assert by_key["word_count"].passed is passes


# -------------------------------------------------------------------- date formats


def test_mixed_date_formats_are_flagged(tmp_path: Path):
    by_key = _score(tmp_path, ["Intern May 2024 - Jul 2024", "Analyst 05/2023 - 09/2023"])
    assert not by_key["date_format"].passed
    assert set(by_key["date_format"].evidence) == {"Month YYYY", "MM/YYYY"}


def test_one_consistent_date_format_passes(tmp_path: Path):
    assert _score(tmp_path, GOOD_LINES)["date_format"].passed


def test_date_styles_detects_each_form():
    assert checks.date_styles("May 2024") == ["Month YYYY"]
    assert checks.date_styles("May '24") == ["Mon 'YY"]
    assert checks.date_styles("2024-05") == ["YYYY-MM"]
    assert checks.date_styles("05/2024") == ["MM/YYYY"]


@pytest.mark.parametrize(
    "ratio",
    [
        "deterministic 60/20/20 splits",   # train/val/test split
        "counted every object in 20/20 trials",
        "global Otsu managed 1/20",
        "10/10 randomized targets reached",
        "the 2024-25 academic year",
    ],
)
def test_ratios_are_not_mistaken_for_dates(ratio: str):
    """Regression: an unanchored MM/YY pattern read ML split ratios as dates."""
    assert checks.date_styles(ratio) == []


# ------------------------------------------------------------------- verb variety


def test_repeated_openers_are_named(tmp_path: Path):
    lines = ["EXPERIENCE"] + [f"- Built the component number {i} for the platform" for i in range(4)]
    by_key = _score(tmp_path, lines)
    assert not by_key["verb_variety"].passed
    assert by_key["verb_variety"].evidence == ["built (x4)"]


# --------------------------------------------------------------------------- keywords


def test_multiword_phrase_matches_as_one_term():
    terms = keywords.tokenize("We use machine learning daily")
    assert "machine learning" in terms
    assert "machine" not in terms and "learning" not in terms


JD = """Software Engineer Intern
We are looking for a candidate with Python and Docker experience.
You will build a data pipeline. PostgreSQL is required. PostgreSQL and Kubernetes
are used across the team. Strong communication is preferred.
"""


def test_missing_terms_are_reported():
    match = keywords.compare("Python and Docker", "Python, Docker, PostgreSQL")
    assert "postgresql" in match.missing
    assert {"python", "docker"} <= set(match.matched)


def test_empty_job_description_returns_zero_without_dividing():
    match = keywords.compare("Python and Docker", "   ")
    assert match.percent == 0.0
    assert match.matched == [] and match.missing == []


def test_terms_are_split_into_hard_and_soft():
    match = keywords.compare("Python and Docker", JD)
    assert "postgresql" in match.hard_missing
    assert "kubernetes" in match.hard_missing
    assert "communication" in match.soft_missing
    assert "communication" not in match.hard_missing
    assert set(match.hard_matched) == {"python", "docker", "data pipeline"} & set(match.matched)


@pytest.mark.parametrize(
    "term,kind",
    [("python", "hard"), ("rest api", "hard"), ("c++", "hard"), ("python3", "hard"),
     ("communication", "soft"), ("leadership", "soft"), ("mission", "other")],
)
def test_classify_sorts_terms(term: str, kind: str):
    assert keywords.classify(term) == kind


def test_a_missing_hard_skill_costs_more_than_a_missing_soft_skill():
    """The whole point of weighting: not every absent word is equally expensive."""
    jd = "Kubernetes. Communication."
    lost_hard = keywords.compare("Communication skills", jd).percent
    lost_soft = keywords.compare("Kubernetes clusters", jd).percent
    assert lost_soft > lost_hard


def test_emphasis_counts_repeated_posting_terms():
    match = keywords.compare("Python", JD)
    assert match.emphasis["postgresql"] == 2
    # Priority puts the twice-mentioned term ahead of once-mentioned ones.
    assert match.priority[0] == "postgresql"


def test_job_title_is_extracted_and_matched():
    match = keywords.compare(
        "Software Engineer Intern at Acme, built things with Python", JD
    )
    assert match.title == "Software Engineer Intern"
    assert match.title_matched is True


def test_job_title_absent_from_resume_is_reported():
    match = keywords.compare("Data Analyst who used Python", JD)
    assert match.title_matched is False


def test_title_match_ignores_seniority_and_term_noise():
    assert keywords.title_present("Software Engineer Intern - Summer 2027",
                                  "Software Engineer, Acme Corp")
    assert not keywords.title_present("Mechanical Engineer", "Software Engineer")


def test_labelled_titles_are_preferred_over_the_first_line():
    jd = "About us\nWe are great.\nJob Title: Backend Engineer\nYou will write Go."
    assert keywords.job_title(jd) == "Backend Engineer"


def test_title_weight_moves_the_match_rate():
    with_title = keywords.compare("Software Engineer Intern, Python, Docker", JD).percent
    without = keywords.compare("Python, Docker", JD).percent
    assert with_title > without


# ------------------------------------------------------------------ competencies


@pytest.mark.parametrize(
    "bullet,wrongly_claimed",
    [
        ("while global Otsu managed 1/20", "leadership"),
        ("Wrote a deterministic scene generator", "communication"),
        ("Supported 500 concurrent connections", "teamwork"),
        ("Reviewed the cache contents before each run", "teamwork"),
        ("Owned the retry logic", "leadership"),
    ],
)
def test_thing_objects_do_not_earn_people_competencies(bullet: str, wrongly_claimed: str):
    """Regression: a lexical detector read "global Otsu managed 1/20" as leadership."""
    assert wrongly_claimed not in competencies.assess([bullet]).covered


@pytest.mark.parametrize(
    "bullet,competency",
    [
        ("Managed a team of 4 engineers through the migration", "leadership"),
        ("Owned the release process for the platform group", "leadership"),
        ("Supported two junior interns during onboarding", "teamwork"),
        ("Wrote the API documentation for the service", "communication"),
        ("Reported the results in a paper", "communication"),
    ],
)
def test_human_objects_still_earn_people_competencies(bullet: str, competency: str):
    assert competency in competencies.assess([bullet]).covered


def test_people_competencies_are_undetectable_not_missing_on_a_solo_resume():
    report = competencies.assess([
        "Built a parser from scratch",
        "Analyzed 2000 samples for the accuracy regression",
    ])
    assert report.undetectable == ["leadership", "teamwork"]
    assert "leadership" not in report.missing
    # Percent is over what could be judged, so solo work is not punished for it.
    assert report.missing == ["communication"]
    assert report.percent == pytest.approx(200 / 3, abs=0.1)


def test_real_resume_no_longer_claims_leadership_from_an_algorithm():
    report = competencies.assess([
        "Benchmarked four segmentation methods head to head across 20 seeded scenes "
        "while global Otsu managed 1/20",
    ])
    assert "leadership" not in report.covered
    assert "analytical" in report.covered


def test_competencies_detects_each_signal():
    report = competencies.assess([
        "Analyzed 2000 samples to find the accuracy regression",
        "Presented the findings to the research group",
        "Led a team of 4 through the migration",
        "Collaborated with the platform team on code review",
        "Built the pipeline from scratch",
    ])
    assert report.missing == []
    assert report.percent == 100.0


def test_competencies_reports_what_is_absent():
    report = competencies.assess(["Built a parser", "Created a dashboard"])
    assert "initiative" in report.covered
    assert "communication" in report.missing
    # Leadership is not absent here, it is invisible - a different claim.
    assert "leadership" in report.undetectable


def test_competency_detail_carries_an_example_and_terms():
    report = competencies.assess(["Led a team of 4 through the migration"])
    detail = {d["name"]: d for d in report.to_dict()["detail"]}
    assert detail["leadership"]["covered"] is True
    assert "led" in detail["leadership"]["terms"]
    assert detail["leadership"]["example"].startswith("Led a team")
    assert detail["analytical"]["example"] == ""


def test_model_training_does_not_count_as_teaching():
    """'Trained a U-Net' is not evidence of communication; the lexicon excludes it."""
    report = competencies.assess(["Trained a U-Net on 590 images"])
    assert "communication" in report.missing


def test_competencies_with_no_bullets_is_empty_not_an_error():
    report = competencies.assess([])
    assert report.covered == []
    assert report.percent == 0.0


# ------------------------------------------------------ inapplicable checks (Bug 2)


def test_a_near_empty_file_cannot_collect_points_for_absence(tmp_path: Path):
    """Regression: a file containing "x" scored 32% by passing every 'contains
    nothing bad' check."""
    path = tmp_path / "x.txt"
    path.write_text("x", encoding="utf-8")
    report = checks.run(extract.load(path))
    assert report.percent < 10
    assert report.max_score < MAX_SCORE


def test_absence_checks_are_marked_not_applicable_not_passed(tmp_path: Path):
    path = tmp_path / "x.txt"
    path.write_text("x", encoding="utf-8")
    by_key = {c.key: c for c in checks.run(extract.load(path)).checks}
    for key in ("no_pronouns", "no_filler", "active_voice", "tech_casing",
                "clean_extraction", "date_format"):
        assert by_key[key].applicable is False
        assert by_key[key].max_points == 0


def test_skipped_checks_are_not_reported_as_failures(tmp_path: Path):
    path = tmp_path / "x.txt"
    path.write_text("x", encoding="utf-8")
    report = checks.run(extract.load(path))
    assert all(c.applicable for c in report.failures)
    assert {c.key for c in report.skipped} >= {"no_pronouns", "tech_casing"}


def test_a_full_resume_keeps_every_check_applicable(good_pdf: Path):
    report = checks.run(extract.load(good_pdf))
    assert report.skipped == []
    assert report.max_score == MAX_SCORE


def test_no_dates_marks_the_format_check_inapplicable(tmp_path: Path):
    lines = [line for line in GOOD_LINES if "202" not in line and "2023" not in line]
    by_key = _score(tmp_path, lines)
    assert by_key["date_format"].applicable is False


# ------------------------------------------------------ the Analysis object (Bug 3)


def test_analysis_to_dict_is_actually_json_serialisable(good_pdf: Path):
    """Regression: analyze() promised a JSON-ready dict but smuggled in a Report."""
    analysis = analyze(good_pdf, "Python and Docker")
    json.dumps(analysis.to_dict())  # must not raise


def test_analysis_exposes_the_real_objects(good_pdf: Path):
    analysis = analyze(good_pdf, "Python and Docker")
    assert isinstance(analysis.report, checks.Report)
    assert isinstance(analysis.keyword_match, keywords.KeywordMatch)
    assert analysis.score == analysis.report.score
    assert analysis.percent == analysis.report.percent


def test_analysis_omits_keywords_when_no_job_description(good_pdf: Path):
    assert analyze(good_pdf).keyword_match is None
    assert "keywords" not in analyze(good_pdf).to_dict()


# --------------------------------------------------------------------------- discover


def test_discover_finds_a_resume_named_file(tmp_path: Path):
    write_pdf(tmp_path / "Jane-Doe-Resume.pdf", GOOD_LINES)
    found = discover.find(tmp_path)
    assert [c.path.name for c in found] == ["Jane-Doe-Resume.pdf"]
    assert found[0].confidence == 1.0
    assert any("filename" in r for r in found[0].reasons)


def test_discover_skips_node_modules(tmp_path: Path):
    nested = tmp_path / "node_modules" / "pkg"
    nested.mkdir(parents=True)
    write_pdf(nested / "resume.pdf", GOOD_LINES)
    assert discover.find(tmp_path) == []


def test_discover_rejects_a_non_resume_document(tmp_path: Path):
    write_pdf(tmp_path / "meeting-notes.pdf", ["Agenda", "Discuss the roadmap"])
    assert discover.find(tmp_path) == []


def test_discover_requires_a_directory(tmp_path: Path):
    path = tmp_path / "resume.txt"
    path.write_text("hi", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        discover.find(path)


# ---------------------------------------------------------------------------- tracker


def test_tracker_writes_header_once_then_appends_rows(tmp_path: Path, good_pdf: Path):
    report = checks.run(extract.load(good_pdf))
    log = tmp_path / "logs" / "runs.csv"

    tracker.append(log, resume="a.pdf", report=report, company="Acme", role="SWE Intern")
    tracker.append(log, resume="b.pdf", report=report, company="Globex")

    assert log.read_text(encoding="utf-8").count("timestamp,resume") == 1
    rows = tracker.read(log)
    assert [r["company"] for r in rows] == ["Acme", "Globex"]
    assert rows[0]["role"] == "SWE Intern"
    assert rows[0]["score"] == str(report.score)


def test_tracker_read_missing_log_returns_empty(tmp_path: Path):
    assert tracker.read(tmp_path / "nope.csv") == []


def test_tracker_records_keyword_misses(tmp_path: Path, good_pdf: Path):
    report = checks.run(extract.load(good_pdf))
    match = keywords.compare("Python", "Python, Kubernetes")
    log = tmp_path / "runs.csv"
    tracker.append(log, resume="a.pdf", report=report, keyword_match=match)
    assert "kubernetes" in tracker.read(log)[0]["missing_keywords"]


# ---------------------------------------------------------------------------- suggest


def test_parse_rewrites_handles_a_fenced_json_block():
    text = (
        '```json\n{"rewrites": [{"original": "did stuff", '
        '"suggested": "Built a parser", "why": "stronger verb"}]}\n```'
    )
    out = suggest.parse_rewrites(text)
    assert len(out) == 1
    assert out[0].suggested == "Built a parser"
    assert out[0].to_dict()["why"] == "stronger verb"


def test_parse_rewrites_survives_malformed_json():
    assert suggest.parse_rewrites("{not json at all") == []
    assert suggest.parse_rewrites("no braces here") == []


def test_parse_rewrites_drops_entries_with_no_suggestion():
    text = '{"rewrites": [{"original": "x", "suggested": ""}, {"suggested": "Built it"}]}'
    assert [s.suggested for s in suggest.parse_rewrites(text)] == ["Built it"]


def test_rewrite_without_a_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(suggest.SuggestionUnavailable, match="ANTHROPIC_API_KEY"):
        suggest.rewrite(["Built a thing"])


def test_rewrite_posts_bullets_and_parses_the_reply(monkeypatch):
    """The API is mocked - no test in this suite makes a network call."""
    payload = json.dumps(
        {"content": [{"text": '{"rewrites": [{"original": "did stuff", '
                              '"suggested": "Built a parser", "why": "verb"}]}'}]}
    ).encode()

    captured = {}

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.data)
        return FakeResponse(payload)

    monkeypatch.setattr(suggest.urllib.request, "urlopen", fake_urlopen)
    out = suggest.rewrite(["did stuff"], api_key="test-key")

    assert [s.suggested for s in out] == ["Built a parser"]
    assert captured["headers"]["X-api-key"] == "test-key"
    assert "did stuff" in captured["body"]["messages"][0]["content"]


def test_rewrite_with_no_bullets_skips_the_call():
    with mock.patch.object(suggest.urllib.request, "urlopen") as urlopen:
        assert suggest.rewrite([], api_key="test-key") == []
    urlopen.assert_not_called()


def test_weak_bullets_prefers_the_flagged_evidence(good_pdf: Path):
    doc = extract.load(good_pdf)
    report = checks.run(doc)
    picked = suggest.weak_bullets(doc, report)
    assert picked and all(p in doc.bullets for p in picked)


# ---------------------------------------------------------------------- analyze / API


def test_analyze_returns_a_json_ready_payload(good_pdf: Path):
    result = analyze(good_pdf, "machine learning and Docker").to_dict()
    assert result["bullets"] == 4
    assert result["max_score"] == MAX_SCORE
    assert "machine learning" in result["keywords"]["matched"]
    json.dumps(result)  # must serialise without help


def test_analyze_reports_suggestions_unavailable_without_a_key(good_pdf: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    analysis = analyze(good_pdf, with_suggestions=True)
    assert analysis.suggestions == []
    assert "ANTHROPIC_API_KEY" in analysis.suggestions_error
    assert "ANTHROPIC_API_KEY" in analysis.to_dict()["suggestions_error"]


def test_api_score_endpoint_returns_the_same_shape(good_pdf: Path):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from resume_radar.api import app

    with fastapi_testclient.TestClient(app) as client:
        response = client.post(
            "/score",
            files={"file": ("resume.pdf", good_pdf.read_bytes(), "application/pdf")},
            data={"job_description": "machine learning"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["max_score"] == MAX_SCORE
    assert body["resume"] == "resume.pdf"
    assert "machine learning" in body["keywords"]["matched"]


def test_api_serves_the_upload_page():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from resume_radar.api import app

    with fastapi_testclient.TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # The page posts to a relative path so it works behind a tunnel or a subpath.
    assert "fetch('score'" in response.text


def test_api_health_reports_the_version():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from resume_radar import __version__
    from resume_radar.api import app

    with fastapi_testclient.TestClient(app) as client:
        body = client.get("/health").json()
    assert body == {"status": "ok", "version": __version__}


def test_api_rejects_an_empty_upload():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from resume_radar.api import app

    with fastapi_testclient.TestClient(app) as client:
        response = client.post(
            "/score", files={"file": ("resume.pdf", b"", "application/pdf")}
        )
    assert response.status_code == 400


def test_api_reports_an_unreadable_pdf_as_unprocessable():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from resume_radar.api import app

    with fastapi_testclient.TestClient(app) as client:
        response = client.post(
            "/score", files={"file": ("resume.pdf", b"not a pdf at all", "application/pdf")}
        )
    assert response.status_code == 422


def test_api_reports_a_malformed_docx_as_unprocessable(malformed_docx: bytes):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from resume_radar.api import app

    with fastapi_testclient.TestClient(app) as client:
        response = client.post(
            "/score", files={"file": ("resume.docx", malformed_docx)}
        )
    assert response.status_code == 422
    assert "could not extract text" in response.json()["detail"]


def test_api_rejects_an_unsupported_file_type():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from resume_radar.api import app

    with fastapi_testclient.TestClient(app) as client:
        response = client.post(
            "/score", files={"file": ("resume.rtf", b"hello", "application/rtf")}
        )
    assert response.status_code == 415
