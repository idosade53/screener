# Per-function roles, each scoped to exactly what that function touches (§9.1). No shared
# god-role: the public webhook and the scheduled scan do not carry the export's S3 rights, etc.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ---- shared statements --------------------------------------------------------
data "aws_iam_policy_document" "dynamo_rw" {
  statement {
    sid = "TableRW"
    actions = [
      "dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan",
      "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem", "dynamodb:ConditionCheckItem",
    ]
    resources = [aws_dynamodb_table.screener.arn]
  }
}

data "aws_iam_policy_document" "dynamo_ro" {
  statement {
    sid       = "TableRead"
    actions   = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = [aws_dynamodb_table.screener.arn]
  }
}

data "aws_iam_policy_document" "ssm_read" {
  statement {
    sid       = "ReadSecrets"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [for p in aws_ssm_parameter.secret : p.arn]
  }
  statement {
    sid       = "DecryptSecrets"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

# ---- scan ---------------------------------------------------------------------
resource "aws_iam_role" "scan" {
  name               = "${var.project}-scan"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "scan_basic" {
  role       = aws_iam_role.scan.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "scan_dynamo" {
  name   = "dynamo-rw"
  role   = aws_iam_role.scan.id
  policy = data.aws_iam_policy_document.dynamo_rw.json
}

resource "aws_iam_role_policy" "scan_ssm" {
  name   = "ssm-read"
  role   = aws_iam_role.scan.id
  policy = data.aws_iam_policy_document.ssm_read.json
}

# ---- webhook ------------------------------------------------------------------
# Full table RW, not universe-only as originally sketched in §9.1: the /scan bot command runs a
# MANUAL scan inline, which touches bars/indicators/scans. Access is still gated by the webhook
# secret header + the single-operator chat allowlist. (Future hardening: have /scan async-invoke
# the scan function instead, and drop the webhook back to universe-item writes.)
resource "aws_iam_role" "webhook" {
  name               = "${var.project}-webhook"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "webhook_basic" {
  role       = aws_iam_role.webhook.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "webhook_dynamo" {
  name   = "dynamo-rw"
  role   = aws_iam_role.webhook.id
  policy = data.aws_iam_policy_document.dynamo_rw.json
}

resource "aws_iam_role_policy" "webhook_ssm" {
  name   = "ssm-read"
  role   = aws_iam_role.webhook.id
  policy = data.aws_iam_policy_document.ssm_read.json
}

# ---- export -------------------------------------------------------------------
resource "aws_iam_role" "export" {
  name               = "${var.project}-export"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "export_basic" {
  role       = aws_iam_role.export.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "export_dynamo" {
  name   = "dynamo-ro"
  role   = aws_iam_role.export.id
  policy = data.aws_iam_policy_document.dynamo_ro.json
}

data "aws_iam_policy_document" "export_s3" {
  statement {
    sid       = "WriteExport"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.exports.arn}/*"]
  }
}

resource "aws_iam_role_policy" "export_s3" {
  name   = "s3-write"
  role   = aws_iam_role.export.id
  policy = data.aws_iam_policy_document.export_s3.json
}

# ---- EventBridge Scheduler execution role ------------------------------------
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.project}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.scan.arn, aws_lambda_function.export.arn]
  }
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name   = "invoke-lambdas"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}
