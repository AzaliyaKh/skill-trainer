# Codex Agent Instructions

## Project Goal

Extend the existing repository into a universal framework for developing, testing, evaluating, optimizing, validating, and releasing Agent Skills.

The framework should support Skills used for many kinds of non-code tasks, especially:

- technical specification generation and analysis;
- requirements analysis;
- document generation;
- PDF and DOCX workflows;
- diagram generation and analysis, including:
  - electrical schematics;
  - structural diagrams;
  - functional diagrams;
  - process diagrams;
  - flowcharts;
  - architecture diagrams;
  - other similar visual representations;
- text and file transformation workflows;
- JSON, Markdown, SVG, PDF, DOCX, and similar formats.

At the current stage, do **not** build Skills specifically for source-code generation.

However, the architecture must remain compatible with future Skills for:

- source-code generation;
- repository modification;
- project refactoring;
- unit/integration tests;
- build pipelines;
- linting;
- coding-agent workflows through Codex CLI or similar agents.

The project already contains working code. Do not rewrite it from scratch.

Before changing anything:

1. Inspect the entire existing repository.
2. Identify what already works.
3. Preserve good existing solutions.
4. Fix only what blocks universality, correctness, maintainability, or the required pipeline.
5. Implement the missing stages incrementally.
6. Avoid unnecessary abstractions and avoid creating many files without a clear need.

Keep the repository working after each meaningful change.

---

# Core Architecture Principles

The framework must remain task-agnostic.

Do not implement logic such as:

```python
if task == "technical specification":
    ...
elif task == "diagram":
    ...
```

Task-specific behavior belongs in:

- `case.yaml`;
- deterministic checks;
- rubric definitions;
- reference outputs;
- the Skill itself.

The framework should operate through stable generic contracts.

Prefer the following responsibility split:

```text
case.py
    Dataset and case.yaml loading.

provider.py
    Low-level access to LLM providers.

executor.py
    Task execution modes, for example:
    - TextExecutor
    - Codex/AgentExecutor
    - future executors

runner.py
    Orchestration of one case/run:
    - prepare isolated workspace
    - copy inputs
    - select executor
    - execute the task
    - collect outputs
    - save results incrementally

checks.py
    Deterministic validation.

evaluator.py
    Semantic evaluation using a fixed judge.
    Receives output + reference + rubric.

benchmark.py
    DEV / HOLDOUT benchmark orchestration.

aggregate.py
    Generic aggregation of run results.

error_analysis.py
    Generic error analysis.

optimizer.py
    Skill optimization.

regression.py
    Comparison of previous and new Skill versions.

holdout.py
    Holdout orchestration if a separate module is useful.

release.py
    Release validation and packaging.
```

Do not create a separate module if the behavior can cleanly fit into an existing file.

---

# Execution Model

The framework must not assume that every task returns plain text.

A run may produce:

- text;
- JSON;
- PDF;
- DOCX;
- XLSX;
- SVG;
- images;
- multiple files;
- later, a modified project workspace.

Therefore, use a workspace-oriented execution model.

A typical run should look like:

```text
run-01/
├── workspace/
│   ├── inputs/
│   └── outputs/
│       ├── answer.md
│       └── other generated artifacts
├── usage.json
├── checks.json
├── evaluation.json
└── transcript.json        # optional when available
```

Keep a clear distinction between:

- **Provider** — low-level model/API access;
- **Executor** — how the task is executed;
- **Runner** — orchestration of one run.

At minimum, support:

- text execution;
- agent execution through Codex CLI.

The architecture must allow future executors without changing the higher-level pipeline.

---

# Pipeline

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

A stage must not exist only conceptually. It should leave a machine-readable artifact when practical.

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

Example:

```json
{
  "valid": true,
  "errors": [],
  "warnings": []
}
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

## Dataset Layout

```text
dataset/
├── dev/
│   ├── case-001/
│   │   ├── case.yaml
│   │   ├── inputs/
│   │   └── reference/
│   ├── case-002/
│   │   └── ...
│   └── ...
│
└── holdout/
    └── ...
```

Each case must be isolated.

## `case.yaml`

It should support fields such as:

- `id`
- `name`
- `category`
- `severity`
- `prompt`
- `execution`
- `inputs`
- `reference`
- `expected_outputs`
- `checks`
- `rubric`

Example execution modes:

```yaml
execution:
  mode: text
```

or:

```yaml
execution:
  mode: agent
