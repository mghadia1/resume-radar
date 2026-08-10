# Scoring

20 checks, 115 points, grouped into four categories. Every check is pure string and
regex work on extracted text: no network, no model, no randomness. The same file always
produces the same number.

The categories exist so the output says *what kind* of problem a resume has:

| Category | Points | Question it answers |
|---|---|---|
| `ats` | 46 | Can a machine find the facts? |
| `impact` | 18 | Do the bullets show outcomes or duties? |
| `brevity` | 27 | Will it survive a skim? |
| `style` | 24 | Is the word choice and casing consistent? |

A resume scoring 100% on `ats` and 40% on `impact` has a completely different problem
from the reverse, and a single number hides that. The grouping follows the same logic
Resume Worded uses (Impact / Brevity / Style plus ATS parsing); the checks and weights
inside it are this project's own.

## Where the thresholds come from

These are conventions from published resume and recruiting guidance, not measurements
of any employer's software. Two of them do most of the work and are worth being able to
defend out loud:

- **The 15–30 word bullet ceiling.** The widely cited figure for how long a recruiter
  spends on a first pass over a resume is a handful of seconds — the well-known
  eye-tracking study from TheLadders reported roughly six. Whatever the exact number,
  the implication is the same: a bullet that runs past about 30 words does not get read
  in that pass, it gets skipped. The check scores the *mean* bullet length separately
  from the *share* of bullets over the ceiling, because one long bullet is a different
  problem from a resume where every bullet is long.
- **One page for students.** Standard university career-center guidance for candidates
  without full-time industry experience. It is weighted at 8 points rather than made
  fatal, because a second page is a judgment call, not a parsing failure.

Everything else is either mechanical (does the email parse) or a style convention that
recruiters state explicitly (no first-person pronouns, lead with action verbs, quantify
outcomes).

## The checks

### Contact and links — 16 points

| Check | Points | Passes when | Why |
|---|---|---|---|
| `email` | 5 | A regex-parseable address appears | If the address does not parse as text, an automated screen has no way to reach you. Reported with the address it found so you can confirm it is the right one. |
| `phone` | 5 | A 10-digit US-format number parses | Same reason. Formatting variants (dots, dashes, parens) all pass. |
| `linkedin` | 3 | `linkedin.com` appears | Expected by default; cheap to add. Low weight because its absence is not a parsing failure. |
| `github` | 3 | `github.com` appears | For a software candidate the link is the evidence. Same low weight. |

### Structure — 28 points

| Check | Points | Passes when | Why |
|---|---|---|---|
| `sections` | 8 | Education, experience, skills, and projects each appear as a standalone header line | Section headers are what a parser splits on. The check requires the header to be alone on its line, because that is what makes it a header rather than a passing mention. Names the missing ones. |
| `dates` | 6 | At least 4 parseable `Mon YYYY` dates | Timelines are extracted from dates. Four is one date each for two roles — below that a reader cannot reconstruct the sequence. |
| `date_format` | 4 | At most one date format in the document | `May 2024` next to `05/2024` is two different fields to a parser and carelessness to a human. Names every format it found. See the ratio caveat below. |
| `text_layer` | 6 | More than 200 extractable words | Catches a resume exported as an image. Under 200 words, the file is either scanned or nearly empty; either way an automated reader sees nothing. |

### Brevity — 27 points

| Check | Points | Passes when | Why |
|---|---|---|---|
| `one_page` | 8 | Exactly one page | See above. |
| `word_count` | 5 | Between 400 and 800 words | A one-page resume under 400 words is not using the page; over 800 it is a wall of text that only looks like it fits. This check is what caught 917 words on a resume that legitimately passed `one_page`. **The 400–800 band is a judgement call, not a measured constant** — the same caveat that applies to the keyword weights. |

### Document structure — 21 points

These are the checks the commercial scanners actually sell: whether a parser can
physically read the file, as distinct from whether the writing is good. They need PDF
geometry, so on `.txt` and `.docx` they report **not applicable** rather than passing.

