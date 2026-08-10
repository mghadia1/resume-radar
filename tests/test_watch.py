"""Tests for the watchlist, the digest, and the `watch` command.

No test touches the network: `jobs.fetch` is mocked everywhere.
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from resume_radar import cli, digest, jobs, watchlist

RESUME = """JORDAN REYES
jordan@example.edu | (602) 555-0114 | linkedin.com/in/j | github.com/j
EDUCATION
State University, BS Computer Science, Aug 2023 - May 2027
EXPERIENCE
Software Engineer Intern, Northwind, May 2026 - Aug 2026
- Built an ingest service in Python and Docker that cut latency by 40 percent
- Automated 9 release checks with pytest, removing 6 hours of manual work
PROJECTS
- Designed a classifier in PyTorch reaching 94 percent accuracy on 12000 samples
SKILLS
Python, Docker, PyTorch, SQL, Git, Linux
"""


def make_posting(company="acme", title="Software Engineer Intern",
                 url=None, description="Python and Docker required.",
                 source="greenhouse", location="Tempe, AZ") -> jobs.Posting:
    return jobs.Posting(
        company=company, title=title,
        url=url or f"https://example.com/{company}/{title.replace(' ', '-')}",
        description=description, location=location, source=source,
    )


@pytest.fixture
def resume_file(tmp_path: Path) -> Path:
    path = tmp_path / "resume.txt"
    path.write_text(RESUME, encoding="utf-8")
    return path


@pytest.fixture
def boards_file(tmp_path: Path) -> Path:
    path = tmp_path / "boards.txt"
    path.write_text("greenhouse:acme\n# a comment\nlever:globex\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------- watchlist


def test_posting_id_is_stable_and_source_specific():
    a = watchlist.posting_id("greenhouse", "acme", "https://x/1")
    assert a == watchlist.posting_id("greenhouse", "acme", "https://x/1")
    assert a != watchlist.posting_id("lever", "acme", "https://x/1")
    assert a != watchlist.posting_id("greenhouse", "acme", "https://x/2")


def test_filter_new_returns_everything_on_an_empty_database(tmp_path: Path):
    with watchlist.Watchlist(tmp_path / "seen.db") as store:
        postings = [make_posting(title=f"Role {i}") for i in range(3)]
        assert len(store.filter_new(postings)) == 3


def test_marked_postings_are_not_new_the_second_time(tmp_path: Path):
    postings = [make_posting(title=f"Role {i}") for i in range(3)]
    with watchlist.Watchlist(tmp_path / "seen.db") as store:
        store.mark_seen(store.filter_new(postings))
        assert store.filter_new(postings) == []
        assert len(store) == 3


def test_duplicates_within_one_batch_are_reported_once(tmp_path: Path):
    duplicate = make_posting()
    with watchlist.Watchlist(tmp_path / "seen.db") as store:
        assert len(store.filter_new([duplicate, duplicate])) == 1


def test_mark_seen_is_idempotent_and_keeps_the_first_date(tmp_path: Path):
    posting = make_posting()
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with watchlist.Watchlist(tmp_path / "seen.db") as store:
        store.mark_seen([posting], when=old)
        store.mark_seen([posting], when=datetime.now(timezone.utc))
        assert len(store) == 1
        assert store.rows()[0].first_seen.startswith("2026-01-01")


def test_mark_seen_records_fit_and_blockers(tmp_path: Path):
    posting = make_posting()
    posting.fit = 61.5
    posting.blockers = ["citizenship", "clearance"]
    with watchlist.Watchlist(tmp_path / "seen.db") as store:
        store.mark_seen([posting])
        row = store.rows()[0]
        assert row.fit == pytest.approx(61.5)
        assert row.blockers == "citizenship;clearance"


def test_prune_drops_only_old_rows(tmp_path: Path):
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with watchlist.Watchlist(tmp_path / "seen.db") as store:
        store.mark_seen([make_posting(title="Old")], when=now - timedelta(days=120))
        store.mark_seen([make_posting(title="Recent")], when=now - timedelta(days=5))
        assert store.prune(days=90, now=now) == 1
        assert [r.title for r in store.rows()] == ["Recent"]


def test_watchlist_persists_across_connections(tmp_path: Path):
    path = tmp_path / "seen.db"
    with watchlist.Watchlist(path) as store:
        store.mark_seen([make_posting()])
    with watchlist.Watchlist(path) as store:
        assert len(store) == 1


# ------------------------------------------------------------------------ digest


def test_digest_lists_new_postings_with_fit_and_gaps():
    posting = make_posting()
    ranked = jobs.rank(RESUME, [posting])
    text = digest.render(ranked, already_seen=4, on=date(2026, 8, 4))
    assert "# Internship shortlist — 2026-08-04" in text
    assert "1 new, 4 already seen, 0 filtered as ineligible" in text
    assert "acme — Software Engineer Intern" in text
    assert "Tempe, AZ" in text


def test_digest_says_so_when_nothing_is_new():
    text = digest.render([], already_seen=12)
    assert "Nothing new today." in text
    assert "0 new, 12 already seen" in text


def test_digest_collapses_the_filtered_list_with_reasons():
    blocked = jobs.screen(make_posting(
        title="Defense Engineer",
        description="Applicants must be a US citizen with an active clearance."))
    text = digest.render([], filtered=[blocked])
    assert "<details>" in text and "</details>" in text
    assert "requires US citizenship" in text
    assert "requires a security clearance" in text


def test_digest_warns_about_unreachable_boards():
    text = digest.render([], boards_failed=["greenhouse:acme"])
    assert "could not be reached" in text
    assert "greenhouse:acme" in text


def test_digest_never_calls_fit_a_probability():
    """The word may appear only in the closing disclaimer that denies it.

    Splitting on the first "---" used to define "the body", which quietly stopped
    covering anything below the backfill separator when that section was added. The
    disclaimer is the final line, so drop exactly that.
    """
    ranked = jobs.rank(RESUME, [make_posting()])
    backfill = jobs.rank(RESUME, [make_posting(company="globex")])
    blocked = jobs.screen(make_posting(company="mil", description="Must be a US citizen."))
    off = [make_posting(company="corp", title="Staff Engineer")]

    text = digest.render(ranked, backfill=backfill, filtered=[blocked],
                         off_level=off, already_seen=3).lower()
    assert "not a probability" in text

    body = "\n".join(line for line in text.splitlines()
                     if "not a probability" not in line)
    for word in ("probability", "likelihood", "chance of getting"):
        assert word not in body


def test_digest_lists_off_level_postings_rather_than_only_counting_them():
    """A title-based filter is a heuristic; a bare count cannot be checked."""
    off = [make_posting(company="corp", title="Staff Engineer"),
           make_posting(company="acme", title="Software Engineer")]
    text = digest.render([], off_level=off)
    assert "2 wrong level" in text
    assert "corp — Staff Engineer" in text
    assert "acme — Software Engineer" in text
    assert "`senior`" in text and "`mid`" in text
    assert "--levels" in text


def test_digest_caps_the_off_level_list_and_summarises_the_rest():
    """A real run drops thousands; an unbounded list is as unreadable as no list."""
    off = ([make_posting(company="c", title=f"Staff Engineer {i}") for i in range(30)]
           + [make_posting(company="c", title=f"Software Engineer {i}") for i in range(20)])
    text = digest.render([], off_level=off)
    assert "50 wrong level" in text
    assert text.count("· reads as ") == digest.SAMPLE
    assert "…and 38 more" in text
    assert "senior" in text and "mid" in text


def test_digest_caps_the_filtered_list_and_summarises_the_rest():
    blocked = [jobs.screen(make_posting(company="c", title=f"Cleared {i}",
                                        description="Must be a US citizen."))
               for i in range(20)]
    text = digest.render([], filtered=blocked)
    assert text.count("requires US citizenship ·") == digest.SAMPLE
    assert "…and 8 more" in text


def test_a_short_run_explains_that_internships_are_seasonal():
    text = digest.render([], short=5, target=5)
    assert "seasonal" in text
    assert "August and October" in text


def test_no_source_file_claims_fit_predicts_an_interview():
    """Repo-wide guard: the honest word is 'fit'.

    Every occurrence of a probability word in the package must sit in a sentence that
    denies the claim, not one that makes it.
    """
    package = Path(cli.__file__).parent
    offenders = []
    for source in sorted(package.glob("*.py")):
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            if not any(w in low for w in ("probability", "likelihood", "chance of")):
                continue
            if any(deny in low for deny in ("not a probability", "not a likelihood",
                                            "never a probability", "rather than",
                                            "does not", "is not")):
                continue
            offenders.append(f"{source.name}:{number}: {line.strip()}")
    assert offenders == []


# --------------------------------------------------------------------- watch cmd


def _patch_fetch(postings_by_board):
    def fake_fetch(board, source, **kwargs):
        result = postings_by_board.get(board)
        if isinstance(result, Exception):
            raise result
        return result or []
    return mock.patch.object(jobs, "fetch", side_effect=fake_fetch)


def test_watch_reports_new_then_nothing_on_a_second_run(
        tmp_path: Path, resume_file: Path, boards_file: Path, capsys):
    """The definition of done: running twice in a row must report zero the second time."""
    postings = {"acme": [make_posting()], "globex": [make_posting(company="globex")]}
    db = tmp_path / "seen.db"
    argv = ["watch", "--resume", str(resume_file), "--boards", str(boards_file),
            "--db", str(db), "--min-fit", "0"]

    with _patch_fetch(postings):
        assert cli.main(argv) == 0
        first = capsys.readouterr().out
        assert cli.main(argv) == 0
        second = capsys.readouterr().out

    assert "2 new, 0 already seen" in first
    # The second run finds nothing new. The two are shown once, as carried over -
    # counting them in "already seen" as well would report the same rows twice.
    assert "0 new" in second
    assert "2 carried over" in second
    assert "2 already seen" not in second


def test_watch_exits_zero_when_there_is_nothing_new(
        tmp_path: Path, resume_file: Path, boards_file: Path):
    with _patch_fetch({}):
        assert cli.main(["watch", "--resume", str(resume_file), "--boards",
                         str(boards_file), "--db", str(tmp_path / "s.db")]) == 0


@pytest.mark.parametrize(
    "failure",
    [
        # Found against 29 live boards: none of these is a URLError, and each one
        # used to abort the entire scheduled run.
        __import__("http").client.RemoteDisconnected("Remote end closed connection"),
        __import__("http").client.BadStatusLine("''"),
        TimeoutError("timed out"),
        ConnectionResetError(54, "Connection reset by peer"),
        __import__("ssl").SSLError("handshake failure"),
        __import__("urllib").error.URLError("nodename nor servname provided"),
        ValueError("Expecting value: line 1 column 1"),
    ],
)
def test_fetch_turns_every_network_failure_into_a_skippable_error(failure):
    with mock.patch.object(jobs.urllib.request, "urlopen", side_effect=failure):
        with pytest.raises(RuntimeError, match="could not fetch"):
            jobs.fetch("acme", "greenhouse")


def test_fetch_rejects_an_unexpected_payload_shape():
    """A 200 that is not the documented shape skips the board, it does not crash."""
    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with mock.patch.object(jobs.urllib.request, "urlopen",
                           return_value=FakeResponse(b'["not", "a", "posting"]')):
        with pytest.raises(RuntimeError, match="unexpected payload"):
            jobs.fetch("acme", "lever")


def test_watch_survives_a_board_that_drops_the_connection(
        tmp_path: Path, resume_file: Path, boards_file: Path, capsys):
    """The end-to-end version of the same bug: the other board must still report."""
    def fake_fetch(board, source, **kwargs):
        if board == "acme":
            raise __import__("http").client.RemoteDisconnected("closed")
        return [make_posting(company="globex")]

    with mock.patch.object(jobs, "fetch", side_effect=fake_fetch):
        code = cli.main(["watch", "--resume", str(resume_file), "--boards",
                         str(boards_file), "--db", str(tmp_path / "s.db"),
                         "--min-fit", "0"])
    out = capsys.readouterr()
    assert code == 0
    assert "globex" in out.out


def test_watch_continues_when_one_board_is_unreachable(
        tmp_path: Path, resume_file: Path, boards_file: Path, capsys):
    postings = {"acme": RuntimeError("could not fetch greenhouse/acme: 503"),
                "globex": [make_posting(company="globex")]}
    with _patch_fetch(postings):
        assert cli.main(["watch", "--resume", str(resume_file), "--boards",
                         str(boards_file), "--db", str(tmp_path / "s.db"),
                         "--min-fit", "0"]) == 0
    out = capsys.readouterr()
    assert "globex" in out.out
    assert "could not be reached" in out.out
    assert "503" in out.err


def test_watch_filters_ineligible_postings_but_still_shows_them(
        tmp_path: Path, resume_file: Path, boards_file: Path, capsys):
    postings = {
        "acme": [make_posting(
            title="Cleared Engineer",
            description="Must be a US citizen. Python required.")],
        "globex": [make_posting(company="globex")],
    }
    with _patch_fetch(postings):
        cli.main(["watch", "--resume", str(resume_file), "--boards", str(boards_file),
                  "--db", str(tmp_path / "s.db"), "--min-fit", "0"])
    out = capsys.readouterr().out
    assert "1 new, 0 already seen, 1 filtered as ineligible" in out
    assert "requires US citizenship" in out


def test_watch_writes_the_digest_to_a_file(
        tmp_path: Path, resume_file: Path, boards_file: Path):
    out_file = tmp_path / "digest.md"
    with _patch_fetch({"acme": [make_posting()]}):
        cli.main(["watch", "--resume", str(resume_file), "--boards", str(boards_file),
                  "--db", str(tmp_path / "s.db"), "--out", str(out_file), "--min-fit", "0"])
    assert "# Internship shortlist" in out_file.read_text(encoding="utf-8")


def test_watch_dry_run_does_not_record(
        tmp_path: Path, resume_file: Path, boards_file: Path, capsys):
    db = tmp_path / "seen.db"
    argv = ["watch", "--resume", str(resume_file), "--boards", str(boards_file),
            "--db", str(db), "--min-fit", "0", "--dry-run"]
    with _patch_fetch({"acme": [make_posting()]}):
        cli.main(argv)
        capsys.readouterr()
        cli.main(argv)
    assert "1 new, 0 already seen" in capsys.readouterr().out


def test_watch_marks_low_fit_postings_seen_so_they_do_not_resurface(
        tmp_path: Path, resume_file: Path, boards_file: Path, capsys):
    """A posting below --min-fit is still 'seen'; it must not reappear tomorrow."""
    postings = {"acme": [make_posting(title="Chef", description="Cooking only.")]}
    db = tmp_path / "seen.db"
    argv = ["watch", "--resume", str(resume_file), "--boards", str(boards_file),
            "--db", str(db), "--min-fit", "95"]
    with _patch_fetch(postings):
        cli.main(argv)
        capsys.readouterr()
        cli.main(argv)
    assert "0 new" in capsys.readouterr().out


def test_watch_json_output_is_machine_readable(
        tmp_path: Path, resume_file: Path, boards_file: Path, capsys):
    with _patch_fetch({"acme": [make_posting()]}):
        cli.main(["watch", "--resume", str(resume_file), "--boards", str(boards_file),
                  "--db", str(tmp_path / "s.db"), "--min-fit", "0", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_seen"] == 0
    assert payload["new"][0]["company"] == "acme"
