# Evidence-Based Deep Scoring

## Goal

Add an opt-in deep AI scoring mode that explains how each job requirement is
supported by the resume. The existing no-AI prefilter remains the first gate
for every scoring run.

## Configuration

Add `scoring.deep_scoring`, defaulting to `false`. When disabled, the current
scoring prompt and result shape remain available for compatibility. When
enabled, both workbench scoring and the job-pool scoring action use the deep
prompt. Existing concurrency, pause, retry, threshold, and low-score deletion
behavior remains unchanged.

## Prefilter Contract

Prefilter runs before any AI request and is unchanged:

- blocked company keyword match;
- anonymous company detection;
- excluded keyword in the job title;
- internship, `intern`, or management-trainee title when internships are not
  allowed;
- configured minimum salary when the job's salary ceiling is below it.

Passing jobs persist `quick_score=100`; rejected jobs persist `quick_score=0`
and are filtered without an AI call.

## Deep Scoring Contract

The prompt receives the resume, title, company, salary, experience requirement,
and full JD. The model must split the JD into core responsibilities, required
skills, years/level, and optional/preferred items. It must inspect each resume
work entry and project for responsibilities, outcomes, tools, and context.

The score is 0-100 with these weights:

- core responsibilities and project experience: 55%;
- skills, tools, and methods: 25%;
- years and role level: 15%;
- industry background and optional bonuses: 5%.

Industry and optional items add evidence but do not create hard deductions when
absent. Salary is reported separately as a hard-condition result or warning,
not mixed into ability-match scoring.

The required JSON response is:

```json
{
  "score": 0,
  "reason": "short evidence-based summary",
  "missing": "required gaps only",
  "salary_assessment": "pass|warning|fail|not_provided",
  "evidence_mapping": [
    {
      "requirement": "JD requirement",
      "category": "core|skill|experience|bonus",
      "evidence": "resume evidence or empty string",
      "match": "strong|partial|none",
      "gap": "unproven gap or empty string"
    }
  ]
}
```

`score`, `reason`, and `missing` are mandatory. The remaining fields are
validated and normalized; malformed optional fields do not discard an
otherwise valid score. The evidence mapping is stored separately from the
short reason so the UI can render it as an expandable audit trail.

## Persistence and UI

Add a nullable `score_evidence` JSON/text field to jobs with an idempotent
SQLite migration. Existing rows remain readable with an empty mapping. The
jobs API includes the parsed mapping when valid. Job cards continue showing
the score and short reason; job details expose the per-requirement evidence,
match level, and gap.

## Error Handling and Compatibility

Deep scoring uses the existing retry, concurrency, cancellation, and batch-stop
rules. A response without the required score/reason remains a retryable scoring
failure. A valid score with malformed optional evidence stores an empty mapping
and a visible warning in the reason. No prefilter rule may invoke the AI.

## Testing

- config defaults deep scoring off and accepts the boolean field;
- deep prompt contains the required inputs, weights, optional-item rule, and
  JSON contract;
- valid deep JSON parses evidence and salary assessment;
- malformed optional evidence does not invalidate score/reason/missing;
- prefilter rejection skips AI and persists quick score 0;
- prefilter pass persists quick score 100 before deep scoring;
- workbench and job-pool scoring both pass the deep flag through;
- API/UI expose evidence mapping without changing legacy rows.
