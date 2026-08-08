# Analytical export bucket (§9.4). Name is globally unique via the account id suffix.
resource "aws_s3_bucket" "exports" {
  bucket = "${var.project}-exports-${local.account_id}"
}

resource "aws_s3_bucket_public_access_block" "exports" {
  bucket                  = aws_s3_bucket.exports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "exports" {
  bucket = aws_s3_bucket.exports.id
  versioning_configuration {
    status = "Enabled"
  }
}

# The export is a full rebuild overwriting one key every night (ADR A9). Versioning + this rule
# gives the "7 daily + ~12 monthly" depth from §9.4 while expiring the churn of old versions.
resource "aws_s3_bucket_lifecycle_configuration" "exports" {
  bucket = aws_s3_bucket.exports.id

  rule {
    id     = "expire-old-export-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days           = 365
      newer_noncurrent_versions = 20
    }
  }
}
