"""Reject malformed title/identity fields before ranking or watchlist storage."""

import io
import json

import pytest

from resume_radar import cli, jobs


@pytest.fixture(params=["greenhouse", "lever"])
def board(request):
    source = request.param
    if source == "greenhouse":
        record = {"title": "Python Intern", "absolute_url": "https://example.com/1",
                  "content": "<p>Python. Must be a US citizen.</p>"}
        fields = ("title", "absolute_url")
    else:
        record = {"text": "Python Intern", "hostedUrl": "https://example.com/1",
                  "descriptionPlain": "Python. Must be a US citizen."}
        fields = ("text", "hostedUrl")
    return source, record, fields


def serve(monkeypatch, source, records):
    payload = {"jobs": records} if source == "greenhouse" else records
    monkeypatch.setattr(jobs.urllib.request, "urlopen",
                        lambda *a, **kw: io.BytesIO(json.dumps(payload).encode()))


@pytest.mark.parametrize("field_index", [0, 1], ids=["title", "url"])
@pytest.mark.parametrize("bad", [None, "", " \t\n", 42, False, [], {}])
def test_fetch_rejects_invalid_required_fields(board, field_index, bad, monkeypatch):
    source, record, fields = board
    record[fields[field_index]] = bad
    serve(monkeypatch, source, [record])

    with pytest.raises(RuntimeError, match=f"unexpected payload from {source}/synthetic"):
        jobs.fetch("synthetic", source)


@pytest.mark.parametrize("field_index", [0, 1], ids=["title", "url"])
def test_fetch_rejects_missing_required_fields(board, field_index, monkeypatch):
    source, record, fields = board
    del record[fields[field_index]]
    serve(monkeypatch, source, [record])

    with pytest.raises(RuntimeError, match="unexpected payload"):
        jobs.fetch("synthetic", source)


def test_valid_fields_keep_identity_and_screening_text(board, monkeypatch):
    source, record, fields = board
    record[fields[0]] = "Développeur Python Intern"
    record[fields[1]] = "https://example.com/1?language=fr#apply"
    serve(monkeypatch, source, [record])

    posting, = jobs.fetch("synthetic", source)
    assert posting.title == record[fields[0]]
    assert posting.identity == (source, "synthetic", record[fields[1]])
    assert len({posting}) == 1
    assert jobs.screen(posting).blockers == ["citizenship"]


def test_watch_survives_null_title_and_reports_other_board(board, monkeypatch,
                                                         tmp_path, capsys):
    source, record, fields = board
    record[fields[0]] = None
    broken = {"jobs": [record]} if source == "greenhouse" else [record]
    healthy = {"jobs": [{"title": "Python Intern",
                         "absolute_url": "https://example.com/healthy",
                         "content": "Python Docker"}]}

    def respond(request, **kwargs):
        value = healthy if "/boards/healthy/" in request.full_url else broken
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(jobs.urllib.request, "urlopen", respond)
    resume = tmp_path / "resume.txt"
    resume.write_text("Python Docker", encoding="utf-8")
    boards = tmp_path / "boards.txt"
    boards.write_text(f"{source}:synthetic\ngreenhouse:healthy\n", encoding="utf-8")

    assert cli.main(["watch", "--resume", str(resume), "--boards", str(boards),
                     "--db", str(tmp_path / "seen.db"), "--min-fit", "0",
                     "--dry-run"]) == 0
    output = capsys.readouterr()
    assert "healthy — Python Intern" in output.out
    assert f"{source}:synthetic" in output.out
    assert "unexpected payload" in output.err
