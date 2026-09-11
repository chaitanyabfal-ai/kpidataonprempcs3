"""
Sensor payload schema validation, shared by s3_uploader.py, garage_sync.py,
and ec2_poller.py so every hop agrees on what a "valid" sensor record is.

Expected shape (JSON list of records, or CSV with the same columns):

    [
        {"timestamp": "2026-09-11T08:00:00Z", "sensor_id": "line1-temp", "value": 72.4},
        ...
    ]

Extra fields are allowed and passed through untouched; only the required
fields below are enforced.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass

REQUIRED_FIELDS = ("timestamp", "sensor_id", "value")


@dataclass
class ValidationResult:
    ok: bool
    record_count: int
    errors: list[str]


def _validate_records(records: list[dict]) -> ValidationResult:
    errors: list[str] = []
    if not isinstance(records, list):
        return ValidationResult(ok=False, record_count=0, errors=["payload is not a list of records"])

    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            errors.append(f"record[{i}] is not an object")
            continue
        for field_name in REQUIRED_FIELDS:
            if field_name not in rec:
                errors.append(f"record[{i}] missing required field '{field_name}'")
        if "value" in rec:
            try:
                float(rec["value"])
            except (TypeError, ValueError):
                errors.append(f"record[{i}] field 'value' is not numeric: {rec.get('value')!r}")

    return ValidationResult(ok=not errors, record_count=len(records), errors=errors)


def validate_json_bytes(raw: bytes) -> tuple[list[dict], ValidationResult]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [], ValidationResult(ok=False, record_count=0, errors=[f"invalid JSON: {exc}"])
    if isinstance(parsed, dict):
        parsed = [parsed]
    return parsed, _validate_records(parsed)


def validate_csv_bytes(raw: bytes) -> tuple[list[dict], ValidationResult]:
    try:
        text = raw.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        records = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        return [], ValidationResult(ok=False, record_count=0, errors=[f"invalid CSV: {exc}"])
    return records, _validate_records(records)


def validate_bytes(raw: bytes, filename: str) -> tuple[list[dict], ValidationResult]:
    if filename.lower().endswith(".csv"):
        return validate_csv_bytes(raw)
    return validate_json_bytes(raw)
