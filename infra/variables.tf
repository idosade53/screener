variable "aws_region" {
  description = "AWS region. us-east-1 keeps the DynamoDB free tier and the Billing metric simple."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for all resources."
  type        = string
  default     = "screener"
}

variable "image_tag" {
  description = "ECR image tag the three Lambdas run. Push this tag before applying the Lambdas."
  type        = string
  default     = "latest"
}

variable "dynamodb_table" {
  description = "Single-table name (architecture §9.3). Must match SCREENER_DYNAMODB_TABLE."
  type        = string
  default     = "screener"
}

variable "ssm_prefix" {
  description = "SSM Parameter Store path holding the Telegram secrets (SCREENER_SSM_PREFIX)."
  type        = string
  default     = "/screener"
}

variable "scan_times_et" {
  description = "PRE,OPEN,CLOSE wall-clock ET times, passed through to the app (SCREENER_SCAN_TIMES_ET)."
  type        = string
  default     = "09:00,09:45,20:15"
}

# EventBridge Scheduler cron expressions, interpreted in America/New_York (DST-correct). Keep these
# in sync with scan_times_et — the cron drives *when*, scan_times_et drives the scan_id/labels.
variable "cron_pre" {
  type    = string
  default = "cron(0 9 ? * MON-FRI *)"
}

variable "cron_open" {
  type    = string
  default = "cron(45 9 ? * MON-FRI *)"
}

variable "cron_close" {
  type    = string
  default = "cron(15 20 ? * MON-FRI *)"
}

variable "cron_export" {
  description = "Daily analytical export, shortly after CLOSE (§9.4)."
  type        = string
  default     = "cron(30 20 ? * MON-FRI *)"
}

variable "export_key" {
  description = "S3 object key for the latest analytical SQLite copy."
  type        = string
  default     = "screener-latest.db"
}

variable "billing_alarm_threshold_usd" {
  description = "CloudWatch EstimatedCharges alarm threshold (architecture R4)."
  type        = number
  default     = 1
}

variable "alarm_email" {
  description = "Optional email subscribed to the billing-alarm SNS topic. Empty = no subscription."
  type        = string
  default     = ""
}
