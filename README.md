# ILDS Sensor KPI Pipeline — Syncthing → Garage S3 → AWS S3 → SQS → EC2 → Dashboard

Complete, end-to-end data pipeline for ingesting **high-frequency sensor data**
from an on-prem server, staging it through Garage S3 over Tailscale, landing it
in AWS S3, fanning it out through SNS/SQS, computing KPIs on EC2, and
visualizing the results in a Streamlit dashboard.

This is a rebuild of the original `kpidatareporttf` project with a new,
production-grade ingestion front end (Syncthing + local uploader) bolted onto
the existing Garage → AWS → EC2 → dashboard pipeline, plus reliability,
observability, and testing improvements throughout.

## Architecture

```
On-Prem Server (sensor writer)
    │  writes <name>.tmp -> atomic rename -> <name>.json/.csv
    ▼
Syncthing (Send Only)  ───────────────►  Syncthing (Receive Only, your PC)
                                              │
                                              ▼
                                   scripts/garage_uploader.py
                                   (watchdog folder watcher)
                                              │  boto3 PUT (Tailscale)
                                              ▼
                                   Garage S3  (sensor-data-staging)
                                              │
                                   scripts/garage_sync.py
                                   (continuous polling, checksum de-dupe)
                                              │  boto3 PUT
                                              ▼
                                   AWS S3  (raw-sensor-data/)
                                              │
                                   S3 ObjectCreated:* event
                                              ▼
                                        SNS Topic
                                              ▼
                                        SQS Queue  (+ DLQ)
                                              │
                                   scripts/ec2_poller.py
                                   (long-poll, batch, idempotent)
                                              ├─► data/kpi_reports/ec2_queue_kpi_latest.json
                                              ├─► data/kpi_reports/windows/*.json   (per-file KPI window)
                                              │
                                   scripts/kpi_aggregator.py
                                              ├─► data/kpi_reports/hourly/*.json
                                              └─► data/kpi_reports/daily/*.json
                                              │
                                   dashboard/app.py (Streamlit)
```

## What's new vs. the original project

| Area | Before | Now |
|---|---|---|
| Ingestion | Manual CSV drop into `data/incoming_csvs/` | Syncthing-based live sync from on-prem server, atomic-write aware |
| Local staging | n/a | `garage_uploader.py`: resilient watchdog service, retries, checksum de-dupe, structured logs |
| Garage → AWS | `garage_sync.py` (basic) | Adds checksum/state tracking, exponential backoff, Prometheus-style metrics file, `--once`/`--daemon` modes |
| AWS fan-out | Direct S3→EC2 assumption | Documented + Terraform'd S3 → SNS → SQS (+ DLQ) fan-out |
| EC2 processing | Shell poller only | New `ec2_poller.py`: batched long-polling, idempotent processing via a seen-keys store, structured per-window KPI JSON, dead-letter handling |
| KPI logic | Ad hoc | `scripts/utils/kpi_engine.py`: shared, unit-tested mean/min/max/std/rate-of-change/threshold-breach engine used by both the poller and the aggregator |
| Aggregation | `kpi_aggregator.py` (basic) | Hourly + daily rollups with gap detection and schema versioning |
| Dashboard | Streamlit, basic | Live/window/hourly/daily tabs, staleness indicators, per-sensor drill-down |
| Infra | Terraform for EC2/S3 only | Adds `sns_sqs.tf` (topic, queue, DLQ, policy, event notification), `iam.tf` least-privilege roles |
| Ops | Manual | systemd units for every long-running process, `.service` files included |
| Tests | 1 contract test | Contract + unit tests for uploader, KPI engine, and aggregator |

## Repository layout

