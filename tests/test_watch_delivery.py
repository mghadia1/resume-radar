"""Failed digest delivery must leave postings new and preserve existing history."""

import io
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from resume_radar import cli, jobs, watchlist
from test_watch import boards_file, make_posting, resume_file


def seed_history(db):
    with watchlist.Watchlist(db) as store:
        store.mark_seen([make_posting(company="old")], when=(
            datetime.now(timezone.utc) - timedelta(days=120)))
        return store.rows()


@pytest.mark.parametrize("json_output", [False, True], ids=["markdown", "json"])
@pytest.mark.parametrize("failure", ["file", "write", "flush"])
def test_failed_delivery_preserves_history_and_retry_is_new(
        tmp_path, resume_file, boards_file, capsys, json_output, failure):
    db = tmp_path / "seen.db"
    before = seed_history(db)
    posting = make_posting()
    argv = ["watch", "--resume", str(resume_file), "--boards", str(boards_file),
            "--db", str(db), "--min-fit", "0"]
    if json_output:
        argv.append("--json")

    class FailedStream(io.StringIO):
        def write(self, text):
            if failure == "write":
                raise BrokenPipeError("synthetic closed pipe")
            return super().write(text)

        def flush(self):
            raise OSError("synthetic buffered output failure")

    # A real directory destination fails without relying on filesystem permissions.
    output = tmp_path / "digest.md"
    with mock.patch.object(jobs, "fetch", side_effect=lambda board, *a: (
            [posting] if board == "acme" else [])):
        if failure == "file":
            with pytest.raises(IsADirectoryError):
                cli.main(argv + ["--out", str(tmp_path)])
        else:
            with mock.patch.object(cli.sys, "stdout", FailedStream()):
                with pytest.raises(OSError):
                    cli.main(argv + ["--out", str(output)])
            # File delivery alone must not commit when the stdout sink fails.
            assert "# Internship shortlist" in output.read_text(encoding="utf-8")

        with watchlist.Watchlist(db) as store:
            assert store.rows() == before

        capsys.readouterr()
        assert cli.main(argv + ["--out", str(output)]) == 0
        delivered = capsys.readouterr().out
        if json_output:
            assert [p["url"] for p in json.loads(delivered)["new"]] == [posting.url]
        else:
            assert "1 new, 0 already seen" in delivered
        assert "# Internship shortlist" in output.read_text(encoding="utf-8")
        with watchlist.Watchlist(db) as store:
            assert [row.url for row in store.rows()] == [posting.url]


@pytest.mark.parametrize("json_output", [False, True], ids=["markdown", "json"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["record", "dry-run"])
def test_successful_delivery_keeps_recording_policy(
        tmp_path, resume_file, boards_file, capsys, json_output, dry_run):
    db = tmp_path / "seen.db"
    before = seed_history(db)
    postings = [
        make_posting(),
        make_posting(title="Sales Intern", description="Cooking only."),
        make_posting(title="Staff Engineer"),
        make_posting(title="Defense Intern", description="Must be a US citizen."),
    ]
    output = tmp_path / "digest.md"
    argv = ["watch", "--resume", str(resume_file), "--boards", str(boards_file),
            "--db", str(db), "--out", str(output), "--min-fit", "50"]
    if json_output:
        argv.append("--json")
    if dry_run:
        argv.append("--dry-run")

    # Return each synthetic posting from one board only.
    with mock.patch.object(jobs, "fetch", side_effect=lambda board, *a: (
            postings if board == "acme" else [])):
        assert cli.main(argv) == 0
    delivered = capsys.readouterr().out
    if json_output:
        payload = json.loads(delivered)
        assert len(payload["new"]) == 1
        assert len(payload["filtered"]) == len(payload["off_level"]) == 1
    else:
        assert "1 new, 0 already seen, 1 filtered as ineligible, 1 wrong level" in delivered
        assert ("dry run - nothing recorded" in delivered) == dry_run
    assert "# Internship shortlist" in output.read_text(encoding="utf-8")
    with watchlist.Watchlist(db) as store:
        if dry_run:
            assert store.rows() == before
        else:
            # Off-level, low-fit and blocked postings are still remembered.
            assert {row.url for row in store.rows()} == {p.url for p in postings}
