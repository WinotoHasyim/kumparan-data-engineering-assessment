# Kumparan Data Engineering Assessment

An hourly ELT pipeline from PostgreSQL to GCS and BigQuery, orchestrated by
Kestra and transformed with dbt. It supports bounded historical backfills and
hourly incremental processing.

## Prerequisites

Docker Engine or Docker Desktop with Docker Compose v2 is required. The complete
pipeline also requires:

- A Google Cloud project with billing enabled
- Google Cloud CLI (`gcloud` and `bq`)
- Terraform 1.6 or newer
- Permission to enable APIs and manage GCS, BigQuery, service accounts, and IAM

GNU Make is optional.

## 1. Configure Google Cloud

```bash
gcloud auth application-default login

cp infrastructure/terraform/terraform.tfvars.example \
  infrastructure/terraform/terraform.tfvars
cp .env.example .env
```

Replace the placeholders in both files. The bucket name must be new and
globally unique. Keep these values aligned:

| `terraform.tfvars` | `.env` |
| --- | --- |
| `project_id` | `GCP_PROJECT_ID` |
| `raw_bucket_name` | `GCS_RAW_BUCKET` |
| `location` | `BIGQUERY_LOCATION` |
| `landing_dataset_id` | `BIGQUERY_LANDING_DATASET` |
| `analytics_dataset_id` | `BIGQUERY_ANALYTICS_DATASET` |

Load the runtime values and configure ADC:

```bash
set -a
. ./.env
set +a

gcloud config set project "$GCP_PROJECT_ID"
gcloud auth application-default set-quota-project "$GCP_PROJECT_ID"

export GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH="$(
  gcloud info --format='value(config.paths.global_config_dir)'
)/application_default_credentials.json"

test -r "$GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH"
```

## 2. Provision GCP

```bash
terraform -chdir=infrastructure/terraform init
terraform -chdir=infrastructure/terraform validate
terraform -chdir=infrastructure/terraform plan -out=deployment.tfplan
terraform -chdir=infrastructure/terraform apply deployment.tfplan
terraform -chdir=infrastructure/terraform output
```

Terraform creates the bucket, BigQuery datasets, required APIs, and a keyless
pipeline service account. Local containers use your ADC credentials.

## 3. Start the pipeline

```bash
docker compose build article-ingestion
docker compose up -d --wait source-postgres kestra
```

Or, using a Make command:

```bash
make orchestration-up
```

Open <http://localhost:8080> and sign in:

```text
Username: admin@localhost.dev
Password: Admin1234!
```

## 4. Run an end-to-end test

In Kestra, execute `kumparan.dev.articles_historical_backfill` with:

```text
window_start = 2026-11-01T00:00:00Z
window_end   = 2026-12-01T00:00:00Z
```

This seeded interval runs the complete path:

```text
PostgreSQL → GCS → BigQuery landing → dbt models → analytical mart
```

Wait until the parent execution and its subflows show `SUCCESS`.

## 5. Verify the output

```bash
gcloud storage ls --recursive "gs://$GCS_RAW_BUCKET/"

bq head --max_rows=10 \
  "$GCP_PROJECT_ID:$BIGQUERY_LANDING_DATASET.article_observations"

bq head --max_rows=10 \
  "$GCP_PROJECT_ID:$BIGQUERY_ANALYTICS_DATASET.articles_current"

bq head --max_rows=10 \
  "$GCP_PROJECT_ID:$BIGQUERY_ANALYTICS_DATASET.active_article_author_daily"
```

## Execution evidence

See [execution evidence](docs/EVIDENCE.md) for the end-to-end run and one-million-row volume test.

## Available flows

- `articles_window_ingest`: extract and load one explicit interval.
- `articles_historical_backfill`: process a bounded range in monthly chunks.
- `articles_hourly`: process hourly `updated_at` windows after a one-hour grace
  period.

## Optional local validation

Run the automated test suite without using Google Cloud:

```bash
docker compose run --build --rm pipeline-test
```

Or, using Make commands:

```bash
make lint
make test
```

## Optional source simulations

Generate 100,000 deterministic historical rows:

```bash
docker compose run --build --rm source-tools \
  python -m demo_source.generate_articles \
  --count 100000 \
  --seed 2026 \
  --start-id 2000000 \
  --batch-size 5000
```

Run continuous inserts, updates, soft deletes, and hard deletes:

```bash
docker compose --profile live up -d --build source-workload
docker compose --profile live logs -f source-workload
docker compose --profile live stop source-workload
```

Run `make help` to list all available commands.

## Stop or reset local services

```bash
docker compose stop kestra kestra-postgres source-postgres
```

Recreate the deterministic source database:

```bash
docker compose rm -sf source-postgres
docker volume rm kumparan-source-postgres-data
docker compose up -d --wait source-postgres
```

Resetting permanently removes the local source-simulator volume.
