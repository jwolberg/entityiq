"""Runtime imports must be declared as runtime (not dev-only) dependencies.

Adapters lazy-import their HTTP/WHOIS clients and degrade to "unavailable" when
the import fails, so a missing dependency never errors — it silently removes a
data source in production (found live on 2026-09-24).  P4-T3.
"""

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _runtime_deps() -> set[str]:
    data = tomllib.loads(_PYPROJECT.read_text())
    names = set()
    for spec in data["project"]["dependencies"]:
        name = spec.split("[")[0]
        for sep in ("==", ">=", "<=", "~=", ">", "<"):
            name = name.split(sep)[0]
        names.add(name.strip().lower())
    return names


def test_httpx_is_a_runtime_dependency():
    assert "httpx" in _runtime_deps()


def test_whois_client_is_a_runtime_dependency():
    assert "python-whois" in _runtime_deps()
