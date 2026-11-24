"""Untrusted posting metadata must stay literal in every digest section."""

import copy
import json
from datetime import date
from html.parser import HTMLParser
from unittest import mock

import pytest
from markdown_it import MarkdownIt

from resume_radar import cli, digest, jobs, keywords

IMAGE = '<img src="https://example.com/synthetic-pixel">'
MARKDOWN = '![pixel](https://example.com/synthetic-pixel)'
ESCAPED_IMAGE = r'&lt;img src="https://example\.com/synthetic\-pixel"&gt;'
ESCAPED_MARKDOWN = r'\!\[pixel\]\(https://example\.com/synthetic\-pixel\)'


class RenderedDigest(HTMLParser):
    """Inspect actual CommonMark output with raw HTML enabled, never a browser."""

    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.links = []
        self.text = []
        self.feed(MarkdownIt("commonmark", {"html": True}).render(text))

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "a":
            self.links.append(dict(attrs).get("href"))

    def handle_data(self, data):
        self.text.append(data)


def posting(**kwargs):
    fields = dict(company="synthetic", title="Python Intern",
                  url="https://example.com/synthetic", description="Python",
                  location="Remote", source="greenhouse")
    fields.update(kwargs)
    return jobs.Posting(**fields)


@pytest.mark.parametrize("section", ["new", "backfill", "filtered", "off_level"])
@pytest.mark.parametrize("field", ["company", "title"])
def test_posting_text_cannot_inject_html_or_markdown(section, field):
    post = posting(**{field: IMAGE + " " + MARKDOWN})
    if section == "new":
        text = digest.render([post])
    else:
        text = digest.render([], **{section: [post]})
    assert IMAGE not in text
    assert MARKDOWN not in text
    assert ESCAPED_IMAGE in text
    assert ESCAPED_MARKDOWN in text
    rendered = RenderedDigest(text)
    assert "img" not in rendered.tags
    assert rendered.links == [post.url]
    assert IMAGE + " " + MARKDOWN in "".join(rendered.text)


def test_location_and_failed_board_names_are_literal_single_lines():
    text = digest.render([posting(location=IMAGE + "\n\n# Forged section")],
                         boards_failed=[MARKDOWN + "\r\n> Forged warning"])
    assert IMAGE not in text and MARKDOWN not in text
    assert r'\# Forged section' in text
    assert '&gt; Forged warning' in text
    assert '\n# Forged section' not in text
    assert '\n> Forged warning' not in text
    rendered = RenderedDigest(text)
    assert "img" not in rendered.tags
    assert rendered.links == ["https://example.com/synthetic"]


@pytest.mark.parametrize("section", ["new", "backfill", "filtered", "off_level"])
def test_links_encode_delimiters_without_changing_posting_identity(section):
    url = 'https://example.com/a)![x](https://example.com/p)<img>?a=1&b=2'
    post = posting(url=url)
    original = copy.deepcopy(post)
    text = (digest.render([post]) if section == "new"
            else digest.render([], **{section: [post]}))
    assert '<https://example.com/a)![x](https://example.com/p)%3Cimg%3E?a=1&b=2>' in text
    assert '<img>' not in text
    rendered = RenderedDigest(text)
    assert rendered.links == [
        'https://example.com/a)!%5Bx%5D(https://example.com/p)%3Cimg%3E?a=1&b=2'
    ]
    assert "img" not in rendered.tags
    assert post.__dict__ == original.__dict__
    assert post.identity == ("greenhouse", "synthetic", url)
    assert post.to_dict()["url"] == url


def test_skills_and_unknown_blockers_cannot_inject_markup():
    post = posting()
    post.match = keywords.compare("Python", "Python Intern\nPython Docker")
    post.match.hard_missing = [MARKDOWN]
    post.match.hard_matched = [IMAGE]
    blocked = posting(blockers=[MARKDOWN])
    text = digest.render([post], filtered=[blocked] * (digest.SAMPLE + 1))
    assert f"Missing: {ESCAPED_MARKDOWN}" in text
    assert f"Have: {ESCAPED_IMAGE}" in text
    assert MARKDOWN not in text
    assert text.count(ESCAPED_MARKDOWN) == digest.SAMPLE + 2
    assert "<details>" in text and "</details>" in text


