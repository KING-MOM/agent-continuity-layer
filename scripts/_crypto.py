#!/usr/bin/env python3
"""_crypto.py — Ed25519 signing primitives for the substrate (v0.5.0+).

The substrate uses Ed25519 for actor identity and decision signing. This
module is the single point of contact with the `cryptography` library so
that signing semantics stay consistent across `_decisions.py`, `_key.py`,
`_team.py`, and any future consumer.

Design choices made explicit here:
  - Library: `cryptography` (PyCA). Mature, audited, already a transitive
    dependency of pip-installed tooling. PyNaCl rejected only because the
    substrate doesn't want to add an installable dependency for OSS users
    when `cryptography` is effectively always present.
  - Algorithm: Ed25519 only. No agility for v1.0 — the cryptographic
    versioning question is harder than the algorithm choice and resolving
    it inside v0.5.0 would expand scope without improving security.
  - Canonical signing form: JSON with sort_keys=True and separators=(',', ':').
    Identical to how decision-entry.id is already computed. Reusing the
    canonical form means a decision's id and its signature payload are
    computed from the same bytes.
  - Signed payload: the entry as serialized for `id`, MINUS `device_signature`
    itself. The signature covers content; it doesn't cover itself.
  - device_key_id: sha256 of the PEM-encoded public key, prefixed with
    `device:fp:`. Stable across devices that hold the same key; sufficiently
    short to be human-comparable.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


# ──────────────────────────────────────────────────────────────────
# Canonical serialization (must match _decisions.py's id formula)

def canonical_json_bytes(obj: dict[str, Any]) -> bytes:
    """Return the bytes used for both id computation and signature payload.

    Stable across Python versions and OSes: sort_keys + tight separators.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_signing_payload(entry: dict[str, Any]) -> bytes:
    """The bytes a signer signs / a verifier verifies for a decision entry.

    Excludes both `id` (content-addressed, recomputable) and `device_signature`
    (the signature itself — can't cover itself). All other fields are signed,
    including manifest_version_observed + role_assertions_head_observed so
    backdating attempts contradict the observable team state.
    """
    payload = {k: v for k, v in entry.items() if k not in ("id", "device_signature")}
    return canonical_json_bytes(payload)


# ──────────────────────────────────────────────────────────────────
# Keypair generation + serialization

def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return priv, pub


def private_key_to_pem(priv: Ed25519PrivateKey) -> str:
    pem_bytes = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem_bytes.decode("ascii")


def public_key_to_pem(pub: Ed25519PublicKey) -> str:
    pem_bytes = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pem_bytes.decode("ascii")


def private_key_from_pem(pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"expected Ed25519 private key, got {type(key).__name__}")
    return key


def public_key_from_pem(pem: str) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem.encode("ascii"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError(f"expected Ed25519 public key, got {type(key).__name__}")
    return key


# ──────────────────────────────────────────────────────────────────
# Fingerprints (the device_key_id and human_actor_id derivation)

def device_key_id_from_pubkey(pub: Ed25519PublicKey) -> str:
    """device:fp:<hex sha256 of the PEM-encoded public key, first 32 chars>.

    Truncating to 32 hex chars (128 bits) gives a fingerprint that's still
    cryptographically large but human-comparable. Same scheme as
    sigstore/cosign fingerprints.
    """
    pem = public_key_to_pem(pub).encode("ascii")
    h = hashlib.sha256(pem).hexdigest()[:32]
    return f"device:fp:{h}"


def human_actor_id_from_device(device_key_id: str) -> str:
    """Default human_actor_id for a solo operator: derived from their first
    device key. When the operator joins a team, the team admin may assign
    a different human_actor_id (linking this device under an existing human).
    """
    # device:fp:abc... → human:fp:abc...
    fp = device_key_id.split(":", 2)[-1] if device_key_id.startswith("device:fp:") else device_key_id
    return f"human:fp:{fp}"


# ──────────────────────────────────────────────────────────────────
# Signing + verification

def sign_payload(priv: Ed25519PrivateKey, payload: bytes) -> str:
    """Returns base64-encoded signature."""
    sig = priv.sign(payload)
    return base64.b64encode(sig).decode("ascii")


def verify_payload(pub: Ed25519PublicKey, payload: bytes, signature_b64: str) -> bool:
    try:
        sig = base64.b64decode(signature_b64.encode("ascii"))
        pub.verify(sig, payload)
        return True
    except (InvalidSignature, ValueError):
        return False


# ──────────────────────────────────────────────────────────────────
# Convenience: sign / verify a decision-entry dict directly

def sign_decision_entry(
    entry: dict[str, Any],
    priv: Ed25519PrivateKey,
) -> str:
    """Returns the base64 signature to attach as `device_signature`."""
    payload = canonical_signing_payload(entry)
    return sign_payload(priv, payload)


def verify_decision_entry(
    entry: dict[str, Any],
    pub: Ed25519PublicKey,
) -> bool:
    """Verifies the entry's device_signature against the provided pubkey."""
    sig = entry.get("device_signature")
    if not sig:
        return False
    payload = canonical_signing_payload(entry)
    return verify_payload(pub, payload, sig)
