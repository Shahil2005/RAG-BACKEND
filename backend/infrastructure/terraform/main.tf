# Starbot infrastructure — extend per cloud provider
terraform {
  required_version = ">= 1.5.0"
}

variable "environment" {
  type    = string
  default = "dev"
}

# Placeholder outputs for CI/CD integration
output "starbot_api_url" {
  value = "https://api.starbot.${var.environment}.example.com"
}

output "starbot_web_url" {
  value = "https://app.starbot.${var.environment}.example.com"
}
