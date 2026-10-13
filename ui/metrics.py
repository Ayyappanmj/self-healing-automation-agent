"""Dashboard numbers. Every value is computed from stored runs; with no data they are None (shown as a dash)."""
from __future__ import annotations

from statistics import mean

SUCCESS = {"passed", "healed"}


def compute_metrics(results: list[dict], records: list[dict]) -> dict:
    healed = [r for r in records if r.get("status") == "healed"]
    attempts = len(records)
    return {
        "total_tests": len(results),
        "successful_tests": sum(1 for r in results if r.get("status") in SUCCESS),
        "failed_tests": sum(1 for r in results if r.get("status") == "failed"),
        "manual_review_tests": sum(1 for r in results if r.get("status") == "manual_review"),
        "healing_attempts": attempts,
        "healed_locators": len(healed),
        "healing_success_rate": (len(healed) / attempts) if attempts else None,
        "avg_healing_ms": mean(r["execution_time_ms"] for r in healed) if healed else None,
    }


def recent_events(records: list[dict], limit: int = 10) -> list[dict]:
    return sorted(records, key=lambda r: r.get("timestamp", ""), reverse=True)[:limit]


def fmt_percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def fmt_ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f} ms" if value < 1000 else f"{value / 1000:.1f} s"
