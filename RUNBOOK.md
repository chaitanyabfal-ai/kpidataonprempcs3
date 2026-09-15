# Runbook

## A. Local setup

```bash
git clone <this repo>
cd kpidatareporttf
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: SYNC_WATCH_DIR, GARAGE_*, AWS_*
# DELETE_AFTER_UPLOAD=true removes the PC copy after Garage accepts it
```

## B. On-prem + PC: Syncthing

Follow **SYNCTHING_SETUP.md**. Verify with:

```bash
python3 scripts/garage_uploader.py --once   # backfill whatever is already in the folder
```

## C. PC: continuous services

```bash
python3 scripts/garage_uploader.py &   # or install systemd/garage-uploader.service
python3 scripts/garage_sync.py &       # or install systemd/garage-sync.service
```

## D. AWS provisioning

### Option 1 — Terraform (recommended)

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # edit bucket name etc.
terraform init
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
terraform output   # copy sqs_queue_url / sns_topic_arn into .env
```

### Option 2 — AWS CLI (manual equivalent)

```bash
# 1. S3 bucket
aws s3api create-bucket --bucket raw-sensor-data-bucket --region us-east-1

# 2. SNS topic
aws sns create-topic --name sensor-data-topic

# 3. SQS queue + DLQ
aws sqs create-queue --queue-name sensor-data-queue-dlq
DLQ_ARN=$(aws sqs get-queue-attributes \
  --queue-url $(aws sqs get-queue-url --queue-name sensor-data-queue-dlq --query QueueUrl --output text) \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)

aws sqs create-queue --queue-name sensor-data-queue --attributes '{
  "RedrivePolicy": "{\"deadLetterTargetArn\":\"'"$DLQ_ARN"'\",\"maxReceiveCount\":\"5\"}"
}'

# 4. Subscribe SQS to SNS
TOPIC_ARN=$(aws sns list-topics --query "Topics[?ends_with(TopicArn,'sensor-data-topic')].TopicArn" --output text)
QUEUE_URL=$(aws sqs get-queue-url --queue-name sensor-data-queue --query QueueUrl --output text)
QUEUE_ARN=$(aws sqs get-queue-attributes --queue-url $QUEUE_URL --attribute-names QueueArn --query Attributes.QueueArn --output text)

aws sns subscribe --topic-arn $TOPIC_ARN --protocol sqs --notification-endpoint $QUEUE_ARN

# 5. Allow SNS to send to SQS (queue access policy)
aws sqs set-queue-attributes --queue-url $QUEUE_URL --attributes '{
  "Policy": "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"sns.amazonaws.com\"},\"Action\":\"sqs:SendMessage\",\"Resource\":\"'"$QUEUE_ARN"'\",\"Condition\":{\"ArnEquals\":{\"aws:SourceArn\":\"'"$TOPIC_ARN"'\"}}}]}"
}'

# 6. S3 -> SNS event notification
aws s3api put-bucket-notification-configuration --bucket raw-sensor-data-bucket --notification-configuration '{
  "TopicConfigurations": [{
    "TopicArn": "'"$TOPIC_ARN"'",
    "Events": ["s3:ObjectCreated:*"],
    "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "raw-sensor-data/"}]}}
  }]
}'
```

Confirm everything is wired up:

```bash
python3 scripts/aws_s3_setup_check.py
```

## E. EC2: run the poller, aggregator, and dashboard

```bash
scp -r . ec2-user@<host>:/opt/kpidatareporttf
ssh ec2-user@<host>
cd /opt/kpidatareporttf
python3 -m pip install -r requirements.txt

sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ec2-kpi-poller.service
sudo systemctl enable --now kpi-aggregator.service
sudo systemctl enable --now kpi-dashboard.service

sudo systemctl status ec2-kpi-poller.service
journalctl -u ec2-kpi-poller.service -f
```

View the dashboard without exposing it publicly:

```bash
aws ssm start-session \
    --target <instance-id> \
    --document-name AWS-StartPortForwardingSession \
    --parameters '{"portNumber":["8501"],"localPortNumber":["8501"]}'
# then open http://127.0.0.1:8501
```

## F. Smoke test end to end

```bash
# 1. Drop a valid test file where Syncthing would put it
mkdir -p "$SYNC_WATCH_DIR"
python3 - <<'PY'
import json, os
recs = [{"timestamp": "2026-09-11T08:00:00Z", "sensor_id": "line1-temp", "value": 72.4}]
path = os.path.join(os.environ["SYNC_WATCH_DIR"], "smoketest.json")
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(recs, f)
os.rename(tmp, path)
PY

# 2. Watch it flow through
tail -f data/logs/garage_uploader.log
tail -f data/logs/garage_sync.log

# 3. On EC2 (or wherever the poller runs)
tail -f data/logs/ec2_poller.log
cat data/kpi_reports/ec2_queue_kpi_latest.json
```

## G. Shutdown

See `shutdownrnbook.md` (carried over from the original project) for the
full EC2/Terraform teardown sequence; stop the systemd services first:

```bash
sudo systemctl stop kpi-dashboard kpi-aggregator ec2-kpi-poller
```
