# Automation log

- 2026-11-10 18:58 UTC — Normalized invalid DOCX ZIP, missing document XML, and malformed XML failures to ExtractionError (API 422) on codex/resume-radar-docx-errors; six regressions failed before the fix; focused DOCX tests 7 passed, full suite 328 passed, workflow JSON validation and git diff --check passed (Python 3.14.7; no project lint/type checks configured); next: bound API upload reads before allocating the entire payload; READY TO MERGE.