def test_ordinary_unicode_text_and_digest_structure_survive():
    post = posting(company="Café & Co", title="C++ / C# Intern",
                   location="München", url="https://example.com/job?a=1&b=2")
    post.fit = 75
    text = digest.render([post], on=date(2026, 11, 24))
    assert "# Internship shortlist — 2026-11-24" in text
    assert r'## 75% · Café &amp; Co — C\+\+ / C\# Intern' in text
    assert 'München · <https://example.com/job?a=1&b=2>' in text
    assert '1 new, 0 already seen, 0 filtered as ineligible' in text
    assert 'Café & Co — C++ / C# Intern' in "".join(RenderedDigest(text).text)


@pytest.mark.parametrize("url,expected", [
    ("https://example.com/?a=1&b=2&literal=&copy;",
     "https://example.com/?a=1&b=2&literal=&copy;"),
    ("https://example.com/a%2Fb?literal=%26#part", "https://example.com/a%2Fb?literal=%26#part"),
    ('https://example.com/\">\n<img/src=x>',
     'https://example.com/%22%3E%0A%3Cimg/src=x%3E'),
])
def test_rendered_link_preserves_queries_and_contains_raw_delimiters(url, expected):
    rendered = RenderedDigest(digest.render([posting(url=url)]))
    assert rendered.links == [expected]
    assert "img" not in rendered.tags


def test_existing_entities_and_backslashes_stay_literal():
    text = digest.render([posting(title=r'&lt;script&gt; \![x](u) Intern')])
    assert r'&amp;lt;script&amp;gt; \\\!\[x\]\(u\) Intern' in text
    assert r'&lt;script&gt; \![x](u) Intern' in "".join(RenderedDigest(text).text)


def test_unexpected_location_type_retains_previous_string_formatting():
    post = jobs.parse_lever([{"text": "Python Intern", "descriptionPlain": "Python",
                              "hostedUrl": "https://example.com/synthetic",
                              "categories": {"location": 123}}], "synthetic")[0]
    assert "123 ·" in digest.render([post])


def test_watch_escapes_both_first_digest_and_carryover_but_preserves_json(tmp_path, capsys):
    resume = tmp_path / "resume.txt"
    resume.write_text("Python", encoding="utf-8")
    boards = tmp_path / "boards.txt"
    boards.write_text("greenhouse:synthetic\n", encoding="utf-8")
    payload = {"jobs": [{"title": IMAGE + " Python Intern", "content": "Python",
                          "absolute_url": "https://example.com/synthetic",
                          "location": {"name": MARKDOWN}}]}
    posts = jobs.parse_greenhouse(payload, "synthetic")
    argv = ["watch", "--resume", str(resume), "--boards", str(boards),
            "--db", str(tmp_path / "seen.db"), "--min-fit", "0"]
    with mock.patch.object(jobs, "fetch", return_value=posts):
        assert cli.main(argv) == 0
        first = capsys.readouterr().out
        assert cli.main(argv) == 0
        second = capsys.readouterr().out
        assert cli.main(argv + ["--json"]) == 0
        data = json.loads(capsys.readouterr().out)
    assert IMAGE not in first and MARKDOWN not in first
    assert IMAGE not in second
    for text in (first, second):
        rendered = RenderedDigest(text)
        assert "img" not in rendered.tags
        assert rendered.links == ["https://example.com/synthetic"]
    assert '1 new, 0 already seen' in first
    assert '1 carried over' in second
    assert data["backfill"][0]["title"] == payload["jobs"][0]["title"]
    assert data["backfill"][0]["location"] == MARKDOWN
