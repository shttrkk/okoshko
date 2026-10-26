.PHONY: up down seed pipeline eval smoke demo-check test lint audit check demo-video contracts
PYTHON ?= .venv/bin/python
REGION ?= pipeline/regions/sochi.yaml
PIPELINE_ARGS ?= --compact-geo

up:
	docker compose up --build -d
down:
	docker compose down
seed:
	docker compose exec app python -m backend.seed
pipeline:
	docker build --platform linux/amd64 -f Dockerfile.pipeline -t okno-pipeline .
	docker run --rm --platform linux/amd64 --user "$$(id -u):$$(id -g)" --mount "type=bind,src=$$PWD/data,dst=/app/data" okno-pipeline --region "$(REGION)" $(PIPELINE_ARGS)
eval:
	$(PYTHON) -m scripts.evaluate
smoke:
	$(PYTHON) scripts/smoke.py
	$(MAKE) demo-check
demo-check:
	$(PYTHON) scripts/demo_check.py
test:
	$(PYTHON) -m pytest -q
lint:
	.venv/bin/ruff check backend geo pipeline scripts tests
	cd frontend && npm run lint
audit:
	$(PYTHON) -m scripts.audit
check: lint test audit smoke
demo-video:
	npm ci --prefix demo
	npx --prefix demo playwright install chromium
	npm run --prefix demo record
contracts:
	$(PYTHON) -m scripts.export_contracts

