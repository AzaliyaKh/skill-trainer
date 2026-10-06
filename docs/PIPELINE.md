# Pipeline

## Complete Lifecycle

The complete Skill development lifecycle is:

1. Baseline Skill
2. Golden Dataset
3. DEV Benchmark
4. Aggregation + Gates
5. Error Analysis
6. Skill Optimization
7. Regression
8. Holdout
9. Expert Review
10. Release

Each stage must have:

- clearly defined input files;
- clearly defined output files;
- a responsible Python module;
- a Makefile target.

A stage must not exist only conceptually.

It should leave a machine-readable artifact when practical.

Prefer JSON for machine-readable outputs and Markdown as an optional human-readable report.

---

# Stage 1 — Baseline Skill

## Purpose

Validate the initial Skill before benchmarking.

## Input

```text
skill/<skill-name>/SKILL.md
```

Optional Skill resources:

```text
skill/<skill-name>/references/
skill/<skill-name>/scripts/
skill/<skill-name>/assets/
```

## Tools / Modules

Prefer the official Agent Skills validation tooling where practical, such as `skills-ref`.

Validation should check at minimum:

- Skill directory exists;
- `SKILL.md` exists;
- YAML frontmatter is valid;
- required fields such as `name` and `description`;
- supported frontmatter fields;
- naming rules;
- length limits;
- directory/name consistency.

## Output

The Skill remains in:

```text
skill/<skill-name>/
```

Optionally persist validation results:

```text
runs/validation/<skill-name>/validation.json
```

A simple exit code `0/1` is acceptable initially if persistent validation output is not yet useful.

## Makefile Target

```text
make validate
```

---

# Stage 2 — Golden Dataset

## Purpose

Define realistic, reproducible evaluation cases.

## Input / Output

The dataset itself is the primary artifact.

Detailed dataset structure is documented in `DATASET.md`.

Optionally add dataset validation:

```text
runs/dataset_validation/dev.json
runs/dataset_validation/holdout.json
```

Possible checks:

- number of cases;
- missing files;
- invalid paths;
- invalid `case.yaml`;
- missing references;
- invalid rubric definitions;
- invalid expected outputs.

## Makefile Target

```text
make load-cases
```

A future target such as `make validate-dataset` is acceptable if useful.

---

# Stage 3 — DEV Benchmark

## Purpose

Execute every DEV case on configured models/providers and preserve raw per-run results.

## Input

```text
skill/<skill-name>/SKILL.md
dataset/dev/*/case.yaml
dataset/dev/*/inputs/
config/benchmark.yaml
```

`reference/` must not be sent to the tested model.

## Main Modules

```text
case.py
provider.py
executor.py
runner.py
checks.py
evaluator.py
benchmark.py
```

## Run Layout

```text
runs/dev/
└── <case-id>/
    └── <safe-model-name>/
        ├── run-01/
        │   ├── workspace/
        │   │   ├── inputs/
        │   │   └── outputs/
        │   │       ├── answer.md
        │   │       └── other generated artifacts
        │   ├── usage.json
        │   ├── checks.json
        │   ├── evaluation.json
        │   └── transcript.json        # optional
        ├── run-02/
        └── run-03/
```

## Incremental Persistence

Persist run results as soon as they are available.

Required order:

```text
generation
→ save outputs + usage
→ deterministic checks
→ save checks
→ semantic evaluator
→ save evaluation
```

If evaluator execution fails after generation succeeded, the generated output must remain available.

## Makefile Target

```text
make dev-benchmark
```

---

# Stage 4 — Aggregation + Gates

## Purpose

Aggregate raw DEV results and apply quality gates.

This stage must not call any LLM.

## Input

```text
runs/dev/**/usage.json
runs/dev/**/checks.json
runs/dev/**/evaluation.json
config/benchmark.yaml
```

Do not read `SKILL.md` or `reference/` directly unless there is a very strong reason.

## Module

```text
aggregate.py
```

## Metrics

Compute at minimum:

- number of runs;
- mean score;
- standard deviation;
- minimum;
- maximum;
- confidence interval;
- per-rubric-criterion statistics;
- deterministic checks pass rate;
- input/output token usage;
- latency;
- total/mean cost when available.

Aggregation must remain generic and task-independent.

## Gates

Support configurable quality gates such as:

- minimum mean score;
- minimum checks pass rate;
- future task-specific gates.

Thresholds must not be hardcoded in Python.

They should come from configuration.

## Output

```text
runs/dev/summary.json
```

Optional human-readable:

```text
runs/dev/summary.md
```

## Makefile Target

```text
make aggregate-dev
```

---

# Stage 5 — Error Analysis

## Purpose

Turn raw failures and low scores into structured explanations of what went wrong.

This stage should not make an additional LLM call when the failure can already be determined from existing data.

## Input

```text
runs/dev/summary.json
runs/dev/**/checks.json
runs/dev/**/evaluation.json
runs/dev/**/usage.json      # when useful
config/benchmark.yaml
```

## Module

```text
error_analysis.py
```

## Detect At Minimum

