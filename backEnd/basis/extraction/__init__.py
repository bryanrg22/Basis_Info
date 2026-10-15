"""Appraisal field extraction (S3_SPEC section 4).

``pipeline.extract_appraisal`` orchestrates: ``azure_di`` (optional) -> ``regex_fields``
and tables -> ``sections`` merge -> ``cleaners`` -> ``validation`` -> ``fallback`` (one
LLM call for failed critical fields) -> ``confidence`` (review gate); ``persist`` writes
the result. ``field_mappings`` is the single field catalogue every step keys on.
"""
