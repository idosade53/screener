# API Gateway HTTP API in front of the webhook Lambda. Replaces a Lambda Function URL because this
# account blocks public (AuthType NONE) function URLs at the platform level (Telegram can't sign
# requests, so AWS_IAM is not an option). HTTP APIs are public and not subject to that block;
# they're also free-tier (1M requests/month). The webhook handler already speaks the v2 proxy
# payload format (event["body"], lowercased headers, returns {statusCode, body}), so no code change.
resource "aws_apigatewayv2_api" "webhook" {
  name          = "${var.project}-webhook"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "webhook" {
  api_id                 = aws_apigatewayv2_api.webhook.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.webhook.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /"
  target    = "integrations/${aws_apigatewayv2_integration.webhook.id}"
}

resource "aws_apigatewayv2_stage" "webhook" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "webhook_apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.webhook.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}
