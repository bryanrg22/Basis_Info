# Alerting (RUNBOOK.md section 4): who is told when the API, the worker, the database or a
# study fails, from Cloud Monitoring, independent of the application. The application's own
# alerts (a failed study, a stale worker heartbeat) travel through the outbox and the email
# sender (basis.notify, docs/api/RETENTION_AND_ALERTS.md); these policies catch what that
# path cannot see: the processes themselves being down.
#
# Everything here is created only with alerts_enabled = true (variables.tf): parked staging
# fails every one of these checks by design. The metric names and the resource types were
# read from the project's metric descriptors on 2026-11-01 (run.googleapis.com/
# container/instance_count applies to cloud_run_worker_pool with the label `state`;
# request_count carries response_code_class; cloudsql.googleapis.com/database/up is a
# GAUGE INT64 on cloudsql_database).

locals {
  alerts   = var.alerts_enabled ? 1 : 0
  api_host = trimprefix(google_cloud_run_v2_service.api.uri, "https://")
  alert_channels = concat(
    var.alert_notification_channels,
    [for channel in google_monitoring_notification_channel.email : channel.id],
  )
  runbook = "Runbook: infra/staging/RUNBOOK.md section 4 in bryanrg22/Basis_Info."
}

# --------------------------------------------------------------------------- channel

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == null ? 0 : 1
  display_name = "Basis staging alerts (email)"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

# --------------------------------------------------------------------------- API up

# GET /health from three regions every 5 minutes. The route answers 200 with the database
# and Temporal ok, 503 when the database is down (STAGING.md 1.1), so a failing check is
# "the API is down or cannot reach its database".
resource "google_monitoring_uptime_check_config" "api_health" {
  count        = local.alerts
  display_name = "staging-basis-api /health"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path           = "/health"
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"
    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = local.api_host
    }
  }
}

# The policy Cloud Monitoring itself generates for an uptime check: over a 20-minute window,
# more than one failed probe (REDUCE_COUNT_FALSE over check_passed) for a minute opens an
# incident. One flaky probe from one region does not page.
resource "google_monitoring_alert_policy" "api_down" {
  count                 = local.alerts
  display_name          = "Basis staging: API /health failing"
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.alert_channels

  conditions {
    display_name = "Uptime check on /health fails"
    condition_threshold {
      filter = join(" AND ", [
        "metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\"",
        "metric.label.check_id = \"${google_monitoring_uptime_check_config.api_health[0].uptime_check_id}\"",
        "resource.type = \"uptime_url\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "60s"
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Basis staging API is down"
    content   = "GET /health on the staging API has failed from more than one region. Check the Cloud Run service staging-basis-api (revision, logs) and Cloud SQL staging-basis-pg. ${local.runbook}"
  }
}

# --------------------------------------------------------------------------- API 5xx

# More than 5 % of the API's requests answered 5xx over 5 minutes. A ratio, so a burst of
# traffic does not page by volume alone; with no traffic (the API scales to zero) the
# condition is inactive rather than open (EVALUATION_MISSING_DATA_INACTIVE).
resource "google_monitoring_alert_policy" "api_5xx" {
  count                 = local.alerts
  display_name          = "Basis staging: API 5xx rate above 5 %"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.alert_channels

  conditions {
    display_name = "5xx responses / all responses > 0.05 for 5 min"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloud_run_revision\"",
        "resource.label.service_name = \"${google_cloud_run_v2_service.api.name}\"",
        "metric.type = \"run.googleapis.com/request_count\"",
        "metric.label.response_code_class = \"5xx\"",
      ])
      denominator_filter = join(" AND ", [
        "resource.type = \"cloud_run_revision\"",
        "resource.label.service_name = \"${google_cloud_run_v2_service.api.name}\"",
        "metric.type = \"run.googleapis.com/request_count\"",
      ])
      comparison              = "COMPARISON_GT"
      threshold_value         = 0.05
      duration                = "300s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
      denominator_aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Basis staging API answering 5xx"
    content   = "Over 5 % of requests to staging-basis-api answered 5xx for 5 minutes. Read the API logs for BASIS-API-019/020 (database), BASIS-API-022 (Temporal) or BASIS-API-024 (a bug). ${local.runbook}"
  }
}

# --------------------------------------------------------------------------- worker down

# The worker pool is sized by hand (worker_instances, 1 while running): fewer than one
# instance for 5 minutes means nothing polls Temporal, so studies stop. The condition also
# opens when the metric stops arriving (EVALUATION_MISSING_DATA_ACTIVE): a pool with no
# instance may report no series at all. The application side of the same event is the
# stale-heartbeat alert the relay enqueues (basis.notify.heartbeats).
resource "google_monitoring_alert_policy" "worker_down" {
  count                 = local.alerts
  display_name          = "Basis staging: worker pool has no instance"
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.alert_channels

  conditions {
    display_name = "staging-basis-worker instance count < 1 for 5 min"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloud_run_worker_pool\"",
        "resource.label.worker_pool_name = \"${google_cloud_run_v2_worker_pool.worker.name}\"",
        "metric.type = \"run.googleapis.com/container/instance_count\"",
      ])
      comparison              = "COMPARISON_LT"
      threshold_value         = 1
      duration                = "300s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_ACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_SUM"
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Basis staging worker is down"
    content   = "The worker pool staging-basis-worker has had no running instance for 5 minutes: studies do not progress and decisions are not delivered. Check the pool's instances and the worker/relay/cloud-sql-proxy container logs; GET /v1/ops/health shows what the API sees. ${local.runbook}"
  }
}

