# Pipeline Enhancements

This document maps each of the requested "new prospects" to what was built.

## 1. Syncthing ingestion from on-prem server
- `SYNCTHING_SETUP.md`: Send Only / Receive Only topology, Watch for
  Changes + 1-hour full-scan interval, ignore patterns, atomic-write
  pattern with a worked Python example.

## 2. Local watcher → Garage S3
- `scripts/garage_uploader.py` rebuilds the snippet you provided into a
  hardened service:
  - retries with exponential backoff instead of a bare `except Exception`
  - checksum + `(name, size, mtime)` state store so restarts and Syncthing
    rescans never double-upload
  - explicit ignore of dotfiles/partial files, plus a settle delay
  - `--backfill` / `--once` modes for catching up an already-populated
    folder
  - structured logging to `data/logs/garage_uploader.log`
  - `DELETE_AFTER_UPLOAD=true` removes the local Syncthing copy only after
    Garage confirms `put_object`; previously recorded uploads are also
    cleaned up during backfill/restart

## 3. AWS S3 → SNS → SQS fan-out
- `infra/terraform/s3.tf` + `sns_sqs.tf` + `iam.tf` codify the manual
  console steps you listed: bucket, topic, queue, subscription, SQS access
  policy, and the S3 event notification — plus additions not in the
  original ask:
  - a dead-letter queue with a redrive policy (`maxReceiveCount`)
  - a CloudWatch alarm on the DLQ
  - a least-privilege EC2 instance role instead of broad permissions
- `RUNBOOK.md` section D gives the exact `aws` CLI equivalent for anyone
  who wants to provision this by hand instead of Terraform.

## 4. EC2 poller → KPI processing
- `scripts/ec2_poller.py` rebuilds your `ec2_poller.py` sketch with:
  - proper SNS envelope unwrapping (`Records` can contain more than one
    item; your example only handled the outer loop, this handles the
    S3-key URL-decoding too)
  - schema validation before computing KPIs
  - real KPI computation (mean/min/max/std/rate-of-change/threshold
    breaches per sensor) via `scripts/utils/kpi_engine.py`, instead of the
    placeholder `# insert custom KPI analysis logic here`
  - idempotency: re-delivered messages don't double-count, and a message
    is only deleted from SQS after its window file is durably written
  - writes both `ec2_queue_kpi_latest.json` (matches your requested path)
    and one `data/kpi_reports/windows/*.json` file per source object
    (matches the `data/kpi_reports/windows/*.json` line in your diagram)

## 5. KPI aggregator → hourly/daily reports → dashboard
- `scripts/kpi_aggregator.py` rolls window reports into
  `data/kpi_reports/{hourly,daily}/*.json` using the same `kpi_engine.py`
  formulas as the poller, so numbers never drift between layers.
- `dashboard/app.py`: Streamlit dashboard with Live / Windows / Hourly /
  Daily tabs, a staleness indicator, and per-sensor drill-down — matches
  the "KPI aggregator → hourly/daily reports → Streamlit dashboard" tail
  of your architecture diagram.

## Cross-cutting improvements
- One shared `config/aws_config.py` — every script reads the same env-driven
  settings instead of hard-coded endpoints/paths scattered across files.
- One shared `scripts/utils/schema.py` — every hop validates the same
  sensor-record shape, so a malformed file is rejected at the earliest
  possible point instead of silently corrupting a downstream KPI.
- One shared `scripts/utils/kpi_engine.py` — no duplicated statistics code
  between the poller and the aggregator.
- `scripts/utils/state_store.py` — atomic, crash-safe local state used by
  three different scripts instead of three different ad hoc solutions.
- systemd units for every long-running process, so the whole pipeline
  survives reboots and crashes without manual intervention.
- Tests for the uploader contract, the KPI engine, and the aggregator.
