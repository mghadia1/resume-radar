"""Title credit requires complete words after the existing normalization."""

import pytest

from resume_radar import jobs, keywords


@pytest.mark.parametrize("title,resume", [
    ("Software Engineer", "Software Engineering"),
    ("Software Engineer", "Software Engineers"),
    ("Data Analyst", "Metadata Analyst"),
    ("Engineer", "Reengineer"),
    ("Data Scientist", "Data Scientists"),
    ("Software Engineer", "xSoftware Engineer"),
])
def test_title_does_not_match_part_of_a_longer_word(title, resume):
    assert not keywords.title_present(title, resume)


@pytest.mark.parametrize("resume", [
    "Software Engineer",
    "Software Engineer, Acme",
    "Experience: Software Engineer",
    "Experience: (SOFTWARE ENGINEER), Acme",
    "Software\n\tEngineer",
    "Software-Engineer",
    "Software Engineering; later Software Engineer at Acme",
])
def test_whole_title_still_matches_after_normalization(resume):
    assert keywords.title_present("Software Engineer Intern - Summer 2027", resume)


@pytest.mark.parametrize("title,resume", [
    ("", "Software Engineer"),
    ("Intern - Summer 2027", "Software Engineer"),
    ("Software Engineer", ""),
    ("Software Engineer", "Software Platform Engineer"),
])
def test_empty_or_noncontiguous_title_does_not_match(title, resume):
    assert not keywords.title_present(title, resume)


def test_partial_title_earns_no_title_weight_even_when_all_terms_match():
    jd = "Software Engineer\nPython"
    partial = keywords.compare("Software Engineering; Software tools; Engineer Python", jd)
    exact = keywords.compare("Software Engineer Python", jd)

    assert partial.matched == exact.matched == ["engineer", "python", "software"]
    assert partial.missing == exact.missing == []
    assert not partial.title_matched
    assert partial.percent == pytest.approx(100 * 5 / 11)
    assert partial.to_dict()["title_matched"] is False
    assert partial.to_dict()["percent"] == 45.5
    assert exact.title_matched
    assert exact.percent == 100.0


def test_rank_reports_no_title_credit_for_partial_words():
    posting = jobs.Posting("acme", "Software Engineer", "https://example.com/1", "Python")
    ranked = jobs.rank("Software Engineering; Software tools; Engineer Python", [posting])

    assert len(ranked) == 1
    assert ranked[0].fit == pytest.approx(100 * 5 / 11)
    assert ranked[0].to_dict()["title_matched"] is False
