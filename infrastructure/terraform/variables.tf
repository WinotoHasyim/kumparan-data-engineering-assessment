variable "project_id" {
  description = "Google Cloud project ID in which resources will be created."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid lowercase Google Cloud project ID."
  }
}

variable "environment" {
  description = "Deployment environment used in resource names and labels."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "location" {
  description = "Shared GCS and BigQuery location. Keep both services co-located."
  type        = string
  default     = "asia-southeast2"
}

variable "raw_bucket_name" {
  description = "Globally unique name of the raw article bucket."
  type        = string

  validation {
    condition = can(regex(
      "^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$",
      var.raw_bucket_name,
    ))
    error_message = "raw_bucket_name must be a valid 3-63 character GCS bucket name."
  }
}

variable "landing_dataset_id" {
  description = "BigQuery dataset ID for manifest-selected raw article observations."
  type        = string
  default     = "kumparan_landing"

  validation {
    condition     = can(regex("^[A-Za-z0-9_]+$", var.landing_dataset_id))
    error_message = "landing_dataset_id may contain only letters, numbers, and underscores."
  }
}

variable "analytics_dataset_id" {
  description = "BigQuery dataset ID for canonical models and analytical marts."
  type        = string
  default     = "kumparan_analytics"

  validation {
    condition     = can(regex("^[A-Za-z0-9_]+$", var.analytics_dataset_id))
    error_message = "analytics_dataset_id may contain only letters, numbers, and underscores."
  }
}

variable "force_destroy" {
  description = "Allow Terraform to delete non-empty data resources. Keep false outside disposable environments."
  type        = bool
  default     = false
}

variable "labels" {
  description = "Additional labels applied to supported Google Cloud resources."
  type        = map(string)
  default     = {}
}
