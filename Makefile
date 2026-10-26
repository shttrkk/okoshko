.PHONY: up down seed pipeline eval smoke test lint audit check demo-video contracts
PYTHON ?= .venv/bin/python
REGION ?= pipeline/regions/sochi.yaml

up:
	docker compose up --build -d
down:
	docker compose down
seed:
	docker compose exec app python -m backend.seed
pipeline:
	REGION=$(REGION) $(PYTHON) -m pipeline.run
eval:
	$(PYTHON) -m scripts.evaluate
smoke:
	$(PYTHON) scripts/smoke.py
test:
	$(PYTHON) -m pytest -q
lint:
	.venv/bin/ruff check backend geo pipeline scripts tests
	cd frontend && npm run lint
audit:
	$(PYTHON) -m scripts.audit
check: lint test smoke
demo-video:
	cd frontend && npm run demo-video
contracts:
	$(PYTHON) -m scripts.export_contracts

