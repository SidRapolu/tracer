terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "demo_log_group" {
  type    = string
  default = "/tracer/demo"
}

variable "schedule" {
  type    = string
  default = "rate(1 minute)"
}

variable "burst_every" {
  type    = number
  default = 15
}

# The dedicated group the emitter writes to (separate from the Lambda's own
# runtime logs, so the data Tracer reads stays clean).
resource "aws_cloudwatch_log_group" "demo" {
  name              = var.demo_log_group
  retention_in_days = 1
}

# Package the handler.
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../handler.py"
  output_path = "${path.module}/build/emitter.zip"
}

# Execution role.
resource "aws_iam_role" "emitter" {
  name = "tracer-demo-emitter"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# Least-privilege: write ONLY to the demo group (and the Lambda's own log
# group for its runtime logs). No broad CloudWatch access.
resource "aws_iam_role_policy" "emitter" {
  name = "tracer-demo-emitter-logs"
  role = aws_iam_role.emitter.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "WriteDemoGroup"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:CreateLogGroup"
        ]
        Resource = [
          aws_cloudwatch_log_group.demo.arn,
          "${aws_cloudwatch_log_group.demo.arn}:*"
        ]
      },
      {
        Sid    = "OwnRuntimeLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:CreateLogGroup"
        ]
        Resource = "arn:aws:logs:${var.region}:*:log-group:/aws/lambda/tracer-demo-emitter:*"
      }
    ]
  })
}

resource "aws_lambda_function" "emitter" {
  function_name    = "tracer-demo-emitter"
  role             = aws_iam_role.emitter.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      DEMO_LOG_GROUP = var.demo_log_group
      BURST_EVERY    = tostring(var.burst_every)
    }
  }
}

# Schedule: invoke on a fixed rate.
resource "aws_cloudwatch_event_rule" "tick" {
  name                = "tracer-demo-emitter-tick"
  schedule_expression = var.schedule
}

resource "aws_cloudwatch_event_target" "tick" {
  rule = aws_cloudwatch_event_rule.tick.name
  arn  = aws_lambda_function.emitter.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.emitter.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.tick.arn
}

output "demo_log_group" {
  value = aws_cloudwatch_log_group.demo.name
}

output "lambda_name" {
  value = aws_lambda_function.emitter.function_name
}
