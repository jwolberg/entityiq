"""Per-subject envelope encryption (IS1-T5, ticket 0033; ADR-0003, ADR-0004).

Each screening subject gets its own random AES-256-GCM data key. The data key
is stored only wrapped, encrypted with the master key from
ENTITYIQ_SCREENING_MASTER_KEY (base64, 32 bytes). Subject PII and frozen
decision inputs are encrypted with the subject's data key, and the subject
id is bound in as associated data so blobs can't be swapped between
subjects.

Crypto-shredding destroys the wrapped data key. Every ciphertext for that
subject becomes permanently unreadable, while the append-only decision rows
stay byte-for-byte unchanged.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.screening.models import ScreeningSubject, ScreeningSubjectKey

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_NONCE_BYTES = 12
MASTER_KEY_ENV = "ENTITYIQ_SCREENING_MASTER_KEY"


class CryptoNotConfigured(RuntimeError):
    """The master key env var is missing or malformed."""


class DecryptionError(RuntimeError):
    """Ciphertext doesn't authenticate (wrong key, wrong subject, tampered)."""


class SubjectShredded(RuntimeError):
    """The subject's data key was destroyed; its data is unrecoverable."""


def _master() -> AESGCM:
    raw = os.environ.get(MASTER_KEY_ENV)
    if not raw:
        raise CryptoNotConfigured(f"{MASTER_KEY_ENV} is not set")
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CryptoNotConfigured(f"{MASTER_KEY_ENV} is not valid base64") from exc
    if len(key) != 32:
        raise CryptoNotConfigured(f"{MASTER_KEY_ENV} must decode to 32 bytes")
    return AESGCM(key)


def _aad(subject_id: str, purpose: bytes) -> bytes:
    return purpose + b":" + subject_id.encode()


def _key_row(db: "Session", subject_id: str) -> ScreeningSubjectKey:
    row = db.query(ScreeningSubjectKey).filter_by(subject_id=subject_id).one_or_none()
    if row is None:
        raise DecryptionError(f"No data key for subject {subject_id}")
    if row.wrapped_key is None:
        raise SubjectShredded(f"Subject {subject_id} was crypto-shredded")
    return row


def _data_key(db: "Session", subject_id: str) -> AESGCM:
    return _unwrap(_key_row(db, subject_id))


def _unwrap(row: ScreeningSubjectKey) -> AESGCM:
    subject_id = row.subject_id
    nonce, wrapped = row.wrapped_key[:_NONCE_BYTES], row.wrapped_key[_NONCE_BYTES:]
    try:
        key = _master().decrypt(nonce, wrapped, _aad(subject_id, b"dek"))
    except InvalidTag as exc:
        raise DecryptionError("Master key cannot unwrap this subject's key") from exc
    return AESGCM(key)


def ensure_subject_key(db: "Session", subject_id: str) -> None:
    """Create and store a wrapped data key for the subject if it has none."""
    if db.query(ScreeningSubjectKey).filter_by(subject_id=subject_id).count():
        return
    master = _master()
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(_NONCE_BYTES)
    wrapped = nonce + master.encrypt(nonce, dek, _aad(subject_id, b"dek"))
    db.add(ScreeningSubjectKey(subject_id=subject_id, wrapped_key=wrapped))
    db.flush()


def encrypt_for_subject(
    db: "Session", subject_id: str, payload: Any, *, purpose: bytes = b"data"
) -> tuple[bytes, bytes]:
    """Encrypt a JSON-serializable payload under the subject's data key."""
    ensure_subject_key(db, subject_id)
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(payload, sort_keys=True).encode()
    ct = _data_key(db, subject_id).encrypt(nonce, plaintext, _aad(subject_id, purpose))
    return ct, nonce


def decrypt_for_subject(
    db: "Session",
    subject_id: str,
    ciphertext: bytes,
    nonce: bytes,
    *,
    purpose: bytes = b"data",
) -> Any:
    try:
        plaintext = _data_key(db, subject_id).decrypt(
            nonce, ciphertext, _aad(subject_id, purpose)
        )
    except InvalidTag as exc:
        raise DecryptionError("Ciphertext does not authenticate") from exc
    return json.loads(plaintext)


def set_subject_pii(db: "Session", subject: ScreeningSubject, pii: dict) -> None:
    subject.pii_ciphertext, subject.pii_nonce = encrypt_for_subject(
        db, subject.id, pii, purpose=b"pii"
    )


def get_subject_pii(db: "Session", subject: ScreeningSubject) -> dict:
    if subject.shredded_at is not None or subject.pii_ciphertext is None:
        raise SubjectShredded(f"Subject {subject.id} was crypto-shredded")
    return decrypt_for_subject(
        db, subject.id, subject.pii_ciphertext, subject.pii_nonce, purpose=b"pii"
    )


def get_subjects_pii(
    db: "Session", subjects: list[ScreeningSubject]
) -> dict[str, dict | None]:
    """PII for many subjects with one key query; None where shredded."""
    live = [
        s for s in subjects if s.shredded_at is None and s.pii_ciphertext is not None
    ]
    keys = {
        row.subject_id: row
        for row in db.query(ScreeningSubjectKey).filter(
            ScreeningSubjectKey.subject_id.in_([s.id for s in live]),
            ScreeningSubjectKey.wrapped_key.is_not(None),
        )
    }
    out: dict[str, dict | None] = {s.id: None for s in subjects}
    for subject in live:
        row = keys.get(subject.id)
        if row is None:
            continue
        try:
            plaintext = _unwrap(row).decrypt(
                subject.pii_nonce, subject.pii_ciphertext, _aad(subject.id, b"pii")
            )
        except InvalidTag as exc:
            raise DecryptionError("Ciphertext does not authenticate") from exc
        out[subject.id] = json.loads(plaintext)
    return out


def shred_subject(db: "Session", subject: ScreeningSubject, now: datetime) -> None:
    """Destroy the subject's data key and clear its PII blob."""
    row = db.query(ScreeningSubjectKey).filter_by(subject_id=subject.id).one_or_none()
    if row is not None:
        row.wrapped_key = None
        row.destroyed_at = now
    subject.pii_ciphertext = None
    subject.pii_nonce = None
    subject.shredded_at = now


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)
