# Score Trace API

Score Trace records the explanation produced by a successful structured AI score. It is a read-only detail endpoint and does not change the existing job-list payload.

## Endpoint

`GET /api/jobs/{job_id}/score-trace`

### Successful response

```json
{
  "job_id": "job-123",
  "state": "available",
  "trace": {
    "schema_version": 1,
    "role_summary": "岗位职责概述",
    "components": {
      "core_duties": {"score": 34, "max_score": 40, "evidence": "..."},
      "transferable_evidence": {"score": 21, "max_score": 25, "evidence": "..."},
      "hard_requirements": {"score": 12, "max_score": 15, "evidence": "..."},
      "tools_industry": {"score": 7, "max_score": 10, "evidence": "..."},
      "practical_fit": {"score": 8, "max_score": 10, "evidence": "..."}
    },
    "raw_score": 82,
    "final_score": 55,
    "caps": ["technical_required"],
    "hard_gaps": ["缺少 Linux 私有化部署经历"],
    "summary_reason": "产品交付经验匹配，但有硬技术缺口",
    "missing": "Linux 部署",
    "review_status": "initial"
  }
}
```

`review_status` is `initial` for the first assessment and `reviewed` after a successful secondary review. `caps` may contain `technical_required`, `sales_acquisition_core`, or `weak_core_transfer`.

## States

| HTTP | State | Meaning |
| --- | --- | --- |
| 200 | `available` | A validated V1 trace is available. |
| 200 | `legacy_missing` | The job has historical scoring information but no trace. |
| 200 | `prefilter_only` | The job stopped at pre-filtering, so no AI trace exists. |
| 200 | `failed` | AI scoring failed before a trace could be saved. |
| 200 | `unavailable` | No usable trace is available, including malformed stored data. |
| 404 | — | The job does not exist: `{"error": "job_not_found"}`. |

## Persistence and safety

- The latest trace is written atomically with a successful structured score.
- Pre-filter and AI-score failures do not create a trace and do not overwrite a previously valid trace.
- The API exposes a strict V1 whitelist only; it never returns resumes, raw JD text, API keys, or raw model responses.
