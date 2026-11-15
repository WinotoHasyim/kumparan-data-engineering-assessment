# Execution Evidence

The screenshots below document a successful end-to-end run followed by a one-million-row volume test.

## End-to-end validation

### 1. Successful orchestration

![Successful historical backfill execution](../assets/img/e2e/01-kestra-execution.png)

*Kestra completed the bounded historical backfill successfully.*

### 2. Execution timeline

![Historical backfill Gantt view](../assets/img/e2e/02-kestra-timeline.png)

*The timeline shows ingestion completing before the dbt transformation.*

### 3. Workflow topology

![Historical backfill topology](../assets/img/e2e/03-kestra-topology.png)

*The topology shows chunk planning, ingestion, and transformation dependencies.*

### 4. Raw GCS delivery

![Raw Parquet and manifest objects in GCS](../assets/img/e2e/04-gcs-raw-delivery.png)

*The raw objects use the child window-ingestion execution ID as their immutable attempt ID.*

### 5. BigQuery landing table

![BigQuery landing observations](../assets/img/e2e/05-bigquery-landing.png)

*The landing table preserves append-only article observations.*

### 6. Canonical current table

![BigQuery current articles](../assets/img/e2e/06-bigquery-current.png)

*The current model keeps one latest record per article ID.*

### 7. Active article view

![BigQuery active articles](../assets/img/e2e/07-bigquery-active.png)

*The active view excludes the soft-deleted article.*

### 8. Analytical mart

![BigQuery daily author mart](../assets/img/e2e/08-bigquery-mart.png)

*The mart aggregates active published articles by date and author.*

## One-million-row volume test

### 1. Successful execution

![Successful one-million-row execution](../assets/img/volume-test/01-kestra-execution.png)

*Kestra completed the one-million-row backfill in 2 minutes 10.85 seconds.*

### 2. Extracted row count

![One-million-row Kestra output](../assets/img/volume-test/02-kestra-row-count.png)

*The ingestion output reports exactly 1,000,000 extracted rows.*

### 3. Partitioned raw output

![One-million-row Parquet parts in GCS](../assets/img/volume-test/03-gcs-parquet-parts.png)

*The bounded extraction produced 20 Parquet parts and one completion manifest.*

### 4. Canonical row count

![One-million-row BigQuery validation](../assets/img/volume-test/04-bigquery-row-count.png)

*The canonical table contains 1,000,094 distinct article IDs after including existing test data.*

### 5. BigQuery job metrics

![One-million-row BigQuery job metrics](../assets/img/volume-test/05-bigquery-job-metrics.png)

*The benchmark recorded 2.50 GiB loaded and 5.03 GiB processed across transformation queries.*
