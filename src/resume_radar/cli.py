"""Command line entry point.

    resume-radar scan  ~/Documents             find resumes in a folder
    resume-radar score resume.pdf --jd jd.txt  score one resume
    resume-radar jobs  --boards boards.txt     rank open postings by fit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analyze, compare, discover, jobs, keywords, tracker


def _print_report(result: dict) -> None:
    print(f"\n{result['resume']}")
    print(f"  {result['percent']}%  ({result['score']}/{result['max_score']} points)"
          f"   {result['pages']} page(s), {result['bullets']} bullets\n")

    cats = result["categories"]
    print("  " + "   ".join(
        f"{name.upper()} {cats[name]['percent']:.0f}%" for name in cats) + "\n")

    for check in result["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        if not check["applicable"]:
            mark = "n/a "
        print(f"  [{mark}] {check['message']}")
        if not check["passed"] and check["evidence"]:
            for item in check["evidence"][:3]:
                print(f"           - {item}")

    comp = result["competencies"]
    print(f"\n  Competencies evidenced: {', '.join(comp['covered']) or 'none'}")
    if comp["missing"]:
        print(f"  Not evidenced: {', '.join(comp['missing'])}")
    if comp["undetectable"]:
        print(f"  Not visible from bullets alone: {', '.join(comp['undetectable'])}")

    if "keywords" in result:
        kw = result["keywords"]
        print(f"\n  Job-description match: {kw['percent']}%")
        if kw["title"]:
            state = "found on resume" if kw["title_matched"] else "NOT on resume"
            print(f"  Job title \"{kw['title']}\": {state}")
        if kw["priority"]:
            print(f"  Missing hard skills (most emphasised first): "
                  f"{', '.join(kw['priority'][:15])}")
        if kw["soft_missing"]:
            print(f"  Missing soft skills: {', '.join(kw['soft_missing'][:10])}")

    for suggestion in result.get("suggestions", []):
        print(f"\n  REWRITE  {suggestion['original'][:70]}...")
        print(f"        ->  {suggestion['suggested']}")
    if result.get("suggestions_error"):
        print(f"\n  (suggestions off: {result['suggestions_error']})")


def _print_jobs(ranked: list, skipped: list) -> None:
    if skipped:
        print(f"\nFiltered out {len(skipped)} posting(s) you cannot win:")
        for post in skipped[:10]:
            print(f"  {post.company}/{post.title[:50]} - {', '.join(post.blockers)}")

    if not ranked:
        print("\nNo eligible postings found.")
        return

    print(f"\nRanked by fit ({len(ranked)} eligible):\n")
    for i, post in enumerate(ranked, 1):
        flag = "  [title match]" if post.match and post.match.title_matched else ""
        print(f"{i:3d}. {post.fit:5.1f}%  {post.company} - {post.title}{flag}")
        print(f"      {post.location or 'location n/a'}   {post.url}")
        if post.match and post.match.priority:
            print(f"      missing: {', '.join(post.match.priority[:6])}")
    print("\nFit is keyword overlap with the posting, not a probability of an offer.")


def _print_comparison(cmp) -> None:
    arrow = {"left": "<-", "right": "->", "tie": "=="}
    print(f"\n  A  {cmp.left_name}   {cmp.left_percent:.1f}%")
    print(f"  B  {cmp.right_name}   {cmp.right_percent:.1f}%")
    verdict = {"left": "A wins", "right": "B wins", "tie": "tie"}[cmp.winner]
    print(f"  overall: {verdict}\n")

    print("  category      A       B      change")
    for name, cat in cmp.categories.items():
        sign = "+" if cat["delta"] > 0 else ""
        print(f"  {name:<12} {cat['left']:5.1f}%  {cat['right']:5.1f}%   "
              f"{sign}{cat['delta']:.1f}")

    if not cmp.deltas:
        print("\n  No check changed.")
        return

    if cmp.gains:
        print(f"\n  B fixed ({len(cmp.gains)}):")
        for d in cmp.gains:
            print(f"    {d.key}: {d.left_message}")
            print(f"      {arrow['right']} {d.right_message}")
    if cmp.losses:
        print(f"\n  B lost ({len(cmp.losses)}) - this is what the rewrite cost:")
        for d in cmp.losses:
            print(f"    {d.key}: {d.left_message}")
            print(f"      {arrow['right']} {d.right_message}")

    unchanged = [d for d in cmp.deltas if d.winner == "tie"]
    if unchanged:
        print(f"\n  Same score, different detail ({len(unchanged)}): "
              f"{', '.join(d.key for d in unchanged)}")


def _fetch_boards(boards_file: str) -> tuple[list, list[str]]:
    """Fetch every board, collecting failures instead of raising.

    One unreachable board must not lose the whole run: a scheduled job that fails
    completely because a single company's board 503'd is a job that gets ignored.
    """
    postings, failed = [], []
    for source, board in jobs.load_boards(boards_file):
        try:
            postings.extend(jobs.fetch(board, source))
        except Exception as exc:  # noqa: BLE001 - deliberate, see below
            # Broad on purpose, and only here. `fetch` already converts the failures
            # it knows about, but this loop is the last thing standing between one
            # third-party board and a scheduled job that silently dies every morning.
            # Losing a company is recoverable; losing the run is not.
            failed.append(f"{source}:{board}")
            print(f"warning: {source}/{board}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
    return postings, failed


def _run_watch(args) -> int:
    from . import digest, extract, watchlist

    resume_text = extract.load(args.resume).text
    postings, failed = _fetch_boards(args.boards)

    screened = [jobs.screen(p) for p in postings]
    eligible = [p for p in screened if p.eligible]
    filtered = [p for p in screened if not p.eligible]

    levels = tuple(args.levels.split(",")) if args.levels else jobs.STUDENT_LEVELS

    store = watchlist.Watchlist(args.db)
    try:
        known = store.known_ids()

        def _is_new(post) -> bool:
            return watchlist.posting_id(post.source, post.company, post.url) not in known

        slate = jobs.build_slate(
            resume_text, eligible, is_new=_is_new,
            target=args.target, min_fit=args.min_fit, levels=levels,
        )
        ranked = slate.picks
        fresh = slate.new
        # Count only the ones held back. The backfill is also "already seen", but it
        # is shown below, so counting it in both places double-reports the same rows.
        already = max(0, sum(1 for p in eligible if not _is_new(p)) - len(slate.backfill))

        text = digest.render(slate.new, already_seen=already, filtered=filtered,
                             boards_failed=failed, backfill=slate.backfill,
                             off_level=slate.off_level, short=slate.short,
                             target=args.target)

        if not args.dry_run:
            # Mark every eligible posting seen, not just the ones that cleared
            # --min-fit: a posting below the bar today should not resurface tomorrow
            # just because it never made a digest.
            # Every eligible posting is recorded, not just the ones that made the
            # shortlist. A role dropped for level or fit today must not come back
            # tomorrow labelled new - and backfill draws from this same pool.
            store.mark_seen(eligible)
            store.mark_seen(filtered)
            store.prune(args.prune_days)
        pruned_note = "" if not args.dry_run else "  (dry run - nothing recorded)"
    finally:
        store.close()

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")

    if args.json:
        print(json.dumps({
            "new": [p.to_dict() for p in slate.new],
            "backfill": [p.to_dict() for p in slate.backfill],
            "off_level": [p.to_dict() for p in slate.off_level],
            "wrong_level": slate.wrong_level,
            "short": slate.short,
            "already_seen": already,
            "filtered": [p.to_dict() for p in filtered],
            "boards_failed": failed,
        }, indent=2))
    else:
        print(text + pruned_note)

    # An empty day is a successful day. Exiting non-zero would mark the scheduled
    # job as failed every time there is simply nothing new.
    return 0


def _run_stats(args) -> int:
    report = tracker.stats(args.log)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"\n  {report['rows']} logged application(s), "
          f"{report['labelled']} with a known outcome")
    if not report["reportable"]:
        print(f"\n  Not enough data yet. {report['reason']}")
        return 0

    print(f"  Overall response rate: {report['response_rate']}%\n")
    print("  by job-description fit:")
    for bucket, entry in report["by_fit_decile"].items():
        print(f"    {bucket:>8}  n={entry['n']:<4} responded {entry['rate']}%")
    print("\n  by job-title match:")
    for label, entry in report["by_title_match"].items():
        print(f"    {label:<16} n={entry['n']:<4} responded {entry['rate']}%")
    print("\n  These are observations from your own applications, not a model.")
    return 0


def _run_answers(args) -> int:
    from . import answers as answer_bank

    if args.init:
        try:
            path = answer_bank.write_template(args.path)
        except FileExistsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote {path}. Edit it before using `resume-radar apply`.")
        return 0

    try:
        bank = answer_bank.load(args.path)
    except answer_bank.AnswersNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for key, value in bank.items():
        print(f"  {key:<22} {value}")
    return 0


def _run_apply(args) -> int:
    from . import answers as answer_bank
    from . import extract

    try:
        bank = answer_bank.load(args.answers)
    except answer_bank.AnswersNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    postings, _ = _fetch_boards(args.boards)
    posting = next((p for p in postings if p.url == args.url), None)
    if posting is None:
        print(f"error: no posting with url {args.url} on the configured boards",
              file=sys.stderr)
        return 1

    doc = extract.load(args.resume)
    jobs.screen(posting)
    posting.match = keywords.compare(doc.text, f"{posting.title}\n{posting.description}")
    prep = answer_bank.prepare(posting, doc, posting.match, bank)

    if args.json:
        print(json.dumps(prep.to_dict(), indent=2))
        return 0

    print(f"\n  {prep.company} — {prep.title}")
    print(f"  {prep.url}\n")
    if prep.blockers:
        print(f"  WARNING: screened as ineligible ({', '.join(prep.blockers)}). "
              "Read the posting yourself before spending time on it.\n")

    print("  Your answers, ready to copy:")
    for item in prep.answers:
        print(f"    Q: {item.question}")
        print(f"    A: {item.answer}\n")

    if prep.unmatched:
        print("  Questions with no stored answer — you write these:")
        for question in prep.unmatched:
            print(f"    - {question}")
        print()

    print("  Three bullets from your resume that match this posting:")
    for bullet in prep.pitch:
        print(f"    - {bullet}")
    if prep.missing_skills:
        print(f"\n  This posting asks for: {', '.join(prep.missing_skills)}")
        print("  Only add one of these if you have actually done it and can be "
              "questioned on it.")

    print("\n  Nothing was submitted. Open the url and fill the form yourself.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="resume-radar")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="find resumes in a folder")
    scan.add_argument("folder")
    scan.add_argument("--threshold", type=float, default=0.5)
    scan.add_argument("--json", action="store_true")

    score = sub.add_parser("score", help="score a resume")
    score.add_argument("resume")
    score.add_argument("--jd", help="path to a job-description text file")
    score.add_argument("--suggest", action="store_true", help="add model rewrites")
    score.add_argument("--log", help="append the result to a CSV log")
    score.add_argument("--company", default="")
    score.add_argument("--role", default="")
    score.add_argument("--json", action="store_true")

    comp = sub.add_parser("compare", help="diff two versions of a resume")
    comp.add_argument("left")
    comp.add_argument("right")
    comp.add_argument("--jd", help="path to a job-description text file")
    comp.add_argument("--json", action="store_true")

    jobs_cmd = sub.add_parser("jobs", help="rank open postings against your resume")
    jobs_cmd.add_argument("--resume", required=True)
    jobs_cmd.add_argument("--boards", required=True,
                          help="file of 'greenhouse:company' / 'lever:company' lines")
    jobs_cmd.add_argument("--min-fit", type=float, default=0.0)
    jobs_cmd.add_argument("--limit", type=int, default=25)
    jobs_cmd.add_argument("--include-ineligible", action="store_true")
    jobs_cmd.add_argument("--json", action="store_true")

    watch = sub.add_parser("watch", help="report postings that are new since last run")
    watch.add_argument("--resume", required=True)
    watch.add_argument("--boards", required=True,
                       help="file of 'greenhouse:company' / 'lever:company' lines")
    watch.add_argument("--min-fit", type=float, default=25.0)
    watch.add_argument("--db", default=None, help="watchlist database path")
    watch.add_argument("--out", help="write the markdown digest to this file")
    watch.add_argument("--target", type=int, default=10,
                       help="how many postings to surface each run (default 10)")
    watch.add_argument("--levels", default="",
                       help="comma-separated role levels to keep "
                            "(intern,new_grad,mid,senior); default intern,new_grad")
    watch.add_argument("--prune-days", type=int, default=90)
    watch.add_argument("--dry-run", action="store_true",
                       help="report without recording, so the next run repeats it")
    watch.add_argument("--json", action="store_true")

    out = sub.add_parser("outcome", help="record what came of a logged application")
    out.add_argument("row_id")
    out.add_argument("value", choices=tracker.OUTCOMES)
    out.add_argument("--log", required=True)

    st = sub.add_parser("stats", help="response rate by fit and by title match")
    st.add_argument("--log", required=True)
    st.add_argument("--json", action="store_true")

    ans = sub.add_parser("answers", help="manage your standard application answers")
    ans.add_argument("--init", action="store_true", help="create a starter answer bank")
    ans.add_argument("--path", default=None)

    ap = sub.add_parser(
        "apply", help="print what you need to fill an application by hand")
    ap.add_argument("url", help="posting url, as shown in the digest")
    ap.add_argument("--resume", required=True)
    ap.add_argument("--boards", required=True)
    ap.add_argument("--answers", default=None, help="path to answers.yml")
    ap.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "scan":
        found = discover.find(args.folder, threshold=args.threshold)
        if args.json:
            print(json.dumps([c.to_dict() for c in found], indent=2))
        elif not found:
            print("No resumes found.")
        else:
            for cand in found:
                print(f"{cand.confidence:.2f}  {cand.path}")
                for reason in cand.reasons:
                    print(f"        {reason}")
        return 0

    if args.command == "watch":
        return _run_watch(args)

    if args.command == "outcome":
        try:
            row = tracker.set_outcome(args.log, args.row_id, args.value)
        except (LookupError, tracker.UnknownOutcome) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"row {row['id']}: {row['company'] or '(no company)'} "
              f"{row['role'] or ''} -> {row['outcome']}")
        return 0

    if args.command == "stats":
        return _run_stats(args)

    if args.command == "answers":
        return _run_answers(args)

    if args.command == "apply":
        return _run_apply(args)

    if args.command == "compare":
        jd = Path(args.jd).read_text(encoding="utf-8") if args.jd else ""
        result = compare.compare(analyze(args.left, jd), analyze(args.right, jd))
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            _print_comparison(result)
        return 0

    if args.command == "jobs":
        from . import extract
        resume_text = extract.load(args.resume).text

        postings, failures = [], []
        for source, board in jobs.load_boards(args.boards):
            try:
                postings.extend(jobs.fetch(board, source))
            except RuntimeError as exc:
                failures.append(str(exc))

        for note in failures:
            print(f"warning: {note}", file=sys.stderr)
        if not postings:
            print("No postings fetched.", file=sys.stderr)
            return 1

        screened = [jobs.screen(p) for p in postings]
        skipped = [p for p in screened if not p.eligible]
        ranked = [p for p in jobs.rank(resume_text, screened,
                                       include_ineligible=args.include_ineligible)
                  if p.fit >= args.min_fit][:args.limit]

        if args.json:
            print(json.dumps([p.to_dict() for p in ranked], indent=2))
        else:
            _print_jobs(ranked, skipped)
        return 0

    jd = Path(args.jd).read_text(encoding="utf-8") if args.jd else ""
    analysis = analyze(args.resume, jd, with_suggestions=args.suggest)

    if args.log:
        tracker.append(args.log, resume=args.resume, report=analysis.report,
                       company=args.company, role=args.role,
                       keyword_match=analysis.keyword_match)

    if args.json:
        print(json.dumps(analysis.to_dict(), indent=2))
    else:
        _print_report(analysis.to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
