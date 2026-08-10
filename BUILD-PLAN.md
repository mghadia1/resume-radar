# ResumeRadar — build plan

Hand this file to Claude Code. The core engine is already written and running; the
remaining work is tests, packaging, the n8n workflow, and docs.

## Why this project exists

Two reasons, and the second one matters more.

1. **It is useful.** It scores a resume against ATS rules and a job description,
   so you stop guessing whether a change helped.
2. **It fixes a gap in the portfolio.** The other seven repos are all ML or
   robotics simulation, all dated July 2026, all solo. This one is integration
   work: file parsing, a workflow engine, an external API, and a data log. That is
   the category recruiters look for and the portfolio currently lacks. Date it
   August 2026 so the portfolio no longer looks like it appeared in a single month.

## Current state

Working and verified against the real resume in `../../resume/`:

| File | Status | What it does |
|---|---|---|
| `src/resume_radar/extract.py` | done | PDF/DOCX/TXT to text; poppler with a pdfminer fallback; bullet detection that joins wrapped lines |
| `src/resume_radar/checks.py` | done | 16 deterministic checks, 97 points, each returning evidence |
| `src/resume_radar/keywords.py` | done | JD matching with multi-word phrases kept intact; reports missing terms |
| `src/resume_radar/discover.py` | done | Scans a folder, scores files by name and content, explains each pick |
| `src/resume_radar/suggest.py` | done | Optional LLM rewrite of flagged bullets; degrades cleanly with no API key |
| `src/resume_radar/tracker.py` | done | Appends each run to CSV for Sheets import |
| `src/resume_radar/cli.py` | done | `scan` and `score` subcommands |

Verified runs:

```bash
PYTHONPATH=src python3 -m resume_radar.cli score ../../resume/Mayank-Ghadia-Resume.pdf
# 85.6%  (83/97)  1 page, 16 bullets — correctly flags 15/16 bullets over 30 words

PYTHONPATH=src python3 -m resume_radar.cli scan ../../resume
# finds every resume with confidence 1.00 and prints why
```

## Remaining work

### 1. Packaging

Create `pyproject.toml` matching the other repos (src layout, `resume-radar` console
script pointing at `resume_radar.cli:main`). Runtime deps: `pdfminer.six`, `pypdf`.
Dev extra: `pytest`. Keep it dependency-light — poppler is optional, not required.

### 2. Tests — this is the gate

Nothing counts until `pytest -q tests` is green. Write `tests/test_resume_radar.py`
covering:

- **extract**: bullet joining across wrapped lines; `.txt` and `.docx` paths;
  `ExtractionError` on a missing file and on an unsupported extension
- **checks**: one test per check that can fail, using small synthetic documents —
  a resume with no email, one with 45-word bullets, one with `I` and `my`,
  one containing `Calculus I–III` that must **not** trip the pronoun check
  (that regression is already handled by `ROMAN_RE`; lock it in)
- **keywords**: `"machine learning"` matches as one phrase, not two tokens;
  missing terms are reported; empty JD returns 0 without dividing by zero
- **discover**: a resume-named file is found, a `node_modules` file is skipped,
  a non-resume PDF scores below threshold
- **tracker**: header written once, second append adds a row not a header
- **suggest**: `parse_rewrites` handles fenced ```json blocks and malformed JSON;
  `rewrite` raises `SuggestionUnavailable` with no key. **Do not call the real API
  in tests** — mock it.

Target 18–22 tests. Build fixtures with a tiny generated PDF so tests do not depend
on personal files.

### 3. n8n workflow

`n8n/resume-radar.json`, importable via Workflows → Import from File.

```
Form Trigger  (file: resume PDF, textarea: job description, text: company/role)
   → Extract from File   (PDF → text)
   → Code node (Python)  → calls the scoring logic, returns JSON
   → IF  score < 80
        → HTTP Request   (Anthropic API, rewrite prompt from suggest.PROMPT)
   → Merge
   → Google Sheets: Append Row
   → Respond to Webhook (JSON)
```

Two notes that will save time:

- n8n's Python Code node runs **Pyodide**, which cannot import your local package or
  use subprocess. Two options: (a) port `checks.py` scoring into the Code node as
  self-contained Python — it is pure regex and string work, so this is feasible; or
  (b) run this repo as a small FastAPI service and have n8n call it over HTTP.
  **Option (b) is the better resume story** — it makes this a real service with an
  API rather than a script pasted into a node. Recommend b, mention a as the
  fallback if you want zero extra hosting.
- Store the API key in n8n Credentials, never inline in the node.

### 4. Optional FastAPI wrapper

If you take option (b): `POST /score` accepting multipart file + `job_description`
form field, returning the same dict `analyze()` already produces. Roughly 40 lines.
This is the piece that makes the resume bullet say "REST API" honestly.

### 5. Docs

- `README.md`: what it does, install, the two CLI commands, sample output, and an
  explicit note that scores are heuristics modelled on public ATS guidance, not a
  reproduction of any vendor's algorithm.
- `docs/scoring.md`: every check, its weight, and the reasoning for the threshold.
  The 15–30 word bullet ceiling and the 6-second scan come from recruiter guidance;
  cite it so the numbers are defensible in an interview.

### 6. CI

`.github/workflows/ci.yml` mirroring the other repos: install, `pytest -q tests`.
Add a `Dockerfile` so the run is reproducible.

## Honesty rules for this repo

Same as the rest of the portfolio, from `CLAUDE.md`:

- The score is a heuristic built on public guidance. Do **not** describe it as an
  "ATS simulator" or imply it reproduces Workday, Greenhouse, or Taleo behaviour.
- Do not put this on the resume until `pytest` is green and you can explain, unaided,
  how one check works and one case where it gives a wrong answer. It already has a
  known weakness worth being able to name: bullet detection depends on layout
  extraction and will mis-split on multi-column resumes.
- No invented metrics in the README.

## Suggested resume bullet, once tests pass

> **ResumeRadar — Resume Scoring Service** | Python, FastAPI, n8n, Docker
> - Built a deterministic resume scorer with 16 weighted checks and job-description
>   keyword matching, exposed as a REST endpoint and wired into an n8n workflow.
> - Added folder scanning that locates and ranks resume files by filename and content
>   signals, and a CSV tracker logging every score per company.
> - Degraded cleanly without an API key: model-assisted rewrites are optional while
>   the deterministic score always runs; NN automated tests.

Replace NN with the real number once the suite is green.