Each one was built against a real LaTeX resume first, because every plausible signal
here has an obvious false positive waiting behind it.

| Check | Points | Passes when | Why, and what nearly broke it |
|---|---|---|---|
| `no_images` | 5 | No embedded images | Text inside an image is invisible to every parser. |
| `no_tables` | 5 | No table *grid* | Tables scramble reading order. **Horizontal rules are not tables** — a normal resume underlines its section headings, and the real one has four such rules and no table. A grid needs ruling in *both* directions, or a run of four or more rectangles. |
| `no_header_footer_content` | 4 | Nothing stranded in the top/bottom 40pt band | Some parsers discard those regions entirely. **A single low line is not a running footer** — on a one-page resume that is just the last bullet. Only two things count: the same band carrying text on *every* page of a multi-page document, or contact details sitting in the band, which are what you would actually lose. |
| `standard_fonts` | 4 | No decorative or unembedded fonts | Exotic fonts extract as mojibake. **A font-name whitelist would be wrong**: the real resume uses `NimbusRomNo9L` and `CMSY10`, which are what LaTeX emits and are completely fine. The check instead flags decorative families by name, and fonts that are neither subset-embedded (`ABCDEF+Name`) nor one of the base-14 faces every reader has built in. |
| `font_size_sane` | 3 | Body text 9–12pt | Under 9pt is a shrink-to-fit tell. Measured as the *most common* glyph size, so a large name heading or small footnote does not decide it. |

Deliberately **not** implemented: `no_text_boxes`. pdfminer reports 43 text boxes on a
perfectly ordinary one-column resume, so "an isolated text box far from the main flow"
has no threshold that separates it from normal typesetting. Shipping it would mean
shipping a check that is wrong more often than right.

### Extraction quality — 6 points

| Check | Points | Passes when | Why |
|---|---|---|---|
| `clean_extraction` | 6 | No single capital letter is stranded before a word | The signature of a kerning or font-embedding problem, where `Python` extracts as `P ython`. The document looks fine to a human and is broken to a parser, which is exactly the failure mode worth catching. Lists the affected words. |

### Style — 23 points (plus `verb_variety`, below)

| Check | Points | Passes when | Why |
|---|---|---|---|
| `no_pronouns` | 5 | No first-person pronouns | Resume convention is implied-subject phrasing: "Built the parser", not "I built the parser". Roman-numeral course names are excluded first — see the ambiguity note below. |
| `no_filler` | 5 | None of the listed weak phrases appear | "Responsible for", "worked on", "team player" and similar describe presence rather than contribution. Names every phrase it found. |
| `active_voice` | 5 | No passive constructions | "was rebuilt by the platform group" spends your line giving someone else the credit. Irregular participles are listed explicitly rather than pattern-matched, because a generic `-en` rule flags adverbs like "often". |
| `tech_casing` | 4 | Tool names use their canonical capitalisation | `javascript`, `Pytorch`, and `postgresql` are the details a technical reviewer notices immediately. Only terms whose lowercase form is not an ordinary English word are checked, so `excel`, `go`, `rust`, and `spark` are deliberately absent — flagging those would be noise. URLs are stripped first, because `github.com` is correct lowercase. |
| `tense_consistency` | 4 | No dated entry mixes tenses, and no finished role is written in the present tense | Judged **per entry**, because there is no single correct tense for a resume: present is right for a role you still hold and wrong for one you left. Entries are found by their date range, and `Present` / `Current` / `Ongoing` marks the current one. Verbs spelled the same in both tenses (`fit`, `put`, `cut`, `read`) are skipped rather than guessed at. A current role in the present tense is never flagged. |

### Bullets — 37 points

All five are scored against the bullets the extractor detected. If none are detected they
all score zero, which is deliberate: a resume with no parseable bullets has a real
problem, and silently skipping the checks would hide it.

