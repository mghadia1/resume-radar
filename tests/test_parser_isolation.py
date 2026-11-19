"""A structurally malformed posting must not discard its valid siblings."""

import io
import json
import logging

import pytest

from resume_radar import jobs


def record(source, number):
    if source == "greenhouse":
        return {"title": f"Python Intern {number}",
                "absolute_url": f"https://example.com/{number}",
                "location": {"name": "Remote"}, "content": "<p>Python</p>"}
    return {"text": f"Python Intern {number}",
            "hostedUrl": f"https://example.com/{number}",
            "categories": {"location": "Remote"},
            "descriptionPlain": "Python", "lists": [{"content": "<p>Docker</p>"}]}


def payload(source, records):
    return {"jobs": records} if source == "greenhouse" else records


def parse(source, records):
    parser = jobs.parse_greenhouse if source == "greenhouse" else jobs.parse_lever
    return parser(payload(source, records), "synthetic")


@pytest.mark.parametrize("source", ["greenhouse", "lever"])
@pytest.mark.parametrize("bad", [None, "private-payload-marker", 42, [], True])
def test_non_object_entry_does_not_discard_siblings(source, bad, caplog):
    with caplog.at_level(logging.WARNING, logger="resume_radar.jobs"):
        posts = parse(source, [record(source, 1), bad, record(source, 2)])

    assert [p.url for p in posts] == ["https://example.com/1", "https://example.com/2"]
    assert [p.title for p in posts] == ["Python Intern 1", "Python Intern 2"]
    assert all(p.company == "synthetic" and p.source == source for p in posts)
    assert all(p.location == "Remote" and "Python" in p.description for p in posts)
    assert len(caplog.records) == 1
    assert f"{source}/synthetic" in caplog.text and "index 1" in caplog.text
    assert "private-payload-marker" not in caplog.text


@pytest.mark.parametrize("source,override", [
    ("greenhouse", {"location": "private-payload-marker"}),
    ("greenhouse", {"content": {"private-payload-marker": True}}),
    ("lever", {"categories": "private-payload-marker"}),
    ("lever", {"lists": None}),
    ("lever", {"lists": [None]}),
    ("lever", {"lists": [{"content": {"private-payload-marker": True}}]}),
])
def test_nested_parse_failure_skips_the_entire_record(source, override, caplog):
    bad = record(source, 2) | override
    posts = parse(source, [bad, record(source, 1), bad])

    assert [p.url for p in posts] == ["https://example.com/1"]
    assert len(caplog.records) == 2
    assert "private-payload-marker" not in caplog.text
    assert "index 0" in caplog.text and "index 2" in caplog.text


def mock_response(monkeypatch, value):
    # BytesIO is a context manager; no test performs an HTTP request.
    monkeypatch.setattr(jobs.urllib.request, "urlopen",
                        lambda *a, **kw: io.BytesIO(json.dumps(value).encode()))


@pytest.mark.parametrize("source", ["greenhouse", "lever"])
def test_fetch_retains_siblings_and_all_screening_text(source, monkeypatch, caplog):
    first = record(source, 1)
    blocked = record(source, 2)
    if source == "greenhouse":
        blocked["content"] = "<p>Python. Must be a US citizen.</p>"
    else:
        blocked["lists"] = [{"content": "<p>Must be a US citizen.</p>"}]
    mock_response(monkeypatch, payload(source, [first, None, blocked]))

    posts = jobs.fetch("synthetic", source)
    slate = jobs.build_slate("Python Docker", posts, is_new=lambda p: True, target=2)

    assert [p.url for p in slate.picks] == ["https://example.com/1"]
    assert [p.url for p in slate.filtered] == ["https://example.com/2"]
    assert slate.filtered[0].blockers == ["citizenship"]
    assert slate.short == 1
    assert len(caplog.records) == 1


@pytest.mark.parametrize("source", ["greenhouse", "lever"])
def test_all_unparseable_entries_remain_a_board_error(source, monkeypatch):
    mock_response(monkeypatch, payload(source, [None, "bad"]))
    with pytest.raises(RuntimeError, match=f"unexpected payload from {source}/synthetic"):
        jobs.fetch("synthetic", source)


@pytest.mark.parametrize("source,value", [
    ("greenhouse", []), ("greenhouse", None),
    ("greenhouse", {"jobs": None}), ("greenhouse", {"jobs": {}}),
    ("lever", {}), ("lever", None), ("lever", "bad"),
])
def test_invalid_containers_remain_board_errors(source, value, monkeypatch):
    mock_response(monkeypatch, value)
    with pytest.raises(RuntimeError, match="unexpected payload"):
        jobs.fetch("synthetic", source)


@pytest.mark.parametrize("source", ["greenhouse", "lever"])
def test_empty_boards_remain_successful_without_warnings(source, monkeypatch, caplog):
    mock_response(monkeypatch, payload(source, []))
    assert jobs.fetch("synthetic", source) == []
    assert caplog.records == []
