"""
Centralized, environment-driven configuration for the whole pipeline.

Every script in scripts/ imports from here instead of reading os.environ
directly, so there is exactly one place that defines defaults and validates
required settings.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # python-dotenv is optional; env vars can also be exported directly.
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
LOG_DIR = Path(os.getenv("LOG_DIR", DATA_DIR / "logs"))
KPI_REPORTS_DIR = Path(os.getenv("KPI_REPORTS_DIR", DATA_DIR / "kpi_reports"))
KPI_WINDOWS_DIR = KPI_REPORTS_DIR / "windows"
KPI_HOURLY_DIR = KPI_REPORTS_DIR / "hourly"
KPI_DAILY_DIR = KPI_REPORTS_DIR / "daily"

for _d in (DATA_DIR, LOG_DIR, KPI_REPORTS_DIR, KPI_WINDOWS_DIR, KPI_HOURLY_DIR, KPI_DAILY_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: list[str] | None = None) -> list[str]:
    val = os.getenv(name)
    if not val:
        return default or []
    return [item.strip() for item in val.split(",") if item.strip()]


@dataclass(frozen=True)
class SyncthingConfig:
    """Local Syncthing folder that garage_uploader.py watches."""

    watch_dir: str = os.getenv("SYNC_WATCH_DIR", str(REPO_ROOT / "data" / "incoming_csvs"))
    ignore_suffixes: tuple[str, ...] = field(
        default_factory=lambda: tuple(_env_list("SYNC_IGNORE_SUFFIXES", [".tmp", ".part", ".swp"]))
    )
    settle_seconds: float = _env_float("SYNC_SETTLE_SECONDS", 0.5)
    delete_after_upload: bool = _env_bool("DELETE_AFTER_UPLOAD", False)


@dataclass(frozen=True)
class GarageConfig:
    """Garage S3 (reached over Tailscale)."""

    endpoint_url: str = os.getenv("GARAGE_ENDPOINT_URL", "http://100.78.2.20:3900")
    access_key_id: str = os.getenv("GARAGE_ACCESS_KEY_ID", "")
    secret_access_key: str = os.getenv("GARAGE_SECRET_ACCESS_KEY", "")
    bucket: str = os.getenv("GARAGE_BUCKET", "sensor-data-staging")
    region: str = os.getenv("GARAGE_REGION", "garage")

    def is_configured(self) -> bool:
        return bool(self.access_key_id and self.secret_access_key)


@dataclass(frozen=True)
class AWSConfig:
    """AWS S3 / SNS / SQS."""

    region: str = os.getenv("AWS_REGION", "us-east-1")
    raw_bucket: str = os.getenv("AWS_RAW_BUCKET", "raw-sensor-data-bucket")
    raw_prefix: str = os.getenv("AWS_RAW_PREFIX", "raw-sensor-data/")
    sns_topic_arn: str = os.getenv("SNS_TOPIC_ARN", "")
    sqs_queue_url: str = os.getenv("SQS_QUEUE_URL", "")
    sqs_dlq_url: str = os.getenv("SQS_DLQ_URL", "")
    max_messages_per_poll: int = _env_int("SQS_MAX_MESSAGES", 10)
    wait_time_seconds: int = _env_int("SQS_WAIT_TIME_SECONDS", 20)
    visibility_timeout: int = _env_int("SQS_VISIBILITY_TIMEOUT", 60)


@dataclass(frozen=True)
class KPIConfig:
    """Thresholds and windowing for KPI computation."""

    # Comma-separated "field:min:max" triples, e.g. "temperature:-10:80,pressure:900:1100"
    thresholds: dict = field(default_factory=dict)
    hourly_align_minutes: int = _env_int("KPI_HOURLY_ALIGN_MINUTES", 60)

    def __post_init__(self):
        raw = os.getenv("KPI_THRESHOLDS", "")
        parsed = {}
        for triple in _env_list("KPI_THRESHOLDS_RAW_PLACEHOLDER"):
            pass
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(":")
            if len(parts) == 3:
                field_name, lo, hi = parts
                try:
                    parsed[field_name] = (float(lo), float(hi))
                except ValueError:
                    continue
        object.__setattr__(self, "thresholds", parsed)


SYNCTHING = SyncthingConfig()
GARAGE = GarageConfig()
AWS = AWSConfig()
KPI = KPIConfig()


def summarize() -> str:
    """Human-readable, secret-free summary used by preflight scripts and logs."""
    return (
        f"Syncthing watch_dir={SYNCTHING.watch_dir}\n"
        f"Garage endpoint={GARAGE.endpoint_url} bucket={GARAGE.bucket} "
        f"configured={GARAGE.is_configured()}\n"
        f"AWS region={AWS.region} raw_bucket={AWS.raw_bucket} "
        f"sqs_queue_url={'set' if AWS.sqs_queue_url else 'unset'} "
        f"sns_topic_arn={'set' if AWS.sns_topic_arn else 'unset'}\n"
        f"KPI thresholds={KPI.thresholds or '(none configured)'}"
    )


if __name__ == "__main__":
    print(summarize())
