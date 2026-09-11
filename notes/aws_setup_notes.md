# AWS setup notes

- Region: set once via `AWS_REGION` in `.env`; every script and Terraform
  module reads from the same variable so there's no risk of the bucket,
  topic, and queue ending up in different regions.
- The S3 event notification is scoped to the `raw-sensor-data/` prefix
  only, so uploading unrelated objects to the same bucket (e.g. Terraform
  state, if you ever colocate it — don't) won't trigger the pipeline.
- The SNS→SQS subscription uses raw message delivery *disabled* (default),
  so `ec2_poller.py` has to unwrap the SNS envelope — this is intentional:
  it means the same SQS queue can, in the future, receive messages from a
  second SNS topic (e.g. a manual "reprocess" trigger) with a consistent
  envelope shape.
- DLQ retention is set to 14 days (vs. 4 days on the main queue) to give
  time to notice and act on the CloudWatch alarm before messages expire.
- `garage_sync.py` and `ec2_poller.py` both maintain their own local state
  file under `data/state/` rather than sharing one — they run on different
  machines (PC vs. EC2) so a shared file wouldn't be reachable anyway.
