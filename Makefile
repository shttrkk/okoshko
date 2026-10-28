.PHONY: up down seed pipeline connectivity-data eval parser-eval group-parser-eval ranking-eval smoke demo-check test coverage lint audit check demo-video demo-voice contracts snapshots
PYTHON ?= .venv/bin/python
REGION ?= pipeline/regions/sochi.yaml
PIPELINE_ARGS ?= --compact-geo
TZ_PIPELINE_ARGS ?= --stage all --dry-run
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
connectivity-data:
	$(PYTHON) -m pipeline.collect_connectivity --data-dir data
parser-eval:
	$(PYTHON) -m scripts.parser_eval --output data/eval/parser_results.json
group-parser-eval:
	$(PYTHON) -m scripts.group_parser_eval --output data/eval/group_parser_results.json
ranking-eval:
	$(PYTHON) -m scripts.ranking_eval --output data/eval/ranking_results.json
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
# Optional offline step: views of the open-data 3D model for hotels without a
# photo. Needs a running stand (make up), Node with frontend deps and Pillow.
snapshots:
	node scripts/render_building_snapshots.mjs $(or $(BASE),http://localhost:8080)
	python3 -m scripts.build_snapshot_index

.PHONY: tz-osm-data tz-derived-data tz-climate-data tz-stage2-data tz-data tz-pipeline
# Incremental supplemental data only: never rebuilds the original seed/facts.
tz-pipeline:
	docker build --platform linux/amd64 -f Dockerfile.pipeline -t okno-pipeline .
	docker run --rm --platform linux/amd64 --user "$$(id -u):$$(id -g)" --mount "type=bind,src=$$PWD/data,dst=/app/data" --entrypoint python okno-pipeline -m pipeline.tz_rebuild $(TZ_PIPELINE_ARGS)
tz-osm-data:
	$(PYTHON) -m pipeline.fetch_osm_extra
	$(PYTHON) -m pipeline.fetch_tz_areas
	$(PYTHON) -m pipeline.fetch_tz_osm_metadata
	$(PYTHON) -m pipeline.fetch_tz_fisht
	$(PYTHON) -m pipeline.fetch_tz_places
tz-climate-data:
	$(PYTHON) -m pipeline.fetch_tz_climate
tz-derived-data:
	$(PYTHON) -m pipeline.fetch_tz_osm_facts
	$(PYTHON) -m pipeline.fetch_tz_construction
	$(PYTHON) -m pipeline.fetch_tz_routes
	$(PYTHON) -m pipeline.fetch_tz_history
	$(PYTHON) -m pipeline.fetch_tz_landmarks
	$(PYTHON) -m pipeline.fetch_tz_transfer_diagnostics
	$(PYTHON) -m pipeline.fetch_tz_location
	$(PYTHON) -m pipeline.fetch_tz_metadata
	$(PYTHON) -m pipeline.fetch_tz_supplements
	$(PYTHON) -m pipeline.fetch_tz_catalog
	$(PYTHON) -m pipeline.fetch_tz_manifest
tz-stage2-data:
	$(PYTHON) -m pipeline.fetch_tz_catalog
	$(PYTHON) -m pipeline.fetch_tz_manifest
tz-data: tz-osm-data tz-climate-data tz-derived-data

.PHONY: parser-eval-legacy group-parser-eval-legacy
# Keep the previous authored contract measurable after the R3 stroller policy.
parser-eval-legacy:
	$(PYTHON) -m scripts.parser_eval --gold data/eval/queries_gold.jsonl
group-parser-eval-legacy:
	$(PYTHON) -m scripts.group_parser_eval --gold data/eval/group_gold.jsonl
