"""Tests for outcome tracking, stats, the answer bank, and `apply`.

The recurring assertion in this file is a negative one: `apply` prepares and prints,
and never submits. No test here touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from resume_radar import answers, checks, cli, extract, jobs, keywords, tracker

from test_watch import RESUME, make_posting  # reuse the fixtures


@pytest.fixture
def resume_file(tmp_path: Path) -> Path:
    path = tmp_path / "resume.txt"
    path.write_text(RESUME, encoding="utf-8")
    return path


@pytest.fixture
def boards_file(tmp_path: Path) -> Path:
    path = tmp_path / "boards.txt"
    path.write_text("greenhouse:acme\n", encoding="utf-8")
    return path


@pytest.fixture
def answers_file(tmp_path: Path) -> Path:
    return Path(answers.write_template(tmp_path / "answers.yml"))


def _log_row(tmp_path: Path, log: Path, resume_file: Path, *, company="acme",
             fit=50.0, title_matched=True, outcome="applied") -> None:
    doc = extract.load(resume_file)
    report = checks.run(doc)
    match = keywords.compare(doc.text, "Python Docker")
    match.percent = fit
    match.title_matched = title_matched
    tracker.append(log, resume=str(resume_file), report=report, company=company,
                   keyword_match=match, outcome=outcome)


# ------------------------------------------------------------------- outcomes


def test_rows_get_sequential_ids(tmp_path: Path, resume_file: Path):
    log = tmp_path / "runs.csv"
    for i in range(3):
        _log_row(tmp_path, log, resume_file, company=f"c{i}")
    assert [r["id"] for r in tracker.read(log)] == ["1", "2", "3"]


def test_new_rows_default_to_applied(tmp_path: Path, resume_file: Path):
    log = tmp_path / "runs.csv"
    _log_row(tmp_path, log, resume_file)
    assert tracker.read(log)[0]["outcome"] == "applied"


def test_set_outcome_updates_one_row_only(tmp_path: Path, resume_file: Path):
    log = tmp_path / "runs.csv"
    _log_row(tmp_path, log, resume_file, company="a")
    _log_row(tmp_path, log, resume_file, company="b")
    tracker.set_outcome(log, 2, "interview")
    rows = tracker.read(log)
    assert rows[0]["outcome"] == "applied"
    assert rows[1]["outcome"] == "interview"
    assert len(rows) == 2


def test_set_outcome_rejects_an_unknown_value(tmp_path: Path, resume_file: Path):
    log = tmp_path / "runs.csv"
    _log_row(tmp_path, log, resume_file)
    with pytest.raises(tracker.UnknownOutcome):
        tracker.set_outcome(log, 1, "ghosted")


def test_set_outcome_rejects_an_unknown_row(tmp_path: Path, resume_file: Path):
    log = tmp_path / "runs.csv"
    _log_row(tmp_path, log, resume_file)
    with pytest.raises(LookupError):
        tracker.set_outcome(log, 99, "offer")


def test_outcome_cli_updates_the_row(tmp_path: Path, resume_file: Path, capsys):
    log = tmp_path / "runs.csv"
    _log_row(tmp_path, log, resume_file, company="acme")
    assert cli.main(["outcome", "1", "screen", "--log", str(log)]) == 0
    assert "-> screen" in capsys.readouterr().out
    assert tracker.read(log)[0]["outcome"] == "screen"


def test_outcome_cli_reports_a_bad_row_id(tmp_path: Path, resume_file: Path, capsys):
    log = tmp_path / "runs.csv"
    _log_row(tmp_path, log, resume_file)
    assert cli.main(["outcome", "42", "offer", "--log", str(log)]) == 1
    assert "error" in capsys.readouterr().err


# ---------------------------------------------------------------------- stats


def test_stats_refuses_to_report_below_the_minimum(tmp_path: Path, resume_file: Path):
    log = tmp_path / "runs.csv"
    for i in range(5):
        _log_row(tmp_path, log, resume_file, company=f"c{i}", outcome="rejected")
    report = tracker.stats(log)
    assert report["reportable"] is False
    assert "need 20" in report["reason"]
    assert "response_rate" not in report


def test_stats_ignores_rows_still_marked_applied(tmp_path: Path, resume_file: Path):
    log = tmp_path / "runs.csv"
    for i in range(30):
        _log_row(tmp_path, log, resume_file, company=f"c{i}", outcome="applied")
    report = tracker.stats(log)
    assert report["rows"] == 30
    assert report["labelled"] == 0
    assert report["reportable"] is False


def test_stats_reports_once_there_is_enough_data(tmp_path: Path, resume_file: Path):
    log = tmp_path / "runs.csv"
    # 12 high-fit rows, half of which got a reply; 12 low-fit rows, none did.
    for i in range(12):
        _log_row(tmp_path, log, resume_file, company=f"hi{i}", fit=75.0,
                 outcome="interview" if i % 2 == 0 else "rejected")
    for i in range(12):
        _log_row(tmp_path, log, resume_file, company=f"lo{i}", fit=15.0,
                 title_matched=False, outcome="rejected")

    report = tracker.stats(log)
    assert report["reportable"] is True
    assert report["labelled"] == 24
    assert report["response_rate"] == 25.0
    assert report["by_fit_decile"]["70-79%"]["rate"] == 50.0
    assert report["by_fit_decile"]["10-19%"]["rate"] == 0.0
    assert report["by_title_match"]["title matched"]["rate"] == 50.0
    assert report["by_title_match"]["no title match"]["rate"] == 0.0


def test_stats_cli_explains_why_it_cannot_report(tmp_path: Path, resume_file: Path, capsys):
    log = tmp_path / "runs.csv"
    _log_row(tmp_path, log, resume_file, outcome="rejected")
    assert cli.main(["stats", "--log", str(log)]) == 0
    out = capsys.readouterr().out
    assert "Not enough data yet" in out
    assert "need 20" in out


def test_stats_cli_json(tmp_path: Path, resume_file: Path, capsys):
    log = tmp_path / "runs.csv"
    _log_row(tmp_path, log, resume_file, outcome="offer")
    assert cli.main(["stats", "--log", str(log), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["labelled"] == 1


# --------------------------------------------------------------- answer bank


def test_answers_init_writes_a_template(tmp_path: Path):
    path = answers.write_template(tmp_path / "answers.yml")
    bank = answers.load(path)
    assert "work_authorization" in bank
    assert "requires_sponsorship" in bank


def test_answers_init_refuses_to_overwrite(answers_file: Path):
    with pytest.raises(FileExistsError):
        answers.write_template(answers_file)


def test_answers_load_skips_comments_and_blanks(tmp_path: Path):
    path = tmp_path / "a.yml"
    path.write_text("# a comment\n\ngpa: 3.55/4.00\nemail: me@example.edu\n",
                    encoding="utf-8")
    assert answers.load(path) == {"gpa": "3.55/4.00", "email": "me@example.edu"}


def test_answers_load_without_a_file_raises(tmp_path: Path):
    with pytest.raises(answers.AnswersNotFound):
        answers.load(tmp_path / "missing.yml")


@pytest.mark.parametrize(
    "question,key",
    [
        ("Will you now or in the future require sponsorship?", "requires_sponsorship"),
        ("Are you legally authorized to work in the United States?", "work_authorization"),
        ("Do you hold an active security clearance?", "security_clearance"),
        ("What is your expected graduation date?", "graduation_date"),
        ("What is your GPA?", "gpa"),
        ("Please share your GitHub profile", "github"),
    ],
)
def test_questions_map_to_the_right_answer(question: str, key: str):
    matched = [k for pattern, k in answers.COMPILED_QUESTIONS if pattern.search(question)]
    assert matched and matched[0] == key


def test_find_questions_pulls_requirements_and_questions():
    description = ("About us. Must be authorized to work in the US. "
                   "What is your expected graduation date? We build things.")
    found = answers.find_questions(description)
    joined = " ".join(found).lower()
    assert "authorized to work" in joined
    assert "graduation date" in joined


def test_prepare_matches_answers_and_flags_the_rest(resume_file: Path, answers_file: Path):
    posting = make_posting(description=(
        "Must be authorized to work in the US. What is your GPA? "
        "Describe a time you disagreed with a teammate? Python and Docker required."))
    doc = extract.load(resume_file)
    match = keywords.compare(doc.text, posting.description)
    prep = answers.prepare(posting, doc, match, answers.load(answers_file))

    keys = {a.key for a in prep.answers}
    assert "gpa" in keys
    assert "work_authorization" in keys
    assert any("disagreed" in q for q in prep.unmatched)


def test_prepare_always_surfaces_work_authorization(resume_file: Path, answers_file: Path):
    """Every real form asks, whether or not the posting text does."""
    posting = make_posting(description="We build things with Python.")
    doc = extract.load(resume_file)
    prep = answers.prepare(posting, doc, None, answers.load(answers_file))
    keys = {a.key for a in prep.answers}
    assert {"work_authorization", "requires_sponsorship"} <= keys


def test_pitch_bullets_come_verbatim_from_the_resume(resume_file: Path, answers_file: Path):
    """Selection, not generation - nothing invented to fit the posting."""
    posting = make_posting(description="Python, Docker and PyTorch required.")
    doc = extract.load(resume_file)
    match = keywords.compare(doc.text, posting.description)
    prep = answers.prepare(posting, doc, match, answers.load(answers_file))
    assert prep.pitch
    for bullet in prep.pitch:
        assert bullet in doc.bullets


# ------------------------------------------------------------------ apply cmd


def test_apply_prints_answers_and_submits_nothing(
        tmp_path: Path, resume_file: Path, boards_file: Path, answers_file: Path, capsys):
    posting = make_posting(description=(
        "Must be authorized to work in the US. What is your GPA? "
        "Python, Docker and Kubernetes required."))
    with mock.patch.object(jobs, "fetch", return_value=[posting]):
        code = cli.main(["apply", posting.url, "--resume", str(resume_file),
                         "--boards", str(boards_file), "--answers", str(answers_file)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Your answers, ready to copy:" in out
    assert "Nothing was submitted" in out
    assert "kubernetes" in out.lower()
    assert "Only add one of these if you have actually done it" in out


def test_apply_warns_when_the_posting_is_ineligible(
        tmp_path: Path, resume_file: Path, boards_file: Path, answers_file: Path, capsys):
    posting = make_posting(description="Applicants must be a US citizen. Python required.")
    with mock.patch.object(jobs, "fetch", return_value=[posting]):
        cli.main(["apply", posting.url, "--resume", str(resume_file),
                  "--boards", str(boards_file), "--answers", str(answers_file)])
    assert "screened as ineligible" in capsys.readouterr().out


def test_apply_reports_an_unknown_url(
        tmp_path: Path, resume_file: Path, boards_file: Path, answers_file: Path, capsys):
    with mock.patch.object(jobs, "fetch", return_value=[make_posting()]):
        code = cli.main(["apply", "https://example.com/nope", "--resume", str(resume_file),
                         "--boards", str(boards_file), "--answers", str(answers_file)])
    assert code == 1
    assert "no posting with url" in capsys.readouterr().err


def test_apply_requires_an_answer_bank(
        tmp_path: Path, resume_file: Path, boards_file: Path, capsys):
    code = cli.main(["apply", "https://x/1", "--resume", str(resume_file),
                     "--boards", str(boards_file),
                     "--answers", str(tmp_path / "missing.yml")])
    assert code == 1
    assert "no answer bank" in capsys.readouterr().err


def test_apply_never_opens_a_browser_or_posts(
        tmp_path: Path, resume_file: Path, boards_file: Path, answers_file: Path):
    """Hard constraint, asserted rather than trusted."""
    import urllib.request
    import webbrowser

    posting = make_posting(description="Python required. What is your GPA?")
    with mock.patch.object(jobs, "fetch", return_value=[posting]), \
         mock.patch.object(webbrowser, "open") as opened, \
         mock.patch.object(urllib.request, "urlopen") as posted:
        cli.main(["apply", posting.url, "--resume", str(resume_file),
                  "--boards", str(boards_file), "--answers", str(answers_file)])
    opened.assert_not_called()
    posted.assert_not_called()