```

## `inputs/`

Contains only data that the tested model/agent is allowed to see.

Examples:

- Markdown requirements;
- PDF/DOCX documents;
- JSON;
- CSV;
- XLSX;
- images;
- folders;
- later, code repositories.

## `reference/`

Contains the expected solution, expected artifacts, or evaluator reference material.

The tested model must **never** receive `reference/`.

Reference material is used only by:

- deterministic checks;
- semantic evaluator;
- expert review.

## Output

The dataset itself is the primary output.

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

## Providers

Support multiple providers.

Existing OpenRouter and Codex CLI support should be preserved or carefully improved.

Typical approach:

- OpenRouter for standard model inference;
- Codex CLI for GPT/Codex agent execution.

Do not hardcode provider-specific behavior into higher-level benchmark logic.

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

## `workspace/inputs/`

Copy of the inputs used for that specific run.

## `workspace/outputs/`

Real task outputs.

Possible artifacts:

- text;
- DOCX;
- PDF;
- SVG;
- JSON;
- images;
- multiple files;
- later, modified project files.

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

Possible future checks include:

- required text sections;
- JSON validity;
- JSON Schema validation;
- required files;
- expected outputs;
- PDF validity;
- DOCX validity;
- SVG validity;
- document structure;
- required diagram elements;
- later:
  - pytest;
  - build;
  - lint;
  - repository validation.

Do not implement every specialized checker immediately. Build an extensible mechanism and implement a minimal useful set.

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

Do not hardcode names such as:

- correctness;
- completeness;
- clarity.

The evaluator should work with any criterion defined in `case.yaml`.

The evaluator receives:

- generated output;
- reference;
- rubric.

The tested model does not receive the reference.

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

It must not know what a PDF, DOCX, diagram, or codebase specifically is.

Domain-specific failures should already be represented by generic check records:

```json
{
  "name": "...",
  "passed": false,
  "category": "...",
  "severity": "...",
  "evidence": "..."
}
```

## Output

```text
runs/dev/error_analysis.json
```

Optional:

```text
runs/dev/error_analysis.md
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

The intended artifact flow is:

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

---

# Configuration

External runtime settings belong in configuration files, preferably:

```text
config/benchmark.yaml
```

or similarly scoped config files if separation becomes necessary.

Do not scatter configuration constants throughout Python code.

Configuration should cover at minimum:

- dataset path;
- result path;
- models;
- providers;
- runs per case;
- generation settings;
- judge model/settings;
- aggregation gates;
- error-analysis thresholds.

---

# OpenRouter / Environment Requirements

Do not hardcode API keys.

Read OpenRouter credentials from:

- environment variables;
- `.env`.

Example:

```text
OPENROUTER_API_KEY=...
```

`.env` must be excluded from Git.

Temporary provider failures must be handled gracefully.

Retry with backoff for transient errors such as:

- 429;
- 502;
- 503;
- 504.

Do not hide provider failures.

If the API returns an error payload, expose a useful diagnostic message.

A temporary provider failure should not destroy already completed run results.

---

# Makefile Requirements

Normal project workflows must be run through `Makefile`.

Use the project virtual environment.

Do not require users to manually type long `python -m ...` commands for normal usage.

Expected targets include approximately:

```text
make install
make validate
make load-cases
make dev-benchmark
make aggregate-dev
make error-analysis
make optimize
make regression
make holdout
make review
make release
```

Not every target has to be implemented immediately if the corresponding stage is not ready yet, but the command structure should remain consistent.

---

# Validation and Testing Requirements

Before using paid or remote model calls to test framework logic, prefer local deterministic tests.

After meaningful changes:

1. check Python imports;
2. check syntax;
3. run smoke tests;
4. run dataset loading;
5. test deterministic checks;
6. only then use real API calls when necessary.

Do not waste model/API calls to test simple Python control flow.

When possible, provide a `FakeProvider` or smoke-test path for validating runner/evaluator logic without network access.

---

# Existing Code

Assume the repository already contains working or partially working versions of:

```text
case.py
provider.py
runner.py
checks.py
evaluator.py
benchmark.py
aggregate.py
error_analysis.py
Makefile
```

Do not assume existing code is wrong simply because it can be written differently.

Prefer:

- small, justified refactors;
- stable interfaces;
- reuse of working logic.

Avoid a large rewrite.

---

# Initial Task for Codex

Before implementing changes, inspect the repository and produce a short implementation plan that states:

1. what already exists and should remain;
2. what architectural issues must be corrected;
3. which pipeline stages are missing or incomplete;
4. which existing files need modification;
5. which new files, if any, are genuinely required;
6. which Makefile targets will be added or changed.

Then implement incrementally.

Do not perform a full rewrite in one pass.

Keep the repository in a runnable state after each stage.

The final result should be a minimal, clean, extensible framework for developing Agent Skills that works now for:

- technical specifications;
- requirements;
- documents;
- diagrams;
- text outputs;
- file outputs;

and remains architecturally ready for future Skills that generate or modify program code.

## Output Language

All user-facing artifacts produced by Skills must be in Russian by default.

This includes:
- technical specifications;
- reports;
- DOCX/PDF document content;
- tables and table cell text;
- diagram labels, captions, legends, and annotations;
- explanations and final user-facing text.

Keep framework internals in English:
- Python code;
- module/class/function names;
- JSON/YAML keys;
- configuration fields;
- Makefile targets;
- logs and internal identifiers.

The output language must be configurable, not hardcoded.

Default:
`output_language: ru`

A case may override it, for example:
`output_language: en`

The runner/executor must ensure that the tested Skill receives the requested output language.