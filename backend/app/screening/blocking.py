"""Blocking: cheap, recall-first candidate generation (IS2-T2, ticket 0037).

A watchlist record becomes a candidate when any of its name variants
(primary, aliases, original script) shares at least one key with the
subject's name. Candidates are ranked by the number of shared keys and cut
at a cap. The cap must never drop a labeled true hit in the corpus; a CI
test enforces that.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from app.screening.names import name_keys, token_keys, tokens

DEFAULT_CAP = 200


@dataclass(frozen=True)
class Candidate:
    record_id: str
    matched_keys: tuple[str, ...]


class BlockingIndex:
    """Inverted index key → record ids over a set of watchlist records."""

    def __init__(self) -> None:
        self._by_key: dict[str, set[str]] = defaultdict(set)
        self._keys_of: dict[str, set[str]] = defaultdict(set)

    @classmethod
    def build(cls, records: Iterable) -> "BlockingIndex":
        index = cls()
        for record in records:
            rid = _get(record, "id")
            for name in _get(record, "names") or []:
                for key in name_keys(name["name"]):
                    index._by_key[key].add(rid)
                    index._keys_of[rid].add(key)
        return index

    def candidates(
        self, subject_name: str, *, cap: int = DEFAULT_CAP
    ) -> list[Candidate]:
        """Records sharing a key with the subject name.

        Records that match *every* keyed token of the subject name are always
        returned. Common names can have hundreds (real lists: 440 for
        "Mohammed Ali"), and dropping any would be a silent recall loss.
        ``cap`` only bounds the partial matches added after them.
        """
        token_key_groups = [token_keys(t) for t in tokens(subject_name)]
        token_key_groups = [g for g in token_key_groups if g]
        subject_keys = set().union(*token_key_groups) if token_key_groups else set()

        hits: dict[str, set[str]] = defaultdict(set)
        for key in subject_keys:
            for rid in self._by_key.get(key, ()):
                hits[rid].add(key)

        def is_full(keys: set[str]) -> bool:
            return all(group & keys for group in token_key_groups)

        full = sorted(rid for rid, keys in hits.items() if is_full(keys))
        partial = sorted(
            ((rid, keys) for rid, keys in hits.items() if not is_full(keys)),
            key=lambda kv: (-len(kv[1]), kv[0]),
        )
        ordered = [(rid, hits[rid]) for rid in full] + partial[:cap]
        return [Candidate(rid, tuple(sorted(keys))) for rid, keys in ordered]


def _get(record, attr: str):
    return record[attr] if isinstance(record, dict) else getattr(record, attr)
