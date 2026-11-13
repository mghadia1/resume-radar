"""Tests for posting fetch, screening, and ranking.

No test touches the network. ``fetch`` is a thin wrapper over two pure parsers, and
the parsers are what carry the logic worth testing.
"""

from __future__ import annotations

import pytest

from resume_radar import jobs


def gh_job(title: str, content: str, **kw) -> dict:
    return {
        "title": title,
        "absolute_url": kw.get("url", "https://example.com/job"),
        "location": {"name": kw.get("location", "Remote")},
        "content": content,
    }


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def test_greenhouse_payload_becomes_postings():
    payload = {"jobs": [gh_job("ML Intern", "<p>Python and PyTorch</p>")]}
    posts = jobs.parse_greenhouse(payload, "acme")
    assert len(posts) == 1
    assert posts[0].company == "acme"
    assert posts[0].title == "ML Intern"
    assert posts[0].source == "greenhouse"
    assert "PyTorch" in posts[0].description


def test_greenhouse_html_is_stripped_and_unescaped():
    payload = {"jobs": [gh_job("X", "&lt;p&gt;Python &amp;amp; Docker&lt;/p&gt;")]}
    description = jobs.parse_greenhouse(payload, "acme")[0].description
    assert "<p>" not in description
    assert "Python" in description and "Docker" in description


def test_lever_payload_includes_list_sections():
    payload = [{
        "text": "Backend Intern",
        "hostedUrl": "https://jobs.lever.co/beta/1",
        "categories": {"location": "NYC"},
        "descriptionPlain": "Build services.",
        "lists": [{"content": "<li>PostgreSQL</li><li>Kafka</li>"}],
    }]
    post = jobs.parse_lever(payload, "beta")[0]
    assert post.location == "NYC"
    assert "PostgreSQL" in post.description and "Kafka" in post.description


def test_empty_payloads_are_not_errors():
    assert jobs.parse_greenhouse({}, "acme") == []
    assert jobs.parse_lever([], "beta") == []


# --------------------------------------------------------------------------- #
# Screening - the part that decides what you never see
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,expected", [
    ("Applicants must be a U.S. citizen.", "citizenship"),
    ("US citizens only for this role.", "citizenship"),
    ("Requires an active security clearance.", "clearance"),
    ("TS/SCI required.", "clearance"),
    ("We will not sponsor visas for this position.", "no_sponsorship"),
    ("Sponsorship is not available.", "no_sponsorship"),
    ("Candidates must be authorized to work without sponsorship.", "no_sponsorship"),
    ("This role is subject to ITAR.", "export_control"),
])
def test_blockers_are_detected(text, expected):
    post = jobs.screen(jobs.Posting("acme", "Role", "u", text))
    assert expected in post.blockers
    assert not post.eligible


def test_explicit_offer_to_sponsor_beats_boilerplate():
    """A posting that says it sponsors must not be filtered out."""
    text = ("We are happy to sponsor visas. Some roles at other sites "
            "may not sponsor, check with the recruiter.")
    post = jobs.screen(jobs.Posting("acme", "ML Intern", "u", text))
    assert post.sponsorship_friendly
    assert "no_sponsorship" not in post.blockers
    assert post.eligible


@pytest.mark.parametrize("text", [
    "This employer does not offer visa sponsorship.",
    "This employer does not offer sponsorship.",
    "We are unable to sponsor visas.",
    "We are not able to sponsor visas.",
    "We are unable to offer visa sponsorship.",
    "We are not able to offer sponsorship.",
    "We are not able to offer visa sponsorship.",
    "We are unable to provide visa sponsorship.",
])
@pytest.mark.parametrize("in_title", [False, True])
def test_negated_sponsorship_offers_are_blockers(text, in_title):
    title, description = (text, "Python") if in_title else ("ML Intern", text)
    post = jobs.screen(jobs.Posting("acme", title, "u", description))

    assert post.blockers == ["no_sponsorship"]
    assert not post.sponsorship_friendly
    assert not post.eligible


@pytest.mark.parametrize("text", [
    "We will sponsor visas.",
    "We do sponsor visas.",
    "We can sponsor visas.",
    "We are happy to sponsor visas.",
    "We are able to sponsor visas.",
    "Sponsorship is available.",
    "Sponsorship available.",
    "We sponsor visas.",
    "We offer visa sponsorship.",
    "The employer offers sponsorship.",
    "We are open to candidates requiring sponsorship.",
])
def test_affirmative_sponsorship_offers_remain_eligible(text):
    post = jobs.screen(jobs.Posting("acme", "ML Intern", "u", text))

    assert post.sponsorship_friendly
    assert post.blockers == []
    assert post.eligible


