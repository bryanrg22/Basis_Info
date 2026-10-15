"""Component classification: rules, the approved-answer cache, and the residual model.

``router.classify`` is the entry point (S3_SPEC section 2). ``tools.build_tools`` binds
the retrieval tools to one study; ``residual.run_residual`` is the bounded model loop;
``verify`` checks every citation; ``cache`` reads and seeds ``classification_cache``.
``store_protocol`` restates the retrieval store's interface so this package does not
depend on ``basis.retrieval``.
"""
