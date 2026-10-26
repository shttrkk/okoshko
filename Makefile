.PHONY: up down seed pipeline eval smoke demo-check test coverage lint audit check demo-video demo-voice contracts
PYTHON ?= .venv/bin/python
REGION ?= pipeline/regions/sochi.yaml
PIPELINE_ARGS ?= --compact-geo
VIDEO ?= demo/okno-demo.mp4
VOICED_VIDEO ?= demo/okno-demo-voiced.mp4

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
coverage:
	$(PYTHON) -m pytest -q --cov=backend --cov=geo --cov-report=term --cov-report=json:docs/checks/coverage.json
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
demo-voice:
	@test -n "$(VOICE)" || { echo 'Usage: make demo-voice VOICE=/path/to/voice.wav'; exit 2; }
	$(PYTHON) -m scripts.mix_voice --voice "$(VOICE)" --video "$(VIDEO)" --output "$(VOICED_VIDEO)"
contracts:
	$(PYTHON) -m scripts.export_contracts

