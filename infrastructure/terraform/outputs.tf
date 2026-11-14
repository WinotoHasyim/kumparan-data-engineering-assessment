output "raw_bucket_name" {
  description = "Name of the GCS bucket that stores immutable raw article attempts."
  value       = google_storage_bucket.raw.name
}

output "raw_bucket_url" {
  description = "GCS URL of the raw article bucket."
  value       = google_storage_bucket.raw.url
}

output "bigquery_dataset_ids" {
  description = "BigQuery dataset IDs keyed by logical layer."
  value = {
    for layer, dataset in google_bigquery_dataset.datasets : layer => dataset.dataset_id
  }
}

output "pipeline_service_account_email" {
  description = "Runtime service account used by the article data pipeline."
  value       = google_service_account.pipeline.email
}

output "location" {
  description = "Shared location used by GCS and BigQuery."
  value       = var.location
}

output "project_id" {
  description = "Google Cloud project that owns the resources."
  value       = var.project_id
}
