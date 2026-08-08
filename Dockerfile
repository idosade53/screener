# Screener Lambda image (architecture §9.1/§9.2): ONE image, THREE handlers.
# The handler a function runs is chosen by `image_config.command` in Terraform (Stage 4); the
# CMD below is only the default. Sharing one image means one build, one ECR repository, one
# dependency set — keeping ECR (the only non-zero cost) to a single image.
#
# Build for linux/amd64 now (arm64 later from this same file via buildx):
#   docker buildx build --platform linux/amd64 -t screener:latest --load .

# ── builder: resolve the locked deps and build our wheel ─────────────────────
FROM python:3.13-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1
# poetry 2.x moved `export` into a plugin.
RUN pip install "poetry>=2,<3" poetry-plugin-export
WORKDIR /build
COPY pyproject.toml poetry.lock README.md ./
COPY src ./src
# Runtime deps only (the `dev` group — pytest/moto/ruff/mypy — never reaches the image), plus a
# wheel of the screener package itself.
RUN poetry export -f requirements.txt --without dev --without-hashes -o requirements.txt \
 && poetry build -f wheel

# ── final: AWS Lambda Python 3.13 runtime ────────────────────────────────────
FROM public.ecr.aws/lambda/python:3.13
# Locked third-party deps first (best layer-cache reuse), then our package — both into the Lambda
# task root. `--no-compile` keeps bytecode out; the base image is minimal (no coreutils `find`),
# so we rely on that rather than a post-install strip.
COPY --from=builder /build/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-compile -r /tmp/requirements.txt -t "${LAMBDA_TASK_ROOT}"
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir --no-compile --no-deps /tmp/*.whl -t "${LAMBDA_TASK_ROOT}" \
 && rm -rf /tmp/*.whl /tmp/requirements.txt

# Default handler; Terraform overrides per function:
#   scan    -> screener.composition.lambda_scan.handler
#   webhook -> screener.composition.lambda_webhook.handler
#   export  -> screener.composition.lambda_export.handler
CMD ["screener.composition.lambda_scan.handler"]
