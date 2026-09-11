#!/usr/bin/env python3
"""
Streamlit dashboard over data/kpi_reports/{windows,hourly,daily} and
ec2_queue_kpi_latest.json.

    streamlit run dashboard/app.py

On EC2, run as a localhost-only systemd service (systemd/kpi-dashboard.service)
and reach it via SSM port forwarding rather than exposing 8501 publicly.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.aws_config import KPI_DAILY_DIR, KPI_HOURLY_DIR, KPI_REPORTS_DIR, KPI_WINDOWS_DIR  # noqa: E402

st.set_page_config(page_title="Sensor KPI Dashboard", layout="wide")


def load_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def load_dir(dir_path: Path) -> list[dict]:
    reports = []
    if not dir_path.exists():
        return reports
    for path in sorted(dir_path.glob("*.json")):
        data = load_json(path)
        if data:
            data["_file"] = path.name
            reports.append(data)
    return reports


def sensors_to_dataframe(report: dict) -> pd.DataFrame:
    sensors = report.get("sensors", {})
    if not sensors:
        return pd.DataFrame()
    rows = []
    for sensor_id, stats in sensors.items():
        row = {"sensor_id": sensor_id, **stats}
        rows.append(row)
    return pd.DataFrame(rows).set_index("sensor_id")


st.title("📈 Sensor KPI Dashboard")
st.caption("Syncthing → Garage S3 → AWS S3 → SNS/SQS → EC2 KPI Poller → Dashboard")

latest_path = KPI_REPORTS_DIR / "ec2_queue_kpi_latest.json"
latest = load_json(latest_path)

col1, col2, col3, col4 = st.columns(4)
if latest:
    age_seconds = time.time() - latest.get("timestamp", 0)
    stale = age_seconds > 300  # 5 minutes with no new data looks stale for high-frequency sensors
    col1.metric("Last object processed", latest.get("latest_key", "—"))
    col2.metric("Total records (last file)", latest.get("total_records", "—"))
    col3.metric("Sensors (last file)", latest.get("sensor_count", "—"))
    col4.metric("Data age", f"{age_seconds:,.0f}s", delta="STALE" if stale else "fresh", delta_color="inverse" if stale else "normal")
    if stale:
        st.warning("No new data processed in over 5 minutes — check garage_uploader.py, garage_sync.py, and the EC2 poller.")
else:
    st.info("No reports yet. Once garage_uploader.py, garage_sync.py, and ec2_poller.py have run, data will show up here.")

resolution = st.radio("Report resolution", ["Live (last window)", "Windows", "Hourly", "Daily"], horizontal=True)

if resolution == "Live (last window)":
    if latest and latest.get("window_file"):
        window_path = KPI_WINDOWS_DIR / latest["window_file"]
        report = load_json(window_path)
    else:
        windows = load_dir(KPI_WINDOWS_DIR)
        report = windows[-1] if windows else None

    if report:
        st.subheader(f"Window: {report.get('source_key', report.get('_file', ''))}")
        st.dataframe(sensors_to_dataframe(report), use_container_width=True)
    else:
        st.info("No window reports yet.")

elif resolution == "Windows":
    windows = load_dir(KPI_WINDOWS_DIR)
    if not windows:
        st.info("No window reports yet.")
    else:
        options = [w.get("source_key", w["_file"]) for w in windows]
        idx = st.selectbox("Choose a window", range(len(options)), format_func=lambda i: options[i], index=len(options) - 1)
        st.dataframe(sensors_to_dataframe(windows[idx]), use_container_width=True)

elif resolution == "Hourly":
    hourly = load_dir(KPI_HOURLY_DIR)
    if not hourly:
        st.info("No hourly rollups yet — run scripts/kpi_aggregator.py.")
    else:
        options = [h["bucket"] for h in hourly]
        idx = st.selectbox("Choose an hour", range(len(options)), format_func=lambda i: options[i], index=len(options) - 1)
        st.caption(f"{hourly[idx]['window_count']} window(s) aggregated")
        st.dataframe(sensors_to_dataframe(hourly[idx]), use_container_width=True)

else:  # Daily
    daily = load_dir(KPI_DAILY_DIR)
    if not daily:
        st.info("No daily rollups yet — run scripts/kpi_aggregator.py.")
    else:
        options = [d["bucket"] for d in daily]
        idx = st.selectbox("Choose a day", range(len(options)), format_func=lambda i: options[i], index=len(options) - 1)
        st.caption(f"{daily[idx]['window_count']} window(s) aggregated")
        df = sensors_to_dataframe(daily[idx])
        st.dataframe(df, use_container_width=True)
        if not df.empty and "mean" in df.columns:
            st.bar_chart(df["mean"])

st.divider()
st.caption("Data directories: " + str(KPI_REPORTS_DIR))
