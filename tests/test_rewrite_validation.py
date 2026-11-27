"""Malformed optional model output must not interrupt deterministic scoring."""

import io
import json
from unittest import mock

import pytest

from resume_radar import analyze, cli, suggest
from test_watch import RESUME


VALID = {
    "original": "Built an ingest service in Python and Docker that cut latency by 40 percent",
    "suggested": "Built a Python and Docker ingest service, cutting latency by 40 percent",
    "why": "Tighter wording",
}


@pytest.mark.parametrize("rewrites", [None, True, 1, "text", {"suggested": "text"}])
def test_non_list_rewrites_are_ignored(rewrites):
    assert suggest.parse_rewrites(json.dumps({"rewrites": rewrites})) == []


@pytest.mark.parametrize("item", [None, True, 1, "text", [], [VALID]])
def test_malformed_entries_do_not_discard_valid_siblings(item):
    second = {"suggested": "Automated 9 release checks"}
    text = json.dumps({"rewrites": [item, VALID, item, second, item]})

    assert [s.to_dict() for s in suggest.parse_rewrites(text)] == [
        VALID, {"original": "", "suggested": second["suggested"], "why": ""},
    ]


@pytest.mark.parametrize("field", ["original", "suggested", "why"])
@pytest.mark.parametrize("value", [None, True, 42, ["text"], {"text": "text"}])
def test_non_string_fields_discard_the_entry_without_coercion(field, value):
    invalid = {**VALID, field: value}
    text = json.dumps({"rewrites": [invalid, VALID]})

    assert [s.to_dict() for s in suggest.parse_rewrites(text)] == [VALID]


@pytest.mark.parametrize("suggested", ["", " \t\n"])
def test_blank_suggestions_are_ignored(suggested):
    text = json.dumps({"rewrites": [{**VALID, "suggested": suggested}]})
    assert suggest.parse_rewrites(text) == []


def test_valid_fenced_text_is_preserved_verbatim():
    entry = {
        "original": "  Built a parser for café data.\n",
        "suggested": "  Built a café data parser.\n",
        "why": "  Shorter wording.\n",
        "extra": {"ignored": True},
    }
    text = "```json\n" + json.dumps({"rewrites": [entry]}) + "\n```"
    assert [s.to_dict() for s in suggest.parse_rewrites(text)] == [
        {key: entry[key] for key in ("original", "suggested", "why")},
    ]


def test_missing_and_empty_lists_remain_empty():
    assert suggest.parse_rewrites("{}") == []
    assert suggest.parse_rewrites('{"rewrites": []}') == []


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("rewrites", [
    None,
    [None, {**VALID, "original": 42}, VALID, {**VALID, "suggested": ["text"]}],
])
def test_score_cli_survives_malformed_model_output(
        tmp_path, monkeypatch, capsys, json_output, rewrites):
    path = tmp_path / "synthetic.txt"
    path.write_text(RESUME, encoding="utf-8")
    baseline = analyze(path).to_dict()
    payload = json.dumps({"content": [{
        "text": json.dumps({"rewrites": rewrites}),
    }]}).encode()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-key")
    argv = ["score", str(path), "--suggest"] + (["--json"] if json_output else [])

    with mock.patch.object(suggest.urllib.request, "urlopen",
                           return_value=io.BytesIO(payload)) as request:
        assert cli.main(argv) == 0
    request.assert_called_once()
    output = capsys.readouterr()
    assert output.err == ""
    if json_output:
        result = json.loads(output.out)
        for key in baseline:
            assert result[key] == baseline[key]
        assert result.get("suggestions", []) == ([VALID] if rewrites else [])
    else:
        assert f"{baseline['percent']}%" in output.out
        assert ("REWRITE" in output.out) == bool(rewrites)
        if rewrites:
            assert VALID["suggested"] in output.out
