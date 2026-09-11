data "aws_ami" "al2023" {
  count       = var.create_ec2_instance && var.ec2_ami_id == "" ? 1 : 0
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}

resource "aws_security_group" "ec2_poller" {
  count       = var.create_ec2_instance ? 1 : 0
  name        = "${var.project_name}-ec2-poller-sg"
  description = "No inbound rules -- access via SSM Session Manager only."
  vpc_id      = null # uses default VPC unless overridden by subnet's VPC

  egress {
    description = "Allow all outbound (S3, SQS, SNS, SSM endpoints)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = var.tags
}

resource "aws_instance" "ec2_poller" {
  count                  = var.create_ec2_instance ? 1 : 0
  ami                    = var.ec2_ami_id != "" ? var.ec2_ami_id : data.aws_ami.al2023[0].id
  instance_type          = var.ec2_instance_type
  subnet_id              = var.ec2_subnet_id != "" ? var.ec2_subnet_id : null
  key_name               = var.ec2_key_name != "" ? var.ec2_key_name : null
  iam_instance_profile   = aws_iam_instance_profile.ec2_poller.name
  vpc_security_group_ids = [aws_security_group.ec2_poller[0].id]

  user_data = <<-EOF
    #!/bin/bash
    dnf install -y python3-pip git
    pip3 install boto3 streamlit pandas python-dotenv
  EOF

  tags = merge(var.tags, { Name = "${var.project_name}-ec2-poller" })
}
