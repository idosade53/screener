# Billing guardrail (architecture R4). EstimatedCharges is only published in us-east-1 and only
# when "Receive Billing Alerts" is enabled in the account's Billing preferences — enable that once,
# manually, or this alarm sits in INSUFFICIENT_DATA.
resource "aws_sns_topic" "alarms" {
  name = "${var.project}-alarms"
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "billing" {
  alarm_name          = "${var.project}-estimated-charges"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EstimatedCharges"
  namespace           = "AWS/Billing"
  period              = 21600 # 6h — the metric updates a few times a day
  statistic           = "Maximum"
  threshold           = var.billing_alarm_threshold_usd
  alarm_description   = "Total estimated AWS charges exceeded ${var.billing_alarm_threshold_usd} USD."
  dimensions          = { Currency = "USD" }
  alarm_actions       = [aws_sns_topic.alarms.arn]
  treat_missing_data  = "notBreaching"
}
