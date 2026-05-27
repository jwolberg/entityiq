"""Source adapters — uniform interface over external evidence providers.

Each adapter implements fetch(context) -> list[Evidence] per the contract
in base.py.  Failures are typed (never silent) and never propagate out of
the pipeline.
"""