| Check | Points | Passes when | Why |
|---|---|---|---|
| `quantified` | 10 | ≥60% of bullets contain a digit | The single highest-leverage edit, hence the largest weight. 60% rather than 100% because some genuinely have nothing to count, and demanding a number everywhere invites inventing them. Lists the bullets with no number. |
| `bullet_length` | 8 | Mean bullet ≤30 words | See the ceiling note above. |
| `long_bullets` | 6 | ≤25% of bullets exceed 30 words | Separate from the mean so that a resume with one long bullet and a resume where everything is long score differently. |
| `action_verbs` | 8 | ≥80% of bullets open with a verb from the strong-verb list | The opening word is the one that survives a skim. 80%, not 100%, because a bullet occasionally has a good reason to open otherwise. Lists the openers it did not recognise. |
| `verb_variety` | 5 | Distinct openers ≥60% of bullet count | Six bullets that all start with "Developed" read as one bullet. Lowest weight of the five — it is a polish issue, not a defect. Names the repeated verbs and how often each recurs. |

## Job-description matching

A flat "you matched 62% of the words" is close to useless, because the words are not
equally important. Terms are classified and weighted instead:

| Kind | Weight | Rationale |
|---|---|---|
| Job title | 6 | The best-evidenced item here. Jobscan's study of over 2.5 million applications reports that candidates whose resume title matched the target title had an interview rate **10.6× higher** than those who did not. The weight of 6 is still a judgement call — it is not derived from that multiplier. |
| Hard skill | 3 | Named tools and techniques are what an automated keyword filter is configured with. Detected from a curated vocabulary, plus anything containing a digit, `+`, or `#` (so `c++` and `python3` classify correctly without being listed). |
| Soft skill | 1 | Real, but no keyword filter screens on "collaboration". |
| Everything else | 1 | Ordinary posting vocabulary. |

**Be careful with the hard-skill weighting.** It models what an automated filter matches
on, *not* what a human recruiter values — and the evidence says those differ. In
Jobscan's February–March 2025 survey of 384 recruiters, 49.6% called hard and soft skills
equally important, 27.0% prioritised **soft** skills, and only 14.3% prioritised hard
skills. So a low weighted match rate means "a keyword filter may not surface this",
never "a recruiter will not value this". Treat the missing-soft-skill list as advice
about your bullets, not as filler.

Two refinements on top of that:

- **Emphasis.** A posting that names PostgreSQL three times is telling you something a
  posting that names it once is not. Missing hard skills are ordered by how often the
  posting repeats them, so the top of the list is the highest-value edit.
- **Title matching ignores noise.** "Software Engineer Intern — Summer 2027" and
  "Software Engineer" are the same claim to a recruiter scanning for the title, so
  seniority words, terms, years, and work-arrangement words are stripped before
  comparing.

These weights are a judgement call informed by published guidance. They are not a
measured constant, and no vendor's real weighting is public.

## Competency coverage

Separate from the score, and deliberately not worth points: which of five transferable
competencies your bullets give evidence for — **analytical, communication, leadership,
teamwork, initiative**. These are the signals VMock reports and the ones university
career centres screen for, and a resume can be technically strong while evidencing only
one of them.

The five names are VMock's, as published by the university career centres that license
it — its three modules are Impact, Presentation, and Competencies, and the Competencies
module scores exactly these five.

Detection is lexical, so it finds the *vocabulary* of a competency rather than the
competency itself. A bullet that demonstrates leadership without using any leadership
word is missed — which is a real limitation, and also roughly what a six-second human
scan would miss.

### Verb sense: the bug worth knowing about

The first version of this module was purely lexical, and on a technical resume that
produces confident nonsense. The same verb means different things depending on its
object:

| Bullet | Was claimed | Actually |
|---|---|---|
| `while global Otsu managed 1/20` | leadership | an algorithm succeeding |
| `Wrote a deterministic scene generator` | communication | writing code |
| `Supported 500 concurrent connections` | teamwork | a load test |
| `Owned the retry logic` | leadership | code ownership, not people |

On a real resume this reported **leadership: evidenced** on the strength of the word
`managed` inside `global Otsu managed 1/20`. The docstring had honestly warned about
false *negatives*; the dominant error was the opposite.