# --------------------------------------------------------------------------- Cloud SQL down

# database/up is 1 while the server answers; 0 or absent while it is stopped or failed.
resource "google_monitoring_alert_policy" "sql_down" {
  count                 = local.alerts
  display_name          = "Basis staging: Cloud SQL not up"
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.alert_channels

  conditions {
    display_name = "staging-basis-pg database/up < 1 for 5 min"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"cloudsql_database\"",
        "resource.label.database_id = \"${var.project_id}:${google_sql_database_instance.staging.name}\"",
        "metric.type = \"cloudsql.googleapis.com/database/up\"",
      ])
      comparison              = "COMPARISON_LT"
      threshold_value         = 1
      duration                = "300s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_ACTIVE"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_MAX"
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Basis staging database is not up"
    content   = "Cloud SQL staging-basis-pg has not reported up for 5 minutes. If staging was parked on purpose, disable alerts_enabled. Otherwise see the instance's operations and, if data is lost, RUNBOOK.md section 1 (point-in-time recovery). ${local.runbook}"
  }
}

# --------------------------------------------------------------------------- study failures

# A log-based alert on the workflow error codes (docs/ERROR_CODES.md, area WF): every
# BASIS-WF- code except the two recorded outcomes (WF-010 a study cancelled by its
# engineer, WF-011 a review reminder). The worker logs JSON (basis.obs.logging): the code
# is the `error_code` field, or inside the message as ` [BASIS-WF-nnn]`. At most one
# notification every 5 minutes; the incident closes 30 minutes after the last match.
resource "google_monitoring_alert_policy" "workflow_failures" {
  count                 = local.alerts
  display_name          = "Basis staging: workflow failure code logged"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.alert_channels

  conditions {
    display_name = "BASIS-WF- error code in the worker's logs"
    condition_matched_log {
      filter = join(" AND ", [
        "resource.type = \"cloud_run_worker_pool\"",
        "resource.labels.worker_pool_name = \"${google_cloud_run_v2_worker_pool.worker.name}\"",
        "severity >= WARNING",
        "(jsonPayload.error_code =~ \"^BASIS-WF-\" OR jsonPayload.message =~ \"\\\\[BASIS-WF-[0-9]{3}\\\\]\")",
        "NOT jsonPayload.error_code =~ \"^BASIS-WF-01[01]$\"",
        "NOT jsonPayload.message =~ \"\\\\[BASIS-WF-01[01]\\\\]\"",
      ])
      label_extractors = {
        error_code = "EXTRACT(jsonPayload.error_code)"
        study_id   = "EXTRACT(jsonPayload.study_id)"
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
    notification_rate_limit {
      period = "300s"
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Basis staging: a study step failed"
    content   = "The worker logged a workflow error code (BASIS-WF-, docs/ERROR_CODES.md): a step timed out, was cancelled or failed. The study is `failed` and waits for POST /v1/studies/{id}/retry; GET /v1/studies/{id}/timeline?event_type=stage.failed names the step. The relay also emails ALERT_EMAIL_TO for every failed study. ${local.runbook}"
  }
}
