"""Direct sponsorship denials must not enter the shortlist (synthetic inputs only)."""

import json

import pytest

from resume_radar import cli, jobs


DENIALS = [
    "We cannot sponsor visas for this role.",
    "We can't sponsor visas for this role.",
    "We can’t sponsor visas for this role.",
    "We won't sponsor visas for this role.",
    "We won’t sponsor visas for this role.",
    "We don't sponsor visas for this role.",
    "We don’t sponsor visas for this role.",
    "WE CANNOT\nSPONSOR VISAS.",
]


def posting(description, title="Python Intern", url="https://example.com/role"):
    return jobs.Posting("synthetic", title, url, description)


@pytest.mark.parametrize("denial", DENIALS)
@pytest.mark.parametrize("field", ["title", "description"])
def test_direct_denial_is_ineligible_in_either_screened_field(denial, field):
    post = posting("Python", title="Python Intern")
    setattr(post, field, denial)

    assert jobs.screen(post) is post
    assert post.blockers == ["no_sponsorship"]
    assert not post.eligible
    assert not post.sponsorship_friendly
    assert post.to_dict()["eligible"] is False


@pytest.mark.parametrize("text", [
    "We can sponsor visas.",
    "We sponsor visas.",
    "We cannot predict the project schedule.",
    "We don't require prior internship experience.",
])
def test_offers_and_unrelated_inability_remain_eligible(text):
    assert jobs.screen(posting(text)).eligible


@pytest.mark.parametrize("offer_first", [False, True])
def test_separate_affirmative_offer_retains_precedence(offer_first):
    offer = "We can sponsor visas for this internship."
    boilerplate = "Some other teams cannot sponsor visas."
    text = f"{offer} {boilerplate}" if offer_first else f"{boilerplate} {offer}"

    post = jobs.screen(posting(text))
    assert post.eligible
    assert post.sponsorship_friendly


def test_ranking_excludes_denial_unless_ineligible_results_are_requested():
    denied = posting(DENIALS[0])
    allowed = posting("Python", url="https://example.com/allowed")

    assert jobs.rank("Python", [denied, allowed]) == [allowed]
    included = jobs.rank("Python", [denied], include_ineligible=True)
    assert included == [denied]
    assert included[0].blockers == ["no_sponsorship"]


@pytest.mark.parametrize("is_new", [False, True])
def test_denial_is_reported_as_filtered_instead_of_filling_either_pool(is_new):
    denied = posting(DENIALS[1])
    slate = jobs.build_slate("Python", [denied], is_new=lambda p: is_new, target=1)

    assert slate.new == slate.backfill == []
    assert slate.filtered == [denied]
    assert slate.short == 1
    assert slate.filtered[0].blockers == ["no_sponsorship"]
    assert slate.to_dict()["filtered_count"] == 1


def test_watch_json_exposes_the_denial_with_mocked_boards(tmp_path, monkeypatch, capsys):
    resume = tmp_path / "resume.txt"
    resume.write_text("Python", encoding="utf-8")
    boards = tmp_path / "boards.txt"
    boards.write_text("lever:synthetic\n", encoding="utf-8")
    monkeypatch.setattr(jobs, "fetch", lambda *a, **kw: [posting(DENIALS[2])])

    assert cli.main([
        "watch", "--resume", str(resume), "--boards", str(boards),
        "--db", str(tmp_path / "seen.db"), "--dry-run", "--json", "--min-fit", "0",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["new"] == payload["backfill"] == []
    assert payload["filtered"][0]["blockers"] == ["no_sponsorship"]
