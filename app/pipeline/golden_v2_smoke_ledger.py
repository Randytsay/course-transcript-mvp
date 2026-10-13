"""Isolated one-clip provider dispatch with atomic no-retry paid request slots.

This is a narrow acceptance-smoke ledger, NOT a replacement for Golden V2's
general job pipeline. It neither knows how to obtain Google credentials nor
has a live provider executable. A trusted operator has to connect an explicitly
approved client, verify actual model cost and API support, and provide two
independent owner/capability verifiers before it may dispatch a paid call.

The SQLite reservation is committed BEFORE calling an external client. Any
ambiguous or interrupted request stays blocked for human reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Callable

from app.pipeline.golden_v2_live_smoke_plan import build_paid_smoke_plan

MODELS = ("gemini-3.8-flash", "chirp_3", "gemini-3.5-transcribe")


class SmokeConflict(ValueError):
    """A smoke request is unsafe, stale, or cannot be charged again."""


def _wire(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SmokeConflict("missing or linked trial source")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _receipt_once(path: Path, obj: Any) -> str:
    """Exclusive create with fsync: immutable provider response, never replace."""
    data = _wire(obj)
    if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise SmokeConflict("linked provider receipt")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with path.open("xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError as exc:
        raise SmokeConflict("existing paid receipt may not be overwritten") from exc
    parent_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return hashlib.sha256(data).hexdigest()


class SingleClipSmokeLedger:
    """Never permits a second call for the same (clip SHA, model) pair."""

    def __init__(self, *, db: Path, plan_path: Path, offline_manifest: Path,
                 source_preflight: Path, original_media: Path,
                 excerpt_audio: Path, receipt_root: Path):
        self.db = Path(db)
        self.plan_path = Path(plan_path)
        self.offline_manifest = Path(offline_manifest)
        self.source_preflight = Path(source_preflight)
        self.original_media = Path(original_media)
        self.excerpt_audio = Path(excerpt_audio)
        self.receipt_root = Path(receipt_root)
        if (any(p.is_symlink() for p in (self.db, self.receipt_root))
                or self.db.parent.resolve() != self.plan_path.parent.resolve()
                or self.receipt_root.resolve() != self.plan_path.parent.resolve() / "receipts"):
            raise SmokeConflict("smoke ledger must remain isolated beside its plan")

    def _scope(self) -> dict[str, Any]:
        """Recheck *both* parent Drive source and derived excerpt before each claim."""
        if self.plan_path.is_symlink():
            raise SmokeConflict("unsafe linked trial plan")
        try:
            observed = json.loads(self.plan_path.read_text(encoding="utf-8"))
            rebuilt = build_paid_smoke_plan(
                offline_manifest=self.offline_manifest,
                original_preflight=self.source_preflight,
                original_media=self.original_media,
                excerpt_audio=self.excerpt_audio,
                max_total_cents=1000,
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise SmokeConflict("trial source or approval scope could not be verified") from exc
        if observed != rebuilt or observed.get("cap_usd_cents") != 1000:
            raise SmokeConflict("trial scope differs from user-approved 4-minute plan")
        if (observed["models_in_order"] != list(MODELS)
                or observed["clip_duration_seconds"] != 240
                or observed["clip_is_independent_drive_source"] is not False
                or observed["paid_execution_enabled"] is not False):
            raise SmokeConflict("untrusted clip provenance or enabled plan")
        return observed

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if self.db.is_symlink() or not self.db.is_file():
            raise SmokeConflict("isolated smoke database missing")
        target = self.db.resolve().as_uri() + "?mode=ro" if read_only else str(self.db)
        db = sqlite3.connect(target, uri=read_only, timeout=8, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=8000")
        return db

    def record_provider_event(self, *, model: str, event: dict[str, Any]) -> None:
        """Persist raw response/operation BEFORE validation, billing or waiting.

        Only the currently committed request can write an event. An audit
        write failure stops the adapter; the request barrier remains intact.
        """
        if (model not in MODELS or not isinstance(event, dict)
                or event.get("kind") not in {
                    "gemini_response", "chirp_submitted", "chirp_response"}):
            raise SmokeConflict("invalid provider audit event")
        if self.receipt_root.is_symlink():
            raise SmokeConflict("linked provider evidence directory")
        with self._connect() as db:
            row = db.execute("SELECT * FROM stage WHERE model=?", (model,)).fetchone()
            scope = db.execute("SELECT * FROM scope WHERE singleton=1").fetchone()
            if row is None or row["state"] != "REQUEST_STARTED":
                raise SmokeConflict("provider event has no active request barrier")
            request = row["request_digest"]
            if (not isinstance(request, str) or len(request) != 64
                    or any(c not in "0123456789abcdef" for c in request)):
                raise SmokeConflict("invalid provider event request identity")
            _receipt_once(self.receipt_root / row["request_digest"] /
                          (event["kind"] + ".json"), {
                "schema": "golden-v2-smoke-provider-event-v1",
                "model": model, "clip_sha256": scope["clip_sha"],
                "request_digest": row["request_digest"], "event": event,
            })

    def _verified_receipt(self, row: sqlite3.Row, clip_sha: str) -> dict[str, Any]:
        request = row["request_digest"]
        if (not isinstance(request, str) or len(request) != 64
                or any(c not in "0123456789abcdef" for c in request)
                or self.receipt_root.is_symlink()):
            raise SmokeConflict("invalid paid receipt identity")
        path = self.receipt_root / (request + ".json")
        if _sha(path) != row["receipt_sha"]:
            raise SmokeConflict("paid receipt checksum mismatch")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (data.get("schema") != "golden-v2-paid-smoke-response-v1"
                    or data.get("model") != row["model"]
                    or data.get("clip_sha256") != clip_sha
                    or data.get("request_digest") != request
                    or type(data.get("accounted_cents")) is not int
                    or data["accounted_cents"] != row["actual_cents"]):
                raise SmokeConflict("paid receipt differs from committed ledger")
            return data
        except (ValueError, AttributeError) as exc:
            raise SmokeConflict("unreadable paid receipt") from exc

    def verified_receipts(self) -> list[dict[str, Any]]:
        """Provider-free, checksum-bound readback for a completed three-way run."""
        plan = self._scope()
        with self._connect(read_only=True) as db:
            db.execute("BEGIN")
            scope = db.execute("SELECT * FROM scope WHERE singleton=1").fetchone()
            rows = db.execute("SELECT * FROM stage ORDER BY ord").fetchall()
            if (scope is None or scope["plan_digest"] != hashlib.sha256(_wire(plan)).hexdigest()
                    or len(rows) != len(MODELS)
                    or [r["model"] for r in rows] != list(MODELS)
                    or any(r["state"] != "COMPLETE" for r in rows)):
                raise SmokeConflict("all three verified completed stages required")
            return [self._verified_receipt(row, plan["clip_sha256"]) for row in rows]

    def initialize(self, *, owner_reference: str) -> dict[str, Any]:
        """Create one never-resettable trial with no provider or cloud side effects."""
        plan = self._scope()
        if (not isinstance(owner_reference, str) or not owner_reference.strip()
                or len(owner_reference) > 240):
            raise SmokeConflict("owner-authorized source/cap reference required")
        if self.db.exists() or self.db.is_symlink():
            raise SmokeConflict("an existing trial may not be initialized twice")
        self.db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw_plan_digest = hashlib.sha256(_wire(plan)).hexdigest()
        stages = plan["stage_reservation_caps_usd_cents"]
        caps = (stages["gemini_3_8_primary_cap_cents"],
                stages["chirp_3_cap_cents"],
                stages["gemini_3_5_transcribe_cap_cents"])
        # O_EXCL prevents racing creators from overwriting another trial DB.
        try:
            fd = os.open(self.db, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
        except FileExistsError as exc:
            raise SmokeConflict("trial already initialized") from exc
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE scope (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    plan_digest TEXT NOT NULL,
                    clip_sha TEXT NOT NULL,
                    cap_cents INTEGER NOT NULL,
                    owner_reference_digest TEXT NOT NULL
                );
                CREATE TABLE stage (
                    ord INTEGER PRIMARY KEY,
                    model TEXT NOT NULL UNIQUE,
                    cap_cents INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    request_digest TEXT,
                    actual_cents INTEGER,
                    receipt_sha TEXT
                );
            """)
            db.execute(
                "INSERT INTO scope VALUES (1,?,?,?,?)",
                (raw_plan_digest, plan["clip_sha256"], 1000,
                 hashlib.sha256(owner_reference.encode()).hexdigest()),
            )
            db.executemany(
                "INSERT INTO stage(ord,model,cap_cents,state) VALUES (?,?,?,'PENDING')",
                [(i, model, cap) for i, (model, cap) in
                 enumerate(zip(MODELS, caps, strict=True))],
            )
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._connect(read_only=True) as db:
            scope = db.execute("SELECT * FROM scope WHERE singleton=1").fetchone()
            rows = db.execute("SELECT * FROM stage ORDER BY ord").fetchall()
        return {
            "schema": "golden-v2-one-clip-paid-trial-v1",
            "clip_sha256": scope["clip_sha"],
            "cap_cents": scope["cap_cents"],
            "stages": [
                {"model": row["model"], "state": row["state"],
                 "cap_cents": row["cap_cents"],
                 "actual_cents": row["actual_cents"],
                 "receipt_sha256": row["receipt_sha"]}
                for row in rows
            ],
            "provider_calls_started": any(x["state"] != "PENDING"
                                          for x in rows),
        }

    def execute_one(
        self, *, model: str,
        provider_call: Callable[[Path, str], dict[str, Any]],
        cost_accountant: Callable[[dict[str, Any]], int],
        verify_owner: Callable[[dict[str, Any]], bool],
        verify_capability_and_pricing: Callable[[dict[str, Any], str], bool],
    ) -> dict[str, Any]:
        """Dispatch at most once after independent checks; fail closed forever."""
        if model not in MODELS:
            raise SmokeConflict("model not authorized by short-clip plan")
        plan = self._scope()
        if (not all(callable(x) for x in (
                provider_call, cost_accountant,
                verify_owner, verify_capability_and_pricing))):
            raise SmokeConflict("missing trusted provider and dual verifiers")
        # Verifiers never derive authority from a self-asserted plan flag.
        if (verify_owner(plan) is not True
                or verify_capability_and_pricing(plan, model) is not True):
            raise SmokeConflict("trusted owner, provider or pricing check blocked")
        if self._scope() != plan:
            raise SmokeConflict("trial source changed during trusted verification")
        if self.receipt_root.is_symlink():
            raise SmokeConflict("linked provider evidence directory")
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                scope = db.execute("SELECT * FROM scope WHERE singleton=1").fetchone()
                rows = db.execute("SELECT * FROM stage ORDER BY ord").fetchall()
                if (len(rows) != len(MODELS)
                        or [r["model"] for r in rows] != list(MODELS)
                        or [r["ord"] for r in rows] != list(range(len(MODELS)))):
                    raise SmokeConflict("corrupt trial stage identities")
                if (scope is None or scope["plan_digest"] != hashlib.sha256(
                        _wire(plan)).hexdigest()
                        or scope["clip_sha"] != plan["clip_sha256"]
                        or scope["cap_cents"] != 1000):
                    raise SmokeConflict("trial plan or clip no longer approved")
                index = MODELS.index(model)
                if (rows[index]["state"] != "PENDING"
                        or any(rows[i]["state"] != "COMPLETE"
                               for i in range(index))):
                    raise SmokeConflict("trial stage already charged or prior stage unresolved")
                for prior in rows[:index]:
                    self._verified_receipt(prior, plan["clip_sha256"])
                committed = sum(row["actual_cents"] or 0 for row in rows)
                remaining_reservations = sum(
                    row["cap_cents"] for row in rows[index:]
                )
                if committed + remaining_reservations > scope["cap_cents"]:
                    raise SmokeConflict("global USD 10 trial cap exceeded")
                request_id = hashlib.sha256(_wire({
                    "clip_sha256": scope["clip_sha"],
                    "model": model,
                    "ordinal": index,
                    "trial_plan_sha256": scope["plan_digest"],
                })).hexdigest()
                db.execute(
                    "UPDATE stage SET state='REQUEST_STARTED',request_digest=? "
                    "WHERE ord=? AND state='PENDING'", (request_id, index),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        # Barrier committed: any crash, timeout, or unknown provider outcome
        # must block a second call for this exact clip/model slot.
        try:
            response = provider_call(self.excerpt_audio, model)
            if not isinstance(response, dict) or not response:
                raise SmokeConflict("empty or invalid paid response")
            # Preserve the provider return even when cost counters are absent
            # or the accountant fails. Final accounted receipt is separate.
            _receipt_once(self.receipt_root / request_id / "response.json", {
                "schema": "golden-v2-smoke-unaccounted-response-v1",
                "model": model, "clip_sha256": plan["clip_sha256"],
                "request_digest": request_id, "raw_response": response,
            })
            charged = cost_accountant(response)
            if type(charged) is not int or charged < 0:
                raise SmokeConflict("unknown cost: stop and reconcile")
            receipt = {
                "schema": "golden-v2-paid-smoke-response-v1",
                "model": model,
                "clip_sha256": plan["clip_sha256"],
                "request_digest": request_id,
                "raw_response": response,
                "accounted_cents": charged,
            }
            digest = _receipt_once(self.receipt_root / (request_id + ".json"), receipt)
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM stage WHERE ord=?", (index,)).fetchone()
                if row["state"] != "REQUEST_STARTED" or row["request_digest"] != request_id:
                    raise SmokeConflict("paid stage changed since request")
                state = ("COMPLETE" if charged <= row["cap_cents"] else "OVER_BUDGET")
                db.execute(
                    "UPDATE stage SET state=?,actual_cents=?,receipt_sha=? "
                    "WHERE ord=?", (state, charged, digest, index),
                )
                db.execute("COMMIT")
            if charged > plan["stage_reservation_caps_usd_cents"][
                ("gemini_3_8_primary_cap_cents",
                 "chirp_3_cap_cents",
                 "gemini_3_5_transcribe_cap_cents")[index]
            ]:
                raise SmokeConflict("actual billed cost exceeded stage reservation")
            return {"model": model, "state": "COMPLETE", "actual_cents": charged,
                    "receipt_sha256": digest}
        except Exception:
            # REQUEST_STARTED and UNKNOWN are both **irreversible** by design.
            # Never change a completed/over-budget row to unknown.
            with self._connect() as db:
                db.execute("UPDATE stage SET state='OUTCOME_UNKNOWN' "
                           "WHERE ord=? AND state='REQUEST_STARTED'", (index,))
            raise
