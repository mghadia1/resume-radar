# Running the digest daily

`resume-radar watch` is a **one-shot** command. It fetches, builds a shortlist, records
what it showed, and exits. There is no daemon and no background process to babysit —
scheduling is the operating system's job, and it is better at it.

Every run exits `0`, including the days when nothing is new. A scheduled job that
reports failure on a quiet day is a job you start ignoring within a week.

## What one run produces

`--target` (default 10) is how many postings you want in front of you each morning.
New postings fill those slots first. If there are fewer than `--target` new ones, the
best still-open postings from earlier runs are **carried over** into their own section,
labelled as already seen. A digest that just says "nothing new" is not worth opening;
one that quietly re-lists yesterday's roles as if they were fresh is worse.

`--levels` decides which role levels survive, read from the posting title. The default
is `intern,new_grad`. Everything dropped for level is listed in a collapsed section of
the digest with how it was classified — the filter is a title heuristic, and a role
titled plainly "Software Engineer" has no level word in it and is dropped. If the
dropped list is full of things you would apply to:

```bash
resume-radar watch ... --levels intern,new_grad,mid
```

Postings blocked by citizenship, clearance, sponsorship, or ITAR are listed the same
way, with the reason. Both lists exist so a wrong screen is visible rather than silent.

## Setup

```bash
resume-radar answers --init          # create ~/.resume-radar/answers.yml, then edit it
```

Create a board list. One `source:company` per line, `#` for comments. The company is
the slug in the board's own URL — for `boards.greenhouse.io/figma` that is `figma`:

```
# ~/.resume-radar/boards.txt
greenhouse:figma
greenhouse:anthropic
lever:netflix
```

Check it works before scheduling anything:

```bash
resume-radar watch --resume ~/resume.pdf --boards ~/.resume-radar/boards.txt --dry-run
```

`--dry-run` reports without recording, so you can run it repeatedly while you tune
`--min-fit` without burning through the "new" postings.

## Option 1 — cron (Linux, and macOS if you do not mind it skipping while asleep)

```bash
crontab -e
```

```cron
0 8 * * 1-5 /usr/local/bin/resume-radar watch \
  --resume "$HOME/resume.pdf" \
  --boards "$HOME/.resume-radar/boards.txt" \
  --out "$HOME/.resume-radar/digest.md" \
  >> "$HOME/.resume-radar/watch.log" 2>&1
```

Weekdays at 08:00. Use absolute paths: cron's `PATH` is nearly empty, so `resume-radar`
alone will not resolve. `which resume-radar` gives you the right path.

**cron does not catch up.** If the machine is asleep at 08:00 the run is skipped
entirely, and the postings it would have reported simply appear in the next run —
nothing is lost, but the digest is late.

## Option 2 — launchd (macOS, the better choice)

launchd runs a missed job when the machine wakes, which cron does not. Save as
`~/Library/LaunchAgents/com.resumeradar.watch.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>com.resumeradar.watch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/resume-radar</string>
    <string>watch</string>
    <string>--resume</string>  <string>/Users/YOU/resume.pdf</string>
    <string>--boards</string>  <string>/Users/YOU/.resume-radar/boards.txt</string>
    <string>--out</string>     <string>/Users/YOU/.resume-radar/digest.md</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>    <integer>8</integer>
    <key>Minute</key>  <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>   <string>/Users/YOU/.resume-radar/watch.log</string>
  <key>StandardErrorPath</key> <string>/Users/YOU/.resume-radar/watch.err</string>
</dict>
</plist>
```

Replace `YOU`, then:

```bash
launchctl load ~/Library/LaunchAgents/com.resumeradar.watch.plist
```

`launchctl list | grep resumeradar` confirms it is registered. To stop it, `launchctl
unload` the same path.

## Option 3 — GitHub Actions

Runs whether or not your laptop is on. The database must **not** be committed to the
repo — it changes every run, and committing it turns a job-search log into public git
history. Use the cache action instead, which persists between runs without a commit.

```yaml
# .github/workflows/watch.yml
name: daily postings

on:
  schedule:
    - cron: "0 15 * * 1-5"   # 15:00 UTC = 08:00 Phoenix. Cron here is always UTC.
  workflow_dispatch:          # so you can trigger a run by hand

jobs:
  watch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -e .

      # Restore yesterday's database. The key must change every run or the cache is
      # never updated; restore-keys falls back to the most recent previous one.
      - uses: actions/cache@v4
        with:
          path: .watch-db
          key: watch-db-${{ github.run_id }}
          restore-keys: watch-db-

      - run: mkdir -p .watch-db
      - run: |
          resume-radar watch \
            --resume resume.pdf \
            --boards boards.txt \
            --db .watch-db/seen.db \
            --out digest.md

      - run: cat digest.md >> "$GITHUB_STEP_SUMMARY"

      - uses: actions/upload-artifact@v4
        with:
          name: digest
          path: digest.md
```

The digest lands in the run summary, so you read it on the Actions page without
downloading anything; the artifact is there if you want the file.

Two caveats worth knowing before you rely on this:

- **Scheduled workflows are disabled after 60 days of repository inactivity.** GitHub
  emails you first. A `workflow_dispatch` run re-enables it.
- **`schedule:` is best-effort.** Runs are queued and can be delayed by tens of minutes
  under load. It is a daily digest, so this does not matter — but do not expect 15:00
  sharp.

If your resume is not in the repo (it probably should not be), commit a redacted copy
or fetch it in a step from somewhere private. Do not commit the answer bank: it holds
your phone number and your immigration status.

## When a board misbehaves

A board that 404s, times out, drops the connection, or answers 200 with something that
is not its documented shape is skipped with a warning on stderr, and the run continues
with the rest. That guarantee is load-bearing for a scheduled job — losing one company
is recoverable, losing the morning's run is not — so it is enforced in two places:
`jobs.fetch` converts the failures it knows about, and the loop in `cli._fetch_boards`
catches everything else.

This was not theoretical. Running against 29 live boards raised
`http.client.RemoteDisconnected`, which is **not** a `URLError`, and the entire run
died. Tests that mock a tidy exception will not find that; only pointing it at real
servers did.

## Expect quiet runs, and know why

Internship postings are seasonal. Most Summer roles open between **August and October**.
A run in late spring or midsummer that finds one or two new postings across thirty
boards is the calendar, not a broken filter — across Stripe, Anthropic, Figma and Waymo
in late July, 1,517 open roles contained exactly **one** internship.

The digest says how many were dropped and why, so you can tell a quiet season from a
misconfiguration: a large `wrong level` count with a `dropped as the wrong level` list
full of senior roles is the filter working, not failing.

## Housekeeping

The database prunes itself. Anything first seen more than 90 days ago is forgotten, so
a posting that is still open after three months will resurface once — which is usually
what you want, since it means they are still hiring. `--prune-days` changes the window.

```bash
resume-radar watch ... --prune-days 30
```

## Recording what happened

The digest tells you what is new. It cannot tell you what worked — only you can, and
only by writing it down as replies arrive:

```bash
resume-radar score resume.pdf --jd jd.txt --company Acme --role "SWE Intern" --log ~/runs.csv
resume-radar outcome 7 interview --log ~/runs.csv
resume-radar stats --log ~/runs.csv
```

`stats` refuses to report a response rate until 20 applications have a recorded
outcome, and says so. Below that, every rate it could print would be noise, and the
whole point of the column is to avoid reading a pattern into noise.
