.DEFAULT_GOAL := help

COUNT ?= 100000
SEED ?= 2026
START_ID ?= 2000000
BATCH_SIZE ?= 5000

.PHONY: help build lint test source-up source-down source-reset source-list source-generate source-generate-million source-live-up source-live-down source-live-logs orchestration-up orchestration-down orchestration-logs

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "%-24s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Build the Docker runtime and test images
	docker compose build source-tools pipeline-test

lint: ## Run formatting and lint checks inside Docker
	docker compose run --build --rm pipeline-test \
		ruff format --check demo_source ingestion tests
	docker compose run --rm pipeline-test ruff check demo_source ingestion tests

test: ## Run unit and PostgreSQL integration tests inside Docker
	docker compose run --build --rm pipeline-test

source-up: ## Start PostgreSQL and wait until it is healthy
	docker compose up -d --wait source-postgres

source-down: ## Stop PostgreSQL without deleting its data
	docker compose stop source-postgres

source-reset: ## Delete only simulator data, then recreate the fixed seed
	docker compose rm -sf source-postgres
	@if docker volume inspect kumparan-source-postgres-data >/dev/null 2>&1; then \
		docker volume rm kumparan-source-postgres-data; \
	fi
	docker compose up -d --wait source-postgres

source-list: ## List seeded articles using the Docker tools image
	docker compose run --build --rm source-tools \
		python -m demo_source.mutate_articles list --limit 20

source-generate: ## Generate historical volume; override COUNT, SEED, START_ID
	docker compose run --build --rm source-tools \
		python -m demo_source.generate_articles \
		--count $(COUNT) \
		--seed $(SEED) \
		--start-id $(START_ID) \
		--batch-size $(BATCH_SIZE)

source-generate-million: ## Generate one million historical rows
	$(MAKE) source-generate COUNT=1000000

source-live-up: ## Start continuous deterministic source changes
	docker compose --profile live up -d --build source-workload

source-live-down: ## Stop continuous changes without deleting source data
	docker compose --profile live stop source-workload

source-live-logs: ## Follow continuous workload operations
	docker compose --profile live logs -f source-workload

orchestration-up: ## Build ingestion and start PostgreSQL plus Kestra
	docker compose build article-ingestion
	docker compose up -d --wait source-postgres kestra

orchestration-down: ## Stop Kestra and its metadata database
	docker compose stop kestra kestra-postgres

orchestration-logs: ## Follow Kestra server logs
	docker compose logs -f kestra