@pytest.mark.parametrize("denial,offer", [
    ("Another site does not offer visa sponsorship.", "We offer visa sponsorship."),
    ("Another site is unable to sponsor visas.", "We are able to sponsor visas."),
    ("Another site is unable to sponsor visas.", "We are happy to sponsor visas."),
])
@pytest.mark.parametrize("offer_first", [False, True])
def test_separate_affirmative_offer_still_beats_sponsorship_boilerplate(
        denial, offer, offer_first):
    text = f"{offer} {denial}" if offer_first else f"{denial} {offer}"
    post = jobs.screen(jobs.Posting("acme", "ML Intern", "u", text))

    assert post.sponsorship_friendly
    assert post.blockers == []
    assert post.eligible


def test_sponsorship_offer_cannot_override_other_blockers():
    text = "We offer sponsorship. Applicants must be a US citizen."
    post = jobs.screen(jobs.Posting("acme", "ML Intern", "u", text))

    assert post.sponsorship_friendly
    assert post.blockers == ["citizenship"]
    assert not post.eligible


def test_negated_sponsorship_offer_is_excluded_from_ranking_and_slate():
    denied = jobs.Posting("acme", "ML Intern", "u1",
                          "Python. This employer does not offer visa sponsorship.")
    allowed = jobs.Posting("beta", "ML Intern", "u2", "Python.")

    assert jobs.rank("Python", [denied, allowed]) == [allowed]
    assert jobs.rank("Python", [denied], include_ineligible=True) == [denied]
    slate = jobs.build_slate("Python", [denied, allowed], is_new=lambda p: True, target=2)
    assert slate.picks == [allowed]
    assert slate.filtered == [denied]
    assert slate.short == 1


def test_ordinary_posting_has_no_blockers():
    post = jobs.screen(jobs.Posting("acme", "ML Intern", "u",
                                    "Python, PyTorch, Docker. Great team."))
    assert post.eligible and post.blockers == []


def test_blockers_are_read_from_the_title_too():
    post = jobs.screen(jobs.Posting("acme", "Engineer (US Citizens Only)", "u", "Python."))
    assert "citizenship" in post.blockers


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #

RESUME = ("Mayank Ghadia. Python, PyTorch, Docker, OpenCV, machine learning, "
          "computer vision, pytest, GitHub Actions. Machine Learning Intern projects.")


def test_better_matching_posting_ranks_higher():
    posts = [
        jobs.Posting("a", "Backend Intern", "u1", "Kafka, Kubernetes, PostgreSQL, Go."),
        jobs.Posting("b", "Machine Learning Intern", "u2",
                     "Python, PyTorch, Docker, computer vision."),
    ]
    ranked = jobs.rank(RESUME, posts)
    assert ranked[0].title == "Machine Learning Intern"
    assert ranked[0].fit > ranked[1].fit


def test_ineligible_postings_are_dropped_by_default():
    posts = [
        jobs.Posting("a", "ML Intern", "u1", "Python, PyTorch. We will not sponsor."),
        jobs.Posting("b", "ML Intern", "u2", "Python, PyTorch, Docker."),
    ]
    assert [p.company for p in jobs.rank(RESUME, posts)] == ["b"]
    assert len(jobs.rank(RESUME, posts, include_ineligible=True)) == 2


def test_rank_reports_the_skills_you_lack():
    post = jobs.Posting("a", "Backend Intern", "u",
                        "PostgreSQL, Kafka, Kubernetes required.")
    ranked = jobs.rank(RESUME, [post])
    missing = ranked[0].match.priority
    assert "postgresql" in missing and "kafka" in missing


def test_posting_serializes_to_json_ready_dict():
    import json
    ranked = jobs.rank(RESUME, [jobs.Posting("a", "ML Intern", "u", "Python, PyTorch.")])
    json.dumps(ranked[0].to_dict())


# --------------------------------------------------------------------------- #
# Board list
# --------------------------------------------------------------------------- #

def test_board_file_parsing_skips_comments_and_junk(tmp_path):
    path = tmp_path / "boards.txt"
    path.write_text(
        "# my boards\n"
        "greenhouse:figma\n"
        "\n"
        "lever:netflix   # trailing comment\n"
        "linkedin:something\n"      # unsupported source, must be ignored
        "greenhouse:\n"             # missing board, must be ignored
    )
    assert jobs.load_boards(str(path)) == [
        ("greenhouse", "figma"), ("lever", "netflix")
    ]


def test_fetch_wraps_network_errors(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(jobs.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="could not fetch"):
        jobs.fetch("figma", "greenhouse")
