# How ResumeRadar compares to the paid tools

Honest assessment against Jobscan (~$50/mo), Resume Worded (~$49/mo), and VMock,
based on what each publicly documents it checks.

## Where they are genuinely better

**1. ~~ATS-specific formatting checks~~ — closed.**
Five of the six proposed checks now ship: `no_images`, `no_tables`,
`no_header_footer_content`, `standard_fonts`, `font_size_sane`. See the bottom of this
file for what each one required and which one was deliberately dropped. Jobscan is
still ahead on breadth of ATS-vendor research; the gap is no longer *capability*.

**2. Calibration against outcomes.**
VMock trained on a large resume corpus; Jobscan cites a 2.5M-application study for its
title-match weight. Every ResumeRadar threshold is a judgement call, and
`docs/scoring.md` says so. That honesty is correct but it does not make the numbers
right. The `outcome` column is the path out, and it needs 50-80 labelled rows.

**3. Peer benchmarking.** VMock scores you against other students at your school.
Requires a dataset that cannot be reproduced solo.

**4. LinkedIn profile analysis.** Resume Worded scores your profile too. Different
artifact, not currently in scope.

## Where ResumeRadar is genuinely better

**1. It finds jobs. None of them do.** Jobscan scores a resume against a posting you
already found. ResumeRadar pulls 29 company boards, filters to intern and new-grad
level, ranks by fit, and hands you a daily shortlist.

**2. Visa eligibility screening.** No commercial tool filters postings by citizenship
requirement, active clearance, ITAR, or explicit no-sponsorship language. On an F-1
this is worth more than any scoring refinement — it removes applications that cannot
succeed regardless of how good the resume is.

**3. The resume never leaves the machine.** Jobscan, Resume Worded, and most free
"ATS scanners" require uploading a document containing your phone number, address, and
full work history. Many free ones are lead generation. ResumeRadar runs locally.

**4. Every point is auditable.** A commercial score of 68 is a number. ResumeRadar
names the failing check, quotes the offending line, and the code is readable. When it
is wrong you can see *why* it is wrong, which is how the two bugs in `CODE-REVIEW*.md`
were found.

**5. Comparing versions.** `compare a.pdf b.pdf` shows what a rewrite cost as well as
what it gained. Useful when you keep several drafts.

**6. Free and unlimited.** Jobscan's free tier caps scans per month.

## Verdict

For **scoring one resume against one posting**, Jobscan is better today, on the
strength of its format checks.

For **the actual job-search loop** — find roles you can legally hold, rank them, see
what you are missing, track what happened — nothing commercial does this, because no
vendor's business model rewards telling you to apply to fewer places.

## The one gap worth closing: ATS format checks

Verified as detectable with the pdfminer dependency already installed. Probing two
documents:

```
Mayank (1-col)   figures=0 images=0 rects=0 lines=4 curves=4
                 fonts=['NimbusRomNo9L-Regu', 'NimbusRomNo9L-Medi', ...]
                 sizes=[8, 9, 12]   lines in top 40pt=0  bottom 40pt=1
```

Everything Jobscan sells is reachable from this. Proposed checks, all in the `ats`
category:

| Check | Signal | Why it matters |
|---|---|---|
| `no_images` | `LTImage` / `LTFigure` count > 0 | Text inside an image is invisible to every parser |
| `no_tables` | many `LTRect` / grid-aligned `LTLine`s | Tables scramble reading order in Workday and Taleo |
| `no_header_footer_content` | text lines within ~40pt of a page edge | Some parsers discard the header and footer region entirely, taking your contact details with them |
| `standard_fonts` | font names outside a known-safe set | Exotic or non-embedded fonts extract as mojibake |
| `font_size_sane` | body text under 9pt or over 12pt | Under 9pt is a shrink-to-fit tell and reads badly |
| `no_text_boxes` | isolated `LTTextBox` far from the main flow | Same reading-order problem as tables |

Careful with two false positives:

- **Horizontal rules are not tables.** Mayank's resume has 4 `LTLine`s — they are
  section underlines. Require a *grid*: multiple lines in both orientations, or
  several rects sharing edges.
- **A one-line footer is not header/footer content.** His resume has 1 line in the
  bottom band; that is the last bullet sitting low on a full page, not a running
  footer. Only flag when the same band carries text on *every* page, or when contact
  details specifically appear there.

Both are exactly the class of mistake that produced the previous two bugs: a signal
that looks decisive until you test it on a real document.

## Not worth chasing

- **Peer benchmarking** — needs a corpus you do not have and cannot ethically obtain.
- **LinkedIn scoring** — a different artifact; would double the scope for a feature
  you would use twice a year.
- **"Beat the ATS" claims** — no vendor's algorithm is public. ResumeRadar should keep
  describing itself as a heuristic scorer built on public guidance. Claiming to
  simulate Workday would be the same overclaim the whole repo has been avoiding.

## Suggested order

1. `no_images`, `standard_fonts`, `font_size_sane` — unambiguous signals, quick wins
2. `no_header_footer_content` — needs the multi-page rule above
3. `no_tables` / `no_text_boxes` — hardest to get right, most false-positive risk
4. Then stop adding checks and start logging outcomes. Twenty more checks calibrated
   on nothing is worth less than twenty applications with recorded results.

## What shipped, and what each one actually took

Five checks, 21 points, all in the `ats` category, all reporting **not applicable** on
`.txt` and `.docx` rather than passing for free. Every one was built against the real
resume first, and all five pass on it — which is the point, since a structure check
that fires on a good document is worse than no check.

Both predicted false positives were real, and a third was not predicted:

- **Rules are not tables.** Confirmed: 4 horizontal rules, 0 vertical, 0 rects. A grid
  now requires ruling in both directions or 4+ rectangles. Verified it still fires on a
  synthesised 3×3 grid and on 5 boxes.
- **One low line is not a footer.** Confirmed: 1 line in the bottom band, single page,
  the last bullet. Only a band populated on *every* page of a multi-page document, or
  contact details in the band, now counts.
- **A font whitelist would have been wrong** — this one was not anticipated. The real
  resume uses `IWZWNL+NimbusRomNo9L-Regu` and `IYELBM+CMSY10`: URW Nimbus Roman and
  Computer Modern Symbol, which is simply what LaTeX emits. Any Arial/Times/Calibri
  whitelist flags a perfectly good document. The check instead flags decorative
  families by name, plus fonts that are neither subset-embedded (`ABCDEF+Name`) nor one
  of the base-14 faces every PDF reader has built in.

**`no_text_boxes` was dropped, not deferred.** pdfminer reports 43 text boxes on the
real one-column resume and 27 on the other. "An isolated text box far from the main
flow" has no threshold that separates it from ordinary typesetting, so the check would
be wrong more often than right. That is the same class of mistake as the two bugs in
`CODE-REVIEW*.md`, caught before shipping this time rather than after.

Now do step 4.
