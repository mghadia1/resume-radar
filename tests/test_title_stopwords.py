"""Title stopwords must be ignored on both sides before awarding title credit."""

import pytest

from resume_radar import jobs, keywords


@pytest.mark.parametrize("title,resume", [
    ("Head of Research", "Head of Research"),
    ("Director of Engineering", "Director of Engineering, Acme"),
    ("Research and Development Engineer", "Research and Development Engineer"),
    ("Engineer in Test", "Engineer in Test"),
    ("Head of Research", "Experience: HEAD OF RESEARCH"),
    ("Head of Research", "Head\nof\tResearch"),
    ("Head of Research", "Head-of-Research"),
    ("Head of Research Intern - Summer 2027", "Head of Research"),
    ("Head Research", "Head of Research"),
    ("Head of Research", "Head Research"),
])
def test_title_stopwords_are_ignored_in_both_inputs(title, resume):
    assert keywords.title_present(title, resume)


@pytest.mark.parametrize("title,resume", [
    ("Head of Research", "Head of Product Research"),
    ("Head of Research", "Research Head"),
    ("Head of Research", "Head of Engineering"),
    ("Software Engineer", "Software QA Engineer"),
    ("Software Engineer", "Software AI Engineer"),
    ("Software Engineer", "Software Platform Engineer"),
    ("and of the", "and of the"),
    ("Intern - Summer 2027", "Intern - Summer 2027"),
    ("", "Head of Research"),
    ("Head of Research", ""),
])
def test_stopword_filter_preserves_content_order_and_empty_title_guard(title, resume):
    assert not keywords.title_present(title, resume)


def test_stopwords_affect_title_credit_without_changing_matched_terms():
    jd = "Head of Research\nPython"
    exact = keywords.compare("Head of Research; Python", jd)
    different = keywords.compare("Research Head; Python", jd)

    assert exact.matched == different.matched == ["head", "python", "research"]
    assert exact.missing == different.missing == []
    assert exact.title_matched
    assert exact.percent == 100.0
    assert exact.to_dict()["title_matched"] is True
    assert exact.to_dict()["percent"] == 100.0
    assert not different.title_matched
    assert different.percent == pytest.approx(100 * 5 / 11)
    assert different.to_dict()["title_matched"] is False
    assert different.to_dict()["percent"] == 45.5


def test_ranking_uses_title_credit_when_resume_includes_stopwords():
    posts = [
        jobs.Posting("acme", "Research Head", "https://example.com/reversed", "Python"),
        jobs.Posting("acme", "Head of Research", "https://example.com/exact", "Python"),
    ]
    ranked = jobs.rank("Head of Research; Python", posts)

    assert [p.title for p in ranked] == ["Head of Research", "Research Head"]
    assert ranked[0].fit == 100.0
    assert ranked[0].to_dict()["title_matched"] is True
    assert ranked[1].fit == pytest.approx(100 * 5 / 11)
