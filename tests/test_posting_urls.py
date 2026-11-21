"""Board URLs must be usable HTTP(S) links before reaching the shortlist."""

import io
import json

import pytest

from resume_radar import cli, digest, jobs


def payload(source, url):
    if source == "greenhouse":
        return {"jobs": [{"title": "Python Intern", "absolute_url": url,
                          "content": "Python required."}]}
    return [{"text": "Python Intern", "hostedUrl": url,
             "descriptionPlain": "Python required."}]


def parse(source, url):
    parser = getattr(jobs, f"parse_{source}")
    return parser(payload(source, url), "synthetic")


@pytest.mark.parametrize("source", ["greenhouse", "lever"])
@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "data:text/html,synthetic",
    "file:///tmp/synthetic",
    "ftp://example.com/role",
    "/roles/1",
    "//example.com/role",
    "https:///role",
    "https://",
    "https://[broken/role",
    "https://example.com:invalid/role",
    "https://example.com:65536/role",
    "https://user:synthetic-secret@example.com/role",
    "https://@example.com/role",
    " https://example.com/role",
    "https://example.com/a b",
    "https://example.com/\nrole",
    "https://exam\tple.com/role",
    "\x00https://example.com/role",
    "https://example.com/role\x7f",
    "https://example.com\\other/role",
])
def test_parsers_reject_non_web_or_malformed_urls(source, url):
    with pytest.raises(TypeError, match="posting URL") as error:
        parse(source, url)
    assert str(error.value) == "expected absolute HTTP(S) posting URL without credentials"


@pytest.mark.parametrize("source", ["greenhouse", "lever"])
@pytest.mark.parametrize("url", [
    "https://example.com/role?ref=a%20b&team=R%26D#apply",
    "http://example.com:8080/role",
    "HTTPS://Example.COM/Role",
    "https://careers.example.org/équipe/1",
    "https://[2001:db8::1]/role",
])
def test_valid_urls_keep_verbatim_identity_and_digest_output(source, url):
    posting, = parse(source, url)
    assert posting.identity == (source, "synthetic", url)
    assert posting.to_dict()["url"] == url
    assert url in digest.render(jobs.rank("Python", [posting]))


@pytest.mark.parametrize("source", ["greenhouse", "lever"])
def test_watch_skips_invalid_url_board_without_leaking_credentials(
        source, tmp_path, monkeypatch, capsys):
    bad_url = "https://synthetic-user:synthetic-secret@example.com/role"
    healthy_url = "https://example.org/healthy"
    responses = iter([payload(source, bad_url), payload(source, healthy_url)])
    requests = []

    def urlopen(request, **kwargs):
        requests.append(request.full_url)
        return io.BytesIO(json.dumps(next(responses)).encode())

    monkeypatch.setattr(jobs.urllib.request, "urlopen", urlopen)
    resume = tmp_path / "resume.txt"
    resume.write_text("Python Intern projects", encoding="utf-8")
    boards = tmp_path / "boards.txt"
    boards.write_text(f"{source}:broken\n{source}:healthy\n", encoding="utf-8")
    assert cli.main([
        "watch", "--resume", str(resume), "--boards", str(boards),
        "--db", str(tmp_path / "seen.db"), "--min-fit", "0", "--dry-run",
    ]) == 0
    output = capsys.readouterr()
    assert "1 new" in output.out
    assert healthy_url in output.out
    assert f"{source}:broken" in output.out
    assert "unexpected payload" in output.err
    assert "posting URL" in output.err
    assert "synthetic-user" not in output.out + output.err
    assert "synthetic-secret" not in output.out + output.err
    assert len(requests) == 2
