# Spend: the paid-call ledger

Every paid call a study makes is one row of `llm_calls` (EVAL_REPORT section 40): the model
calls of every stage (`kind: chat`), each Azure Document Intelligence analysis
(`document_analysis`, priced per page), each SAM 3 prediction (`localization`, priced per
predict second; the engineer's manual box prompts included) and each surface-segmenter
prediction (`segmentation`). The study budget is the SUM of `cost_usd` over the rows. Two
routes read it, both for the firm's owners by default (`403` for a member below the role, `404`
for a study not visible to the caller, a non-member included):

| Route | Answers |
|---|---|
| `GET /v1/studies/{id}/spend` | The rollup: the whole study, then by stage, activity, component, model and kind. |
| `GET /v1/studies/{id}/calls` | The rows, oldest first, keyset-paginated (`limit` up to 200, `cursor`), filtered by `activity`, `kind` or `entity_id`. |

**Who may read it.** What the firm pays per call is the owner's to see, so both routes need
the firm role `SPEND_ACCESS_ROLE` in the study's firm: `owner` (the default), `engineer`
(engineers and owners) or `viewer` (every member, the behaviour before the setting). The check
is the server's (`basis.api.routes.spend.spend_access`, read per request); the client hiding
the spend panel from engineers and viewers is presentation only. A member below the role gets
`403 forbidden` (`BASIS-API-010`, the detail names the role needed); a non-member gets the same
`404` as for an unknown study (`BASIS-STUDY-017`), so study ids cannot be probed.

The export (`rev-<n>.json`) carries the same rollup as `summary.spend`, and
`rev-<n>.calls.jsonl` (named in `files.calls_jsonl`) is the transcript: one JSON line per
call with the row's fields and, read back from the stored payloads, the prompt as sent and
the raw response.

## What one row says

The column names follow the OpenTelemetry GenAI semantic conventions where one exists
(`gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.id`,
`gen_ai.response.finish_reasons`, `gen_ai.usage.*`, `server.address`, `code.function.name`).

| Group | Members | Meaning |
|---|---|---|
| Who called | `stage`, `activity`, `activity_attempt`, `revision`, `run_id`, `component`, `build_sha` | The study's stage at the time, the Temporal activity and attempt, the Python `module.function` that made the call, the worker build. |
| About what | `entity_type`, `entity_id` | `photo` (document id), `room`, `component` (the component an asset is built from), `appraisal` (document id). |
| With which prompt | `prompt_version`, `prompt_name`, `prompt_sha256` | The released prompt (`basis/prompts/<name>/v<N>.md`) and the sha256 of its text. |
| Which model | `provider`, `endpoint`, `requested_model`, `model`, `response_id`, `request_id`, `finish_reason` | The provider and base URL (or Azure endpoint), the model asked for and the one the provider says answered (a dated snapshot on OpenAI), the provider's ids for the exchange, why it stopped. |
| With which parameters | `reasoning_effort`, `max_output_tokens`, `temperature`, `response_format`, `image_count`, `image_bytes`, `request_params` | As sent on the wire: the effort in effect, the output cap (on Claude the answer budget plus the thinking headroom), the temperature (null where the model takes none), the response schema's name, the images counted and sized; `request_params` has everything, tool names included, never the content. |
| Tokens | `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, `reasoning_output_tokens` | As reported. `input_tokens` is the whole prompt, cached tokens included; `output_tokens` the whole answer, reasoning included; the other three are the parts billed at their own rates (Anthropic reports the cache parts, OpenAI the cached and reasoning parts). |
| Cost | `cost_usd`, `price_row`, `pricing_version`; `quantity`, `unit`, `unit_price_usd` | Each token part at its own rate from the price table (`basis/llm/cost.py`); `price_row` is the table row used (`unknown`: the most expensive row was charged, add the model). Non-token kinds: pages or seconds at the unit price (`AZURE_DI_USD_PER_PAGE`, the localizer's hardware rate). |
| Time | `latency_ms`, `elapsed_ms`, `created_at` | The answering round trip; the call's wall time with the provider's retry and the pool's fallback. |
| Outcome | `status`, `error` | `error` rows reached the provider and were billed (an unusable answer, a failed prediction). |
| Payloads | `request_uri`, `request_sha256`, `response_uri`, `response_sha256` | `studies/<id>/llm/<call id>.request.json` (system prompt, messages as sent with each image replaced by its ref, media type, byte size and sha256, the tools, the response schema) and `.response.json` (the raw provider response, the usage as reported, the cost). API-key-shaped strings are redacted before writing. |

Rows written before the ledger (migration `0012_call_ledger`) have `kind: chat`, `status: ok`
and null in every other new column: the information was not recorded then.

## Examples

```http
GET /v1/studies/5f0c6a52-.../spend
```

```json
{
  "study_id": "5f0c6a52-...",
  "total": {"key": null, "calls": 724, "errors": 3, "input_tokens": 2814302,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "output_tokens": 165114, "reasoning_output_tokens": 61200,
            "cost_usd": "7.671234", "latency_ms_total": 3912345, "latency_ms_max": 48210,
            "quantity": "131.4000"},
  "by_stage": [{"key": "components_processing", "calls": 410, "...": "..."}],
  "by_activity": [{"key": "analyze_photo", "calls": 156, "...": "..."}],
  "by_component": [{"key": "basis.vision.analyze.call_until_usable", "...": "..."}],
  "by_model": [{"key": "claude-opus-5-5", "...": "..."}],
  "by_kind": [{"key": "chat", "...": "..."},
              {"key": "document_analysis", "calls": 1, "quantity": "6.0000", "cost_usd": "0.060000"}]
}
```

Lines are ordered by cost, largest first. `quantity` sums pages and seconds across kinds, so
read it per kind.

```http
GET /v1/studies/5f0c6a52-.../calls?activity=analyze_photo&limit=2
```

```json
{
  "items": [
    {
      "id": "0c1b...", "created_at": "2026-10-29T18:02:11.412Z", "kind": "chat", "status": "ok",
      "stage": "extracting", "revision": 1, "activity": "analyze_photo", "activity_attempt": 1,
      "component": "basis.vision.analyze.call_until_usable",
      "entity_type": "photo", "entity_id": "9d42...",
      "provider": "anthropic", "endpoint": "https://api.anthropic.com/",
      "requested_model": "claude-opus-5-5", "model": "claude-opus-5-5",
      "response_id": "msg_01...", "finish_reason": "end_turn",
      "prompt_version": "photo_countables/v3", "prompt_sha256": "3a9f...",
      "reasoning_effort": "medium", "max_output_tokens": 5296, "temperature": null,
      "response_format": "ModelPhotoAnalysis", "image_count": 1, "image_bytes": 141212,
      "input_tokens": 2931, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
      "output_tokens": 812, "reasoning_output_tokens": 0,
      "cost_usd": "0.027964", "price_row": "claude-opus-5-5", "pricing_version": "2026-10-29",
      "latency_ms": 6210, "elapsed_ms": 6214, "build_sha": "9c8f443",
      "request_uri": "gs://basis-local/studies/5f0c.../llm/0c1b....request.json",
      "response_uri": "gs://basis-local/studies/5f0c.../llm/0c1b....response.json"
    }
  ],
  "next_cursor": "eyJ0Ijoi..."
}
```

Errors: `422` for a malformed `cursor` or a `kind` outside the four; the usual `401`, `403`,
`404`, `429`, `503`.
