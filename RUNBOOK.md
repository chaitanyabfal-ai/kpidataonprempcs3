# Runbook: Syncthing to S3 to KPI Dashboard

This is the supported production path:

```text
On-prem server
  -> Syncthing Send Only
  -> local PC Receive Only folder
  -> scripts/garage_uploader.py
  -> AWS S3 landing bucket
  -> S3 ObjectCreated notification
  -> SNS topic
  -> SQS queue and DLQ
  -> EC2 poller
  -> KPI window, hourly, and daily JSON reports
  -> Streamlit dashboard
```

Garage is no longer in the critical path. `garage_uploader.py` retains an
optional Garage mode for backward compatibility, but the normal configuration
is `UPLOAD_TARGET=aws`.

## 1. Prerequisites

You need:

- An AWS account and credentials with permission to provision the Terraform resources.
- AWS CLI configured on the local PC: `aws sts get-caller-identity` must succeed.
- Syncthing installed on both the on-prem server and local PC.
- The repository at `/home/bfa/kpidatareporttf` on the local PC.
- Python dependencies installed in the repository's `premgarage` environment.

The on-prem Syncthing folder should be **Send Only**. The local PC folder should
be **Receive Only**. Configure this using [SYNCTHING_SETUP.md](SYNCTHING_SETUP.md).

## 2. LOCAL PC: prepare the virtual environment

Run these commands on the local PC, not on EC2:

```bash
cd /home/bfa/kpidatareporttf
source premgarage/bin/activate
python -m pip install -r requirements.txt
aws sts get-caller-identity
```

The prompt should show `(premgarage)` after activation. All local uploader,
AWS CLI, and Terraform commands below assume this shell is active.

## 3. LOCAL PC: configure direct S3 upload

Create the local configuration from the template if needed:

```bash
cd /home/bfa/kpidatareporttf
cp .env.example .env
```

Edit `.env` with the actual Syncthing Receive Only path and these AWS-first
values. Use the bucket name created by Terraform.

```dotenv
SYNC_WATCH_DIR=/home/bfa/incoming_sensor_data
SYNC_IGNORE_SUFFIXES=.tmp,.part,.swp
SYNC_SETTLE_SECONDS=0.5
DELETE_AFTER_UPLOAD=true
UPLOAD_TARGET=aws

AWS_REGION=ap-south-1
AWS_RAW_BUCKET=raw-sensor-data-bucket-bfa-20260915-ap
AWS_RAW_PREFIX=raw-sensor-data/
```

Use an AWS profile, environment credentials, or another supported AWS
credential source. Do not commit `.env` or AWS secrets.

Behavior of the local uploader:

- Ignores Syncthing partial files and dotfiles.
- Validates the required fields `timestamp`, `sensor_id`, and `value`.
- Uploads valid files to `s3://<bucket>/raw-sensor-data/`.
- Retries AWS failures with exponential backoff.
- Deletes the local file only after a successful S3 upload when
  `DELETE_AFTER_UPLOAD=true`.
- Leaves invalid or failed files in place for retry.

## 4. LOCAL PC: provision AWS with Terraform

The Terraform project is under `infra/terraform`, not the repository root.
Run Terraform from that directory or use `terraform -chdir`.

The current production values include:

```hcl
project_name         = "sensor-kpi-ap"
raw_bucket_name      = "raw-sensor-data-bucket-bfa-20260915-ap"
create_ec2_instance  = true
create_ec2_ssh_key   = true
ec2_root_volume_size = 20
ec2_key_name         = ""
ec2_private_key_path = "/home/bfa/kpidatareporttf/sensor-kpi-ec2.pem"
ec2_ssh_allowed_cidr = "<your-current-public-ip>/32"
```

Set `ec2_ssh_allowed_cidr` to the local PC's current public IPv4 address. A
`/32` allows only that address. Leave it empty to disable inbound SSH and use
SSM Session Manager instead.

Run on the local PC:

```bash
cd /home/bfa/kpidatareporttf/infra/terraform
terraform init
terraform workspace new ap-south-1
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
terraform output
```

The workspace command is required because the existing default workspace tracks
the original `us-east-1` deployment. Do not apply the `ap-south-1` configuration
from the old default workspace. The new region requires a new S3 bucket name,
and the `sensor-kpi-ap` project name avoids collisions with global IAM names.
The original `us-east-1` resources remain running until you explicitly migrate
and retire them.

Terraform creates the S3, SNS, SQS, IAM, security group, and EC2 resources. It
also generates a 4096-bit RSA key when `ec2_key_name` is empty, registers the
public key with EC2, and writes the private key to:

```text
/home/bfa/kpidatareporttf/sensor-kpi-ec2.pem
```

Verify it without printing its contents:

```bash
ls -l /home/bfa/kpidatareporttf/sensor-kpi-ec2.pem
chmod 600 /home/bfa/kpidatareporttf/sensor-kpi-ec2.pem
terraform -chdir=/home/bfa/kpidatareporttf/infra/terraform output ec2_ssh_key_name
terraform -chdir=/home/bfa/kpidatareporttf/infra/terraform output ec2_private_key_path
```

