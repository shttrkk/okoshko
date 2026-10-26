.PHONY: up down seed pipeline eval smoke test lint demo-video contracts
PYTHON ?= .venv/bin/python

up:
	docker compose up --build -d
down:
	docker compose down
seed:
	docker compose exec app python -m backend.seed
pipeline:
	$(PYTHON) -m pipeline.run
eval:
	$(PYTHON) -m scripts.evaluate
smoke:
	$(PYTHON) scripts/smoke.py
test:
	$(PYTHON) -m pytest -q
lint:
	.venv/bin/ruff check backend geo pipeline scripts tests
	cd frontend && npm run lint
demo-video:
	cd frontend && npm run demo-video
contracts:
	$(PYTHON) -m scripts.export_contracts

