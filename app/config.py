"""Application settings, read once from the environment (.env supported)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The fixture "world" is frozen at this instant so policy windows and staleness
# checks are deterministic for tests, evals and demos (see README: Assumptions).
DEFAULT_REFERENCE_TIME = "2026-10-30T12:00:00+00:00"


def _parse_api_keys(raw: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for pair in filter(None, (p.strip() for p in raw.split(","))):
        key, _, role = pair.partition(":")
        keys[key.strip()] = role.strip()
    return keys


@dataclass(frozen=True)
class Settings:
    llm_mode: str = "mock"
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    llm_timeout_seconds: float = 30.0
    tool_timeout_seconds: float = 2.0
    max_agent_steps: int = 8
    stale_after_hours: int = 24
    max_auto_refund: float = 500.0
    reference_time: datetime = field(
        default_factory=lambda: datetime.fromisoformat(DEFAULT_REFERENCE_TIME)
    )
    api_keys: dict[str, str] = field(
        default_factory=lambda: {
            "dev-requester-key": "requester",
            "dev-operator-key": "operator",
        }
    )
    data_dir: Path = ROOT / "data" / "fixtures"
    trace_dir: Path = ROOT / "reports" / "traces"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        ref = env.get("APP_REFERENCE_TIME")
        keys = env.get("API_KEYS")
        return cls(
            llm_mode=env.get("LLM_MODE", "mock"),
            openrouter_api_key=env.get("OPENROUTER_API_KEY", ""),
            openrouter_model=env.get("OPENROUTER_MODEL", cls.openrouter_model),
            llm_timeout_seconds=float(env.get("LLM_TIMEOUT_SECONDS", 30)),
            reference_time=datetime.fromisoformat(ref)
            if ref
            else datetime.fromisoformat(DEFAULT_REFERENCE_TIME).astimezone(UTC),
            **({"api_keys": _parse_api_keys(keys)} if keys else {}),
        )
