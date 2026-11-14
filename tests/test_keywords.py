"""Regression coverage for short technology names in keyword scoring."""

import pytest

from resume_radar import keywords


@pytest.mark.parametrize("skill", ["C", "R", "Go", "C#"])
def test_short_hard_skills_survive_case_and_punctuation(skill):
    text = f"({skill}), {skill.upper()}; {skill.lower()}."
    assert keywords.tokenize(text) == {skill.lower()}


@pytest.mark.parametrize("skill", ["C", "R", "Go", "C#"])
def test_short_hard_skills_affect_matching_emphasis_and_score(skill):
    jd = f"Software Engineer\n{skill}, {skill}."
    absent = keywords.compare("Software Engineer with Python", jd)
    present = keywords.compare(f"Software Engineer with {skill}", jd)
    term = skill.lower()

    assert absent.hard_missing == [term]
    assert absent.priority == [term]
    assert term in absent.missing and term not in absent.matched
    assert present.hard_matched == [term]
    assert term in present.matched and term not in present.missing
    assert present.hard_missing == []
    assert present.percent == 100.0
    assert absent.percent < present.percent
    assert absent.emphasis == present.emphasis == {term: 2}
    assert present.to_dict()["hard_matched"] == [term]


def test_short_hard_skills_remain_distinct_from_each_other_and_cpp():
    skills = {"c", "r", "go", "c#", "c++", "sql"}
    assert keywords.tokenize("C, R, Go, C#, C++, SQL") == skills
    match = keywords.compare("C++", "C, C#, C++")
    assert match.hard_matched == ["c++"]
    assert match.hard_missing == ["c", "c#"]


def test_unlisted_short_tokens_and_stopwords_are_still_ignored():
    assert keywords.tokenize("A I X UI UX an in on to at by we us") == set()


@pytest.mark.parametrize("word,skill", [
    ("Scala", "c"), ("Ruby", "r"), ("Golang", "go"), ("CSharp", "c#"),
])
def test_short_skills_are_not_extracted_from_longer_words(word, skill):
    assert keywords.tokenize(word) == {word.lower()}
    assert skill not in keywords.compare(word, f"Skills\n{skill}").matched