The fix splits every lexicon in two. **Unambiguous** terms imply a person by themselves
(`mentored`, `collaborated`, `presented`). **Ambiguous** terms (`led`, `managed`,
`owned`, `supported`, `wrote`, `reported`) only count when the bullet also contains a
qualifying object — a human noun for leadership and teamwork, an audience or written
artifact for communication. `analytical` and `initiative` stay purely lexical, because
`built`, `designed`, and `benchmarked` mean the same thing whatever the object.

One further exclusion: `trained` is not treated as communication, because on an ML
resume "trained a U-Net" means fitting a model, not teaching a person.

### Three states, not two

Leadership on a solo-project resume is not a failure — it is unobservable. Reporting it
as missing implies a fix that does not exist, and the honest fix ("claim you led people")
is the dishonest one. So each competency reports **evidenced**, **not evidenced**, or
**not detectable**: if no bullet mentions a person at all, leadership and teamwork are
marked not detectable and excluded from the percentage entirely.

## Checks that cannot apply

A file containing the single character `x` once scored **32%**. It passed
`no_pronouns`, `no_filler`, `active_voice`, `tech_casing`, `clean_extraction`, and
`date_format` — every check phrased as "does not contain something bad" is trivially
satisfied by containing nothing.

Those checks now report a third state. Below `MIN_WORDS_FOR_LANGUAGE` (50 words), or
when there are no dates to compare, a check is marked **not applicable** and scores
0 out of 0 — so it is excluded from the denominator rather than passed for free. The
same file now scores 9.3% (8/86), and `max_score` varies by document, which is the
honest consequence: you cannot compare raw point totals across resumes, only percentages.

## Multi-column layouts

Bullet detection reads laid-out text, so column handling decides whether the bullet
checks see anything real. Columns are found from pdfminer geometry (not
`pdftotext -bbox-layout`, since poppler is optional and the detection has to work on a
bare install) by sampling the middle half of the page for a vertical band the columns
do not cross.

The first version required a band that **zero** lines crossed. Real two-column resumes
are hybrid — full-width name and contact header, two-column body, often a full-width
section underneath — so a single header line defeated detection and the document was
scored as the single column it was not. A minority of lines may now cross a candidate
band (`max_cross`), the split is judged on the rest, and a band must be several points
wide (`min_width`) before it counts, so an accident of where words happen to end is not
read as a column boundary.

Why it matters more than it looks: with poppler installed — as it is in the Dockerfile —
`pdftotext -layout` emits a hybrid page one *physical* line at a time, so a right-column
bullet is preceded by left-column text and the `^`-anchored bullet pattern matches
nothing at all. The document then scores as though it had no bullets. Detecting the
columns and re-reading them one at a time is what prevents that, and it is why
`order_columns` also keeps the full-width lines rather than discarding anything that
belongs to neither column.

Counting empty bands alone still finds too many. A resume that **right-aligns its
dates** puts a narrow date column between the two real ones, splitting the empty region
into two candidate bands; the page then reads as three columns, is reported unresolved,
and loses every bullet. So candidates are pruned by how much *text* each resulting
column actually holds: a column carrying under 15% of the page's text is not a column,
and the gutter creating it is dropped. Lines spanning a gutter are counted in no column
— attributing a full-width header by its midpoint credits its whole length to the
narrow middle, which is precisely what the measurement exists to catch.

Three or more columns that all carry real text are reported **unresolved**: reading
order is genuinely ambiguous there, so the bullet checks are marked not-applicable
instead of scored against text spliced from unrelated columns.

## Known false positives, and what was done about them

Both of these were caught by running the scorer against a real resume, and both are now
regression tests:

- **Ratios read as dates.** `deterministic 60/20/20 splits`, `20/20 trials`, and `10/10
  targets` all matched an unanchored `MM/YY` pattern. The date pattern now requires a
  four-digit year and a month of 01–12. The cost: a resume that genuinely writes
  `05/24` is no longer detected as a second date format.
