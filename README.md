# Kumparan Data Engineering Assessment

An hourly ELT project for synchronizing article data from PostgreSQL to an
analytical warehouse. The project is designed to cover historical backfills,
incremental processing, source updates, and deletion handling.

## Prerequisites

- Docker Engine or Docker Desktop
- Docker Compose v2 (`docker compose`)
- Google Cloud CLI (optional; only for GCP provisioning and ingestion)
- Terraform 1.6 or newer (optional; only for provisioning GCP resources)

Python, uv, PostgreSQL, and project packages run inside containers and do not
need to be installed on the host. GNU Make is optional and provides shorter
aliases for the Docker Compose commands. Local tests do not require Google Cloud
credentials.

## Quickstart

Start PostgreSQL and wait for its health check:

```bash
docker compose up -d --wait source-postgres
```

Inspect the deterministic seed data:

```bash
docker compose run --build --rm source-tools \
  python -m demo_source.mutate_articles list --limit 20
```

Run formatting checks, static analysis, unit tests, and PostgreSQL integration
tests:

```bash
docker compose run --build --rm pipeline-test \
  ruff format --check demo_source ingestion tests
docker compose run --rm pipeline-test ruff check demo_source ingestion tests
docker compose run --rm pipeline-test pytest -q
```

Or, using Make commands:

```bash
make source-up
make source-list
make lint
make test
```

## Generate historical volume

The generator creates deterministic articles distributed across the configured
historical interval:

```bash
docker compose run --build --rm source-tools \
  python -m demo_source.generate_articles \
  --count 100000 \
  --seed 2026 \
  --start-id 2000000 \
  --batch-size 5000
```

Or, using Make commands:

```bash
make source-generate COUNT=100000 SEED=2026 BATCH_SIZE=5000
make source-generate-million
```

## Provision GCP resources

```bash
gcloud auth application-default login
cp infrastructure/terraform/terraform.tfvars.example \
  infrastructure/terraform/terraform.tfvars
```

Set `project_id` and `raw_bucket_name` in `terraform.tfvars`, then run:

```bash
terraform -chdir=infrastructure/terraform init
terraform -chdir=infrastructure/terraform plan -out=deployment.tfplan
terraform -chdir=infrastructure/terraform apply deployment.tfplan
```

## Configure ingestion

```bash
cp .env.example .env
```

Set the GCP values in `.env`, then keep the bucket, datasets, and location
aligned with `terraform.tfvars`.

```bash
export GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH="$(
  gcloud info --format='value(config.paths.global_config_dir)'
)/application_default_credentials.json"

test -r "$GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH"
```

## Run ingestion manually

```bash
docker compose run --build --rm \
  --mount "type=bind,source=$GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH,target=/run/secrets/gcp.json,readonly" \
  --env GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gcp.json \
  article-ingestion \
  python -m ingestion.extract_articles \
  --window-start 2026-11-12T13:00:00Z \
  --window-end 2026-11-12T14:00:00Z \
  --execution-type BACKFILL
```

## Run ingestion with Kestra

```bash
docker compose build article-ingestion
docker compose up -d --wait source-postgres kestra
```

Or, using a Make command:

```bash
make orchestration-up
```

Open <http://localhost:8080> and sign in with the local credentials from
`docker-compose.yml`.

Use these flows:

- `articles_window_ingest` processes one explicit interval through BigQuery.
- `articles_hourly` runs hourly with a one-hour source grace period.
- `articles_historical_backfill` splits a bounded range into monthly chunks.

## Continuous source activity

The `live` profile inserts articles periodically and performs
deterministic updates, soft deletes, and hard deletes on earlier workload rows:

```bash
docker compose --profile live up -d --build source-workload
docker compose --profile live logs -f source-workload
docker compose --profile live stop source-workload
```

Or, using Make commands:

```bash
make source-live-up
make source-live-logs
make source-live-down
```

Change the interval in `.env` with `WORKLOAD_INTERVAL_SECONDS`.

## Controlled mutations

```bash
# Insert
docker compose run --rm source-tools \
  python -m demo_source.mutate_articles insert --id 3000001

# Update
docker compose run --rm source-tools \
  python -m demo_source.mutate_articles update 1000002 \
  --title "Judul setelah diperbarui"

# Soft delete
docker compose run --rm source-tools \
  python -m demo_source.mutate_articles soft-delete 1000002

# Simulate a source that changes deleted_at without changing updated_at
docker compose run --rm source-tools \
  python -m demo_source.mutate_articles soft-delete 1000008 \
  --preserve-updated-at

# Restore
docker compose run --rm source-tools \
  python -m demo_source.mutate_articles restore 1000002

# Hard delete
docker compose run --rm source-tools \
  python -m demo_source.mutate_articles hard-delete 1000008

# Exact interval boundary
docker compose run --rm source-tools \
  python -m demo_source.mutate_articles set-boundary-case 1000002 \
  --at 2026-11-13T01:00:00Z
```

Every mutation prints JSON containing the affected row and timestamps.

## Stop or reset the source

Stop PostgreSQL without deleting its data:

```bash
docker compose stop source-postgres
```

Recreate the database and deterministic seed from scratch:

```bash
docker compose rm -sf source-postgres
docker volume rm kumparan-source-postgres-data
docker compose up -d --wait source-postgres
```

Or, using Make commands:

```bash
make source-down
make source-reset
```

Resetting permanently removes the local simulator volume.
