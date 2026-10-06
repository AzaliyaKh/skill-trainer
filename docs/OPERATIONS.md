# Operations

## Configuration

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

## OpenRouter / Environment Requirements

Do not hardcode API keys.

Read OpenRouter credentials from:

- environment variables;
- `.env`.

Example:

```text
OPENROUTER_API_KEY=...
```

`.env` must be excluded from Git.

## Provider Failures

Temporary provider failures must be handled gracefully.

Retry with backoff for transient errors such as:

- 429;
- 502;
- 503;
- 504.

Do not hide provider failures.

If the API returns an error payload, expose a useful diagnostic message.

A temporary provider failure should not destroy already completed run results.

## Makefile Requirements

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

## Validation and Testing Requirements

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

## Existing Code

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

## Cleanup

`make clean` removes run results (`runs/` and legacy `runs_bad/`),
generated versions in `skill/versions/requirements-analysis/`, build artifacts,
and Python/test caches. It preserves the virtual environment, datasets, baseline
Skills, other Skill versions, configuration, and releases.

`make distclean` performs the same cleanup and also removes `.venv/`
(or the environment specified by `VENV`). Run `make install` to restore it.

For custom result roots configured outside `runs/`, explicitly list them:

```sh
make clean CLEAN_DIRS="runs runs_bad custom-results build dist"
```

`CLEAN_DIRS` replaces the default list. Both commands reject paths outside the
project and paths overlapping protected source directories before deleting files.
Cleanup uses system Python so it also works after the virtual environment is removed.
