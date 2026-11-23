# SPDX-License-Identifier: Apache-2.0
variable "zone_id" {
  description = "The Cloudflare zone id these settings apply to. One run, one zone. Never commit a real id: pass it with TF_VAR_zone_id from the instance repo or the secret manager."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.zone_id))
    error_message = "zone_id must be 32 lowercase hex characters."
  }
}
