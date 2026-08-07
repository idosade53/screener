output "ecr_repository_url" {
  description = "Push the image here before applying the Lambdas."
  value       = aws_ecr_repository.screener.repository_url
}

output "webhook_url" {
  description = "Set this as the Telegram webhook (with the secret token)."
  value       = aws_lambda_function_url.webhook.function_url
}

output "dynamodb_table" {
  value = aws_dynamodb_table.screener.name
}

output "export_bucket" {
  value = aws_s3_bucket.exports.bucket
}

output "set_webhook_command" {
  description = "Run after setting the webhook secret in SSM to register the URL with Telegram."
  value = join(" ", [
    "curl -s \"https://api.telegram.org/bot<BOT_TOKEN>/setWebhook\"",
    "-d url=${aws_lambda_function_url.webhook.function_url}",
    "-d secret_token=<WEBHOOK_SECRET>",
  ])
}
