# infra — Terraform for the Lambda + DynamoDB deployment

Provisions the architecture §9.1 topology: the `screener` DynamoDB table (provisioned 5/5), one ECR
repository (2-image lifecycle), three image Lambdas (`scan`, `webhook`, `export`), EventBridge
Scheduler cron rules in ET, the S3 export bucket, least-privilege IAM, and a `$1` billing alarm.

| File | Resources |
|---|---|
| `dynamodb.tf` | single table, provisioned 5/5, PITR |
| `ecr.tf` | repository + lifecycle policy (keep 2 images) |
| `lambda.tf` | 3 functions off one image + webhook Function URL |
| `scheduler.tf` | EventBridge Scheduler: PRE/OPEN/CLOSE + daily export (ET cron) |
| `s3.tf` | export bucket, versioned, retention |
| `ssm.tf` | Telegram secret parameters (placeholders; values set out-of-band) |
| `iam.tf` | per-function roles + the scheduler invoke role |
| `monitoring.tf` | SNS topic + EstimatedCharges alarm |

## Prerequisites

- Terraform ≥ 1.6, AWS credentials for the target account.
- **Enable "Receive Billing Alerts"** once in the account (Billing → Preferences), or the
  EstimatedCharges alarm sits in `INSUFFICIENT_DATA` (it only publishes in `us-east-1`).

## Deploy (first time)

The Lambdas reference an image that must already exist, so create ECR first, push, then apply the
rest:

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # edit if desired
terraform init

# 1. Create just the ECR repository.
terraform apply -target=aws_ecr_repository.screener

# 2. Build + push the image to it (see ../docs/deploy.md for the buildx command).
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${REPO%/*}"
docker tag screener:latest "$REPO:latest"
docker push "$REPO:latest"

# 3. Apply everything else.
terraform apply

# 4. Set the real Telegram secrets (never stored in Terraform state).
aws ssm put-parameter --name /screener/telegram_bot_token      --type SecureString --overwrite --value "<token>"
aws ssm put-parameter --name /screener/telegram_chat_id        --type SecureString --overwrite --value "<chat_id>"
aws ssm put-parameter --name /screener/telegram_webhook_secret --type SecureString --overwrite --value "$(openssl rand -hex 32)"

# 5. Register the webhook with Telegram (URL from `terraform output webhook_url`,
#    secret = the value you just put in SSM). See `terraform output set_webhook_command`.
```

## Redeploying new code

Build + push a new image tag, then either bump `image_tag` and `terraform apply`, or
`aws lambda update-function-code --function-name screener-<fn> --image-uri "$REPO:<tag>"` for all
three functions.

## Notes

- **Scheduling is EventBridge here**, not the RPi APScheduler (M6T02–T04) — see `scheduler.tf`.
  Holidays are handled by the pipeline's trading-day gate, so the cron only excludes weekends.
- The webhook role carries full table RW because the `/scan` bot command runs a scan inline; it is
  gated by the secret-token header + the chat allowlist. See the comment in `iam.tf` for the
  future hardening (async-invoke the scan function, drop webhook to universe-item writes).
- **Not yet `terraform validate`d / applied** — no Terraform binary was available in the authoring
  environment. Run `terraform fmt -check` and `terraform validate` before the first apply.
