# Dataset

## Golden Dataset Purpose

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

## Expected Outputs

A run may produce:

- text;
- JSON;
- PDF;
- DOCX;
- XLSX;
- SVG;
- images;
- multiple files;
- later, modified project files.

The framework must not assume that every case produces plain text.

## Rubric

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

## Checks

Checks must be extensible.

Possible checks include:

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

## DEV

DEV cases are used for:

- DEV Benchmark;
- Aggregation + Gates;
- Error Analysis;
- Skill Optimization;
- Regression.

Skill optimization should generalize to the task class.

Avoid overfitting to exact DEV examples.

## HOLDOUT

Holdout cases are unseen evaluation cases.

Holdout cases must not be used for Skill optimization.

## Dataset Validation

The dataset itself is the primary output of the Golden Dataset stage.

Optionally add validation results:

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

Normal loading target:

```text
make load-cases
```

A future target such as:

```text
make validate-dataset
```

is acceptable if useful.

## Output Language

Default:

```text
output_language: ru
```

A case may override it, for example:

```text
output_language: en
```

The runner/executor must ensure that the tested Skill receives the requested output language.
