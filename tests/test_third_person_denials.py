"""Third-person sponsorship denials must stay out of both shortlist pools."""

import json

import pytest

from resume_radar import cli, jobs


@pytest.mark.parametrize("denial", [
    "This employer does not sponsor visas.",
    "The company doesn't sponsor visas.",
    "The company doesn’t sponsor visas.",
    "THIS EMPLOYER DOES\nNOT\tSPONSOR VISAS.",
])
@pytest.mark.parametrize("field", ["title", "description"])
def test_third_person_denial_is_screened_in_either_field(denial, field):
    post = posting()
    setattr(post, field, denial)

    assert jobs.screen(post) is post
    assert post.blockers == ["no_sponsorship"]
    assert not post.eligible
    assert not post.sponsorship_friendly
    assert post.to_dict()["eligible"] is False


def posting(description="Python", url="https://example.com/synthetic"):
    return jobs.Posting("synthetic", "Python Intern", url, description)


@pytest.mark.parametrize("text", [
    "This employer does sponsor visas.",
    "This employer does not require previous internship experience.",
    "This employer doesn't require previous internship experience.",
    "We can sponsor visas.",
])
def test_affirmative_and_unrelated_negative_statements_remain_eligible(text):
    assert jobs.screen(posting(text)).eligible


@pytest.mark.parametrize("offer_first", [False, True])
def test_separate_offer_keeps_existing_precedence(offer_first):
    offer = "We can sponsor visas for this internship."
    denial = "A different subsidiary does not sponsor visas."
    text = f"{offer} {denial}" if offer_first else f"{denial} {offer}"

    post = jobs.screen(posting(text))
    assert post.eligible
    assert post.sponsorship_friendly


def test_rank_omits_denial_unless_ineligible_postings_are_requested():
    denied = posting("This employer does not sponsor visas.")
    allowed = posting(url="https://example.com/allowed")

    assert jobs.rank("Python", [denied, allowed]) == [allowed]
    assert jobs.rank("Python", [denied], include_ineligible=True) == [denied]
    assert denied.blockers == ["no_sponsorship"]


@pytest.mark.parametrize("is_new", [False, True])
def test_denial_is_filtered_instead_of_filling_a_new_or_backfill_slot(is_new):
    denied = posting("This employer doesn’t sponsor visas.")
    slate = jobs.build_slate("Python", [denied], is_new=lambda p: is_new, target=1)

    assert slate.new == slate.backfill == []
    assert slate.filtered == [denied]
    assert slate.short == 1
    assert slate.to_dict()["filtered_count"] == 1


def test_watch_reports_denial_using_only_synthetic_mocked_postings(
        tmp_path, monkeypatch, capsys):
    resume = tmp_path / "resume.txt"
    resume.write_text("Python", encoding="utf-8")
    boards = tmp_path / "boards.txt"
    boards.write_text("lever:synthetic\n", encoding="utf-8")
    monkeypatch.setattr(jobs, "fetch", lambda *a, **kw: [
        posting("This employer doesn't sponsor visas.")])

    assert cli.main([
        "watch", "--resume", str(resume), "--boards", str(boards),
        "--db", str(tmp_path / "seen.db"), "--dry-run", "--json", "--min-fit", "0",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["new"] == payload["backfill"] == []
    assert payload["filtered"][0]["blockers"] == ["no_sponsorship"]
