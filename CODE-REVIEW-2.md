# ResumeRadar — review 2

Reviewed after the Part A / Part B work. `pytest -q tests` → **256 passed, 0 skipped**
once `pip install -e ".[dev]"` pulls in FastAPI.

## Everything in the prompt shipped

| Item | Status |
|---|---|
| A1 multi-column extraction | implemented — but see the bug below |
| A2 tense consistency | works, and correctly returns `ambiguous` for verbs like *set* / *cut* that are spelled the same in both tenses rather than guessing |
| A3 `compare` | works |
| A4 calibration corpus | 8 fixtures, ordering asserted |
| A5 FastAPI in dev extras | fixed, the 6 skips are gone |
| B1–B6 watchlist, watch, digest, scheduling docs, outcomes, answer bank | all present |
| No "probability" in user-facing strings | clean — the only hit is the disclaimer *"not a probability of an interview"*, which is the correct usage |

Three commits, sensible messages, nothing pushed. `tracker.stats` refusing to report
under 20 labelled rows is implemented exactly as specified.

## Bug — multi-column detection fails on the layout it was built for

**Severity: high.** Same shape as the competency bug: correct on the clean case the
test constructs, wrong on the realistic one, and silent about it.

`find_gutters` requires a vertical band that **zero** lines cross. Real two-column
resumes are hybrid — a full-width name and contact header, a two-column body, often a
full-width section underneath. A single full-width line spans every candidate x and
kills detection for the whole page.

Reproduced with a two-column resume built in LaTeX:

```
columns detected : 1        <- wrong, it is 2
columns resolved : True     <- so the bullet checks stayed active
bullets found    : 1        <- of 3
score            : 77%      <- scored confidently on a document it misread
```

The two bullets inside the two-column block were dropped because `-layout` puts them
on the same physical lines as the left column:

```
   SKILLS                              • Built a REST API in FastAPI serving 2,000
Python, Docker, PostgreSQL, React,     second with Redis caching.
```

The bullet regex anchors at `^`, so a bullet preceded by left-column text never
matches. The document then scores as if those bullets do not exist.

The existing test passes because `write_columns_pdf` builds a **purely** two-column
page with no full-width header. Almost no real resume looks like that.

### Fix

Allow a minority of lines to cross a gutter, and evaluate the split on the rest:

```python
def find_gutters(lines, page_width, *, min_share=0.2, samples=51, max_cross=0.25):
    ...
    crossing = sum(1 for ln in lines if ln.x0 < x < ln.x1)
    if crossing > len(lines) * max_cross:
        continue
    rest = [ln for ln in lines if not (ln.x0 < x < ln.x1)]
    left = sum(1 for ln in rest if ln.x1 <= x)
    right = len(rest) - left
    if left >= len(rest) * min_share and right >= len(rest) * min_share:
        hits.append(x)
```

Verified against both documents:

| Document | shipped | with tolerance |
|---|---|---|
| Hybrid two-column resume | `[]` (miss) | `[214, 306]` — 306 is the real gutter |
| Mayank's real one-column resume | `[]` | `[]` — no false positive |

Two follow-ups this raises:

1. **Pick one gutter, not two.** The tolerant version reports a spurious band at 214.
   Keep the candidate with the most balanced split, or the widest run of consecutive
   sampled hits.
2. **When a gutter is found, re-read by column before collecting bullets.**
   `order_columns` already does this; it just never gets called because detection
   returns nothing.

### Tests to add

- A hybrid fixture: full-width header, two-column body, full-width footer section.
  Assert `columns == 2` and that all three bullets are found.
- Assert the real single-column resume still reports `columns == 1` — the regression
  that matters most, since a false positive would scramble a correct document.
- Assert that when detection is uncertain, `columns_resolved is False` and the bullet
  checks go `applicable=False`. That path exists and works for three columns; it
  simply is not reached in the two-column hybrid case.

## Smaller

- `Posting` is unhashable, so `set(postings)` raises. Not currently a bug — no caller
  does it — but `test_slate.py` hit it and had to compare url sets instead. Add
  `eq=False` to the dataclass or a `__hash__` on the posting id.
- `boards.example.txt` still has three commented-out companies. Ten internships a day
  needs 30–50 boards; the tool is finished, the input list is not.

## The pattern worth noticing

Both bugs found so far are the same failure: a feature that passes a test built from
the same mental model as the code. The competency detector matched people-verbs
against thing-objects; the column detector assumed a page is either wholly one column
or wholly two. In both cases the fix was easy and the *finding* was the hard part.

That is the honest answer to "how do you know your code works?" — not "it has 256
tests", but "here are two cases where the tests agreed with the bug, and here is how I
caught them."

---

# Progress check — after commit `02a5520`

302 tests pass. The multi-column bug is **half fixed**, and the half that got fixed is
the one that mattered.

## The dangerous behaviour is gone

| | before | now |
|---|---|---|
| columns reported | 1 (wrong) | 3 (still wrong) |
| `columns_resolved` | **True** | **False** |
| bullets found | 1 of 3 | 0 of 3 |
| bullet checks | scored anyway | marked `not applicable` |
| score | 77%, confidently wrong | 66%, on the checks it can actually judge |

It no longer scores a document it cannot read. Refusing to answer beats answering
wrongly, so this is the right failure mode and the severity drops from high to medium.

Also landed and verified: `Posting.__hash__` by identity, `fetch` catching
`http.client` errors after being tested against 29 live boards, entry-level titles
(`Junior`, `Associate Software Engineer`, `Software Engineer I`, `University Graduate`)
no longer silently dropped, and a real `boards.example.txt` with 29 confirmed slugs.
The `Operations Associate` tests are a nice catch — a bare "associate" is a job family
at Stripe, not a seniority level.

## What is still broken

`find_gutters` now finds a gutter on a hybrid page, but returns **two** on mine and so
reports three columns, gives up, and recovers zero bullets. The review's follow-up
"pick one gutter, not two" was not applied.

The shipped hybrid fixture passes because its left column lines are long and uniform.
A real left column is a skills list with short ragged lines, which leaves interior
bands that no line happens to cross:

```
  x=214  left= 4 right= 5   <- interior band inside the left column
  x=306  left= 5 right= 4   <- the true gutter
```

Line counts do not separate them; both look balanced. **Adjacency does:**

```
  nearest content to the right of x=214 starts at x0=274   (60pt away)
  nearest content to the right of x=306 starts at x0=311   ( 5pt away)
```

A real gutter has content resuming immediately on its far side. An interior band has
a wide dead zone because it is not next to a column edge at all.

### Fix

After collecting candidates, keep only those where the gap between the band and the
next content is narrow, then take the single best:

```python
def _gutter_score(lines, x):
    right = [ln.x0 for ln in lines if ln.x0 >= x]
    left  = [ln.x1 for ln in lines if ln.x1 <= x]
    if not right or not left:
        return None
    return (min(right) - x) + (x - max(left))   # dead space around the band

# keep candidates whose dead space is under ~25pt, then pick the minimum
```

On this document that keeps 306 and rejects 214, giving `columns == 2`, which lets
`order_columns` run and recovers all three bullets.

### Test to add

Build the hybrid fixture with a **short ragged left column** — a skills list, not
uniform prose. That is the shape that produces spurious interior bands, and it is what
a real two-column resume looks like.

## Where this leaves the project

Nothing here blocks daily use: your own resume is single-column and parses correctly
(1 column, 16 bullets). This only affects scoring *other people's* two-column resumes.
Fix it when convenient; it is no longer a correctness risk, only a coverage gap.
