"""Regression coverage for whole-phrase keyword matching and weighting."""

import pytest

from resume_radar import keywords


@pytest.mark.parametrize("phrase", keywords.PHRASES)
def test_known_phrases_remain_intact(phrase):
    assert keywords.tokenize(f"({phrase.upper()}), {phrase}.") == {phrase}


@pytest.mark.parametrize("text,expected", [
    ("preunit testing", {"preunit", "testing"}),
    ("unit testingish", {"unit", "testingish"}),
    ("unit tests", {"unit", "tests"}),
    ("deep learningcurve", {"deep", "learningcurve"}),
    ("premachine learning", {"premachine", "learning"}),
    ("unit testing2", {"unit", "testing2"}),
    ("unit testing_helper", {"unit", "testing_helper"}),
    ("unit testingish, unit testing", {"unit", "testingish", "unit testing"}),
])
def test_phrases_do_not_match_inside_longer_words(text, expected):
    assert keywords.tokenize(text) == expected


def test_shorter_phrase_is_counted_separately_from_its_longer_neighbor():
    match = keywords.compare(
        "Software Engineer",
        "Software Engineer\nunit test; unit testing; UNIT TESTING. Python.",
    )
    assert match.missing == ["python", "unit test", "unit testing"]
    assert match.hard_missing == ["python", "unit testing"]
    assert match.emphasis == {"unit testing": 2}
    assert match.priority == ["unit testing", "python"]
    assert match.to_dict()["priority"] == ["unit testing", "python"]


def test_longer_phrase_retains_hard_skill_weight():
    jd = "Software Engineer\nUnit testing. Unit testing. Python. Communication."
    resume = "Software Engineer Python Communication"
    absent = keywords.compare(resume, jd)
    present = keywords.compare(f"{resume} Unit testing", jd)

    assert absent.hard_missing == ["unit testing"]
    assert absent.percent == pytest.approx(80.0)
    assert present.hard_matched == ["python", "unit testing"]
    assert present.hard_missing == []
    assert present.percent == 100.0
    assert absent.emphasis == present.emphasis == {"unit testing": 2}
