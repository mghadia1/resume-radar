# Calibration corpus

Ten synthetic resumes, ordered by how good a human would judge them, from `01` (best)
to `10` (worst). Every one is invented — no real person's resume is in here.

They exist to answer one question: **does the scorer put them in the same order a human
would?** That is an *ordering* test. It says nothing about whether 83.5% is the right
number for any individual resume, and it cannot, because there is no outcome data. See
`docs/scoring.md`.

Each file's first line is a `# rank N — reason` comment, stripped before scoring.

| File | What it is |
|---|---|
| `01-excellent.txt` | Everything right: quantified, tight bullets, complete sections |
| `02-strong.txt` | One weak spot — a couple of unquantified bullets |
| `03-good.txt` | Solid but slightly long bullets |
| `04-wordy.txt` | Good content buried in 40-word bullets |
| `05-thin.txt` | Correct but far too short, under the word floor |
| `06-repetitive.txt` | Same opening verb throughout, no variety |
| `07-passive.txt` | Passive voice and filler phrases |
| `08-pronouns.txt` | First person throughout, miscased tools |
| `09-unstructured.txt` | No section headers, no dates |
| `10-terrible.txt` | Almost nothing a parser can use |
