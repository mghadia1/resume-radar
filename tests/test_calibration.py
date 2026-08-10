"""Ordering tests over the synthetic corpus in ``tests/fixtures/corpus``.

These assert that the scorer *ranks* obviously-better resumes above obviously-worse
ones. They do not assert that any individual percentage is correct, and they cannot:
there is no outcome data, and the corpus was written by the same person who chose the
thresholds. A passing suite here means the scoring is internally consistent, not that
83.5% means anything in the world. See ``docs/scoring.md``.

Where the scorer's order disagrees with the intended order, the disagreement is
encoded as an explicit test with the reason, rather than quietly dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_radar import checks, extract

CORPUS = Path(__file__).parent / "fixtures" / "corpus"


def _score(tmp_path: Path, name: str) -> float:
    """Score one corpus file, stripping its leading ``# rank`` comment."""
    source = CORPUS / name
    body = "\n".join(
        line for line in source.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    )
    target = tmp_path / name
    target.write_text(body, encoding="utf-8")
    return checks.run(extract.load(target)).percent


@pytest.fixture(scope="module")
def scores(tmp_path_factory) -> dict[str, float]:
    tmp = tmp_path_factory.mktemp("corpus")
    return {p.name: _score(tmp, p.name) for p in sorted(CORPUS.glob("*.txt"))}


def test_corpus_is_complete():
    assert len(list(CORPUS.glob("*.txt"))) == 10


# Tiers, not a strict total order. Within a tier the differences are matters of taste;
# between tiers they are not.
WELL_FORMED = ["01-excellent.txt", "02-strong.txt", "06-repetitive.txt"]
REAL_DEFECTS = ["03-good.txt", "04-wordy.txt", "07-passive.txt", "08-pronouns.txt"]
STRUCTURALLY_BROKEN = ["09-unstructured.txt", "10-terrible.txt"]


def test_well_formed_resumes_outrank_every_defective_one(scores):
    assert min(scores[n] for n in WELL_FORMED) > max(scores[n] for n in REAL_DEFECTS)


def test_defective_resumes_outrank_structurally_broken_ones(scores):
    assert min(scores[n] for n in REAL_DEFECTS) > max(scores[n] for n in STRUCTURALLY_BROKEN)


@pytest.mark.parametrize(
    "better,worse,why",
    [
        ("01-excellent.txt", "03-good.txt", "long bullets cost points"),
        ("03-good.txt", "04-wordy.txt", "wordier and unquantified is worse than long alone"),
        ("04-wordy.txt", "07-passive.txt", "passive voice and filler are worse than length"),
        ("07-passive.txt", "08-pronouns.txt", "pronouns plus miscased tools is worse still"),
        ("08-pronouns.txt", "09-unstructured.txt", "no sections or dates is worse than bad prose"),
        ("09-unstructured.txt", "10-terrible.txt", "almost nothing parseable is the floor"),
    ],
)
def test_pairwise_ordering(scores, better: str, worse: str, why: str):
    assert scores[better] > scores[worse], why


def test_the_best_resume_passes_everything(tmp_path: Path):
    body = "\n".join(
        line for line in (CORPUS / "01-excellent.txt").read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    )
    target = tmp_path / "best.txt"
    target.write_text(body, encoding="utf-8")
    report = checks.run(extract.load(target))
    assert report.failures == []
    assert report.percent == 100.0


def test_the_worst_resume_is_near_the_floor(scores):
    assert scores["10-terrible.txt"] < 15


# --------------------------------------------------------------------------- #
# Known calibration limits. These document where the scorer disagrees with human
# judgement. They are asserted so that if the behaviour ever changes, the
# documentation in docs/scoring.md is forced to change with it.
# --------------------------------------------------------------------------- #


def test_known_limit_a_passfail_check_cannot_express_degree(scores):
    """`02-strong` drops the numbers from 6 of 16 bullets and still ties `01`.

    `quantified` passes at >=60% and 02 sits at 62%, so a real regression in bullet
    quality costs nothing. Pass/fail checks have no way to say "worse but still
    acceptable"; a graded score would, at the cost of the traceability that makes
    every number here explainable.
    """
    assert scores["02-strong.txt"] == scores["01-excellent.txt"]


def test_known_limit_a_short_resume_scores_on_writing_not_substance(scores):
    """`05-thin` is 75 words and scores above four resumes with far more content.

    That is the tool working as designed, not a bug: it measures whether the document
    is well-formed, not whether it says enough. The two checks that do speak to
    substance - `text_layer` and `word_count` - are the only two it fails, and they
    name the problem exactly. A reader must not read a high percentage as "this
    resume is sufficient".
    """
    assert scores["05-thin.txt"] > scores["03-good.txt"]
    assert scores["05-thin.txt"] > scores["07-passive.txt"]


def test_the_thin_resume_at_least_names_its_own_problem(tmp_path: Path):
    body = "\n".join(
        line for line in (CORPUS / "05-thin.txt").read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    )
    target = tmp_path / "thin.txt"
    target.write_text(body, encoding="utf-8")
    failed = {c.key for c in checks.run(extract.load(target)).failures}
    assert failed == {"text_layer", "word_count"}
