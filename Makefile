.PHONY: install run test e2e evals evals-live lint all

install:
	uv sync && uv run playwright install chromium

run:
	uv run uvicorn app.main:create_app --factory --reload --port 8000

lint:
	uv run ruff check . && uv run ruff format --check .

test:
	uv run pytest tests -m "not e2e" --cov=app --cov-report=term

e2e:
	uv run pytest tests/e2e --screenshot only-on-failure --output reports/playwright

evals:
	uv run python -m evals.run_evals --mode mock --trials 3

evals-live:
	uv run python -m evals.run_evals --mode live --trials 3 --threshold 0.67

all: lint test e2e evals