```
kpidatareporttf/
├── README.md
├── RUNBOOK.md
├── ARCHITECTURE.md
├── SYNCTHING_SETUP.md
├── SECURITY.md
├── .env.example
├── requirements.txt
├── config/
│   └── aws_config.py
├── scripts/
│   ├── garage_uploader.py        # Syncthing folder -> Garage S3
│   ├── garage_sync.py            # Garage S3 -> AWS S3
│   ├── s3_uploader.py            # schema validation + AWS S3 upload helper
│   ├── aws_s3_setup_check.py     # preflight checks for AWS resources
│   ├── ec2_poller.py             # SQS/SNS consumer -> KPI processing
│   ├── kpi_aggregator.py         # windows -> hourly -> daily rollups
│   ├── test_garage_integration.sh
│   └── utils/
│       ├── logging_config.py
│       ├── kpi_engine.py
│       ├── schema.py
│       └── state_store.py
├── dashboard/
│   └── app.py                    # Streamlit dashboard
├── infra/terraform/
│   ├── main.tf / variables.tf / outputs.tf
│   ├── s3.tf
│   ├── sns_sqs.tf
│   ├── ec2.tf
│   └── iam.tf
├── systemd/
│   ├── garage-uploader.service
│   ├── garage-sync.service
│   ├── ec2-kpi-poller.service
│   └── kpi-dashboard.service
├── data/
│   ├── incoming_csvs/
│   ├── logs/
│   └── kpi_reports/{windows,hourly,daily}/
├── tests/
└── notes/
```

## Quick start

### 1. On-prem + PC: Syncthing

See **[SYNCTHING_SETUP.md](SYNCTHING_SETUP.md)** for the full walkthrough
(Send Only / Receive Only, watch-for-changes, atomic writes, ignore patterns).

### 2. PC: stage sensor files into Garage S3

```bash
cp .env.example .env   # fill in GARAGE_* and SYNC_WATCH_DIR
python3 -m pip install -r requirements.txt
python3 scripts/garage_uploader.py            # continuous
python3 scripts/garage_uploader.py --backfill # push files already sitting in the folder, then continue
```

### 3. PC or a small always-on box: Garage → AWS S3

```bash
python3 scripts/garage_sync.py           # continuous
python3 scripts/garage_sync.py --once    # one-shot, useful for cron/testing
```

### 4. AWS: provision the S3 → SNS → SQS → EC2 fan-out

```bash
cd infra/terraform
terraform init
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

This creates the raw bucket, SNS topic, SQS queue + DLQ, the event
notification, the EC2 instance role, and (optionally) the EC2 instance
itself. See `RUNBOOK.md` for the manual console/CLI equivalent.

### 5. EC2: run the poller + aggregator + dashboard

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ec2-kpi-poller.service
sudo systemctl enable --now kpi-dashboard.service
```

Or run by hand for testing:

```bash
python3 scripts/ec2_poller.py
python3 scripts/kpi_aggregator.py --watch   # rebuild hourly/daily on a timer
streamlit run dashboard/app.py
```

For remote access without exposing Streamlit publicly:

```bash
aws ssm start-session \
    --target <instance-id> \
    --document-name AWS-StartPortForwardingSession \
    --parameters '{"portNumber":["8501"],"localPortNumber":["8501"]}'
```

## Components

- **garage_uploader.py** — watches the Syncthing "Receive Only" folder and pushes completed files to Garage S3 over Tailscale (retry + backoff, checksum de-dupe, ignores `.tmp`/partial files).
- **garage_sync.py** — pulls newly-staged objects from Garage S3 and uploads them to AWS S3 under `raw-sensor-data/`, tracking a local "already synced" state file so restarts don't re-upload.
- **s3_uploader.py** — shared schema validation + upload helper used by both sync scripts.
- **ec2_poller.py** — long-polls the SQS queue, unwraps the SNS envelope, fetches the raw object from S3, computes per-file KPI metrics, and writes idempotent window reports.
- **kpi_aggregator.py** — rolls per-file window reports up into hourly and daily summaries for the dashboard.
- **dashboard/app.py** — Streamlit UI over `data/kpi_reports/{windows,hourly,daily}`.
- **aws_config.py** — centralized configuration (env-driven) for AWS and Garage endpoints.

## Runbook

See [RUNBOOK.md](RUNBOOK.md) for full AWS CLI/Terraform provisioning steps,
and [ARCHITECTURE.md](ARCHITECTURE.md) for a deeper description of each hop
and its failure modes.
