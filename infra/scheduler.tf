# EventBridge Scheduler — the Lambda-target equivalent of the RPi's in-process APScheduler
# (architecture §9.5). Cron is interpreted in America/New_York, so DST is handled by the named
# timezone and the calendar port only answers trading-day/holiday questions. Holidays need no cron
# logic: on a market holiday the job still fires and the pipeline skips it (is_trading_day gate).

locals {
  scans = {
    pre   = { cron = var.cron_pre, type = "PRE" }
    open  = { cron = var.cron_open, type = "OPEN" }
    close = { cron = var.cron_close, type = "CLOSE" }
  }
}

resource "aws_scheduler_schedule" "scan" {
  for_each = local.scans

  name = "${var.project}-${each.key}"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = each.value.cron
  schedule_expression_timezone = "America/New_York"

  target {
    arn      = aws_lambda_function.scan.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ scan_type = each.value.type })
  }
}

resource "aws_scheduler_schedule" "export" {
  name = "${var.project}-export"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = var.cron_export
  schedule_expression_timezone = "America/New_York"

  target {
    arn      = aws_lambda_function.export.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}
