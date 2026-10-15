"""Transactional outbox relay: delivers ``outbox`` rows to Temporal (S2_SPEC section 6).

:mod:`basis.relay.delivery` holds the delivery logic (usable in-process, e.g. by the
worker in development); :mod:`basis.relay.main` is the ``python -m basis.relay`` process.
"""
