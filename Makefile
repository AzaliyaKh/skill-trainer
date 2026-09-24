SYSTEM_PYTHON := python3
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
SKILLS_REF := $(VENV)/bin/skills-ref
CONFIG ?= config/benchmark.yaml
SKILL ?= skill/requirements-analysis
DATASET ?= dataset/example/dev
DATASET_REPORT ?= runs/dataset_validation/dev.json
# Override when generated artifacts use different project-local roots.
CLEAN_DIRS ?= runs skill_versions releases build dist .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage

.PHONY: work setup install validate skill-info load-cases validate-dataset dev-benchmark aggregate-dev error-analysis optimize regression holdout review release test smoke check clean distclean optimize-review optimize-apply

work:
	@$(MAKE) validate load-cases test
	@$(MAKE) optimize
	@echo "Review the optimization, then run make optimize-apply, regression, holdout, review and release."

setup:
	$(SYSTEM_PYTHON) -m venv $(VENV)

install: setup
	$(PIP) install -r requirements.txt

validate:
	@$(PYTHON) -m src.benchmark.checks --skill $(SKILL)

skill-info:
	@$(SKILLS_REF) read-properties $(SKILL)

load-cases validate-dataset:
	@$(PYTHON) -m src.benchmark.case --dataset $(DATASET) --output $(DATASET_REPORT)

dev-benchmark:
	@$(PYTHON) -m src.benchmark.benchmark --reuse --config $(CONFIG)

aggregate-dev: dev-benchmark
	@$(PYTHON) -m src.benchmark.aggregate --config $(CONFIG)

error-analysis: aggregate-dev
	@$(PYTHON) -m src.benchmark.error_analysis --config $(CONFIG)

optimize: error-analysis
	@$(PYTHON) -m src.benchmark.optimizer --config $(CONFIG)

optimize-review:
	@$(PYTHON) -m src.benchmark.optimizer --review --config $(CONFIG)

optimize-apply:
	@$(PYTHON) -m src.benchmark.optimizer --apply --config $(CONFIG)

regression:
	@$(PYTHON) -m src.benchmark.regression --config $(CONFIG)

holdout:
	@$(PYTHON) -m src.benchmark.benchmark --holdout --config $(CONFIG)

review:
	@$(PYTHON) -m src.benchmark.review --config $(CONFIG)

release:
	@$(PYTHON) -m src.benchmark.release --config $(CONFIG)

check:
	@$(PYTHON) -m compileall -q src tests
	@$(PYTHON) -c "import importlib, pkgutil, src.benchmark; [importlib.import_module(m.name) for m in pkgutil.iter_modules(src.benchmark.__path__, 'src.benchmark.')]"

test smoke: check
	@$(PYTHON) -m unittest discover -s tests -v

clean:
	@$(SYSTEM_PYTHON) -m src.benchmark.storage --venv "$(VENV)" --artifacts $(CLEAN_DIRS)

distclean:
	@$(SYSTEM_PYTHON) -m src.benchmark.storage --distclean --venv "$(VENV)" --artifacts $(CLEAN_DIRS)