- failed gates;
- weak rubric criteria;
- failed deterministic checks;
- low-scoring runs;
- high-variance / unstable runs;
- evaluator reasoning;
- recurring failures across runs.

Error analysis must remain generic.

## Output

```text
runs/dev/error_analysis.json
```

Optional:

```text
runs/dev/error_analysis.md
```

## Makefile Target

```text
make error-analysis
```

---

# Stage 6 — Skill Optimization

## Purpose

Create an improved Skill version from benchmark evidence.

## Input

```text
skill/<skill-name>/SKILL.md
runs/dev/summary.json
runs/dev/error_analysis.json
dataset/dev/
selected failed outputs from runs/dev/     # when useful
config/*
```

Reference solutions may influence optimization through evaluator/error-analysis evidence, but the optimized Skill must not become a collection of memorized case-specific answers.

## Module

```text
optimizer.py
```

or a similarly small module if needed.

## Optimization Rules

The optimizer should identify:

- instructions that models repeatedly ignore;
- missing instructions;
- ambiguous instructions;
- unstable behavior;
- weak output requirements;
- repeated failure patterns.

Changes must generalize to the task class.

Avoid overfitting to exact DEV examples.

## Versioning Output

Preferred structure:

```text
skill_versions/
└── <skill-name>/
    ├── v0/
    │   └── SKILL.md
    └── v1/
        └── SKILL.md
```

Preserve bundled resources where relevant.

Also save:

```text
runs/optimization/<skill-name>/v1/optimization.json
```

## Makefile Target

```text
make optimize
```

---

# Stage 7 — Regression

## Purpose

Compare the previous and new Skill versions on the same DEV dataset.

This is where Skill-version comparison happens.

Do **not** use a `without_skill` baseline.

Compare only:

```text
previous Skill version
vs
new Skill version
```

## Input

```text
Skill v0
Skill v1
dataset/dev/
same benchmark configuration
same model set when practical
```

## Modules

```text
benchmark.py
aggregate.py
regression.py
```

## Output Layout

```text
runs/regression/
└── <skill-name>/
    ├── v0/
    │   ├── raw/
    │   └── summary.json
    ├── v1/
    │   ├── raw/
    │   └── summary.json
    └── regression.json
```

## `regression.json`

Include at minimum:

- total score delta;
- per-criterion delta;
- checks delta;
- variance delta;
- improved cases;
- degraded cases;
- critical regressions;
- gate verdict.

## Makefile Target

```text
make regression
```

---

# Stage 8 — Holdout

## Purpose

Evaluate the candidate Skill version on unseen cases.

Holdout cases must not be used for Skill optimization.

## Input

```text
candidate Skill version
dataset/holdout/
benchmark configuration
```

## Modules

Reuse:

```text
benchmark.py
aggregate.py
error_analysis.py
```

Add `holdout.py` only if orchestration requires it.

## Output

```text
runs/holdout/
└── <skill-name>/
    └── <version>/
        ├── raw/
        ├── summary.json
        ├── error_analysis.json
        └── holdout.json
```

## `holdout.json`

Include:

- passed / failed;
- applied gates;
- overall scores;
- failed cases;
- critical failures.

## Makefile Target

```text
make holdout
```

---

# Stage 9 — Expert Review

## Purpose

Allow a human expert to inspect actual outputs and automated evaluation evidence.

## Input

Potentially include:

```text
run outputs
checks.json
evaluation.json
summary.json
reference/
previous Skill version outputs
```

For regression review, allow side-by-side previous/new outputs when useful.

## Implementation

A minimal review exporter/viewer is sufficient initially.

It is acceptable to reuse ideas or components from Anthropic's `skills/skill-creator` eval viewer.

Do not build an unnecessarily large UI.

The data model is more important than UI polish.

## Output

```text
runs/review/<skill-name>/<version>/feedback.json
```

## Makefile Target

A target such as:

```text
make review
```

may be added when implemented.

---

# Stage 10 — Release

## Purpose

Release a Skill only after required validation is complete.

## Input

```text
candidate Skill version
validation result
regression.json
holdout.json
feedback.json
```

## Module

```text
release.py
```

## Release Conditions

A Skill should only be released when required project policy is satisfied, for example:

- Skill validation passed;
- regression passed;
- holdout passed;
- required gates passed;
- expert review approved.

## Output

```text
releases/
└── <skill-name>/
    └── v1/
        ├── SKILL.md
        ├── references/
        ├── scripts/
        ├── assets/
        └── release.json
```

Optionally produce:

```text
<skill-name>-v1.skill
```

Packaging should happen only after successful Skill validation.

## Makefile Target

```text
make release
```

---

# End-to-End Artifact Flow

```text
skill/<skill-name>/SKILL.md
        ↓
dataset/dev/
        ↓
runs/dev/**/run-XX/
        ↓
runs/dev/summary.json
        ↓
runs/dev/error_analysis.json
        ↓
skill_versions/<skill-name>/v1/
        ↓
runs/regression/.../regression.json
        ↓
runs/holdout/.../holdout.json
        ↓
runs/review/.../feedback.json
        ↓
releases/<skill-name>/v1/
```
