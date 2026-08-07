provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  image_uri = "${aws_ecr_repository.screener.repository_url}:${var.image_tag}"

  # Config every function shares. Secrets are NOT here — they come from SSM at cold start.
  common_env = {
    SCREENER_REPOSITORY_BACKEND = "dynamodb"
    SCREENER_DYNAMODB_TABLE     = var.dynamodb_table
    SCREENER_AWS_REGION         = var.aws_region
  }
}
