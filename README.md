# ResumeRadar

Scores a resume against 26 deterministic checks in four categories, reports which
competencies its bullets evidence, and weights job-description terms so a missing hard
skill costs more than a missing buzzword. Then it finds open postings you could
plausibly win, screens out the ones you cannot, and ranks the rest against your resume.

Runs as a CLI, as a FastAPI web service with a browser upload page, and as a scheduled
daily digest.

The point is to stop guessing whether an edit helped. Every check returns the evidence
behind its verdict, so a low score always points at a specific line.

```
Mayank-Ghadia-Resume.pdf
  86.4%  (121/140 points)   1 page(s), 16 bullets

  ATS 100%   IMPACT 100%   BREVITY 30%   STYLE 100%

  [PASS] Email parses: mghadia1@asu.edu
  [PASS] Consistent date format (Month YYYY)
  [PASS] One page
  [FAIL] 917 words - dense, over 800
  [PASS] No passive voice
  [PASS] Tool names capitalised correctly
  [PASS] 75% of bullets contain a number
  [FAIL] Mean bullet 39.7 words (ceiling 30)
  [FAIL] 15/16 bullets over 30 words
           - Trained a 117,681-parameter U-Net with combined BCE and Dice loss on 590 real Kv

  Competencies evidenced: analytical, initiative
  Not evidenced: communication, leadership, teamwork

  Job-description match: 43.1%
  Job title "Software Engineer Intern - Summer 2027": found on resume
  Missing hard skills (most emphasised first): kubernetes, c++, continuous integration,
  data pipeline, postgresql, rest api
  Missing soft skills: collaboration, communication
```

Three things that single output does which a flat percentage does not: it separates a
parsing problem from a writing problem, it names the competency you never evidenced, and
it puts the twice-mentioned `kubernetes` above the once-mentioned `rest api` because the
posting told you which it cares about.

## What the score is, and is not

The 140 points are a **heuristic built on public resume and recruiting guidance** — one
page for students, quantified bullets, no first-person pronouns, parseable contact
details. It is not a reproduction of any applicant tracking system. Workday, Greenhouse,
and Taleo do not publish their parsers, and nothing here reverse-engineers them. A high
score means the document is clean and specific, not that a particular vendor will rank
it highly. `docs/scoring.md` gives the weight and the reasoning for every check.

## How it compares

The commercial checkers were the design input, and they are each better than this at
something. Jobscan has a far larger skill taxonomy and real ATS-vendor research behind
it. Resume Worded gives model-written rewrites for every bullet. VMock grades visual
presentation from the rendered page, which this does not attempt at all.

What this does that their free tiers generally do not:

- **Shows its work.** Every check returns the evidence that produced the verdict, and
  every threshold has a documented reason. There is no number you cannot trace.
- **Runs offline and deterministically.** No account, no upload to a third party, no
  daily scan limit. The same file always produces the same score, which is what makes
  before/after comparison meaningful.
- **Weights the job description.** Missing hard skills are ranked by how often the
  posting repeats them, and the job title is checked separately, rather than every term
  counting the same.
- **Reports competency coverage** as a first-class output rather than a paid add-on.
- **Is a library, a CLI, and an HTTP API**, so it scripts against a folder of resumes.
- **Finds the jobs.** None of them do — see below.

It also checks document structure the way the paid scanners do: images, table grids,
header/footer content, fonts, and body size. Those are PDF-only, and report *not
applicable* on `.txt` and `.docx` rather than passing for free.

What it does not do: judge visual design (margins, alignment, whitespace balance),
understand meaning, or model any specific vendor's parser.

## Install

```bash
python -m pip install -e '.[dev]'
```

Runtime dependencies are `pdfminer.six` and `pypdf` only. If poppler's `pdftotext` is on
the path it is used instead, because it preserves reading order better — but it is
optional, and the test suite never assumes it.

## Use

Score one resume:

```bash
resume-radar score resume.pdf --jd job-description.txt
```

Find resumes in a folder, with the reasoning for each pick:

```bash
resume-radar scan ~/Documents
```

Log a run for later comparison:

```bash
resume-radar score resume.pdf --jd jd.txt --company Acme --role "SWE Intern" --log runs.csv
```

`--json` on either command prints machine-readable output. `--suggest` adds model-written
bullet rewrites; it needs `ANTHROPIC_API_KEY`, and without one the deterministic score
still runs and the suggestion step reports itself as unavailable.

## Web service

```bash
python -m pip install -e '.[api]'
uvicorn resume_radar.api:app --port 8000
```

Then open <http://localhost:8000>. The root route serves an upload page — pick a resume,
paste a job description, get the score and the failing checks back in the browser. No
build step and no CDN: it is one self-contained HTML file served by the app.

| Route | Method | Returns |
|---|---|---|
| `/` | GET | The upload page |
| `/score` | POST | Score for one multipart-uploaded resume, plus optional `job_description` |
| `/health` | GET | Service status and version |
| `/docs` | GET | Interactive OpenAPI docs, generated by FastAPI |

`/score` returns the same dict the CLI produces, so the CLI and the service can never
disagree about a score:

```bash
curl -F file=@resume.pdf -F job_description="$(cat jd.txt)" http://localhost:8000/score
```

Uploads are capped at 10 MB and written to a temp file that is deleted after scoring.
Unsupported extensions return 415, empty uploads 400, and unreadable files 422.

Running it in Docker:

```bash
docker build -t resume-radar . && docker run --rm -p 8000:8000 resume-radar
```

