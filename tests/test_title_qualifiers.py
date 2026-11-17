"""Technical qualifiers must survive normalization before awarding title credit."""

import pytest

from resume_radar import jobs, keywords


@pytest.mark.parametrize("title,resume", [
    ("C++ Engineer", "Python Engineer"),
    ("C# Engineer", "Python Engineer"),
    ("C Engineer", "Python Engineer"),
    ("R Analyst", "Data Analyst"),
    ("Go Developer", "Python Developer"),
    ("Python3 Engineer", "Python Engineer"),
    ("C++ Engineer", "C# Engineer"),
    ("C# Engineer", "C++ Engineer"),
    ("C Engineer", "C++ Engineer"),
    ("C++ Engineer", "C Engineer"),
    ("Engineer C++", "Engineer C#"),
])
def test_missing_or_different_qualifier_earns_no_title_credit(title, resume):
    assert not keywords.title_present(title, resume)


@pytest.mark.parametrize("title,resume", [
    ("C++ Engineer Intern - Summer 2027", "Experience: (C++ ENGINEER), Acme"),
    ("C# Engineer", "C#-Engineer"),
    ("C Engineer", "C\nEngineer"),
    ("R Analyst", "R Analyst, Acme"),
    ("Go Developer", "GO Developer"),
    ("Python3 Engineer", "Python3 Engineer"),
    ("C++ Intern", "Skills: C++"),
    ("C# Intern", "Skills: C#"),
    ("R Intern", "Skills: R"),
    ("Go Intern", "Skills: Go"),
])
def test_exact_qualifier_still_matches_with_case_and_noise(title, resume):
    assert keywords.title_present(title, resume)


def test_qualifier_changes_title_weight_and_json_without_changing_term_matches():
    jd = "C++ Engineer\nPython"
    wrong = keywords.compare("Python Engineer; C++ projects", jd)
    exact = keywords.compare("C++ Engineer; Python projects", jd)

    assert wrong.matched == exact.matched == ["c++", "engineer", "python"]
    assert wrong.missing == exact.missing == []
    assert not wrong.title_matched
    assert wrong.percent == pytest.approx(100 * 7 / 13)
    assert wrong.to_dict()["title_matched"] is False
    assert wrong.to_dict()["percent"] == 53.8
    assert exact.title_matched
    assert exact.percent == 100.0


def test_ranking_uses_the_qualified_title():
    posts = [
        jobs.Posting("acme", "C++ Engineer", "https://example.com/cpp", "Python"),
        jobs.Posting("acme", "Python Engineer", "https://example.com/python", "C++"),
    ]
    ranked = jobs.rank("Python Engineer; C++ projects", posts)

    assert [p.title for p in ranked] == ["Python Engineer", "C++ Engineer"]
    assert ranked[0].fit == 100.0
    assert ranked[1].fit == pytest.approx(100 * 7 / 13)
    assert ranked[1].to_dict()["title_matched"] is False
