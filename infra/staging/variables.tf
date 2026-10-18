variable "project_id" {
  description = "GCP project that holds staging (created 2026-10-17; cost-seg-basis's IAM could not be granted)."
  type        = string
  default     = "basis-staging-512000"
}

variable "project_number" {
  description = "Number of project_id (used in Workload Identity principal names)."
  type        = string
  default     = "138338018557"
}

variable "firebase_project_id" {
  description = <<-EOT
    Firebase project whose ID tokens the API accepts (FIREBASE_PROJECT_ID). Firebase Auth
    stays in cost-seg-basis with the frontend: verifying a token needs only Google's public
    keys and the project id, no permission in that project.
  EOT
  type        = string
  default     = "cost-seg-basis"
}

variable "region" {
  description = "Region of every regional resource (Artifact Registry is already here)."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Zone of the Temporal VM and its data disk."
  type        = string
  default     = "us-central1-a"
}

variable "image" {
  description = <<-EOT
    Backend image for the first apply (the API, worker, relay and jobs share it). CI
    deploys every later image with gcloud, and the Cloud Run resources ignore image
    changes, so re-applying Terraform never rolls a deployment back.
  EOT
  type        = string
}

variable "github_repository" {
  description = "owner/name of the repository whose staging deploy job may impersonate the deployer."
  type        = string
  default     = "bryanrg22/Basis_Info"
}

variable "github_repository_id" {
  description = "Numeric id of github_repository; the WIF condition uses it because a name can be re-registered."
  type        = string
  default     = "1110700763"
}

variable "cors_allowed_origins" {
  description = "Browser origins of the frontend: the API's CORS_ALLOWED_ORIGINS and the uploads bucket's CORS."
  type        = list(string)
  default = [
    "https://cost-seg-basis.web.app",
    "https://cost-seg-basis.firebaseapp.com",
    "http://localhost:3000",
  ]
}

variable "sql_tier" {
  description = "Cloud SQL machine tier. db-g1-small: 1.7 GB, max_connections 50 (STAGING.md, database)."
  type        = string
  default     = "db-g1-small"
}

variable "temporal_machine_type" {
  description = "Machine type of the self-hosted Temporal VM (STAGING.md, Temporal)."
  type        = string
  default     = "e2-medium"
}

variable "temporal_namespace" {
  description = "Temporal namespace the worker and the API use."
  type        = string
  default     = "basis-staging"
}

variable "api_max_instances" {
  description = "Upper bound of API instances; each holds DB_POOL_SIZE connections."
  type        = number
  default     = 4
}

variable "worker_instances" {
  description = "Worker pool instances (each runs one worker and one relay). 0 parks staging."
  type        = number
  default     = 1
}

variable "app_env" {
  description = "Non-secret settings shared by the API, worker, relay and jobs (basis/config.py)."
  type        = map(string)
  default = {
    LOG_LEVEL = "INFO"
    # OpenAI models go to OpenAI directly: an Azure-first pool maps gpt-6-luna and
    # gpt-6.1-sol onto the Azure deployments (EVAL_REPORT 27.2 and 28 gotchas).
    LLM_PRIMARY = "openai"
  }
}


variable "cost_table_object" {
  description = "Object in the private bucket holding the licensed unit-cost table (COST_TABLE_PATH)."
  type        = string
  default     = "costs/unit_costs_rsmeans_2020.json"
}