## n8n workflow (unverified)

`n8n/resume-radar.json` is a form → score → conditional rewrite → Google Sheets workflow
that calls `/score` over HTTP rather than reimplementing the checks in a Code node. **It
has never been imported into a running n8n instance**, so treat it as a sketch rather
than a working integration: node `typeVersion`s and the Google Sheets parameter shape are
the likely breakages. n8n Cloud additionally cannot reach a service on your own machine
without a tunnel, and blocks `$env` in expressions.

## Finding jobs

`jobs` and `watch` read **Greenhouse** and **Lever** board APIs — both publish
documented, no-auth, per-company endpoints. LinkedIn, Indeed, and Glassdoor are not
supported and will not be: their terms prohibit scraping.

```bash
resume-radar jobs  --resume resume.pdf --boards boards.txt
resume-radar watch --resume resume.pdf --boards boards.txt --out digest.md
```

`jobs` ranks every open posting. `watch` builds a daily shortlist: new postings first,
remembering everything it has shown in a SQLite watchlist, and writes a markdown digest.
See [docs/daily.md](docs/daily.md) for cron, launchd, and GitHub Actions setup.

If fewer than `--target` postings are new, the best still-open older ones are **carried
over** into a separate, clearly labelled section, so a slow day still gives you
something to work on without a repeat ever being mistaken for a fresh opening.

`--levels` controls which role levels are kept — the default is `intern,new_grad`, read
from the title. Postings dropped for level are **listed** in the digest, not just
counted, because that filter is a heuristic: a role titled plainly "Software Engineer"
carries no level word and is dropped by default. Widen it with
`--levels intern,new_grad,mid` if the dropped list looks wrong.

Postings are screened before ranking: anything requiring US citizenship, a security
clearance, ITAR eligibility, or stating that it offers no visa sponsorship is filtered
out and listed separately with the reason. On an F-1 visa those are hard blockers, not
preferences, and filtering them is worth more than the ranking.

**Fit is keyword overlap, weighted.** It is not a probability of an interview and does
not predict one — that claim needs outcome data this tool does not have. Record real
outcomes and it can eventually be checked:

```bash
resume-radar outcome 7 interview --log runs.csv
resume-radar stats --log runs.csv
```

`stats` refuses to report a response rate until 20 applications have a recorded
outcome, because below that every rate is noise.

## Preparing an application

```bash
resume-radar answers --init          # then edit ~/.resume-radar/answers.yml
resume-radar apply <posting-url> --resume resume.pdf --boards boards.txt
```

Prints the posting's questions, your stored answers beside them, the questions it has
no answer for, and the three bullets **already on your resume** that best match the
posting. Then it stops.

**It does not fill or submit anything, and it will not.** Automated submission is
prohibited by every major board's terms, and work-authorisation questions have to be
answered correctly every single time. The tool prepares; you click submit.

## Comparing drafts

```bash
resume-radar compare old.pdf new.pdf --jd jd.txt
```

Shows which checks each version wins, so you can see what a rewrite *cost* as well as
what it fixed — tightening bullets to pass `bullet_length` often drops `quantified`,
and a single percentage hides that.

## Tests

```bash
python -m pytest -q tests
```

91 tests. Fixtures are generated — the PDF is assembled byte by byte in the test module
and the DOCX is a zip built in memory — so the suite depends on no personal files, no
network, and no poppler install. The model call is mocked; no test hits the API.

Two of those tests exist because running the scorer against a real resume produced wrong
answers: ML split ratios (`60/20/20`) were being read as dates, and the correct passive
gerund in "rather than being tuned against it" was flagged as a defect. Both are fixed
and locked in.

## Bugs worth knowing about, and how they were found

Each of these passed a test suite written from the same assumption as the code, and was
found by pointing the tool at something real. That is the honest answer to "how do you
know it works": not the test count, but the cases where the tests agreed with the bug.

| Bug | Found by | Cause |
|---|---|---|
| Leadership claimed from `global Otsu managed 1/20` | scoring the real resume | A lexical detector matched a people-verb against a thing-object |
| `60/20/20 splits` read as a date | scoring the real resume | `MM/YY` pattern with no four-digit-year anchor |
| Entry date range never matched | scoring the real resume | The LaTeX template writes `−` (U+2212), not a hyphen |
| Hybrid two-column resumes scored as one column | building a realistic fixture | Detection required a band that *zero* lines crossed, so one full-width header line defeated it |
| Right-aligned dates read as a third column | building a realistic fixture | The date column split the empty region into two bands, so a two-column resume was declared unresolved and lost every bullet |
| One flaky board killed the whole scheduled run | running against 29 live boards | `http.client.RemoteDisconnected` is not a `URLError`, so it escaped the handler |
| Bare "associate" read as entry level | running against 29 live boards | It is a job family, not a level — it matched 20-odd Stripe "Operations Associate" roles and almost nothing else |
| A near-empty file scored 32% | reviewing the score's meaning | Every "contains nothing bad" check passes trivially on nothing |

## Known weakness

Bullet detection reads the layout text produced by PDF extraction. On a **multi-column
resume** the extractor interleaves columns, so bullets get split at the wrong points and
the length and verb checks are measured against fragments. Single-column resumes — which
is what the one-page student format almost always is — extract correctly. The pronoun
check carries a smaller version of the same problem: a lone `I` is read as a course
number ("Physics I") unless a lowercase word follows it, so "Calculus I and Statistics"
is correctly ignored while "I built" is correctly flagged.
