# Schemas

This document collects the machine-readable formats described by the project specification.

## Skill Validation Result

Optional path:

```text
runs/validation/<skill-name>/validation.json
```

Example:

```json
{
  "valid": true,
  "errors": [],
  "warnings": []
}
```

A simple exit code `0/1` is acceptable initially if persistent validation output is not yet useful.

---

## `usage.json`

Minimum schema:

```json
{
  "model": "...",
  "execution_mode": "text",
  "input_tokens": null,
  "output_tokens": null,
  "cost_usd": null,
  "latency_seconds": 0.0,
  "created_files": []
}
```

Providers that cannot expose some fields should use `null`, not fake values.

---

## `checks.json`

Use a generic check schema.

Preferred format:

```json
{
  "passed": 0,
  "failed": 0,
  "checks": [
    {
      "name": "...",
      "passed": true,
      "category": "...",
      "severity": "...",
      "evidence": "..."
    }
  ]
}
```

Checks must be extensible.

---

## `evaluation.json`

Semantic evaluation result.

Preferred schema:

```json
{
  "criteria": [
    {
      "name": "...",
      "score": 0,
      "max_score": 0,
      "reasoning": "..."
    }
  ],
  "total_score": 0,
  "max_score": 0
}
```

Rubric criterion names must be fully dynamic.

The evaluator receives:

- generated output;
- reference;
- rubric.

The tested model does not receive the reference.

---

## DEV `summary.json`

Path:

```text
runs/dev/summary.json
```

Example structure:

```json
{
  "cases": {
    "case-001": {
      "model-name": {
        "runs_count": 3,
        "score": {
          "mean": 84.0,
          "stddev": 4.2,
          "min": 80.0,
          "max": 88.0,
          "ci95_low": 79.2,
          "ci95_high": 88.8
        },
        "criteria": {},
        "checks": {},
        "usage": {},
        "gate": {}
      }
    }
  }
}
```

---

## `error_analysis.json`

Path:

```text
runs/dev/error_analysis.json
```

Example:

```json
{
  "cases": {
    "case-001": {
      "model-name": {
        "has_errors": true,
        "issues": [],
        "failed_checks": {},
        "judge_reasoning": {},
        "runs": []
      }
    }
  }
}
```

---

## `optimization.json`

Path:

```text
runs/optimization/<skill-name>/v1/optimization.json
```

Example:

```json
{
  "from_version": "v0",
  "to_version": "v1",
  "changes": [
    {
      "problem": "...",
      "change": "...",
      "reason": "..."
    }
  ]
}
```

---

## `regression.json`

Path:

```text
runs/regression/<skill-name>/regression.json
```

Include at minimum:

- total score delta;
- per-criterion delta;
- checks delta;
- variance delta;
- improved cases;
- degraded cases;
- critical regressions;
- gate verdict.

---

## `holdout.json`

Path:

```text
runs/holdout/<skill-name>/<version>/holdout.json
```

Include:

- passed / failed;
- applied gates;
- overall scores;
- failed cases;
- critical failures.

---

## `feedback.json`

Path:

```text
runs/review/<skill-name>/<version>/feedback.json
```

Example:

```json
{
  "reviews": [
    {
      "case_id": "case-001",
      "run_id": "run-01",
      "approved": true,
      "feedback": "..."
    }
  ],
  "status": "complete"
}
```

---

## `release.json`

Path:

```text
releases/<skill-name>/<version>/release.json
```

Example:

```json
{
  "skill_name": "...",
  "version": "v1",
  "released_at": "...",
  "validation_passed": true,
  "regression_passed": true,
  "holdout_passed": true,
  "expert_review_passed": true
}
```
