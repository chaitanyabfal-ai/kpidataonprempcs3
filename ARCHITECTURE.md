# Architecture

## Hops and failure modes

### 1. On-prem sensor → Syncthing → PC
- **Transport**: Syncthing, LAN or Tailscale between on-prem and PC.
- **Failure mode**: on-prem process crashes mid-write → mitigated by
  atomic temp-file + rename (see `SYNCTHING_SETUP.md`).
- **Failure mode**: PC offline for a while → Syncthing queues changes on the
  server (Send Only) and catches up on reconnect; no data loss as long as
  the server's local disk doesn't fill up first.

### 2. PC → Garage S3 (`garage_uploader.py`)
- **Transport**: `watchdog` filesystem events → boto3 S3 API over Tailscale.
- **Idempotency**: a local SQLite/JSON state file (`scripts/utils/state_store.py`)
  records `(filename, size, mtime) -> uploaded` so a restart doesn't
  re-upload everything, and so a Syncthing rescan doesn't cause duplicate
  uploads.
- **Failure mode**: Garage or Tailscale unreachable → uploads are retried
  with exponential backoff and re-queued; files are only deleted locally
  (if `DELETE_AFTER_UPLOAD=true`) after a confirmed successful `put_object`.

### 3. Garage S3 → AWS S3 (`garage_sync.py`)
- Polls Garage for new/changed objects, downloads, validates against the
  expected sensor schema (`scripts/utils/schema.py`), and uploads to AWS S3
  under `raw-sensor-data/<YYYY>/<MM>/<DD>/<file>`.
- Tracks synced keys in a local state file so `--once` runs (e.g. from cron)
  are cheap and idempotent.

### 4. AWS S3 → SNS → SQS
- S3 `ObjectCreated:*` on `raw-sensor-data/` publishes to an SNS topic.
- SNS fans out to an SQS queue (allows adding more subscribers later —
  e.g. a second consumer for anomaly detection — without touching S3 config).
- The SQS queue has a **dead-letter queue** with `maxReceiveCount = 5`: a
  message that fails processing five times is moved to the DLQ instead of
  looping forever, and is visible for alerting.

### 5. EC2 poller (`ec2_poller.py`)
- Long-polls SQS (`WaitTimeSeconds=20`) in batches of up to 10 messages.
- Unwraps the SNS envelope, extracts every S3 record (a single S3 event can
  contain multiple records).
- Fetches the object, runs it through `scripts/utils/kpi_engine.py`
  (mean/min/max/std/rate-of-change per numeric field, plus threshold-breach
  counts if `KPI_THRESHOLDS` is configured).
- Writes one JSON "window" report per processed file into
  `data/kpi_reports/windows/`, and refreshes
  `data/kpi_reports/ec2_queue_kpi_latest.json` as a cheap "last processed"
  pointer for health checks.
- Deletes the SQS message only after the window file is durably written —
  if the process crashes mid-processing, the message becomes visible again
  after the visibility timeout and is retried (this is why processing must
  be idempotent: re-processing the same S3 key just overwrites the same
  window file).

### 6. KPI aggregator (`kpi_aggregator.py`)
- Periodically (cron or `--watch`) reads all window files, groups them by
  hour and by day, and writes rollups to `data/kpi_reports/hourly/` and
  `data/kpi_reports/daily/`.
- Uses the same `kpi_engine.py` so hourly/daily numbers are computed with
  identical formulas to the per-file windows (no drift between layers).
- Flags gaps (an expected window with no data) so the dashboard can show
  staleness instead of silently plotting a flat line.

### 7. Dashboard (`dashboard/app.py`)
- Streamlit app with tabs for Live (latest window), Windows, Hourly, Daily.
- Shows a staleness badge based on the timestamp in
  `ec2_queue_kpi_latest.json` vs. wall clock.
- Runs as a localhost-only systemd service on EC2; access via SSM port
  forwarding, never expose 8501 publicly.

## Security notes

- Garage and AWS credentials are read from environment variables /
  `.env` (see `.env.example`), never hard-coded.
- The EC2 instance role is scoped to `s3:GetObject` on the raw bucket,
  `sqs:ReceiveMessage`/`DeleteMessage`/`GetQueueAttributes` on the one
  queue, and nothing else (`infra/terraform/iam.tf`).
- See `SECURITY.md` for the full policy.
