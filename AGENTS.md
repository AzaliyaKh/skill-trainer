# Codex Agent Instructions

## Project Goal

Extend the existing repository into a universal framework for developing, testing, evaluating, optimizing, validating, and releasing Agent Skills.

The framework should support Skills used for many kinds of non-code tasks, especially:

- technical specification generation and analysis;
- requirements analysis;
- document generation;
- PDF and DOCX workflows;
- diagram generation and analysis;
- text and file transformation workflows;
- JSON, Markdown, SVG, PDF, DOCX, and similar formats.

At the current stage, do **not** build Skills specifically for source-code generation.

The architecture must remain compatible with future Skills for:

- source-code generation;
- repository modification;
- project refactoring;
- unit/integration tests;
- build pipelines;
- linting;
- coding-agent workflows through Codex CLI or similar agents.

The project already contains working code. Do not rewrite it from scratch.

## Core Rules

The framework must remain task-agnostic.

Do not hardcode task-specific behavior into the core framework.

Task-specific behavior belongs in:

- `case.yaml`;
- deterministic checks;
- rubric definitions;
- reference outputs;
- the Skill itself.

Keep these responsibilities separate:

- **Provider** — low-level model/API access;
- **Executor** — how the task is executed;
- **Runner** — orchestration of one run;
- **Checks** — deterministic validation;
- **Evaluator** — semantic evaluation;
- **Benchmark** — DEV/HOLDOUT orchestration;
- **Aggregation** — aggregation and quality gates;
- **Error Analysis** — structured analysis of failures;
- **Optimizer** — Skill optimization;
- **Regression** — comparison of Skill versions;
- **Release** — release validation and packaging.

Do not create a separate module if the behavior can cleanly fit into an existing file.

The framework must not assume that every task returns plain text.

Use a workspace-oriented execution model that supports text, JSON, PDF, DOCX, XLSX, SVG, images, multiple files, and future modified project workspaces.

Never expose `reference/` to the tested model.

Holdout cases must not be used for Skill optimization.

Persist run results incrementally:

```text
generation
→ save outputs + usage
→ deterministic checks
→ save checks
→ semantic evaluator
→ save evaluation
```

If evaluator execution fails after generation succeeded, the generated output must remain available.

## Development Rules

Before changing existing code:

1. inspect the files relevant to the requested change;
2. identify what already works;
3. preserve good existing solutions;
4. fix only what blocks universality, correctness, maintainability, or the required pipeline;
5. implement missing behavior incrementally;
6. avoid unnecessary abstractions and avoid creating many files without a clear need.

Keep the repository working after each meaningful change.

Prefer:

- small, justified refactors;
- stable interfaces;
- reuse of working logic.

Avoid a large rewrite.

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

## Pipeline

The Skill lifecycle is:

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

Normal project workflows must be available through the `Makefile`.

## Documentation

Read only the documentation relevant to the current task:

- `docs/ARCHITECTURE.md` — architecture, module responsibilities, execution model.
- `docs/PIPELINE.md` — all lifecycle stages, their inputs, outputs, modules, and Makefile targets.
- `docs/DATASET.md` — dataset layout, `case.yaml`, `inputs/`, `reference/`, DEV/HOLDOUT isolation.
- `docs/SCHEMAS.md` — JSON/YAML result formats described by the project specification.
- `docs/OPERATIONS.md` — configuration, providers, environment, retries, Makefile, validation.

Do not read all documentation by default.

## Configuration and Providers

External runtime settings belong in configuration files, preferably `config/benchmark.yaml`.

Do not scatter configuration constants throughout Python code.

Do not hardcode API keys.

Read OpenRouter credentials from environment variables or `.env`.

`.env` must be excluded from Git.

Temporary provider failures must be handled gracefully.

Retry with backoff for transient errors such as:

- 429;
- 502;
- 503;
- 504.

Do not hide provider failures.

A temporary provider failure must not destroy already completed run results.

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

```text
output_language: ru
```

A case may override it, for example:

```text
output_language: en
```

The runner/executor must ensure that the tested Skill receives the requested output language.
