PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: install lint typecheck test eval demo graph check docker-build docker-demo clean

install:  ## create the virtualenv and install the package with dev tools
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

typecheck:
	$(PY) -m mypy

test:
	$(PY) -m pytest --cov --cov-report=term --cov-fail-under=80

eval:  ## offline evaluation on the 21 historical cases, writes eval/results/
	$(PY) -m sra.cli eval --runs 5 --noise 0.2

demo:  ## offline demo: recorded Steam/Roblox responses + scripted model
	$(PY) -m sra.cli demo

graph:
	$(PY) -m sra.cli graph

check: lint typecheck test eval

docker-build:
	docker build -t steam-roblox-agents .

docker-demo: docker-build
	docker run --rm steam-roblox-agents demo

clean:
	rm -rf runs .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
