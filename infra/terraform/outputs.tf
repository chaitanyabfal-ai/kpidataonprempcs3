output "raw_bucket_name" {
  value = aws_s3_bucket.raw_sensor_data.bucket
}

output "sns_topic_arn" {
  value = aws_sns_topic.sensor_data.arn
}

output "sqs_queue_url" {
  value = aws_sqs_queue.sensor_data.id
}

output "sqs_queue_arn" {
  value = aws_sqs_queue.sensor_data.arn
}

output "sqs_dlq_url" {
  value = aws_sqs_queue.sensor_data_dlq.id
}

output "ec2_poller_role_arn" {
  value = aws_iam_role.ec2_poller.arn
}

output "ec2_instance_id" {
  value = var.create_ec2_instance ? aws_instance.ec2_poller[0].id : null
}
