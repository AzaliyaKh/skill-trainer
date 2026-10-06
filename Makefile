SYSTEM_PYTHON := python3
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
SKILLS_REF := $(VENV)/bin/skills-ref
CONFIG ?= config/benchmark.yaml
VERSION ?=
FROM ?=
TO ?=
SKILL ?= skill/requirements-analysis
# Empty uses dataset.path from CONFIG; DATASET remains an explicit override.
DATASET ?=
DATASET_REPORT ?= runs/dataset_validation/dev.json
# Remove run results, generated requirements-analysis versions, and caches.
# Override when generated artifacts use different project-local roots.
CLEAN_DIRS ?= runs runs_bad skill/versions/requirements-analysis build dist .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage

.PHONY: help work setup install validate skill-info load-cases validate-dataset benchmark aggregate error-analysis optimize regression next-version holdout review release test smoke check clean distclean optimize-review approve optimize-apply dev-benchmark aggregate-dev version-benchmark aggregate-version optimize-version

help:
	@echo "Lifecycle:"
	@echo "  make benchmark VERSION=v0"
	@echo "  make aggregate VERSION=v0"
	@echo "  make error-analysis VERSION=v0"
	@echo "  make optimize VERSION=v0"
	@echo "  make regression FROM=v0 TO=v1"

.PHONY: preflight
preflight:
	@$(PYTHON) -c "from src.benchmark.storage import load_config; from src.benchmark.provider import make_provider; c=load_config('$(CONFIG)'); p=make_provider('codex', c['providers']['codex']); m=c['models'][0]['model']; print('Checking configured provider/model:', m, flush=True); r=p.generate(model=m, system='Provider readiness check.', prompt='Return OK.', temperature=0, max_tokens=16); print(r.output)"

work:
	@$(MAKE) validate load-cases test
	@$(MAKE) benchmark VERSION=previous
	@$(MAKE) aggregate VERSION=previous
	@$(MAKE) error-analysis VERSION=previous
	@$(MAKE) optimize VERSION=previous
	@echo "Run the versioned lifecycle shown by make help."

setup:
	$(SYSTEM_PYTHON) -m venv $(VENV)

install: setup
	$(PIP) install -r requirements.txt

validate:
	@$(PYTHON) -m src.benchmark.checks --skill $(SKILL)

skill-info:
	@$(SKILLS_REF) read-properties $(SKILL)

load-cases validate-dataset:
	@$(PYTHON) -m src.benchmark.case --config $(CONFIG) $(if $(DATASET),--dataset $(DATASET)) --output $(DATASET_REPORT)

benchmark:
	@test -n "$(VERSION)" || (echo "VERSION is required, for example: make benchmark VERSION=v0"; exit 2)
	@$(PYTHON) -m src.benchmark.benchmark --version $(VERSION) --reuse --config $(CONFIG)

aggregate:
	@test -n "$(VERSION)" || (echo "VERSION is required, for example: make aggregate VERSION=v0"; exit 2)
	@$(PYTHON) -m src.benchmark.aggregate --version $(VERSION) --config $(CONFIG)

error-analysis:
	@test -n "$(VERSION)" || (echo "VERSION is required, for example: make error-analysis VERSION=v0"; exit 2)
	@$(PYTHON) -m src.benchmark.error_analysis --version $(VERSION) --config $(CONFIG)

optimize:
	@test -n "$(VERSION)" || (echo "VERSION is required, for example: make optimize VERSION=v0"; exit 2)
	@$(PYTHON) -m src.benchmark.optimizer --evidence-version $(VERSION) --config $(CONFIG)

optimize-review:
	@$(PYTHON) -m src.benchmark.optimizer --review --config $(CONFIG)

approve:
	@$(PYTHON) -m src.benchmark.optimizer --approve --config $(CONFIG)

optimize-apply:
	@$(PYTHON) -m src.benchmark.optimizer --apply --config $(CONFIG)

regression:
	@test -n "$(FROM)" -a -n "$(TO)" || (echo "FROM and TO are required, for example: make regression FROM=v0 TO=v1"; exit 2)
	@$(PYTHON) -m src.benchmark.regression --from-version $(FROM) --to-version $(TO) --config $(CONFIG)

# Compatibility aliases; all delegate to the parameterized implementations above.
dev-benchmark:
	@$(MAKE) benchmark VERSION=previous CONFIG=$(CONFIG)

aggregate-dev:
	@$(MAKE) benchmark VERSION=previous CONFIG=$(CONFIG)
	@$(MAKE) aggregate VERSION=previous CONFIG=$(CONFIG)

version-benchmark:
	@$(MAKE) benchmark VERSION=$(VERSION) CONFIG=$(CONFIG)

aggregate-version:
	@$(MAKE) benchmark VERSION=$(VERSION) CONFIG=$(CONFIG)
	@$(MAKE) aggregate VERSION=$(VERSION) CONFIG=$(CONFIG)

optimize-version:
	@$(MAKE) optimize VERSION=$(VERSION) CONFIG=$(CONFIG)

next-version:
	@$(PYTHON) -m src.benchmark.storage --advance-lifecycle --config $(CONFIG)

holdout:
	@$(PYTHON) -m src.benchmark.benchmark --holdout --config $(CONFIG) $(if $(VERSION),--version $(VERSION))

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