The private key is stored in Terraform state because Terraform generated it.
Protect the state file and never commit the key. Adding a key to an existing
instance can replace that instance, so always review the plan first.

## 5. LOCAL PC: find the EC2 connection details

After Terraform has completed:

```bash
cd /home/bfa/kpidatareporttf/infra/terraform
INSTANCE_ID=$(terraform output -raw ec2_instance_id)
PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text \
  --region ap-south-1)
echo "$INSTANCE_ID"
echo "$PUBLIC_IP"
```

If the result is `None`, the instance has no public IP. Use SSM, a bastion,
VPN, or a private network path instead of direct SSH.

## 6. LOCAL PC: connect to EC2 and copy the application

With the configured security group and public IP, connect from the local PC:

```bash
cd /home/bfa/kpidatareporttf
ssh -i ./sensor-kpi-ec2.pem ec2-user@"$PUBLIC_IP"
```

For the first setup, leave that SSH session open and use a second **LOCAL PC**
terminal to copy the application. Replace `<public-ip>` with the value found
above:

```bash
cd /home/bfa/kpidatareporttf
ssh -i ./sensor-kpi-ec2.pem ec2-user@<public-ip> \
  'sudo mkdir -p /opt/kpidatareporttf && sudo chown -R ec2-user:ec2-user /opt/kpidatareporttf'
scp -i ./sensor-kpi-ec2.pem -r \
  config scripts dashboard systemd requirements.txt .env \
  ec2-user@<public-ip>:/opt/kpidatareporttf/
```

After the copy, SSH into EC2 and change to the target directory before listing
the files. A new SSH session starts in `/home/ec2-user`, not in `/opt/kpidatareporttf`:

```bash
# EC2 BASH
cd /opt/kpidatareporttf
pwd
ls -la
find . -maxdepth 2 -type f | sort
```

You should see `.env`, `config/`, `scripts/`, `dashboard/`, `systemd/`, and
`requirements.txt`.

The EC2 `.env` must contain the EC2-side AWS settings. At minimum, set:

```dotenv
AWS_REGION=ap-south-1
AWS_RAW_BUCKET=raw-sensor-data-bucket-bfa-20260915-ap
AWS_RAW_PREFIX=raw-sensor-data/
SQS_QUEUE_URL=<terraform-output-sqs-queue-url>
SQS_MAX_MESSAGES=10
SQS_WAIT_TIME_SECONDS=20
SQS_VISIBILITY_TIMEOUT=60
```

The EC2 instance uses its Terraform IAM instance profile. Do not copy local
long-lived AWS secret keys to EC2. `SYNC_WATCH_DIR` is not used by the EC2
poller and can be left at its template value.

## 7. EC2 BASH: install and test the application

Run these commands only inside the EC2 SSH session:

```bash
cd /opt/kpidatareporttf
sudo dnf install -y python3-pip python3 git
python3 -m pip install -r requirements.txt
python3 -c 'import boto3; print(boto3.client("sts").get_caller_identity())'
python3 scripts/aws_s3_setup_check.py
```

The identity should be the EC2 instance role, not the local PC IAM user. If
the setup check reports an authorization error, inspect the Terraform IAM
policy and the EC2 instance profile before starting the services.

## 8. EC2 BASH: start the poller, aggregator, and dashboard

The repository service files use `/opt/kpidatareporttf` and Python 3. Start
them on EC2. The dashboard service invokes Streamlit as `python3 -m streamlit`
so it works with the user-installed packages on Amazon Linux:

```bash
cd /opt/kpidatareporttf
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ec2-kpi-poller.service
sudo systemctl enable --now kpi-aggregator.service
sudo systemctl enable --now kpi-dashboard.service

sudo systemctl status ec2-kpi-poller.service --no-pager
sudo systemctl status kpi-aggregator.service --no-pager
sudo systemctl status kpi-dashboard.service --no-pager
```

The services do the following:

- `ec2-kpi-poller.service`: SQS -> S3 object -> KPI window JSON.
- `kpi-aggregator.service`: window JSON -> hourly and daily rollups.
- `kpi-dashboard.service`: serves Streamlit on EC2 localhost port 8501.

Inspect failures on EC2 with:

```bash
sudo journalctl -u ec2-kpi-poller.service -n 100 --no-pager
sudo journalctl -u kpi-aggregator.service -n 100 --no-pager
sudo journalctl -u kpi-dashboard.service -n 100 --no-pager
```

## 9. LOCAL PC: open the dashboard

The dashboard listens on EC2 localhost, so create an SSH tunnel from the
local PC:

```bash
cd /home/bfa/kpidatareporttf
ssh -i ./sensor-kpi-ec2.pem \
  -L 8501:127.0.0.1:8501 \
  ec2-user@<public-ip>
```

