"""Shared watchlist ingestion (IS1-T1, ticket 0029).

Both offerings read sanctions lists from here: business verification screens
company names; individual screening parses person records with attributes.
Every ingest is recorded as a versioned ``list_snapshot`` so decisions can
cite exactly which list content they were made against (PRD-IDV F15/F16).
"""
