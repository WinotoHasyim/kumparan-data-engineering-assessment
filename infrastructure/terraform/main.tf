terraform {
  required_version = ">= 1.6, < 2.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.11.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.location

  default_labels = local.common_labels
}

locals {
  required_services = toset([
    "bigquery.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "storage.googleapis.com",
  ])

  bigquery_datasets = {
    landing = {
      dataset_id    = var.landing_dataset_id
      friendly_name = "Kumparan Articles Landing"
      description   = "Manifest-selected article observations loaded from raw object storage."
    }
    analytics = {
      dataset_id    = var.analytics_dataset_id
      friendly_name = "Kumparan Articles Analytics"
      description   = "Canonical article models and analytical marts managed by dbt."
    }
  }

  common_labels = merge(
    {
      application = "kumparan-articles"
      environment = var.environment
      managed_by  = "terraform"
    },
    var.labels,
  )
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "raw" {
  name                        = var.raw_bucket_name
  project                     = var.project_id
  location                    = var.location
  storage_class               = "STANDARD"
  force_destroy               = var.force_destroy
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  depends_on = [google_project_service.required]
}

resource "google_bigquery_dataset" "datasets" {
  for_each = local.bigquery_datasets

  project                    = var.project_id
  dataset_id                 = each.value.dataset_id
  friendly_name              = each.value.friendly_name
  description                = each.value.description
  location                   = var.location
  max_time_travel_hours      = 168
  delete_contents_on_destroy = var.force_destroy

  depends_on = [google_project_service.required]
}

resource "google_service_account" "pipeline" {
  project      = var.project_id
  account_id   = "kumparan-pipeline-${var.environment}"
  display_name = "Kumparan article pipeline (${var.environment})"
  description  = "Runtime identity for article ingestion, loading, and transformation."

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "pipeline_object_creator" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectCreator"
  member = google_service_account.pipeline.member
}

resource "google_storage_bucket_iam_member" "pipeline_object_viewer" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.pipeline.member
}

resource "google_project_iam_member" "pipeline_bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = google_service_account.pipeline.member
}

resource "google_bigquery_dataset_iam_member" "pipeline_data_editor" {
  for_each = google_bigquery_dataset.datasets

  project    = var.project_id
  dataset_id = each.value.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_service_account.pipeline.member
}
