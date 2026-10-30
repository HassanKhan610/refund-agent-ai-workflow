"""Run the evaluation dataset against the agent, N trials per case, and write reports.

    uv run python -m evals.run_evals                      # mock model, 3 trials
    uv run python -m evals.run_evals --mode live --trials 3 --threshold 0.67
    uv run python -m evals.run_evals --update-baseline    # accept current scores

Exit code 1 if any case falls below threshold or a criterion regresses vs baseline.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.llm import LLMClient, MockLLM, build_llm
from app.config import ROOT, Settings
from app.main import build_container
from app.state.models import RunStatus
from app.tools.payment_gateway import GatewayMode
from evals.scorers import CRITERIA, score

DATASET = Path(__file__).parent / "dataset.jsonl"
BASELINE = Path(__file__).parent / "baseline.json"
REGRESSION_TOLERANCE = 0.05


def load_cases(mode: str, only: list[str] | None = None) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    cases = [c for c in cases if mode in c.get("modes", ["mock", "live"])]
    return [c for c in cases if not only or c["id"] in only]


def make_llm(mode: str, settings: Settings, fault: str | None) -> LLMClient:
    if mode == "mock" or fault:  # model-side faults are simulated deterministically
        return MockLLM(fault=fault)
    return build_llm(replace(settings, llm_mode="live"))


def run_case(case: dict[str, Any], mode: str, settings: Settings) -> dict[str, Any]:
    setup = case.get("setup", {})
    container = build_container(settings, make_llm(mode, settings, setup.get("llm_fault")))
    container.gateway.mode = GatewayMode(setup.get("gateway", "ok"))
    container.gateway.hang_seconds = settings.tool_timeout_seconds * 3
    started = time.perf_counter()
    run = container.agent.start(case["input"], user="eval")
    if run.status == RunStatus.AWAITING_APPROVAL and setup.get("decision"):
        run = container.agent.decide(
            run.id,
            approve=setup["decision"] == "approve",
            operator="eval-operator",
            reason=setup.get("reason", ""),
        )
    scores = score(run, case)
    applicable = [v for v in scores.values() if v is not None]
    return {
        "passed": all(v == 1.0 for v in applicable),
        "scores": scores,
        "status": run.status,
        "final_answer": run.final_answer,
        "tools": [s.data["tool"] for s in run.steps if s.kind == "tool_call"],
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "trace": run.model_dump(mode="json", exclude={"messages"}),
    }


def evaluate(mode: str, trials: int, threshold: float, only: list[str] | None = None) -> dict:
    settings = replace(Settings.from_env(), tool_timeout_seconds=0.3)
    results = []
    for case in load_cases(mode, only):
        runs = [run_case(case, mode, settings) for _ in range(trials)]
        pass_rate = sum(r["passed"] for r in runs) / trials
        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "pass_rate": pass_rate,
                "passed": pass_rate >= threshold,
                "criteria": {c: _mean([r["scores"][c] for r in runs]) for c in CRITERIA},
                "flaky": 0 < pass_rate < 1,
                "latency_ms_p50": statistics.median(r["latency_ms"] for r in runs),
                "trials": [{k: v for k, v in r.items() if k != "trace"} for r in runs],
                "failed_traces": [r["trace"] for r in runs if not r["passed"]][:1],
            }
        )

    summary = {c: _mean([r["criteria"][c] for r in results]) for c in CRITERIA}
    by_category: dict[str, list[float]] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r["pass_rate"])
    return {
        "meta": {
            "mode": mode,
            "trials": trials,
            "threshold": threshold,
            "model": "mock" if mode == "mock" else settings.openrouter_model,
            "generated_at": datetime.now(UTC).isoformat(),
            "cases": len(results),
        },
        "summary": {
            "case_pass_rate": _mean([float(r["passed"]) for r in results]),
            "criteria": summary,
            "by_category": {k: round(statistics.mean(v), 3) for k, v in by_category.items()},
            "flaky_cases": [r["id"] for r in results if r["flaky"]],
            "failed_cases": [r["id"] for r in results if not r["passed"]],
        },
        "cases": results,
    }


def compare_to_baseline(report: dict, baseline: dict) -> list[str]:
    base = baseline.get(report["meta"]["mode"], {})
    regressions = []
    for criterion, value in report["summary"]["criteria"].items():
        old = base.get(criterion)
        if old is not None and value is not None and value < old - REGRESSION_TOLERANCE:
            regressions.append(f"{criterion}: {old:.2f} -> {value:.2f}")
    return regressions


def write_reports(report: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"eval_{report['meta']['mode']}.json").write_text(json.dumps(report, indent=2))
    (out_dir / f"eval_{report['meta']['mode']}.md").write_text(to_markdown(report))


def to_markdown(report: dict) -> str:
    m, s = report["meta"], report["summary"]
    lines = [
        f"# Evaluation report — {m['mode']} ({m['model']})",
        "",
        f"{m['cases']} cases × {m['trials']} trials · threshold {m['threshold']} · "
        f"{m['generated_at']}",
        "",
        f"**Case pass rate: {s['case_pass_rate']:.0%}**  ",
        f"Failed: {', '.join(s['failed_cases']) or 'none'} · "
        f"Flaky: {', '.join(s['flaky_cases']) or 'none'}",
        "",
        "| Criterion | Score |",
        "|---|---|",
        *[f"| {k} | {_fmt(v)} |" for k, v in s["criteria"].items()],
        "",
        "| Case | Category | Pass rate | " + " | ".join(CRITERIA) + " |",
        "|---|---|---|" + "---|" * len(CRITERIA),
        *[
            f"| {r['id']} | {r['category']} | {r['pass_rate']:.0%} | "
            + " | ".join(_fmt(r["criteria"][c]) for c in CRITERIA)
            + " |"
            for r in report["cases"]
        ],
    ]
    if report.get("regressions"):
        lines += ["", "## Regressions vs baseline", *[f"- {x}" for x in report["regressions"]]]
    return "\n".join(lines) + "\n"


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.mean(vals), 3) if vals else None


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["mock", "live"], default="mock")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="min per-case pass rate (default 1.0 mock, 0.67 live)",
    )
    p.add_argument("--cases", nargs="*")
    p.add_argument("--out", type=Path, default=ROOT / "reports")
    p.add_argument("--update-baseline", action="store_true")
    args = p.parse_args(argv)
    threshold = (
        args.threshold if args.threshold is not None else (1.0 if args.mode == "mock" else 0.67)
    )

    report = evaluate(args.mode, args.trials, threshold, args.cases)
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    report["regressions"] = compare_to_baseline(report, baseline)
    write_reports(report, args.out)
    print(to_markdown(report))

    if args.update_baseline:
        baseline[args.mode] = report["summary"]["criteria"]
        BASELINE.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"baseline updated: {BASELINE}")
        return 0
    return 1 if report["summary"]["failed_cases"] or report["regressions"] else 0


if __name__ == "__main__":
    sys.exit(main())
