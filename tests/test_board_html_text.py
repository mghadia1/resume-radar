"""Board HTML code must not become screening or keyword evidence."""

import html
import io
import json

import pytest

from resume_radar import cli, jobs


def parse(body, field="greenhouse"):
    if field == "greenhouse":
        return jobs.parse_greenhouse({"jobs": [{
            "title": "Python Intern", "absolute_url": "https://example.com/synthetic",
            "content": body,
        }]}, "synthetic")[0]
    record = {"text": "Python Intern", "hostedUrl": "https://example.com/synthetic"}
    if field == "lever-description":
        record["description"] = body
    else:
        record["descriptionPlain"] = "Python required."
        record["lists"] = [{"content": body}]
    return jobs.parse_lever([record], "synthetic")[0]


@pytest.mark.parametrize("tag", ["script", "style"])
@pytest.mark.parametrize("field", ["greenhouse", "lever-description", "lever-list"])
def test_code_does_not_create_false_blockers(tag, field):
    post = parse(f'<{tag}>"Must be a US citizen."</{tag}><p>Python required.</p>', field)
    assert jobs.screen(post).eligible
    assert "citizen" not in post.description
    assert "Python required." in post.description


@pytest.mark.parametrize("tag", ["script", "style"])
def test_hidden_offer_cannot_override_visible_denial(tag):
    post = parse(f'<p>We will not sponsor visas.</p><{tag}>"We sponsor"</{tag}>')
    jobs.screen(post)
    assert post.blockers == ["no_sponsorship"]
    assert not post.sponsorship_friendly


@pytest.mark.parametrize("tag", ["script", "style"])
def test_code_terms_do_not_affect_fit_or_missing_skills(tag):
    clean = parse("<p>Python required.</p>")
    noisy = parse(f'<p>Python required.</p><{tag}>"Kubernetes Kafka"</{tag}>')
    ranked = jobs.rank("Python Intern Python required", [clean, noisy])
    assert ranked[0].fit == ranked[1].fit
    assert clean.match.to_dict() == noisy.match.to_dict()


@pytest.mark.parametrize("body", [
    '<p>Python</p><SCRIPT type="application/json">{"x": "Docker"}</SCRIPT><p>SQL</p>',
    '<p>Python</p><StYlE media="all">p::after {content: "Docker"}</StYlE><p>SQL</p>',
    '<p>Python</p><script>"<style>Docker</style></div>"</script><p>SQL</p>',
    '<p>Python</p><style>/* <script>Docker</script></div> */</style><p>SQL</p>',
    '<p>Python</p><script>Docker</script><style>Kafka</style><p>SQL</p>',
    html.escape('<p>Python</p><script>Docker</script><p>SQL</p>'),
])
def test_visible_text_survives_raw_text_elements(body):
    assert jobs.strip_html(body) == "Python SQL"


def test_visible_requirements_entities_and_ordinary_code_words_survive():
    text = "Python & Docker. Write script and style tools. Must be a US citizen."
    post = parse(f"<p>{html.escape(text)}</p><!-- We sponsor -->")
    assert post.description == text
    assert jobs.screen(post).blockers == ["citizenship"]


def test_lever_plain_description_keeps_its_plain_text_contract():
    text = "Write <script> examples and style documentation."
    post = jobs.parse_lever([{
        "text": "Python Intern", "hostedUrl": "https://example.com/synthetic",
        "descriptionPlain": text,
    }], "synthetic")[0]
    assert post.description == text


def test_watch_uses_visible_board_text_for_screening(tmp_path, monkeypatch, capsys):
    resume = tmp_path / "resume.txt"
    resume.write_text("Python Intern Python required", encoding="utf-8")
    boards = tmp_path / "boards.txt"
    boards.write_text("greenhouse:synthetic\n", encoding="utf-8")
    payload = {"jobs": [{
        "title": "Python Intern", "absolute_url": "https://example.com/synthetic",
        "content": '<script>"Must be a US citizen."</script><p>Python required.</p>',
    }]}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(jobs.urllib.request, "urlopen",
                        lambda *a, **kw: Response(json.dumps(payload).encode()))
    assert cli.main(["watch", "--resume", str(resume), "--boards", str(boards),
                     "--db", str(tmp_path / "seen.db"), "--min-fit", "0", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["new"]) == 1
    assert result["filtered"] == []
    assert result["new"][0]["blockers"] == []
