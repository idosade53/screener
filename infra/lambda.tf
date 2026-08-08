# Three functions, one image (§9.1). The handler differs only by image_config.command.
# NOTE: the image tag must already be pushed to ECR before these apply (see infra/README.md).

# ---- log groups (explicit, so retention is bounded) ---------------------------
resource "aws_cloudwatch_log_group" "scan" {
  name              = "/aws/lambda/${var.project}-scan"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "webhook" {
  name              = "/aws/lambda/${var.project}-webhook"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "export" {
  name              = "/aws/lambda/${var.project}-export"
  retention_in_days = 14
}

# ---- scan ---------------------------------------------------------------------
resource "aws_lambda_function" "scan" {
  function_name = "${var.project}-scan"
  role          = aws_iam_role.scan.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  timeout       = 300
  memory_size   = 1024

  # Reserved concurrency 1: at most one scan runs at a time. With the deterministic claim (A6),
  # a retried EventBridge delivery is a no-op rather than a duplicate alert.
  reserved_concurrent_executions = 1

  image_config {
    command = ["screener.composition.lambda_scan.handler"]
  }

  environment {
    variables = merge(local.common_env, {
      SCREENER_SSM_PREFIX   = var.ssm_prefix
      SCREENER_SCAN_TIMES_ET = var.scan_times_et
    })
  }

  depends_on = [aws_cloudwatch_log_group.scan]
}

# ---- webhook ------------------------------------------------------------------
resource "aws_lambda_function" "webhook" {
  function_name = "${var.project}-webhook"
  role          = aws_iam_role.webhook.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  timeout       = 30
  memory_size   = 512

  # A single operator never needs more than a couple of concurrent replies; the public API Gateway
  # endpoint means an unauthenticated POST flood would otherwise fan out unbounded Lambda + SSM
  # invocations (the secret-token check runs inside the handler, after cold start). Cap the blast
  # radius, matching the reserved concurrency on scan/export.
  reserved_concurrent_executions = 5

  image_config {
    command = ["screener.composition.lambda_webhook.handler"]
  }

  environment {
    variables = merge(local.common_env, {
      SCREENER_SSM_PREFIX = var.ssm_prefix
    })
  }

  depends_on = [aws_cloudwatch_log_group.webhook]
}

# The webhook is fronted by an API Gateway HTTP API (see apigw.tf), not a Lambda Function URL:
# this account blocks public (AuthType NONE) function URLs at the platform level, and Telegram
# cannot sign requests. API Gateway public endpoints are not subject to that block. Auth is still
# the secret-token header the handler checks, plus the M5 chat allowlist.

# ---- export -------------------------------------------------------------------
resource "aws_lambda_function" "export" {
  function_name = "${var.project}-export"
  role          = aws_iam_role.export.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  timeout       = 300
  memory_size   = 1024

  reserved_concurrent_executions = 1

  image_config {
    command = ["screener.composition.lambda_export.handler"]
  }

  environment {
    variables = merge(local.common_env, {
      SCREENER_EXPORT_BUCKET = aws_s3_bucket.exports.bucket
      SCREENER_EXPORT_KEY    = var.export_key
    })
  }

  depends_on = [aws_cloudwatch_log_group.export]
}
