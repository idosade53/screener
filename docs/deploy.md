# Deploy — AWS Lambda + DynamoDB (Phase 1 target)

The screener runs as **one container image with three handlers** (architecture §9.1). Scheduling is
EventBridge, storage is DynamoDB, secrets are SSM. This doc covers **Stage 3** (building and
smoke-testing the image). Stage 4 (Terraform: table, ECR, functions, EventBridge, S3) is separate.

| Function | Handler (`image_config.command`) | Trigger |
|---|---|---|
| `screener-scan` | `screener.composition.lambda_scan.handler` | EventBridge × 3 (ET cron) |
| `screener-webhook` | `screener.composition.lambda_webhook.handler` | Function URL (POST) |
| `screener-export` | `screener.composition.lambda_export.handler` | EventBridge (daily, post-CLOSE) |

## Build

The [Dockerfile](../Dockerfile) is multi-stage: a `python:3.13-slim` builder exports the locked
runtime deps (`poetry export`, dev group excluded) and builds the `screener` wheel; the final stage
is the AWS Lambda Python 3.13 base image with everything installed into `${LAMBDA_TASK_ROOT}`.

Build for `linux/amd64` (the Lambda architecture; arm64 later from the same file):

```bash
docker buildx build --platform linux/amd64 --provenance=false --sbom=false -t screener:latest --load .
```

> **`--provenance=false --sbom=false` is required.** Without them buildx attaches attestation
> manifests, producing an OCI image index that Lambda rejects with *"The image manifest, config or
> layer media type … is not supported."* The same flags apply to the `--push` form below.

The default `CMD` is the scan handler; each function overrides it via `image_config.command` in
Terraform, so **one image serves all three**.

## Smoke-test locally (Lambda RIE)

The base image ships the Runtime Interface Emulator, so the image runs the same way Lambda invokes
it. Pick the handler under test with a `command` override:

```bash
# scan handler, no AWS creds needed if you point it at a local/dummy backend
docker run --rm -p 9000:8080 \
  -e SCREENER_REPOSITORY_BACKEND=sqlite -e SCREENER_DB_PATH=/tmp/screener.db \
  screener:latest screener.composition.lambda_scan.handler

# in another shell — invoke it (EventBridge-shaped payload):
curl -s "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -d '{"scan_type":"CLOSE"}'
```

For `webhook`, POST a Telegram-update-shaped body:

```bash
docker run --rm -p 9000:8080 screener:latest screener.composition.lambda_webhook.handler
curl -s "http://localhost:9000/2015-03-31/functions/function/invocations" \
  -d '{"body":"{\"update_id\":1,\"message\":{\"chat\":{\"id\":\"<your-chat-id>\"},\"text\":\"/list\"}}"}'
```

Against real AWS, mount credentials (`-v ~/.aws:/root/.aws -e AWS_PROFILE=...`) and set
`SCREENER_REPOSITORY_BACKEND=dynamodb`, `SCREENER_DYNAMODB_TABLE`, `SCREENER_AWS_REGION`.

## Push to ECR (done by Terraform/CI in Stage 4, shown here for manual runs)

```bash
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
docker tag screener:latest "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/screener:latest"
docker push "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/screener:latest"
```

The ECR repository **must** carry a lifecycle policy retaining ~2 images (architecture R5) — image
storage is the only non-zero cost in the system.

## Runtime configuration (env vars, `SCREENER_` prefix)

| Var | Purpose |
|---|---|
| `SCREENER_REPOSITORY_BACKEND=dynamodb` | select the DynamoDB adapter |
| `SCREENER_DYNAMODB_TABLE`, `SCREENER_AWS_REGION` | table + region |
| `SCREENER_SSM_PREFIX` | when set, Telegram secrets are read from SSM Parameter Store |
| `SCREENER_TELEGRAM_WEBHOOK_SECRET` | (or via SSM) shared secret for the webhook header gate |
| `SCREENER_EXPORT_BUCKET`, `SCREENER_EXPORT_KEY` | export destination (export function only) |
| `SCREENER_SCAN_TIMES_ET` | `PRE,OPEN,CLOSE` times; EventBridge cron is derived from these |

## Status / notes

- **Built and RIE-smoke-tested** (2026-08-07, `linux/amd64`): the scan handler returns a valid
  response for a `{"scan_type":"CLOSE"}` invoke (and a proper `ConfigError` error payload for an
  unknown type); the webhook handler processes an authorized `/list` and silently 200s an
  unauthorized chat. The export handler is covered by the moto DynamoDB+S3 integration test; a live
  smoke-test needs real AWS.
- **Image size ≈ 1.06 GB** — above the <300 MB aspiration. It is dominated by the AWS Lambda base
  (~600 MB) plus pandas/numpy/exchange_calendars. Well within Lambda's 10 GB container limit, so
  not blocking. Future trims if desired: drop `boto3`/`botocore` from the image (the Lambda runtime
  already provides them), and prune `exchange_calendars`/pandas test data.
- **`find` is absent** from the Lambda base image, so the Dockerfile relies on `pip --no-compile`
  rather than a post-install bytecode strip.
- WSL tip: if `docker` prints "could not be found in this WSL 2 distro", the Windows-passthrough
  binary is shadowing the WSL one — use `/usr/bin/docker` with `DOCKER_HOST=unix:///var/run/docker.sock`.
