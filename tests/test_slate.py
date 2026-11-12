"""Role-level filtering and the fixed-size daily shortlist.

The slate exists because a daily digest has two failure modes, not one. Showing
postings a student cannot win wastes the list; showing an empty list on a slow day
gets the whole habit abandoned. These tests pin both.
"""

from __future__ import annotations

import pytest

from resume_radar import jobs

RESUME = ("Python, PyTorch, Docker, OpenCV, computer vision, machine learning, "
          "pytest, ROS 2, Gazebo, NumPy, pandas.")


def post(title: str, description: str = "Python PyTorch Docker", **kw) -> jobs.Posting:
    return jobs.Posting(kw.get("company", "acme"), title,
                        kw.get("url", f"https://x/{abs(hash(title)) % 9999}"),
                        description)


# --------------------------------------------------------------------------- #
# Level classification
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("title,expected", [
    ("Software Engineer Intern", "intern"),
    ("Machine Learning Internship - Summer 2027", "intern"),
    ("Robotics Co-op", "intern"),
    ("Robotics CoOp Engineer", "intern"),
    ("2027 Summer Analyst, Technology", "intern"),
    ("New Grad Software Engineer", "new_grad"),
    ("Software Engineer, Early Career", "new_grad"),
    ("Entry-Level Data Analyst", "new_grad"),
    ("Senior Machine Learning Engineer", "senior"),
    ("Staff Software Engineer", "senior"),
    ("Principal Architect", "senior"),
    ("Engineering Manager", "senior"),
    ("Software Engineer II", "mid"),
    ("Backend Engineer", "mid"),
])
def test_level_is_read_from_the_title(title, expected):
    assert jobs.classify_level(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Junior Software Engineer",
        "Jr. Developer",
        "Associate Software Engineer",
        "Software Engineer I",
        "Software Engineer - University Graduate",
        "New Graduate Engineer",
        "College Grad Program",
        "Trainee Analyst",
    ],
)
def test_entry_level_titles_are_not_dropped(title: str):
    """Regression: these all read as "mid" and were silently discarded.

    "University Graduate" is the subtle one - the pattern was `university grad`
    inside `\\b(...)\\b`, and the trailing boundary cannot match mid-word.
    """
    assert jobs.classify_level(title) in jobs.STUDENT_LEVELS


@pytest.mark.parametrize(
    "title",
    ["Associate Director of Engineering", "Senior Staff Engineer",
     "Principal Architect", "Software Engineer III", "Engineering Manager"],
)
def test_senior_titles_are_still_dropped(title: str):
    """Senior is checked before new_grad so "Associate Director" is not entry level."""
    assert jobs.classify_level(title) == "senior"


@pytest.mark.parametrize(
    "title",
    [
        # Found on real boards: 20-odd Stripe roles matched a bare "associate".
        "Operations Associate",
        "Fraud Operations Associate SDC",
        "Operations Associate, Financial Crimes (AML Investigations)",
        "Credit Risk Operations Associate (CDMX)",
    ],
)
def test_bare_associate_is_a_job_family_not_a_level(title: str):
    assert jobs.classify_level(title) == "mid"


@pytest.mark.parametrize(
    "title",
    ["Associate Software Engineer", "Associate Machine Learning Engineer",
     "Associate Data Scientist"],
)
def test_associate_next_to_a_technical_noun_is_entry_level(title: str):
    assert jobs.classify_level(title) == "new_grad"


def test_level_two_is_not_claimed_as_entry_level():
    """Level I is the entry rung; level II is usually two years in."""
    assert jobs.classify_level("Software Engineer II") == "mid"


def test_off_level_postings_are_kept_for_reporting():
    posts = [post("ML Intern"), post("Staff Engineer"), post("Backend Engineer")]
    slate = jobs.build_slate("Python", posts, is_new=lambda p: True)
    assert [p.title for p in slate.off_level] == ["Staff Engineer", "Backend Engineer"]
    assert slate.wrong_level == 2


def test_postings_are_hashable_by_identity():
    a = post("ML Intern")
    b = jobs.Posting(a.company, a.title, a.url, "different text", "", a.source)
    assert a == b and hash(a) == hash(b)
    assert len({a, b}) == 1
    assert post("ML Intern", url="https://x/other") != a


def test_intern_beats_senior_when_both_words_appear():
    """"Senior Data Science Intern" is an internship, not a senior role."""
    assert jobs.classify_level("Senior Data Science Intern") == "intern"


def test_filter_levels_keeps_only_student_roles_by_default():
    posts = [post("ML Intern"), post("Staff Engineer"), post("New Grad SWE"),
             post("Software Engineer II")]
    kept = [p.title for p in jobs.filter_levels(posts)]
    assert kept == ["ML Intern", "New Grad SWE"]


def test_filter_levels_accepts_an_explicit_level_set():
    posts = [post("ML Intern"), post("Software Engineer II")]
    kept = [p.title for p in jobs.filter_levels(posts, levels=("mid",))]
    assert kept == ["Software Engineer II"]


