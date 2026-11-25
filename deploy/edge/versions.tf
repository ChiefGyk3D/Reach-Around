# SPDX-License-Identifier: Apache-2.0
terraform {
  required_version = ">= 1.9.0"

  required_providers {
    cloudflare = {
      source = "cloudflare/cloudflare"
      # Exact pin. A bump is its own reviewed change and waits 7 days after the
      # provider release (repo dependency rule). Hashes live in
      # .terraform.lock.hcl, which is committed.
      version = "= 5.29.0"
    }
  }
}

# The provider takes its credential from the pipeline's environment only. There
# is deliberately no variable for it, so it can never reach a tfvars file, a plan
# file argument or the shell history.
provider "cloudflare" {}
