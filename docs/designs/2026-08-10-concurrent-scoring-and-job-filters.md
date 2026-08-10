# Concurrent Scoring, Job Filters, and Custom Cities

## Status

Approved for implementation on 2026-08-10.

## Goals

1. Score pending jobs concurrently, with a user-configurable AI request limit.
2. Keep automatic post-collection scoring and manual job-pool scoring as two entry points to one task implementation.
3. Add company-name block rules, including opt-in outsourcing-company presets.
4. Keep the existing city buttons and support custom cities entered with Enter after resolving a platform city code.

## Non-Goals

- Change message sending frequency, confirmation requirements, or anti-abuse controls.
- Add a third-party geocoding service or require another API key.
- Re-score completed jobs unless the existing rescore flow explicitly requests it.

## Configuration Contract

```yaml
profile:
  blocked_companies: []
search:
  cities: []
  city_codes: {}
ai:
  scoring_concurrency: 3
```

- `ai.scoring_concurrency` defaults to `3`; the backend clamps malformed values to `1..5`.
- `profile.blocked_companies` is a list of case-insensitive substring rules.
- `search.cities` remains a list of city names for backward compatibility and stays synchronized with `profile.target_cities`.
- `search.city_codes` maps custom city names to verified platform codes. Built-in `CITY_CODES` remains the fallback for existing names.
- Existing config files without the new keys remain valid.

## Scoring Architecture

`score_jobs(config, job_ids=None)` becomes the sole scoring service.

1. It loads only pending jobs, optionally limited to `job_ids`.
2. The main thread applies the free quick filter and writes quick-filter outcomes to SQLite.
3. Eligible jobs enter a bounded worker pool. Each worker performs an AI request, response validation, and existing per-job retry behavior, then returns a result object. Workers never write to SQLite.
4. The main thread persists each completed result, updates progress, and preserves the current status vocabulary.
5. A quota, authentication, rate-limit, network, or request failure is a batch-stopping error. No further jobs are submitted. Already-started jobs are allowed to finish and their results are persisted; unsubmitted jobs remain pending for a later run.
6. A malformed response or context/token issue that remains unresolved is a per-job failure and leaves that job retryable, without stopping unrelated workers.

The workbench progress payload continues to report completed, total, scored, filtered, and failed counts. It gains no database writes from background threads, avoiding SQLite connection ownership errors.

## Entry Points

Both paths use the same scoring service and the same concurrency setting:

- The full collection flow passes only the IDs inserted during that collection run to automatic scoring.
- The job-pool page provides an `AI Batch Score` command. With selected pending rows, it scores only those rows; otherwise it scores all pending rows.
- The batch command displays the existing task progress and supports the existing stop operation.

## Company Blocking

`matching_blocked_company(company, blocked_companies)` normalizes case and surrounding whitespace, then returns the first rule contained in the collected company name.

The check runs in two places:

1. Before a collected detail job is inserted into the database.
2. In the prefilter before any AI request, covering jobs already in the pool.

The configuration page adds a `Blocked Companies` tags input and three opt-in preset buttons:

- `德科信息有限公司`
- `深德科`
- `中软国际科技服务有限公司`

The stored presets use the user-supplied Chinese names. Clicking an already-added preset is idempotent.

## Custom Cities

The configuration page keeps the current quick-select city buttons. Below them, a tags input accepts a city on Enter.

For a new city, the frontend calls a backend lookup endpoint. The backend fetches the platform city catalog at `https://www.zhipin.com/wapi/zpCommon/data/city.json` with a timeout, flattens its city entries, and performs an exact normalized-name match. On success it returns and persists the city name and code in `search.cities` and `search.city_codes`. On no match or a request error it returns an actionable error and does not add the tag.

Removing a city removes both its name and custom code, and synchronizes `profile.target_cities`. Selecting an existing built-in city continues to update both selected-city fields without a lookup. The scraper resolves `search.city_codes[name]` first, then built-in `CITY_CODES[name]`.

## Tests

- Config defaults, clamping, and old configuration compatibility.
- Worker-pool maximum concurrency, result persistence on the main thread, and batch-stop behavior.
- Automatic collection scoring only newly inserted jobs and job-pool scoring of selected or all pending jobs.
- Case-insensitive company substring filtering at both collection and prefilter stages.
- Preset idempotence in the UI-facing state logic.
- Platform city lookup success, unknown city rejection, timeout/error handling, and custom-code scraper resolution.
- API routes for the new config and job-pool task flow.

Rendered dashboard changes will be checked at desktop and mobile widths: city selection and custom city addition, company presets, concurrency selection, and the job-pool batch scoring action.