Keep this SSH session open, then open the local browser at:

```text
http://127.0.0.1:8501
```

The browser is local, but the dashboard process and KPI report files are on EC2.

If using SSM instead of SSH, run this on the local PC:

```bash
aws ssm start-session \
  --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8501"],"localPortNumber":["8501"]}'
```

## 10. LOCAL PC: run the uploader and verify the full path

Create a valid test payload using a heredoc. Do not type JSON directly as a
shell command:

```bash
cd /home/bfa/kpidatareporttf
source premgarage/bin/activate
set -a; source .env; set +a
mkdir -p "$SYNC_WATCH_DIR"
cat > "$SYNC_WATCH_DIR/smoketest.json" <<'JSON'
[
  {"timestamp": "2026-09-15T08:00:00Z", "sensor_id": "line1-temp", "value": 72.4}
]
JSON
```

For a one-time upload test:

```bash
python scripts/garage_uploader.py --once
tail -n 50 data/logs/garage_uploader.log
aws s3 ls s3://raw-sensor-data-bucket-bfa-20260915-ap/raw-sensor-data/ --region ap-south-1
```

For continuous ingestion on the local PC:

```bash
python scripts/garage_uploader.py --backfill
```

Leave that process running, or install [systemd/garage-uploader.service](systemd/garage-uploader.service)
on the local PC. The file should be deleted from the local Syncthing folder
only after the S3 upload succeeds.

## 11. Confirm KPI reports on EC2

After the S3 object is uploaded, S3 sends the event through SNS and SQS. The
EC2 poller consumes it and writes reports on EC2. Run these commands in the
**EC2 BASH** session:

```bash
cd /opt/kpidatareporttf
find data/kpi_reports -type f -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort | tail -20
cat data/kpi_reports/ec2_queue_kpi_latest.json
```

Expected report locations:

```text
/opt/kpidatareporttf/data/kpi_reports/windows/
/opt/kpidatareporttf/data/kpi_reports/hourly/
/opt/kpidatareporttf/data/kpi_reports/daily/
```

Once a report appears in these directories, refresh the dashboard at
`http://127.0.0.1:8501` on the local PC.

## 12. End-to-end checklist

- [ ] On-prem Syncthing folder is Send Only.
- [ ] Local PC Syncthing folder is Receive Only.
- [ ] Local PC `.env` has `UPLOAD_TARGET=aws`.
- [ ] Local PC `.env` has the correct `SYNC_WATCH_DIR`.
- [ ] Terraform uses the valid globally unique S3 bucket name.
- [ ] Terraform apply completed for S3, SNS, SQS, IAM, EC2, key, and SSH rule.
- [ ] `/home/bfa/kpidatareporttf/sensor-kpi-ec2.pem` exists with mode `0600`.
- [ ] EC2 `.env` contains the bucket and SQS queue URL.
- [ ] EC2 can assume the Terraform-created instance role.
- [ ] Local uploader log shows a successful S3 upload.
- [ ] S3 contains the object under `raw-sensor-data/`.
- [ ] SQS receives the S3/SNS event.
- [ ] EC2 poller writes a window report.
- [ ] Aggregator writes hourly and daily reports.
- [ ] Dashboard displays the new report.

## 13. Troubleshooting

### `terraform output` says no outputs found

Run Terraform from `infra/terraform`, not the repository root:

```bash
cd /home/bfa/kpidatareporttf/infra/terraform
terraform output
```

### SSH times out

Check that:

- The instance has a public IP or a reachable private network path.
- `ec2_ssh_allowed_cidr` matches the current public IP of the local PC.
- The instance security group has TCP port 22 ingress.
- The key path is `/home/bfa/kpidatareporttf/sensor-kpi-ec2.pem`.

If the public IP changed, update `ec2_ssh_allowed_cidr` and apply Terraform.

### AWS CLI returns `AccessDenied`

On the local PC, check the active identity:

```bash
aws sts get-caller-identity
aws configure list
```

The local identity needs S3 upload permission. On EC2, the instance profile
needs S3 read, SQS receive/delete, and related permissions from Terraform.

### The local file remains after upload

Check `DELETE_AFTER_UPLOAD=true`, then inspect:

```bash
tail -n 100 /home/bfa/kpidatareporttf/data/logs/garage_uploader.log
```

Invalid JSON, missing required fields, AWS failures, and files still being
written by Syncthing are intentionally retained for retry.

### S3 contains the file but the dashboard is stale

On EC2, check the poller and aggregator journals, then confirm files under
`/opt/kpidatareporttf/data/kpi_reports/`. The dashboard does not read S3
directly; it reads the reports generated locally on EC2.

## 14. Optional Garage fallback

Only for legacy migration, set `UPLOAD_TARGET=garage` and provide the Garage
endpoint and credentials in `.env`. This is not required for the supported
Syncthing -> AWS pipeline and should not be used when Garage credentials are
unavailable.
