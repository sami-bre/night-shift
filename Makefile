.PHONY: venv sprites cluster-up demo serve test lint screenshots clean

PY = env -u PYTHONPATH .venv/bin/python

venv:
	python3 -m venv .venv && env -u PYTHONPATH .venv/bin/pip install -q -r requirements.txt

sprites: venv
	$(PY) tools/gen_sprites.py

## k3d cluster with the (really) broken payments-api — idempotent
cluster-up:
	bash k3d/bootstrap.sh

cluster-down:
	k3d cluster delete night-shift

## One deterministic mock-LLM run through the real pipeline (no API key needed)
demo: venv
	$(PY) -c "import asyncio, sys; sys.path.insert(0, '.'); \
from nightshift.runner import execute_run; \
run = asyncio.run(execute_run(mode='mock')); \
[print(f\"{c['seq']:>3} {c['city_event']:<16} {c['why']}\") for c in run.read_city_events()]; \
print(f'proof panel: file://{run.dir}/../../.. in server mode; city events: {len(run.read_city_events())}')"

serve: venv
	$(PY) -m uvicorn nightshift.app:app --host 127.0.0.1 --port 8808

test: venv
	env -u PYTHONPATH .venv/bin/python -m pytest tests/ -q

lint: venv
	env -u PYTHONPATH .venv/bin/ruff check nightshift tools tests

## Headless-browser screenshots + hero GIF for the README (needs make serve running)
screenshots: venv
	$(PY) tools/screenshots.py

clean:
	rm -rf runs .pytest_cache web/assets/.DS_Store
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true