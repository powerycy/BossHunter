# Implementation Plan: Concurrent Scoring and Job Filters

Branch: `feat/concurrent-scoring-and-job-filters`

## 1. Backend contracts and filters

- Add defaults and validation for `ai.scoring_concurrency`, `profile.blocked_companies`, and `search.city_codes`.
- Add a case-insensitive substring matcher for blocked company names.
- Apply company blocking in the scraper before insertion and in the AI prefilter for existing jobs.
- Add focused tests for defaults, matching variants, and both filter call sites.

Verification: focused Python tests, then the existing full suite.

## 2. Concurrent scoring service

- Refactor `score_jobs` into a job-id-aware service while preserving the current retry and cancellation behavior.
- Keep SQLite reads and writes on the coordinator thread; workers only perform AI calls and response normalization.
- Bound workers by the clamped `1..5` setting, defaulting to `3`.
- Preserve per-job failures, stop scheduling after batch-level quota/auth/rate-limit/network errors, and retain unscheduled jobs as pending.
- Add tests that measure the configured worker bound, assert result persistence, and cover batch-stop and cancellation behavior.

Verification: scorer-focused tests followed by the full Python suite.

## 3. Workbench and job-pool entry points

- Pass newly inserted job IDs from the collection task to the shared scorer.
- Add a job-pool scoring task mode/API that accepts optional selected pending job IDs.
- Add job-pool selection state, an `AI Batch Score` action, disabled/loading/error/progress states, and refresh behavior in the dashboard.
- Keep the existing stop endpoint and progress payload compatible.
- Add API/task tests for selected IDs, all pending jobs, duplicate starts, and stop handling.

Verification: Web API tests plus a frontend build.

## 4. City catalog lookup and configuration UI

- Add a backend city lookup service using the platform city catalog endpoint with timeout, exact normalized-name matching, and safe error responses.
- Add the lookup route and tests for success, unknown city, malformed response, and timeout.
- Update city config persistence so custom codes are stored in `search.city_codes`; continue synchronizing `search.cities` and `profile.target_cities`.
- Keep built-in city buttons and add an Enter-driven tag input for custom cities.
- Add the three opt-in blocked-company preset buttons and the 1-5 scoring-concurrency control.

Verification: API tests, frontend build, and browser/manual interaction checks for the config page.

## 5. Documentation and final verification

- Update the example configuration and concise development notes for the new settings.
- Run the full Python suite with output redirected on Windows, run targeted Ruff checks only on changed Python files, and build the frontend.
- Review the diff for backwards compatibility, secret handling, and accidental generated artifacts.
