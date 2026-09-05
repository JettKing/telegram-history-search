#!/usr/bin/env python3
"""Analyze admin_audit rows exported from Cloudflare D1.

Accepted input formats:
  1. JSON array: [{...}, {...}]
  2. Wrangler-style JSON: {"results": [{...}, {...}]}
  3. NDJSON: one audit row per line

Example:
  python3 tools/analyze_admin_audit.py audit.json --since-hours 168
  python3 tools/analyze_admin_audit.py audit.json --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HIGH_RISK = {
    "delete_channel": "删除频道",
    "delete_auth_code": "删除授权码",
    "disable_auth_code": "停用授权码",
    "clear_runs": "清空采集日志",
}
MEDIUM_RISK = {
    "disable_channel": "停用频道",
    "enable_channel": "启用频道",
    "retry_run": "重试采集任务",
}
LOW_RISK = {
    "sync_channel": "同步频道",
    "create_auth_code": "创建授权码",
}


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc)


def load_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("results", data.get("rows", data))
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    except json.JSONDecodeError:
        pass
    rows = []
    for line_number, line in enumerate(text.splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"warning: skipped invalid JSON on line {line_number}: {exc}", file=sys.stderr)
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def actor_key(row: dict[str, Any]) -> str:
    role = row.get("role") or "unknown"
    code = row.get("auth_code_id")
    return f"{role}:auth_code:{code}" if code not in (None, "") else f"{role}:ADMIN_TOKEN"


def risk(action: str) -> str:
    if action in HIGH_RISK:
        return "high"
    if action in MEDIUM_RISK:
        return "medium"
    if action in LOW_RISK:
        return "low"
    return "unknown"


def analyze(rows: list[dict[str, Any]], since_hours: int, burst_count: int, burst_minutes: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=since_hours)
    parsed = []
    for row in rows:
        dt = parse_time(row.get("created_at"))
        if not dt or dt < cutoff:
            continue
        item = dict(row)
        item["_dt"] = dt
        item["_risk"] = risk(str(row.get("action") or ""))
        item["_actor"] = actor_key(row)
        parsed.append(item)
    parsed.sort(key=lambda item: item["_dt"], reverse=True)

    high = [item for item in parsed if item["_risk"] == "high"]
    by_action = Counter(item.get("action") or "unknown" for item in parsed)
    by_actor = Counter(item["_actor"] for item in high)
    anomalies = []

    for item in high:
        start = item["_dt"] - timedelta(minutes=burst_minutes)
        window = [x for x in high if x["_actor"] == item["_actor"] and start <= x["_dt"] <= item["_dt"]]
        if len(window) >= burst_count:
            anomalies.append({
                "type": "high_risk_burst",
                "actor": item["_actor"],
                "window_end": item["_dt"].isoformat(),
                "count": len(window),
                "actions": [x.get("action") for x in window],
            })
    for item in parsed:
        if item.get("role") == "admin" and not item.get("auth_code_id"):
            anomalies.append({"type": "admin_without_auth_code", "id": item.get("id"), "action": item.get("action")})

    unique_anomalies = []
    seen = set()
    for anomaly in anomalies:
        key = json.dumps(anomaly, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique_anomalies.append(anomaly)

    events = []
    for item in parsed:
        event = {k: v for k, v in item.items() if not k.startswith("_")}
        event["risk"] = item["_risk"]
        events.append(event)
    return {
        "period": {"since_hours": since_hours, "from_utc": cutoff.isoformat(), "to_utc": now.isoformat()},
        "total_events": len(parsed),
        "risk_counts": dict(Counter(item["_risk"] for item in parsed)),
        "action_counts": dict(by_action),
        "high_risk_by_actor": dict(by_actor),
        "anomalies": unique_anomalies,
        "events": events,
    }


def print_report(report: dict[str, Any]) -> None:
    print("Admin audit analysis")
    print("=" * 24)
    print(f"Period: last {report['period']['since_hours']} hours")
    print(f"Events: {report['total_events']}")
    print(f"Risk counts: {report['risk_counts'] or 'none'}")
    print("\nActions:")
    for action, count in sorted(report["action_counts"].items(), key=lambda pair: (-pair[1], pair[0])):
        print(f"  {count:>5}  {action}  {HIGH_RISK.get(action, MEDIUM_RISK.get(action, LOW_RISK.get(action, '未知操作')))}")
    print("\nHigh-risk actors:")
    for actor, count in sorted(report["high_risk_by_actor"].items(), key=lambda pair: (-pair[1], pair[0])):
        print(f"  {count:>5}  {actor}")
    print("\nAnomalies:")
    if not report["anomalies"]:
        print("  none")
    else:
        for anomaly in report["anomalies"]:
            print("  " + json.dumps(anomaly, ensure_ascii=False))
    print("\nRecent events:")
    for event in report["events"][:20]:
        print(f"  {event.get('created_at','?')}  [{event['risk']}] {event.get('action','?')}  {event.get('resource','?')}#{event.get('resource_id','')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze admin_audit exports")
    parser.add_argument("input", type=Path, help="JSON/NDJSON export file")
    parser.add_argument("--since-hours", type=int, default=168, help="analysis window, default: 168")
    parser.add_argument("--burst-count", type=int, default=3, help="high-risk events needed for burst alert")
    parser.add_argument("--burst-minutes", type=int, default=10, help="burst window, default: 10")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit machine-readable JSON")
    args = parser.parse_args()
    if args.since_hours <= 0 or args.burst_count < 2 or args.burst_minutes <= 0:
        parser.error("since-hours and burst-minutes must be positive; burst-count must be at least 2")
    report = analyze(load_rows(args.input), args.since_hours, args.burst_count, args.burst_minutes)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