# --------------------------------------------------------------------------- #
# The daily slate
# --------------------------------------------------------------------------- #

def test_slate_fills_from_new_postings_first():
    posts = [post(f"ML Intern {i}") for i in range(12)]
    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: True, target=10)
    assert len(slate.new) == 10
    assert slate.backfill == []
    assert slate.short == 0


def test_slate_backfills_when_there_is_not_enough_new():
    posts = [post(f"ML Intern {i}") for i in range(12)]
    fresh = {p.url for p in posts[:3]}
    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: p.url in fresh, target=10)
    assert len(slate.new) == 3
    assert len(slate.backfill) == 7
    assert len(slate.picks) == 10


def test_backfill_is_never_counted_as_new():
    """A slow day must read as a slow day."""
    posts = [post(f"ML Intern {i}") for i in range(5)]
    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: False, target=10)
    assert slate.new == []
    assert len(slate.backfill) == 5
    assert not {p.url for p in slate.new} & {p.url for p in slate.backfill}


def test_slate_reports_the_shortfall_rather_than_padding():
    posts = [post("ML Intern")]
    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: True, target=10)
    assert len(slate.picks) == 1
    assert slate.short == 9


@pytest.mark.parametrize("fresh", [True, False])
@pytest.mark.parametrize("another_opening", [True, False])
def test_slate_duplicates_cannot_consume_slots_or_hide_a_shortfall(fresh, another_opening):
    first = post("ML Intern", url="https://x/1")
    # A board may repeat the object or return a separate copy with changed metadata.
    duplicate = jobs.Posting(first.company, first.title, first.url,
                             "Kubernetes Kafka", location="Elsewhere", source=first.source)
    other = post("ML Intern", url="https://x/2")
    posts = [first, first, duplicate] + ([other] if another_opening else [])

    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: fresh, target=2)

    expected = [first, other] if another_opening else [first]
    assert slate.picks == expected
    assert slate.picks[0] is first  # The first copy supplies the posting's metadata.
    assert slate.new == (expected if fresh else [])
    assert slate.backfill == ([] if fresh else expected)
    assert slate.short == (0 if another_opening else 1)


@pytest.mark.parametrize("title,description,section", [
    ("ML Intern", "Must be a US citizen. Python.", "filtered"),
    ("Staff Engineer", "Python Docker", "off_level"),
])
def test_slate_reports_duplicate_exclusions_once(title, description, section):
    first = post(title, description, url="https://x/1")
    duplicate = post(title, description, url=first.url)

    slate = jobs.build_slate(RESUME, [first, duplicate], is_new=lambda p: True)

    assert getattr(slate, section) == [first]
    assert slate.wrong_level == (1 if section == "off_level" else 0)
    assert slate.picks == []


@pytest.mark.parametrize("difference", [
    {"source": "lever"}, {"company": "other"}, {"url": "https://x/2"},
])
def test_slate_keeps_postings_with_distinct_identities(difference):
    fields = dict(company="acme", title="ML Intern", url="https://x/1",
                  description="Python Docker", source="greenhouse")
    first = jobs.Posting(**fields)
    other = jobs.Posting(**(fields | difference))

    slate = jobs.build_slate(RESUME, [first, first, other], is_new=lambda p: True, target=2)

    assert slate.picks == [first, other]
    assert slate.short == 0


def test_slate_excludes_wrong_level_and_counts_them():
    posts = [post("ML Intern"), post("Staff Engineer"), post("Engineering Manager")]
    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: True, target=10)
    assert [p.title for p in slate.picks] == ["ML Intern"]
    assert slate.wrong_level == 2


def test_slate_excludes_ineligible_but_keeps_them_visible():
    posts = [
        post("ML Intern", "Python PyTorch"),
        post("AI Intern", "Python. Must be a U.S. citizen."),
    ]
    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: True, target=10)
    assert [p.title for p in slate.picks] == ["ML Intern"]
    assert [p.title for p in slate.filtered] == ["AI Intern"]
    assert slate.filtered[0].blockers == ["citizenship"]


def test_slate_orders_by_fit_within_new():
    posts = [
        post("Backend Intern", "Kafka Kubernetes PostgreSQL Go"),
        post("CV Intern", "Python PyTorch OpenCV computer vision Docker"),
    ]
    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: True, target=10)
    assert slate.new[0].title == "CV Intern"
    assert slate.new[0].fit > slate.new[1].fit


def test_min_fit_drops_weak_matches_from_both_pools():
    posts = [
        post("CV Intern", "Python PyTorch OpenCV computer vision"),
        post("Sales Intern", "cold calling quota CRM"),
    ]
    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: True,
                             target=10, min_fit=20.0)
    assert [p.title for p in slate.picks] == ["CV Intern"]


def test_slate_serializes_for_json():
    import json
    posts = [post("ML Intern")]
    slate = jobs.build_slate(RESUME, posts, is_new=lambda p: True, target=10)
    payload = json.dumps(slate.to_dict())
    assert '"target": 10' in payload
    assert '"short": 9' in payload
