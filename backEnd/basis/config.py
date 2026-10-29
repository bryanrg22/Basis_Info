"""Runtime configuration.

Rules (BUILD_PLAN C1):

* The environment is explicit: ``BASIS_ENV`` must be set in the process environment to one
  of ``dev``, ``test``, ``staging``, ``prod``. Nothing is inferred from which other
  variables happen to be present.
* A ``.env`` file is read only when ``BASIS_ENV=dev``. In ``test``, ``staging`` and
  ``prod`` every value comes from the process environment (in the cloud, Secret Manager
  values are injected as environment variables by Cloud Run).
* ``staging`` and ``prod`` refuse to start when a required value is missing.
* Secret values are ``SecretStr`` and connection strings are excluded from ``repr``, so
  printing or logging the settings object does not leak them. Validation errors hide
  their input for the same reason.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final, Literal, get_args

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from basis.llm.families import (
    CLAUDE_EFFORTS,
    is_claude_model,
    is_reasoning_model,
    refuses_no_effort,
)

Env = Literal["dev", "test", "staging", "prod"]
# A reasoning effort: OpenAI's ``reasoning_effort`` on a reasoning model ("none" and
# "minimal" are OpenAI only), Claude's ``output_config.effort`` on a ``claude-*`` model.
Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

ENV_VAR: Final = "BASIS_ENV"
DEFAULT_DEV_ENV_FILE: Final = ".env"
VALID_ENVS: Final[tuple[str, ...]] = get_args(Env)
# Per-photo output budgets when VISION_MAX_TOKENS is unset (EVAL_REPORT section 12.1).
VISION_MAX_TOKENS_REASONING: Final = 6000
VISION_MAX_TOKENS_DEFAULT: Final = 1200
# Output budget of the extraction fallback when EXTRACTION_FALLBACK_MAX_TOKENS is unset: an
# OpenAI reasoning model gets what EVAL_REPORT section 17.1 ran it at (it spends output on
# reasoning first), and so does any model when the fallback asks for every missing field
# (EXTRACTION_FALLBACK_SCOPE=missing: dozens of values, section 17.1 ran every model at
# 8000); otherwise the 800 the pipeline always used for the few critical fields.
EXTRACTION_MAX_TOKENS_REASONING: Final = 8000
EXTRACTION_MAX_TOKENS_DEFAULT: Final = 800

# A browser origin as the Origin header carries it: scheme, host, optional port, nothing else.
_ORIGIN: Final = re.compile(r"https?://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?")
_LOCAL_ORIGIN: Final = re.compile(r"http://(?:localhost|127\.0\.0\.1)(?::\d{1,5})?")

_REQUIRED_IN_DEPLOYED: Final = ("database_url", "gcs_bucket", "firebase_project_id")
_REQUIRED: Final[dict[str, tuple[str, ...]]] = {
    "dev": (),
    "test": (),
    "staging": _REQUIRED_IN_DEPLOYED,
    "prod": _REQUIRED_IN_DEPLOYED,
}


class ConfigError(RuntimeError):
    """Configuration is missing or invalid.

    Deliberately not a ``ValueError``: pydantic would wrap a ``ValueError`` raised in a
    validator into a ``ValidationError``; this one propagates with only our message.
    """


def required_in(env: Env) -> tuple[str, ...]:
    """Names of the settings fields that must be set in ``env``."""
    return _REQUIRED[env]


class Settings(BaseSettings):
    """Process configuration. Build it with :func:`load_settings`, not directly."""

    model_config = SettingsConfigDict(
        env_file=None,  # chosen per construction by load_settings()
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    env: Env = Field(validation_alias=ENV_VAR)
    git_sha: str = "unknown"
    log_level: LogLevel = "INFO"
    database_url: str | None = Field(default=None, repr=False)
    # Connections held per process (SQLAlchemy pool_size; max_overflow is always 0).
    db_pool_size: int = Field(default=5, ge=1, le=100)
    # Cloud SQL instance connection name ("project:region:instance"). When set, the
    # engine connects through the Cloud SQL Python Connector instead of database_url.
    cloud_sql_instance: str | None = None
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_api_key: SecretStr | None = None
    temporal_task_queue: str = "basis-studies"
    temporal_worker_versioning: bool = False
    temporal_deployment_name: str = "basis-worker"
    temporal_build_id: str | None = None  # defaults to git_sha when unset
    photo_concurrency: int = Field(default=5, ge=1, le=32)
    review_reminder_days: int = Field(default=7, ge=1)
    review_deadline_days: int = Field(default=30, ge=1)
    # How long a firm invitation can be accepted (basis.db.repositories.invitation).
    invitation_ttl_days: int = Field(default=14, ge=1, le=90)
    study_budget_usd: float = Field(default=25.0, gt=0)
    relay_poll_seconds: float = Field(default=2.0, gt=0)
    gcs_bucket: str | None = None
    firebase_project_id: str | None = None
    rate_limit_reads_per_min: int = Field(default=240, gt=0)
    rate_limit_mutations_per_min: int = Field(default=60, gt=0)
    # Browser origins allowed to call the API (CORS), comma-separated, e.g.
    # "https://cost-seg-basis.web.app,http://localhost:3000". Each is scheme://host[:port]
    # with no path. Empty: no CORS headers, so only same-origin pages and non-browser
    # clients can use the API. Outside dev and test "*" is refused, and so is plain http
    # except for localhost (a local frontend pointed at staging).
    cors_allowed_origins: Annotated[tuple[str, ...], NoDecode] = ()
    # Local stacks without GCS (dev and test only): the API's own base URL as the browser
    # reaches it, e.g. "http://127.0.0.1:8000". Local storage then signs upload URLs on the
    # API's dev upload route (basis.api.local_uploads) so a browser can upload; unset, they
    # are opaque local-upload:// URLs that only tests and scripts can use.
    local_upload_base_url: str | None = None
    # Sentence-embedding model for the evidence corpora (recorded per corpus version).
    # "hash" selects the deterministic test embedder and is refused outside dev and test.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Hybrid retrieval (Retrieval.hybrid -> basis.retrieval.hybrid.hybrid_search): how BM25
    # and vector results are fused, and BM25's weight in "weighted" (max-normalized convex
    # combination; unused by "rrf"). Measured on the 50 retrieval qrels, EVAL_REPORT section
    # 22: RRF (k=60) stays the default. Weighted 0.3 scored 0.019 nDCG@10 higher with
    # unjudged pages counted as misses, but 0.014 lower on condensed lists (unjudged pages
    # removed), so the 50 queries do not show either fusion to be better.
    retrieval_fusion: Literal["rrf", "weighted"] = "rrf"
    retrieval_bm25_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    # The chunk window at ingest (cl100k tokens; basis.retrieval.chunk). A change applies
    # to documents ingested after it; carried-forward documents keep their chunks.
    # EVAL_REPORT section 25: 200/40 (chunks that fit MiniLM's 256 word pieces) and 512-token
    # embedders on 400/80 (e5-small-v2 best, hybrid +0.015 nDCG@10) are inside the noise of
    # the 50 queries, so the defaults stay.
    retrieval_chunk_tokens: int = Field(default=400, ge=50, le=2000)
    retrieval_chunk_overlap: int = Field(default=80, ge=0)
    # What the classifier's search_reference tool and resolve_rule_citations search with:
    # the store's BM25 or Retrieval.hybrid (BM25 + vectors, fused as above). EVAL_REPORT
    # section 25: on the assets eval the two tie (bucket accuracy identical), and through the
    # hybrid 3 of the 77 rules-table citations stop resolving (RRF lets chunks both lists
    # like outrank BM25's exact match), so BM25 stays. Turning hybrid on for the tool should
    # first keep resolve_rule_citations on BM25.
    retrieval_search: Literal["bm25", "hybrid"] = "bm25"

    # ------------------------------------------------------------------ model providers
    # Environment names match the legacy backEnd/.env (agentic/config/settings.py), so an
    # existing .env works unchanged; the newer names are accepted too. Mapping:
    #   OPENAI_API_KEY                       -> openai_api_key
    #   OPENAI_MODEL | TEXT_MODEL            -> text_model   (extraction fallback model)
    #   EXTRACTION_GROUNDED_MODEL            -> extraction_grounded_model (appraisal reading)
    #   VISION_MODEL                         -> vision_model (model for photos)
    #   VISION_SURFACES_MODEL                -> vision_surfaces_model (decomposed surfaces call)
    #   VISION_REASONING_EFFORT              -> vision_reasoning_effort (photos, default low)
    #   VISION_MAX_TOKENS                    -> vision_max_tokens (photo output budget)
    #   AZURE_OPENAI_ENDPOINT / _API_KEY     -> azure_openai_endpoint / azure_openai_api_key
    #   AZURE_OPENAI_API_VERSION             -> azure_openai_api_version (>= 2024-08-01)
    #   AZURE_OPENAI_DEPLOYMENT_NAME         -> azure_openai_deployment_name (default)
    #   AZURE_OPENAI_VISION_DEPLOYMENT       -> azure_openai_vision_deployment
    #   AZURE_OPENAI_NANO_DEPLOYMENT | AZURE_OPENAI_TEXT_DEPLOYMENT
    #                                        -> azure_openai_text_deployment
    #   AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT / _KEY -> azure_document_intelligence_*
    #   LLM_REQUEST_TIMEOUT | LLM_REQUEST_TIMEOUT_SECONDS -> llm_request_timeout_seconds
    #   ANTHROPIC_API_KEY                    -> anthropic_api_key (Claude models, claude-*)
    #   ANTHROPIC_EFFORT                     -> anthropic_effort (thinking depth, default low)
    openai_api_key: SecretStr | None = None
    # The extraction fallback's model and effort: EVAL_REPORT section 17.1's choice,
    # gpt-6-luna at low effort (0.924 of 75 fields, 12/12 critical, the cheapest measured).
    text_model: str = Field(
        default="gpt-6-luna",
        validation_alias=AliasChoices("text_model", "TEXT_MODEL", "OPENAI_MODEL"),
    )
    extraction_reasoning_effort: Effort = "low"
    # The per-photo vision step's production configuration is EVAL_REPORT section 12.1's
    # decision: gpt-6.1-sol, two-call (decomposed) prompts, reasoning effort low, output
    # budget 6000 (Claude Opus 5.5 decomposed is the fallback: VISION_MODEL=claude-opus-5-5).
    vision_model: str = "gpt-6.1-sol"
    # The decomposed step's surfaces-and-room-type call (photo_surfaces) runs on its own
    # model: EVAL_REPORT section 28.1 measured gpt-6-luna equal to gpt-6.1-sol on room type,
    # surface F1 and report rows at an eighteenth of the price (it lists fewer surfaces per
    # photo, with fewer false ones). VISION_MODEL stays the countables call (and the
    # single-call step), so the Opus fallback above needs both settings;
    # VISION_REASONING_EFFORT and VISION_MAX_TOKENS apply to both calls.
    vision_surfaces_model: str = "gpt-6-luna"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment_name: str | None = None
    azure_openai_vision_deployment: str | None = None
    azure_openai_text_deployment: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "azure_openai_text_deployment",
            "AZURE_OPENAI_TEXT_DEPLOYMENT",
            "AZURE_OPENAI_NANO_DEPLOYMENT",
        ),
    )
    # Claude models (basis.llm.anthropic_provider). A ``claude-*`` request always goes to
    # this provider; ``gpt-*`` and other names go to OpenAI or Azure, whatever the primary.
    anthropic_api_key: SecretStr | None = None
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    # Which configured provider is tried first among those that serve the request's model;
    # the next one is the per-call fallback.
    llm_primary: Literal["azure", "openai", "anthropic"] = "azure"
    llm_request_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=600,
        validation_alias=AliasChoices(
            "llm_request_timeout_seconds", "LLM_REQUEST_TIMEOUT_SECONDS", "LLM_REQUEST_TIMEOUT"
        ),
    )
    # Concurrent model calls per worker process (the provider pool's semaphore).
    llm_concurrency: int = Field(default=4, ge=1, le=64)
    # Output budget of each per-photo call. Unset: VISION_MAX_TOKENS_REASONING for an OpenAI
    # reasoning model (it spends the budget on reasoning before the answer; 12.1 ran sol
    # at 6000), else VISION_MAX_TOKENS_DEFAULT (the budget every non-reasoning run used).
    # Read the value in effect through ``vision_output_budget``.
    vision_max_tokens: int | None = Field(default=None, ge=64, le=16384)
    # Reasoning effort of the per-photo calls: OpenAI ``reasoning_effort`` on a reasoning
    # model, the Anthropic effort on a Claude model; ignored by other models. "minimal"
    # is OpenAI only. The study's text calls keep their own (ANTHROPIC_EFFORT, none).
    vision_reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh", "max"] = "low"
    # The per-photo vision step (basis.vision.analyze): "single" sends one photo_analysis
    # call per photo; "decomposed" sends two narrow calls (photo_countables and
    # photo_surfaces) and merges them (EVAL_REPORT sections 11 and 12.1, the default).
    vision_photo_mode: Literal["single", "decomposed"] = "decomposed"
    # Per-class count fusion (EVAL_REPORT section 15): with "per_class" the decomposed step
    # also sends the countables prompt to VISION_FUSION_MODEL, concurrently, and takes each
    # count class from the source section 15.4 chose (basis.vision.fusion.PER_CLASS_SOURCE).
    # "off" is the two-call step alone. The single-call mode has no countables call, so
    # fusion is inactive there (a deployment pinned to the pre-12.1 configuration keeps it
    # exactly); read the effective value through ``vision_count_fusion_active``.
    vision_count_fusion: Literal["off", "per_class"] = "per_class"
    vision_fusion_model: str = "claude-opus-5-5"
    vision_fusion_reasoning_effort: Effort = "medium"
    # Unset: the family default (see ``extraction_output_budget``). Read the value in effect
    # through ``extraction_output_budget``.
    extraction_fallback_max_tokens: int | None = Field(default=None, ge=64, le=16384)
    # Which appraisal fields the extraction fallback asks the model for (EVAL_REPORT 27):
    # "missing" every catalogue field Azure DI and the regex pass left empty or failed,
    # with the whole document (up to EXTRACTION_FALLBACK_CONTEXT_CHARS) as context, as
    # section 17.1 measured; "critical" only the critical ones, with their sections' pages
    # (before section 27).
    extraction_fallback_scope: Literal["missing", "critical"] = "missing"
    extraction_fallback_context_chars: int = Field(default=60_000, ge=4_000, le=400_000)
    azure_document_intelligence_endpoint: str | None = None
    azure_document_intelligence_key: SecretStr | None = None
    azure_di_timeout_seconds: float = Field(default=180.0, gt=0, le=1800)
    # A critical appraisal field below this confidence sends the extraction to review
    # (S3_SPEC section 4; tuned later by the real-data eval).
    extraction_review_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    # How an appraisal PDF is read (basis.extraction.pipeline). "grounded" (EVAL_REPORT
    # section 33.5's winner, the default): one structured call on EXTRACTION_GROUNDED_MODEL
    # asks every field for its status, value, page and verbatim quote over Azure DI's page
    # text with labelled selection marks, and the answers are checked mechanically
    # (basis.extraction.grounded; 0.940 of all fields on the 16-document subset, every
    # critical field right, about $0.072 a document on gpt-6.1-sol at effort low). When it
    # cannot run or its call fails (no Azure DI geometry, no provider, a provider error, the
    # budget) the document is read by "pipeline" instead, on the same analysis. "pipeline"
    # (before section 33): Azure DI pairs, the text layer and tables, then one TEXT_MODEL
    # fallback call for what they left (0.825 on the same subset). A UAD 3.6 XML or ZIP
    # upload is read by the XML reader whatever this says.
    extraction_method: Literal["grounded", "pipeline"] = "grounded"
    extraction_grounded_model: str = "gpt-6.1-sol"
    extraction_grounded_reasoning_effort: Effort = "low"
    # The grounded call's output budget: the sweep's 32,000 (about 4,900 output tokens were
    # used at most; an OpenAI reasoning model spends part of it reasoning, and a cut-off
    # answer has no fields). Only the tokens used are billed; the study budget reserves it
    # all before the call (basis.llm.call), about $0.35 on gpt-6.1-sol.
    extraction_grounded_max_tokens: int = Field(default=32000, ge=1024, le=128000)

    # The room step (reconcile_rooms, EVAL_REPORT section 16.5): per room of the revision,
    # one-photo rooms take the per-photo analysis as their inventory (ROOM_SINGLE_PHOTO
    # "per_photo"; "reconcile" sends them the call too), multi-photo rooms one
    # room_inventory/ROOM_PROMPT_VERSION call on ROOM_MODEL at ROOM_REASONING_EFFORT, then a
    # surface_material second look (ROOM_MATERIALS: "sensitive" looks at every wainscoting,
    # wall finish and tile, "disagree" only where the photos disagree, "none" skips it) on
    # the same model. ROOM_RECONCILE=false turns the step off: assets come from the
    # per-photo components, as before section 16.
    room_reconcile: bool = True
    room_prompt_version: str = Field(default="v3", pattern=r"^v[1-9]\d*$")
    # gpt-6-luna reconciles within the noise of Sonnet at about half the room step's cost
    # (EVAL_REPORT 28.2 and 31; the production configuration, luna at the reconciler's 4000
    # token budget with Sonnet keeping the second look, was re-run and matched). Materials
    # stay on ROOM_MATERIALS_MODEL because they decide IRS buckets.
    room_model: str = "gpt-6-luna"
    room_reasoning_effort: Effort = "low"
    room_single_photo: Literal["per_photo", "reconcile"] = "per_photo"
    room_materials: Literal["none", "disagree", "sensitive"] = "sensitive"
    # The material second look's own model and effort (EVAL_REPORT 28.2: a cheaper model
    # reconciles rooms within the noise of Sonnet but reads materials worse, so the two can
    # be set apart). Defaults: the second look as section 16.5 measured it, on Sonnet low.
    room_materials_model: str = "claude-sonnet-5-5"
    room_materials_reasoning_effort: Effort = "low"
    # How analyze_photo puts a photo in a room (EVAL_REPORT 23.3 and 27): "filename" groups
    # by the file name's room (basis.vision.rooms.group_photos, after the study's
    # room_aliases) and uses the model's room identity only for a name that says nothing
    # (IMG_1234.JPG); "model" always uses the model's room identity (before section 27).
    room_grouping: Literal["filename", "model"] = "filename"
    # Measured but off (EVAL_REPORT 26.10, to be confirmed on a second client package):
    # ROOM_STEP_MODE=room_call gives every interior room (a room whose voted type is not
    # exterior, parking or mechanical: basis.vision.room_step.room_path) one
    # room_photos_inventory/v2 call with all its photos on ROOM_CALL_MODEL at
    # ROOM_CALL_REASONING_EFFORT, instead of the reconciliation call over per-photo lists
    # (section 26.6: room count MAE 0.236 against 0.350); exterior, site and mechanical
    # rooms keep the "reconcile" path (the ROOM_* settings above, 26.7).
    room_step_mode: Literal["reconcile", "room_call"] = "reconcile"
    room_call_model: str = "claude-opus-5-5"
    room_call_reasoning_effort: Effort = "high"
    # MATERIALS_SECOND_LOOK=surface_crops (EVAL_REPORT 26.3, measured but off): the wall,
    # floor and ceiling masks of Mask2Former (SURFACE_SEGMENTER: "local" runs the measured
    # Swin-L ADE20K panoptic checkpoint in the worker on the CPU; "replicate" a community
    # Swin-S ADE20K semantic deployment, not measured), one surface_crop_material/v1 call
    # per surface on SURFACE_CROP_MODEL, and the room's wall finish, wainscoting, tile and
    # ceiling finish take the crops' majority answer where a crop answered; elsewhere the
    # per-photo material stands (material hit 0.79 against 0.64). "per_photo" is today's.
    materials_second_look: Literal["per_photo", "surface_crops"] = "per_photo"
    surface_crop_model: str = "claude-sonnet-5-5"
    surface_crop_reasoning_effort: Effort = "low"
    surface_segmenter: Literal["local", "replicate"] = "local"
    surface_segmenter_replicate_version: str = "95798aa39f0e174abceaa1235f757574c7c19feae0967602e1a0f1c2d7f816b1"  # pragma: allowlist secret  # noqa: E501

    # Classification and pricing (process_assets, S3_SPEC section 5). The residual
    # classifier is a tool loop: EVAL_REPORT section 17.2/17.3 chose claude-sonnet-5-5 at
    # low effort. An OpenAI reasoning model can run it on Chat Completions only at
    # CLASSIFY_REASONING_EFFORT=none (gpt-6.1-sol not at all); see _check_models.
    classify_model: str = "claude-sonnet-5-5"
    classify_reasoning_effort: Effort = "low"
    classify_concurrency: int = Field(default=4, ge=1, le=32)
    # residential_rental (27.5-year building) or nonresidential (39-year building).
    property_class: Literal["residential_rental", "nonresidential"] = "residential_rental"
    cost_year: int = Field(default=2026, ge=2020, le=2026)  # the cost table's range
    cost_location_factor: float = Field(default=1.0, gt=0, le=3)
    # Where the takeoff's areas come from (basis.domain.measurements; EVAL_REPORT section
    # 35). appraisal: room areas from the appraisal's GLA, basement and garage fields,
    # roof/siding/gutter/landscaping from the footprint and site area, the typical areas
    # only where a field is missing; typical: the legacy typical area per room type for
    # every room and no property-level quantities (the behaviour before section 35).
    takeoff_areas: Literal["appraisal", "typical"] = "appraisal"
    # Where the cost year and location factor come from. study: the state's factor
    # (basis.domain.costs.REGIONAL_COST_FACTORS) from the appraisal's subject.state and the
    # year of its effective date (the placed-in-service date once the intake carries it),
    # each falling back to the setting with the reason on the cost row; setting: COST_YEAR
    # and COST_LOCATION_FACTOR for every study.
    cost_index: Literal["study", "setting"] = "study"
    # The engineer's unit-cost table (basis.domain.cost_table: JSON, licensed data, never
    # in the repository); unset: only the six legacy rows price (EVAL_REPORT 27, item 6).
    cost_table_path: str | None = None

    # Object localization (basis.vision.localize): SAM 3 on Replicate, for trials only
    # (production self-hosts it; VISION_DETECTION_RESEARCH.md section 4). The community
    # model is addressed by version id: it is not served at /v1/models/<owner>/<name>.
    replicate_api_token: SecretStr | None = None
    sam3_replicate_version: str = "1bf97763d5dfd3a1584adca913a8ef4b43c684fca97e04e39e4c50a3a5e09650"  # pragma: allowlist secret  # noqa: E501
    # Concurrent SAM 3 predictions, per worker process (the localizer's semaphore) and per
    # study (the workflow's localization slots). EVAL_REPORT section 30, 52 photos at once on
    # Replicate: 3, 6 and 12 all take 55 to 63 s (the account's create rate limit is the
    # bound), 3 with the fewest 429s; 1 takes 76 s.
    localizer_concurrency: int = Field(default=3, ge=1, le=32)
    # A self-hosted SAM 3 Cog server instead of Replicate (SAM3_HOSTING.md): its base URL,
    # and its credentials: a static bearer token, or a Google ID token minted from the
    # worker's service account (Cloud Run with authentication). Billed per instance second
    # (Cloud Run us-east4: one L4, 4 vCPU, 16 GiB, no zonal redundancy).
    localizer_url: str | None = None
    localizer_token: SecretStr | None = None
    localizer_google_auth: bool = False
    localizer_usd_per_second: float = Field(default=0.0002907, gt=0)
    # The rooms review opens once the photos are analyzed and the room step is done, with
    # SAM 3 still localizing in the background (EVAL_REPORT 30); true waits for it first.
    rooms_gate_waits_for_localization: bool = False
    # An engineer's box at the rooms review that matches no stored instance is sent to the
    # same localizer from the API (POST .../objects) while the engineer waits: the call is
    # bounded by this many seconds, after which the box is stored without an outline.
    object_prompt_timeout_s: float = Field(default=30.0, gt=0, le=120)

    @property
    def vision_output_budget(self) -> int:
        """The per-photo calls' output budget: ``VISION_MAX_TOKENS`` when set, else the
        default for the vision model's family."""
        if self.vision_max_tokens is not None:
            return self.vision_max_tokens
        if is_reasoning_model(self.vision_model):
            return VISION_MAX_TOKENS_REASONING
        return VISION_MAX_TOKENS_DEFAULT

    @property
    def vision_surfaces_output_budget(self) -> int:
        """The surfaces call's output budget: ``VISION_MAX_TOKENS`` when set, else the
        default for the surfaces model's family."""
        if self.vision_max_tokens is not None:
            return self.vision_max_tokens
        if is_reasoning_model(self.vision_surfaces_model):
            return VISION_MAX_TOKENS_REASONING
        return VISION_MAX_TOKENS_DEFAULT

    @property
    def vision_count_fusion_active(self) -> bool:
        """Whether the per-photo step fuses counts (``per_class`` and the decomposed mode)."""
        return self.vision_count_fusion != "off" and self.vision_photo_mode == "decomposed"

    @property
    def vision_fusion_output_budget(self) -> int:
        """The fusion countables call's output budget: ``VISION_MAX_TOKENS`` when set, else
        the default for the fusion model's family (Claude: the 1200-token answer budget
        section 14/15 ran Opus at; the provider adds its thinking allowance on top)."""
        if self.vision_max_tokens is not None:
            return self.vision_max_tokens
        if is_reasoning_model(self.vision_fusion_model):
            return VISION_MAX_TOKENS_REASONING
        return VISION_MAX_TOKENS_DEFAULT

    @property
    def extraction_output_budget(self) -> int:
        """The extraction fallback's output budget: ``EXTRACTION_FALLBACK_MAX_TOKENS`` when
        set, else the default for the text model's family."""
        if self.extraction_fallback_max_tokens is not None:
            return self.extraction_fallback_max_tokens
        if is_reasoning_model(self.text_model) or self.extraction_fallback_scope == "missing":
            return EXTRACTION_MAX_TOKENS_REASONING
        return EXTRACTION_MAX_TOKENS_DEFAULT

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _check_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for origin in value:
            if origin != "*" and not _ORIGIN.fullmatch(origin):
                raise ConfigError(
                    f"CORS_ALLOWED_ORIGINS entry {origin!r} is not an origin: use "
                    "scheme://host[:port] with no path or trailing slash"
                )
        return value

    @model_validator(mode="after")
    def _check_required(self) -> Settings:
        missing = [name for name in required_in(self.env) if not _is_set(getattr(self, name))]
        if missing:
            names = ", ".join(name.upper() for name in missing)
            raise ConfigError(f"{ENV_VAR}={self.env} requires these settings to be set: {names}")
        if self.embedding_model == "hash" and self.env not in ("dev", "test"):
            raise ConfigError(f"EMBEDDING_MODEL=hash is for tests only, not {ENV_VAR}={self.env}")
        if self.env not in ("dev", "test"):
            for origin in self.cors_allowed_origins:
                if origin == "*":
                    raise ConfigError(
                        f"CORS_ALLOWED_ORIGINS=* is for dev and test only, not {ENV_VAR}="
                        f"{self.env}; list the frontend's origins"
                    )
                if origin.startswith("http://") and not _LOCAL_ORIGIN.fullmatch(origin):
                    raise ConfigError(
                        f"CORS_ALLOWED_ORIGINS entry {origin!r} is plain http; outside dev "
                        "and test only https origins and http://localhost are allowed"
                    )
        if self.local_upload_base_url is not None:
            if self.env not in ("dev", "test"):
                raise ConfigError(
                    f"LOCAL_UPLOAD_BASE_URL is for local stacks (dev and test), not "
                    f"{ENV_VAR}={self.env}"
                )
            if not _ORIGIN.fullmatch(self.local_upload_base_url):
                raise ConfigError(
                    f"LOCAL_UPLOAD_BASE_URL {self.local_upload_base_url!r} is not a base URL: "
                    "use scheme://host[:port] with no path or trailing slash"
                )
        if self.retrieval_chunk_overlap >= self.retrieval_chunk_tokens:
            raise ConfigError(
                f"RETRIEVAL_CHUNK_OVERLAP ({self.retrieval_chunk_overlap}) must be smaller than "
                f"RETRIEVAL_CHUNK_TOKENS ({self.retrieval_chunk_tokens})"
            )
        if (
            self.materials_second_look == "surface_crops"
            and self.surface_segmenter == "replicate"
            and not _is_set(self.replicate_api_token)
        ):
            raise ConfigError(
                "MATERIALS_SECOND_LOOK=surface_crops with SURFACE_SEGMENTER=replicate needs "
                "REPLICATE_API_TOKEN; set it or use SURFACE_SEGMENTER=local"
            )
        self._check_models()
        return self

    def _check_models(self) -> None:
        """Refuse model and effort combinations that would fail on the first call."""
        for name, model, effort in (
            ("VISION_SURFACES", self.vision_surfaces_model, self.vision_reasoning_effort),
            ("VISION_FUSION", self.vision_fusion_model, self.vision_fusion_reasoning_effort),
            ("ROOM", self.room_model, self.room_reasoning_effort),
            ("ROOM_MATERIALS", self.room_materials_model, self.room_materials_reasoning_effort),
            ("ROOM_CALL", self.room_call_model, self.room_call_reasoning_effort),
            ("SURFACE_CROP", self.surface_crop_model, self.surface_crop_reasoning_effort),
            ("CLASSIFY", self.classify_model, self.classify_reasoning_effort),
            ("EXTRACTION", self.text_model, self.extraction_reasoning_effort),
            (
                "EXTRACTION_GROUNDED",
                self.extraction_grounded_model,
                self.extraction_grounded_reasoning_effort,
            ),
        ):
            if is_claude_model(model) and effort not in CLAUDE_EFFORTS:
                raise ConfigError(
                    f"{name} reasoning effort {effort!r} is not a Claude effort level for "
                    f"{model}; use one of {', '.join(CLAUDE_EFFORTS)}"
                )
        if (
            refuses_no_effort(self.extraction_grounded_model)
            and self.extraction_grounded_reasoning_effort == "none"
        ):
            # EVAL_REPORT section 17.2: gpt-6.1-sol answers 400 to reasoning_effort=none.
            raise ConfigError(
                f"EXTRACTION_GROUNDED_MODEL={self.extraction_grounded_model} refuses "
                "reasoning effort 'none'; set EXTRACTION_GROUNDED_REASONING_EFFORT=low "
                "(EVAL_REPORT section 33.5) or another level"
            )
        if is_reasoning_model(self.classify_model):
            # Chat Completions refuses function tools with a reasoning effort (EVAL_REPORT
            # section 17.2: 400 "Function tools with reasoning_effort are not supported").
            if refuses_no_effort(self.classify_model):
                raise ConfigError(
                    f"CLASSIFY_MODEL={self.classify_model} cannot run the tool-calling "
                    "classifier: it refuses reasoning_effort=none and Chat Completions "
                    "refuses tools with any other effort (needs the Responses API); use a "
                    "claude-* model (default claude-sonnet-5-5)"
                )
            if self.classify_reasoning_effort != "none":
                raise ConfigError(
                    f"CLASSIFY_MODEL={self.classify_model} is an OpenAI reasoning model: "
                    "the tool-calling classifier runs it on Chat Completions, which refuses "
                    f"tools with reasoning_effort={self.classify_reasoning_effort}; set "
                    "CLASSIFY_REASONING_EFFORT=none or use a claude-* model"
                )


def _is_set(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value().strip())
    if isinstance(value, str):
        return bool(value.strip())
    return True


def load_settings(env_file: str | Path = DEFAULT_DEV_ENV_FILE) -> Settings:
    """Build settings from the process environment.

    ``BASIS_ENV`` is read from the process environment first (never from a file) and
    decides whether ``env_file`` is consulted at all: only ``dev`` reads it. Real
    environment variables always take precedence over the file.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is None or not raw.strip():
        raise ConfigError(f"{ENV_VAR} is required and must be one of: {', '.join(VALID_ENVS)}")
    if raw not in VALID_ENVS:
        raise ConfigError(
            f"{ENV_VAR}={raw!r} is not valid; expected one of: {', '.join(VALID_ENVS)}"
        )
    chosen_file = env_file if raw == "dev" else None
    # env is read from the environment through its BASIS_ENV alias.
    return Settings(_env_file=chosen_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, built once."""
    return load_settings()


def reset_settings() -> None:
    """Drop the cached settings so the next :func:`get_settings` re-reads the environment."""
    get_settings.cache_clear()
