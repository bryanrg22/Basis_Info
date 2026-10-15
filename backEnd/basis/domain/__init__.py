"""Deterministic cost segregation domain logic (S3_SPEC section 1, ``basis/domain/``).

Everything here is a pure function over tables: the IRS classification rules
(:mod:`~basis.domain.rules`), room guidance (:mod:`~basis.domain.rooms`), takeoff
formulas (:mod:`~basis.domain.takeoffs`), cost tables (:mod:`~basis.domain.costs`),
classification validators (:mod:`~basis.domain.validators`) and the cross-stage checks
(:mod:`~basis.domain.cross_validation`). No I/O, no model calls.
"""