- **Correct passive flagged as a defect.** "evaluated once rather than being tuned
  against it" is a passive gerund, deliberate and correct. Passive constructions
  following a subordinator (`than`, `without`, `instead of`, `after`, `before`) are now
  skipped. The cost: a genuine passive in that position is missed.

## The lone-`I` ambiguity

`Calculus I–III` and `I built the parser` contain the same token. The scrubbing runs in
two passes:

1. Roman-numeral **ranges** (`I–III`) and **multi-letter** numerals (`II`, `IV`) are
   removed unconditionally. Neither is ever a pronoun.
2. A **lone `I`** is removed only when it is *not* followed by a lowercase word **on the
   same line**, since a pronoun is the subject of a verb and its verb does not sit on the
   next line. `and` and `or` are excluded from that rule so `Calculus I and Statistics`
   stays a course listing.

The same-line requirement was added after `Coursework: Physics I` followed by a
lowercase continuation line was read as a pronoun.

This is a heuristic and it can be beaten — `I and my co-lead shipped it` scrubs the `I`
and catches only the `my`. It gets the two cases that actually occur on resumes right.

## Calibration: what the corpus test does and does not prove

`tests/fixtures/corpus/` holds ten synthetic resumes ordered from obviously-good to
obviously-bad, and `tests/test_calibration.py` asserts the scorer ranks them in tiers
that match human judgement. **This is an ordering test.** It shows the scoring is
internally consistent — better documents score higher — and it proves nothing about
whether any absolute number is right. It cannot: there is no outcome data, and the
same person wrote the corpus and chose the thresholds. Every threshold on this page
(`WORD_COUNT_FLOOR`, `WORD_COUNT_CEILING`, `BULLET_WORD_CEILING`, the shares, and
`keywords.WEIGHTS`) remains a judgement call.

Building the corpus surfaced two limits worth stating plainly, both now locked in as
tests so the behaviour cannot drift away from this description:

- **A pass/fail check cannot express degree.** `02-strong` strips the numbers out of 6
  of its 16 bullets and still scores exactly the same as `01-excellent`, because
  `quantified` passes at ≥60% and 02 lands at 62%. A real regression in bullet quality
  costs nothing. Grading each check 0–1 instead of pass/fail would fix this, at the
  cost of the traceability that lets every point be explained by a specific line.
- **A short resume is scored on its writing, not its substance.** `05-thin` is 75
  words and outscores four resumes with far more content, because what it does say is
  clean. This is the design working, not failing: the tool measures whether a document
  is well-formed and parseable. Only `text_layer` and `word_count` speak to whether it
  says enough, and they are the only two checks `05-thin` fails. **A high percentage
  never means "this resume is sufficient".**

## Sources

Every external claim in this document traces to one of these. All were fetched and
verified to load and to contain the quoted claim:

- Jobscan, *Job Search Statistics: Lessons From 1 Million Job Applications* —
  <https://www.jobscan.co/blog/interview-rates-study/>
  (the 10.6× job-title figure over 2.5M+ applications; the 384-recruiter skills survey)
- Resume Worded, *Score Guide* — <https://resumeworded.com/score-guide>
  (the Impact / Brevity / Style grouping and weighted-by-importance scoring)
- UC Santa Barbara Career Services, *VMock* —
  <https://career.ucsb.edu/news/new-resource-vmock-for-resume-review-022221>
  (VMock's Impact / Presentation / Competencies modules and the five competencies)

The 15–30 word bullet ceiling and the six-second scan come from widely repeated
recruiter guidance and the TheLadders eye-tracking study. That study is frequently cited
second-hand and the original is not freely available, so treat the exact number as folk
wisdom rather than a measurement — the design does not depend on whether it is six
seconds or ten.

## What this does not do

It does not model any vendor's parser, rank you against other applicants, or read
meaning. A resume can score 97 and be unconvincing: the checks measure whether the
document is clean, specific, and parseable, which is a precondition for being read, not
a substitute for having something to say.
