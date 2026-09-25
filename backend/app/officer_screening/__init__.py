"""Officer/owner screening: the bridge between the two offerings (ADR-0006).

Business verification (KYB) and individual screening stay separate packages:
KYB code never imports ``app.screening`` and screening never imports KYB
domain modules. This package is the one place allowed to use both. It
collects the people behind a company and screens each one, and KYB only
reaches it by registering its pipeline stages (``default_stages``).
"""
