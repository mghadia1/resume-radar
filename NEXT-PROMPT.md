# Prompt for Claude Code

Copy everything below the line into Claude Code, run from
`~/Documents/auto job applier/projects/resume-radar`.

---

You are working in `resume-radar`, a Python package at v0.3.0. Read `README.md`,
`docs/scoring.md`, `CODE-REVIEW.md`, and `src/resume_radar/` before changing anything.

Current state: 127 tests pass, 6 skip (FastAPI not installed). Modules are `extract`,
`checks`, `competencies`, `keywords`, `discover`, `suggest`, `tracker`, `jobs`, `api`,
`cli`. The `jobs` module fetches Greenhouse and Lever boards, screens out postings
blocked by citizenship / clearance / no-sponsorship / ITAR, and ranks the rest by
keyword fit against my resume.

Work through Part A, then Part B. Run `pytest -q tests` after every change and do not
move on while it is red.

## Hard constraints — do not violate these

1. **No automated form submission and no credential handling.** Not for Workday, not
   for Greenhouse, not for LinkedIn Easy Apply. Their terms prohibit it and I am on an
   F-1 visa, where a wrong work-authorization answer is not recoverable. The tool
   prepares; I click submit.
2. **No scraping LinkedIn, Indeed, Glassdoor, or any site whose terms forbid it.**
   Greenhouse and Lever publish documented, no-auth board APIs. Those two only. If you
   want another source, it must have a public API and permissive terms — say so and
   ask before adding it.
3. **Never call it a probability.** There is no outcome data yet. It is a *fit* score.
   Every user-facing string, docstring, and README line must say fit, not probability
   or likelihood or chance.
4. **Invent no metrics.** If a threshold is a judgement call, say so in
   `docs/scoring.md` the way `keywords.WEIGHTS` already does.
5. **Tests are the gate.** A feature without tests is not done. Mock all network.

## Part A — make the analyser better

**A1. Multi-column resumes break bullet detection.** `extract.collect_bullets` assumes
one text column, so a two-column resume interleaves lines from both sides and produces
nonsense bullets. Detect the case and handle it: `pdftotext -bbox-layout` gives x
coordinates, so cluster lines by x-range and read columns in order. If a document looks
multi-column and you cannot split it confidently, set a flag on `Document` and have
`checks.run` mark the bullet-dependent checks `applicable=False` rather than scoring
garbage. Add a two-column fixture PDF and test both paths.

**A2. Tense consistency.** `STRONG_VERBS` deliberately contains both `resolve` and
`resolved` because present tense is correct for a current role. Nothing checks that
past roles use past tense. Add a `tense_consistency` check in the `style` category:
group bullets by the entry they sit under, infer current vs past from the entry's date
range (`Present` / `Current` means current), and flag entries that mix. Do not flag a
current role for using present tense.

**A3. Compare two resumes.** Add `resume-radar compare a.pdf b.pdf [--jd file]`
printing a per-check diff and which one wins each category. I keep several versions and
currently have no way to see what a change cost me.

**A4. Calibrate the thresholds.** `WORD_COUNT_CEILING = 800`, `BULLET_WORD_CEILING = 30`
and the `keywords.WEIGHTS` numbers are judgement calls. Build
`tests/fixtures/corpus/` with 8–12 synthetic resumes spanning obviously-good to
obviously-bad, assert the ranking order the scorer produces matches the intended order,
and document in `docs/scoring.md` that this is an ordering test, not proof the absolute
numbers are right.

**A5. Close the review leftovers.** Move `fastapi` and `httpx` into the dev extra so
the six skipped API tests actually run in CI.

## Part B — the daily job agent

The goal: every morning I get a short list of **new** postings I could plausibly win,
ranked by fit, with the specific skills I am missing for each. No duplicates from
yesterday. I decide what to apply to.

**B1. Remember what has been seen.** Add `src/resume_radar/watchlist.py` backed by
SQLite at a configurable path (default `~/.resume-radar/seen.db`). Key each posting by
a stable id — `sha256(source + company + url)`, since Lever ids and Greenhouse ids have
different shapes. Store first-seen timestamp, title, company, url, fit, blockers.
Expose `mark_seen(postings)`, `filter_new(postings) -> list[Posting]`, and
`prune(days=90)`.

**B2. `resume-radar watch`.** New subcommand:

```
resume-radar watch --resume RESUME --boards boards.txt \
                   [--min-fit 25] [--db PATH] [--out digest.md] [--json]
```

Fetch every board, screen, rank, drop anything already in the database, write a
markdown digest of what is new, then mark everything seen. Exit 0 with "no new
postings" when there is nothing — a daily job that fails on an empty day is useless.
One unreachable board must warn and continue, never abort the run.

**B3. The digest.** Markdown, sorted by fit:

```markdown
# New postings — 2026-08-04
3 new, 12 already seen, 4 filtered as ineligible

## 62% · Company — Machine Learning Intern  [title match]
Tempe, AZ · https://...
Missing: kubernetes, postgresql
Have: python, pytorch, docker, opencv

<!-- then a collapsed section listing what was filtered and why -->
```

Always show the filtered-out list collapsed, with the blocker reason. If the screener
is wrong I need to be able to see it.

**B4. Scheduling.** Do not build a daemon. Ship a `--once` design plus documented
setup for cron, launchd, and a GitHub Actions workflow on a `schedule:` trigger. Put
all three in `docs/daily.md`. The Actions one must not commit the database into the
repo — use the cache action or an artifact.

**B5. Outcome tracking.** Add an `outcome` column to `tracker` with values
`applied / no_reply / rejected / screen / interview / offer`, plus
`resume-radar outcome <row-id> <value>` to update a past row. This is the groundwork:
after 50–80 labelled applications I can check which signals actually predicted
callbacks, and *that* is when fit can become probability. Add
`resume-radar stats` reporting response rate by fit decile and by title-match — it
will be meaningless with 5 rows, so have it refuse to report under 20 and say why.

**B6. Answer bank — the safe half of "auto-fill".** Add `answers.yml` support: a file
holding my standard responses (work authorization, sponsorship needed, graduation
date, GPA, portfolio links, availability). `resume-radar apply <url-or-id>` prints the
posting's requirements, my matching answers ready to copy, and a tailored 3-bullet
"why this role" draft built only from facts already in my resume. It must not open a
browser, fill a form, or submit anything. Print, and stop.

## Definition of done

- `pytest -q tests` green with zero skips, and new tests for every item above
- `resume-radar watch` run twice in a row: the second run reports zero new postings
- `docs/daily.md` and `docs/scoring.md` updated
- README shows the new commands
- No user-facing string contains "probability", "likelihood", or "chance of getting"
- CI green

Commit in logical chunks with real messages. Do not push.
