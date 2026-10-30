# Basis current system design

Snapshot: October 17, 2026, branch `rebuild/stage-2`, HEAD `f8d5f87`.

This is a code architecture snapshot, not a production deployment inventory. Code and checked-in test evidence were inspected; application services and the test suite were not started for this diagram. No application code was changed.

## New backend

- `backEnd/pyproject.toml`: Python 3.12, FastAPI, SQLAlchemy, asyncpg, Temporal SDK and pgvector dependencies.
- `backEnd/basis/api/app.py`: API dependency wiring, Firebase verifier, PostgreSQL and Temporal clients, GCS/local storage selection.
- `backEnd/basis/api/routes/documents.py`: signed direct-to-storage upload and completion verification; this contract is not yet used by the frontend.
- `backEnd/basis/api/routes/workflow.py`: `/v1/studies/{id}/start`, workflow status and cancellation.
- `backEnd/basis/workflow/study_workflow.py`: actual lifecycle, review gates, bounded photo parallelism, corrections and export.
- `backEnd/basis/worker/main.py`: worker registration, health and Prometheus endpoints.
- `backEnd/basis/relay/delivery.py`: outbox delivery to Temporal, retries and duplicate protection. Review decisions go through API → PostgreSQL outbox → relay → Temporal, not directly from a browser to the workflow. The engineer review box represents the business interaction, not a separately deployed service.
- `backEnd/basis/activities/{ingest_appraisal,extract_fields,analyze_photo,process_assets}.py`: placeholder chunks, metadata-based extraction, filename-based photo templates, small asset rules. These are not live AI integrations.
- `backEnd/basis/activities/export_report.py`: deterministic JSON export; PDF/evidence-pack output is future work.
- `backEnd/basis/db/models.py`: application tables and pgvector column. A vector schema does not establish that retrieval is implemented in the new backend.
- `docs/test-evidence/v0.3.0/README.md`: historical test result, including PostgreSQL 16 test image, lifecycle tests and explicit skipped live-cloud checks. Not rerun for this artifact.

## Existing frontend and legacy backend

- `frontend/package.json`: Next.js 14, React 18, TypeScript, Tailwind CSS 3 and Firebase SDK.
- `frontend/src/api/backend/workflow.api.ts`: legacy `/workflow/start`, `/workflow/resume`, status and evidence calls with Firebase tokens.
- `frontend/src/lib/firebase.ts`, `frontend/src/api/storage/file.api.ts`, `frontend/src/services/*.ts`: browser Firebase Auth, direct Firestore CRUD and Firebase Storage uploads.
- `docker-compose.yml`, `backEnd/Dockerfile`: still start `agentic.api.main` and the Firestore job worker, not `basis.api.main` and `basis.worker`.
- `backEnd/agentic/graph/workflow.py`: retained LangGraph workflow. README and regression evidence document incomplete end-to-end behavior.
- `backEnd/agentic/agents/vision_agent.py`, `document_extraction_agent.py`, `backEnd/evidence_layer/`: retained photo VLM, Azure DI/regex extraction and BM25/FAISS modules. The diagram groups modules for readability; it does not assert that all execute on every legacy request.
- `README.md`: documents legacy flow gaps and says backend is not currently deployed. No independent cloud deployment check was performed.

Grounding DINO/SAM2, the old MCP server, and benchmark model combinations are not represented as connected to the new Temporal activity path. Provider/model choice in production was not verified.
