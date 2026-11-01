"""Internal system health (``GET /v1/ops/health``, docs/api/OPS_HEALTH.md).

* :mod:`basis.ops.heartbeat`: the heartbeat row each worker and relay process upserts.
* :mod:`basis.ops.probes`: the free metadata probes the worker runs against its providers
  (model, account and resource lookups; never inference, predictions or analyses).
* :mod:`basis.ops.runtime`: the worker's report: effective models and provider routes,
  adapters, warm-up, cost table, probe results.
* :mod:`basis.ops.evidence`: what the API reads (heartbeats, outbox backlog, ledger, corpus,
  cost index store, storage, Temporal pollers), each bounded.
* :mod:`basis.ops.services`: the stable service list composed from that evidence.
"""
