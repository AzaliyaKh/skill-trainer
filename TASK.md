# Task: Normalize dataset requirements and case files

Read and follow `AGENTS.md`.

Review the existing dataset cases. Do not rewrite the framework unless a dataset inconsistency reveals a small required fix.

## Goal

Normalize every case so that:
- `inputs/requirements.md` is a clean task specification derived from the case source/reference materials;
- `case.yaml` follows the project dataset contract;
- references remain hidden from the tested model;
- generated user-facing artifacts are in Russian by default.

Process all applicable `case-*` directories under the repository dataset structure (DEV and HOLDOUT if present).

## requirements.md rules

For every case, inspect the existing inputs and reference files and rewrite `inputs/requirements.md` when needed.

`requirements.md` must:
- be written in Russian;
- describe the task goal and the required result;
- contain only information supported by the case materials;
- list the required document/output sections;
- briefly explain what each required section must contain;
- include all important technical facts, numbers, units, constraints, interfaces, standards, and mandatory requirements needed to solve the task;
- explicitly identify which sections are required when an input GOST/standard contains sections that are not applicable;
- define the required output format and language;
- state constraints such as when applicable;
- be concise enough to serve as input to a model, but complete enough to reproduce the intended result.

`requirements.md` must NOT:
- copy the reference answer verbatim as a ready-made solution;
- expose `reference/` paths or tell the tested model that a reference answer exists;
- include evaluator scores, rubric points, gates, or benchmark internals;
- include unsupported facts;
- include model/provider-specific instructions;
- include unnecessary GOST sections that are not required for the specific task.

The reference file is the source of truth for deriving expected content, but it must remain hidden during benchmark execution.

## case.yaml rules

Review each existing `case.yaml` and modify it only if it does not conform.

It should describe:
- `id`
- `name`
- `category`
- `severity`
- `output_language` (`ru` by default)
- `prompt`
- `execution.mode`
- `inputs`
- `reference`
- `expected_outputs`
- `checks`
- `rubric`

Requirements:
- all paths must exist and be relative to the case directory;
- `inputs` must include only files visible to the tested model;
- `reference` must include only hidden evaluator/reference files;
- never copy reference files into inputs;
- use `execution.mode: agent` for tasks that must read/write binary artifacts such as DOCX/PDF or create output files;
- use `text` only when plain-text execution is sufficient;
- `expected_outputs` must describe the real required artifact(s);
- deterministic checks must be feasible and relevant;
- rubric criteria must be specific to the task and must not rely on globally hardcoded criterion names;
- rubric points should have a clear total (prefer 100 unless the existing project contract requires otherwise);
- the case prompt should describe the action, while detailed domain requirements belong in `requirements.md`;
- avoid duplicating the entire `requirements.md` inside `case.yaml`.

For technical-specification cases, prefer rubric dimensions such as:
- requirements coverage;
- technical accuracy;
- structure/completeness;
- document quality.

Use different criteria when another task type requires them.

## GOST / standards

If a case contains a GOST or another standard in `inputs/`:
- keep the standard as an input if the tested model is expected to use it;
- do not ask the model to blindly reproduce every section from the standard;
- use `requirements.md` to state which sections are required for this case and briefly describe the expected content;
- preserve requirements supported by the source materials without inventing missing ones.

## Safety against data leakage

Verify that no tested-model prompt, input file, runner-generated prompt, or workspace input contains:
- the reference answer;
- reference file contents;
- evaluator reasoning;
- benchmark scores.

## Validation

After editing:
1. validate all YAML files;
2. verify every referenced input/reference path exists;
3. verify every case has `inputs/requirements.md`;
4. ensure reference files were not modified unless strictly necessary for path/name consistency;
5. run the existing dataset loader/validator via Makefile;
6. run local deterministic/smoke checks only; do not spend API calls for this task.

## Output

Modify the dataset files in place.

At the end, print a concise summary:
- cases reviewed;
- `requirements.md` files rewritten;
- `case.yaml` files changed;
- validation errors found/fixed;
- remaining issues requiring human input.