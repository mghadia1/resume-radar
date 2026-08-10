# ResumeRadar — code review

Reviewed at v0.2.0. `pytest -q tests` → **85 passed, 6 skipped** (skips are FastAPI
tests; the dep is not installed in the review environment).

## What is genuinely good

- **The n8n workflow calls the service over HTTP** rather than pasting logic into a
  Pyodide Code node. That was the right architectural call and it is what makes the
  "REST API" claim on your resume honest.
- **Checks now carry categories** (`ats` / `impact` / `brevity` / `style`), so the
  output says what *kind* of problem a resume has. The category summary line
  (`ATS 100% IMPACT 100% BREVITY 30% STYLE 100%`) is more useful than a single number.
- **Keyword weighting is well-reasoned.** Hard skills at 3.0, title at 6.0, soft at
  1.0, with the Jobscan title study cited in the docstring and explicitly labelled a
  judgement call. Verified working: given a JD wanting Python/PostgreSQL/Docker and a
  resume with Python and Docker, it correctly returns `hard_missing: ['postgresql']`
  and puts it first in `priority`.
- **The regex edge cases are handled carefully.** `ROMAN_RE` and `COURSE_I_RE` stop
  "Calculus I–III" reading as a pronoun; `DATE_STYLES["MM/YYYY"]` requires a
  four-digit year so "60/20/20 splits" and "20/20 trials" are not read as dates.
  Those are exactly the false positives that would otherwise make the tool untrusted.
- **`PASSIVE_ALLOW` and `GERUND_CONTEXT_RE`** show the passive-voice check was
  actually tested against real prose rather than shipped naive.

## Bug 1 — competency detection matches person-verbs against thing-objects

**Severity: high.** This one gives actively wrong advice.

`competencies.py` matches verbs lexically, so any technical bullet reusing a
"people" verb for a non-human object scores a competency it does not evidence.
Reproduced against the real resume and synthetic probes:

| Bullet | Claimed | Reality |
|---|---|---|
| "…while global Otsu **managed** 1/20" | leadership | an algorithm succeeding |
| "**Wrote** a deterministic scene generator" | communication | writing code |
| "**Supported** 500 concurrent connections" | teamwork | a load test |
| "**Reviewed** the cache contents before each run" | teamwork | inspecting data |
| "**Owned** the retry logic" | leadership | code ownership, not people |

On your own resume this reports **leadership: covered** on the strength of the word
"managed" inside `global Otsu managed 1/20`. That is the single most misleading line
the tool currently produces.

The docstring already concedes detection is "lexical and therefore literal", which is
honest — but it frames the risk as *false negatives* ("a bullet that demonstrates
leadership without using the vocabulary will be missed"). The dominant error on a
technical resume is the opposite: **false positives**.

**Fix.** Require a human object within the same clause for the two people-competencies:

```python
HUMAN_OBJECT_RE = re.compile(
    r"\b(team|teams|engineers?|developers?|students?|interns?|peers?|"
    r"colleagues?|group|club|chapter|cohort|staff|volunteers?|"
    r"\d+\s+(?:people|members|engineers|students))\b", re.I)

PEOPLE_COMPETENCIES = {"leadership", "teamwork"}
```

For those two, a term hit only counts if `HUMAN_OBJECT_RE` also matches the bullet.
Leave `analytical` / `initiative` lexical — "built", "designed", "benchmarked" mean
the same thing whatever the object.

Then report three states rather than two: **evidenced**, **not evidenced**, and
**not detectable from bullets alone**. Leadership on a solo-project resume belongs in
the third bucket, not the second — reporting it as a failure implies a fix that does
not exist.

Add regression tests for all five rows in the table above.

## Bug 2 — empty documents earn points for absence

**Severity: medium.** Undermines the score's meaning.

A `.txt` file containing the single character `x` scores **32%**, identical to a file
of unrelated prose. It passes `no_pronouns`, `no_filler`, `no_passive`,
`clean_extraction`, `canonical_terms`, `consistent_dates` — every check phrased as
"does not contain something bad" is trivially satisfied by containing nothing.

**Fix.** Absence-based checks should return a third state, not a pass, when there is
nothing to inspect. Either:

- extend `Check` with `applicable: bool = True` and exclude inapplicable checks from
  `max_score`, so a near-empty file scores 0/12 rather than 32/115; or
- gate them behind the existing `text_layer` check — if that fails, mark the rest
  `not applicable`.

The first is better because it also handles the narrower case: a resume with no
bullets should not collect `no_passive` points for having no sentences.

Test: `x` in a `.txt` file must score under 10%.

## Bug 3 — `analyze()` breaks its own contract

**Severity: medium.** Trips up every caller.

The docstring says "return a JSON-ready dict", but the return value contains
`_report` (a `Report`) and `_keyword_match` (a `KeywordMatch`), so:

```python
json.dumps(rr.analyze(path))
# TypeError: Object of type Report is not JSON serializable
```

Both `cli.py` and `api.py` work around it by popping the two keys. Any third caller
will hit the error, and the FastAPI route only works because someone remembered.

**Fix.** Return a proper result object instead of a dict-with-secrets:

```python
@dataclass
class Analysis:
    resume: str
    report: Report
    keyword_match: KeywordMatch | None
    suggestions: list[Suggestion]
    def to_dict(self) -> dict:   # genuinely JSON-ready
        ...
```

`analyze()` returns `Analysis`; callers use `.report` for objects and `.to_dict()`
for JSON. Removes both `pop` calls and makes the FastAPI route a one-liner.

Test: `json.dumps(analyze(path).to_dict())` must not raise.

## Smaller items

- **FastAPI belongs in dev extras.** Six tests skip silently. Add
  `fastapi`/`httpx` to the dev extra so CI actually exercises the API surface —
  right now the service could break and the suite would stay green.
- **`STRONG_VERBS` mixes tenses** (`resolve`, `run`, `document`, `maintain` next to
  `resolved`, `ran`). Correct for present-tense current roles, but nothing checks
  that past roles use past tense. A `tense_consistency` check per entry would be a
  natural addition.
- **`WORD_COUNT_CEILING = 800`** flags your resume at 917 words. Reasonable, but the
  number needs the same treatment `WEIGHTS` got in `keywords.py`: state in
  `docs/scoring.md` that it is a judgement call, not a measured constant.

## Suggested order

1. Bug 1 — it produces wrong output today
2. Bug 2 — it makes the headline number untrustworthy
3. Bug 3 — cheap, prevents the next caller's bug
4. FastAPI into dev extras so the skips disappear

## For the interview

Once fixed, Bug 1 is the best answer you have to "where does your tool give a wrong
answer?" — you found that a lexical competency detector reports leadership on
"global Otsu managed 1/20", diagnosed it as verb-sense ambiguity, and fixed it by
requiring a human object. That is a real debugging story with a specific cause and a
specific fix, which is worth more than the feature list.
