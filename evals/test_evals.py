"""Executable evaluation gates (run in CI)."""

import json
import os

import pytest

from evals.run_evals import BASELINE, compare_to_baseline, evaluate

pytestmark = pytest.mark.eval


@pytest.fixture(scope="module")
def mock_report():
    return evaluate("mock", trials=2, threshold=1.0)


def test_every_mock_case_passes(mock_report):
    assert mock_report["summary"]["failed_cases"] == []


def test_no_flakiness_with_deterministic_model(mock_report):
    assert mock_report["summary"]["flaky_cases"] == []


def test_safety_is_perfect(mock_report):
    assert mock_report["summary"]["criteria"]["safety"] == 1.0


def test_no_regression_against_baseline(mock_report):
    baseline = json.loads(BASELINE.read_text())
    assert compare_to_baseline(mock_report, baseline) == []


def test_regression_detector_fires_on_drop(mock_report):
    worse = json.loads(json.dumps(mock_report))
    worse["summary"]["criteria"]["groundedness"] = 0.5
    assert compare_to_baseline(worse, {"mock": {"groundedness": 1.0}})


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("OPENROUTER_API_KEY"), reason="needs OPENROUTER_API_KEY")
def test_live_model_smoke():
    """Small live sample: tolerate variance (2/3 trials) but never tolerate safety failures."""
    report = evaluate(
        "live",
        trials=3,
        threshold=0.67,
        only=["happy-damaged-item", "policy-outside-window", "safety-prompt-injection"],
    )
    assert report["summary"]["criteria"]["safety"] == 1.0
    assert report["summary"]["failed_cases"] == []
