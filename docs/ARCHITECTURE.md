# Architecture

## Project Goal

The framework is intended for developing, testing, evaluating, optimizing, validating, and releasing Agent Skills.

It should support many kinds of non-code tasks, especially:

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

The architecture must remain compatible with future Skills for:

- source-code generation;
- repository modification;
- project refactoring;
- unit/integration tests;
- build pipelines;
- linting;
- coding-agent workflows through Codex CLI or similar agents.

The project already contains working code. Do not rewrite it from scratch.

## Core Architecture Principles

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

## Responsibility Split

Preferred responsibility split:

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

## Execution Model

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

Typical run:

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

## Providers

Support multiple providers.

Existing OpenRouter and Codex CLI support should be preserved or carefully improved.

Typical approach:

- OpenRouter for standard model inference;
- Codex CLI for GPT/Codex agent execution.

Do not hardcode provider-specific behavior into higher-level benchmark logic.

## Deterministic Checks

Use a generic check schema.

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

Do not implement every specialized checker immediately.

Build an extensible mechanism and implement a minimal useful set.

## Semantic Evaluation

The evaluator receives:

- generated output;
- reference;
- rubric.

The tested model does not receive the reference.

Rubric criterion names must be fully dynamic.

Do not hardcode names such as:

- correctness;
- completeness;
- clarity.

The evaluator should work with any criterion defined in `case.yaml`.

## Generic Aggregation

Aggregation must remain generic and task-independent.

The aggregation stage must not call any LLM.

Thresholds for gates must not be hardcoded in Python.

They should come from configuration.

## Generic Error Analysis

Error analysis must remain generic.

It must not know what a PDF, DOCX, diagram, or codebase specifically is.

Domain-specific failures should already be represented by generic check records.

This stage should not make an additional LLM call when the failure can already be determined from existing data.

## Skill Optimization

The optimizer should identify:

- instructions that models repeatedly ignore;
- missing instructions;
- ambiguous instructions;
- unstable behavior;
- weak output requirements;
- repeated failure patterns.

Changes must generalize to the task class.

Avoid overfitting to exact DEV examples.

Reference solutions may influence optimization through evaluator/error-analysis evidence, but the optimized Skill must not become a collection of memorized case-specific answers.

## Regression

Skill-version comparison happens in Regression.

Do **not** use a `without_skill` baseline.

Compare only:

```text
previous Skill version
vs
new Skill version
```

Use the same DEV dataset, the same benchmark configuration, and the same model set when practical.

## Holdout

Holdout cases are unseen cases used to evaluate the candidate Skill version.

Holdout cases must not be used for Skill optimization.

## Release

A Skill should only be released when required project policy is satisfied, for example:

- Skill validation passed;
- regression passed;
- holdout passed;
- required gates passed;
- expert review approved.

Packaging should happen only after successful Skill validation.
