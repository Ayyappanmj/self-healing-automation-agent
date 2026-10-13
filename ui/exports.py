"""Downloadable logs and reports built from stored runs. Nothing here invents numbers."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from ui.metrics import compute_metrics, fmt_ms, fmt_percent

CSV_COLUMNS = ["timestamp", "test_name", "action", "action_name", "original_locator", "failure_type",
               "failure_reason", "selected_locator", "strategy", "confidence", "healing_reason", "status",
               "validation_valid", "retry_success", "execution_time_ms"]


def record_row(rec: dict) -> dict:
    return {
        "timestamp": rec.get("timestamp", ""), "test_name": rec.get("test_name", ""),
        "action": rec.get("action", ""), "action_name": rec.get("action_name", ""),
        "original_locator": rec.get("original_locator", ""), "failure_type": rec.get("failure_type", ""),
        "failure_reason": rec.get("failure_reason", ""), "selected_locator": rec.get("selected_locator", ""),
        "strategy": rec.get("strategy", ""), "confidence": rec.get("confidence", 0),
        "healing_reason": rec.get("healing_reason", ""), "status": rec.get("status", ""),
        "validation_valid": rec.get("validation_result", {}).get("valid", ""),
        "retry_success": rec.get("retry_result", {}).get("success", ""),
        "execution_time_ms": rec.get("execution_time_ms", 0),
    }


def records_to_csv(records: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for rec in records:
        writer.writerow(record_row(rec))
    return out.getvalue()


def records_to_json(records: list[dict]) -> str:
    return json.dumps(records, indent=2, default=str)


def build_markdown_report(results: list[dict], records: list[dict], now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if not results:
        return f"# Self-Healing Automation Report\n\nGenerated {now:%Y-%m-%d %H:%M UTC}\n\nNo runs recorded yet.\n"
    m = compute_metrics(results, records)
    lines = ["# Self-Healing Automation Report", "", f"Generated {now:%Y-%m-%d %H:%M UTC}", "", "## Summary", "",
             f"- Tests run: {m['total_tests']} (successful {m['successful_tests']}, failed {m['failed_tests']}, "
             f"manual review {m['manual_review_tests']})",
             f"- Healing attempts: {m['healing_attempts']}, healed {m['healed_locators']} "
             f"(success rate {fmt_percent(m['healing_success_rate'])})",
             f"- Average healing time (healed only): {fmt_ms(m['avg_healing_ms'])}", "",
             "## Tests", "", "| Test | Status | Duration | Healed steps |", "|---|---|---|---|"]
    for r in results:
        healed = sum(1 for s in r.get("steps", []) if s.get("status") == "healed")
        lines.append(f"| {r.get('name', '')} | {r.get('status', '')} | {fmt_ms(r.get('duration_ms'))} | {healed} |")
    lines += ["", "## Healing events", ""]
    if not records:
        lines.append("None.")
    for rec in records:
        lines.append(f"- `{rec.get('original_locator', '')}` -> `{rec.get('selected_locator') or '-'}` "
                     f"({rec.get('strategy') or '-'}, confidence {rec.get('confidence', 0):.2f}): "
                     f"{rec.get('status', '')} in {fmt_ms(rec.get('execution_time_ms'))} [{rec.get('test_name', '')}]")
    return "\n".join(lines) + "\n"
