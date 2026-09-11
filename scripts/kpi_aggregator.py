#!/usr/bin/env python3
"""
kpi_aggregator.py — rolls the per-file window reports written by
ec2_poller.py up into hourly and daily summaries for the dashboard.

    data/kpi_reports/windows/*.json
        -> data/kpi_reports/hourly/<YYYY-MM-DD-HH>.json
        -> data/kpi_reports/daily/<YYYY-MM-DD>.json

Run once (`--once`, e.g. from cron every few minutes) or continuously
(`--watch`, rebuilding on an interval).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.aws_config import KPI_DAILY_DIR, KPI_HOURLY_DIR, KPI_WINDOWS_DIR, LOG_DIR  # noqa: E402
from scripts.utils.kpi_engine import KPIResult, SensorStats, merge_kpi_results  # noqa: E402
from scripts.utils.logging_config import get_logger  # noqa: E402

logger = get_logger("kpi_aggregator", LOG_DIR / "kpi_aggregator.log")


def _load_window_reports() -> list[dict]:
    reports = []
    for path in sorted(KPI_WINDOWS_DIR.glob("*.json")):
        try:
            with open(path) as f:
                reports.append(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping unreadable window report %s: %s", path, exc)
    return reports


def _kpi_result_from_window(window: dict) -> KPIResult:
    result = KPIResult(total_records=window.get("total_records", 0), invalid_records=window.get("invalid_records", 0))
    for sensor_id, s in window.get("sensors", {}).items():
        stats = SensorStats(
            sensor_id=sensor_id,
            count=s.get("count", 0),
            total=(s.get("mean") or 0) * s.get("count", 0),
            min_value=s.get("min", float("inf")) if s.get("min") is not None else float("inf"),
            max_value=s.get("max", float("-inf")) if s.get("max") is not None else float("-inf"),
            sum_sq=((s.get("std") or 0) ** 2 + (s.get("mean") or 0) ** 2) * s.get("count", 0),
            threshold_breaches=s.get("threshold_breaches", 0),
        )
        result.sensors[sensor_id] = stats
    return result


def _bucket_key(processed_at: str, granularity: str) -> str | None:
    try:
        dt = datetime.fromisoformat(processed_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if granularity == "hourly":
        return dt.strftime("%Y-%m-%d-%H")
    return dt.strftime("%Y-%m-%d")


def _write_rollup(out_dir: Path, bucket: str, result: KPIResult, window_count: int, granularity: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "granularity": granularity,
        "bucket": bucket,
        "window_count": window_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **result.to_dict(),
    }
    path = out_dir / f"{bucket}.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    tmp.rename(path)


def aggregate_once() -> tuple[int, int]:
    windows = _load_window_reports()
    if not windows:
        logger.info("No window reports found yet — nothing to aggregate.")
        return 0, 0

    hourly_groups: dict[str, list[dict]] = defaultdict(list)
    daily_groups: dict[str, list[dict]] = defaultdict(list)

    for w in windows:
        processed_at = w.get("processed_at")
        if not processed_at:
            continue
        hour_key = _bucket_key(processed_at, "hourly")
        day_key = _bucket_key(processed_at, "daily")
        if hour_key:
            hourly_groups[hour_key].append(w)
        if day_key:
            daily_groups[day_key].append(w)

    for bucket, group in hourly_groups.items():
        merged = merge_kpi_results([_kpi_result_from_window(w) for w in group])
        _write_rollup(KPI_HOURLY_DIR, bucket, merged, len(group), "hourly")

    for bucket, group in daily_groups.items():
        merged = merge_kpi_results([_kpi_result_from_window(w) for w in group])
        _write_rollup(KPI_DAILY_DIR, bucket, merged, len(group), "daily")

    logger.info(
        "Aggregated %d window report(s) into %d hourly and %d daily bucket(s)",
        len(windows), len(hourly_groups), len(daily_groups),
    )
    return len(hourly_groups), len(daily_groups)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Re-aggregate on an interval instead of exiting.")
    parser.add_argument("--interval", type=float, default=60.0, help="Seconds between re-aggregation passes in --watch mode.")
    args = parser.parse_args()

    if args.watch:
        logger.info("Starting continuous aggregation (interval=%.1fs)", args.interval)
        try:
            while True:
                aggregate_once()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("Shutting down on Ctrl-C")
    else:
        aggregate_once()


if __name__ == "__main__":
    main()
