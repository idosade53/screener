# Telegram secrets live in SSM, not Terraform state. We create the parameters as SecureString
# placeholders and ignore their value — the operator sets the real values out-of-band:
#
#   aws ssm put-parameter --name /screener/telegram_bot_token   --type SecureString --overwrite --value "<token>"
#   aws ssm put-parameter --name /screener/telegram_chat_id     --type SecureString --overwrite --value "<chat_id>"
#   aws ssm put-parameter --name /screener/telegram_webhook_secret --type SecureString --overwrite --value "<random>"
#
# This keeps secrets out of `terraform.tfstate` while still giving the Lambdas a stable path + ARN.
locals {
  secret_names = ["telegram_bot_token", "telegram_chat_id", "telegram_webhook_secret"]
}

resource "aws_ssm_parameter" "secret" {
  for_each = toset(local.secret_names)

  name  = "${var.ssm_prefix}/${each.value}"
  type  = "SecureString"
  value = "PLACEHOLDER-set-out-of-band"

  lifecycle {
    ignore_changes = [value]
  }
}
