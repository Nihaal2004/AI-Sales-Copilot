"""The only owner-write service: signed consent, deterministic rules and atomic effects."""

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from threading import Lock
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from .authorization import RULE_VERSION, actor_allowed, authorize, outcome
from .corpus import PolicyError
from .policy_gate import validate_action_evidence
from .schemas import AssignLead, BoundApprovalDecision
from .tracing import Trace


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def payload_fingerprint(actor_id, action):
    return hashlib.sha256(canonical({"actor_id": actor_id, "lead_id": action.lead_id,
                                    "assignee_id": action.assignee_id,
                                    "operation_type": "first_assignment"}).encode()).hexdigest()


class AssignmentService:
    """Actor comes from the trusted host, never chat or tool arguments.

    Consent signatures are process-local capabilities, not authentication against
    hostile Python code. A caller cannot authorize by constructing a Pydantic object.
    """

    def __init__(self, crm, retriever, *, actor_id=None, trace=None, busy_timeout_ms=1000, fault=None):
        self.crm, self.retriever, self.actor_id = crm, retriever, actor_id
        self.trace = trace if trace is not None else Trace()
        if not 0 <= busy_timeout_ms <= 10000:
            raise ValueError("busy timeout must be 0..10000ms")
        self.busy_timeout_ms, self.fault = busy_timeout_ms, fault
        self._secret = secrets.token_bytes(32)
        self._decided = set()
        self._decision_lock = Lock()

    def _connect(self, readonly=False):
        conn = sqlite3.connect(self.crm.path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                               uri=True, isolation_level=None, timeout=self.busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return conn

    def _seal(self, payload):
        return {"payload": payload, "signature": hmac.new(self._secret, canonical(payload).encode(), hashlib.sha256).hexdigest()}

    def _open(self, envelope, kind):
        if type(envelope) is not dict or set(envelope) != {"payload", "signature"}:
            raise ValueError("invalid_consent")
        payload = envelope["payload"]
        if type(payload) is not dict or type(envelope["signature"]) is not str:
            raise ValueError("invalid_consent")
        expected = self._seal(payload)["signature"]
        if not hmac.compare_digest(envelope["signature"], expected) or payload.get("kind") != kind:
            raise ValueError("invalid_consent")
        if payload["actor_id"] != self.actor_id:
            raise ValueError("consent_actor_mismatch")
        action = AssignLead.model_validate(payload["action"])
        if payload["fingerprint"] != payload_fingerprint(self.actor_id, action):
            raise ValueError("consent_action_mismatch")
        return payload, action

    def preflight(self, action):
        try:
            action = AssignLead.model_validate(action)
            with closing(self._connect(readonly=True)) as conn:
                return self._authorize(conn, action, "preflight")
        except (ValidationError, ValueError):
            return outcome("forbidden", "invalid_assignment_payload")
        except sqlite3.Error:
            return outcome("temporary_failure", "authorization_metadata_unavailable")

    def _authorize(self, conn, action, phase):
        decision = authorize(conn, self.actor_id, action, self.retriever.corpus_dir)
        self.trace.event("authorization_checked", phase=phase,
                         result=decision or {"ok": True, "status": "eligible"})
        return decision

    def propose(self, action, evidence):
        denied = self.preflight(action)
        if denied:
            return None, denied
        try:
            validate_action_evidence(self.retriever, action, evidence)
        except Exception:
            return None, outcome("policy_blocked", "policy_action_binding_invalid")
        action = AssignLead.model_validate(action)
        payload = {"kind": "proposal", "actor_id": self.actor_id, "action": action.model_dump(),
                   "fingerprint": payload_fingerprint(self.actor_id, action), "nonce": str(uuid4()),
                   "expires_at": time.time() + 900, "evidence": json.loads(canonical(evidence))}
        return self._seal(payload), None

    def decision_context(self, proposal):
        payload, action = self._open(proposal, "proposal")
        return {"actor_id": self.actor_id, "operation_id": action.request_key,
                "action_fingerprint": payload["fingerprint"], "challenge_id": payload["nonce"]}

    def confirm(self, proposal, decision):
        """Trusted UI calls after interrupt. No transaction, audit or owner write here."""
        try:
            payload, _ = self._open(proposal, "proposal")
            parsed = BoundApprovalDecision.model_validate(decision)
            expected = self.decision_context(proposal)
            if (parsed.model_dump(exclude={"approved"}) != expected or parsed.approved is not True
                    or payload["expires_at"] < time.time()):
                raise ValueError("invalid_or_replayed_approval")
            with self._decision_lock:
                if payload["nonce"] in self._decided:
                    raise ValueError("invalid_or_replayed_approval")
                self._decided.add(payload["nonce"])
            return self._seal({**payload, "kind": "approved"})
        except (ValidationError, ValueError, TypeError, KeyError):
            return None

    def _completed(self, conn, action, fingerprint):
        previous = conn.execute("SELECT * FROM assignment_operations WHERE request_key=?", (action.request_key,)).fetchone()
        if previous is None:
            return None
        if previous["fingerprint"] != fingerprint or previous["actor_id"] != self.actor_id:
            return outcome("conflict", "operation_key_payload_mismatch")
        self.trace.event("idempotency_hit", result={"ok": True, "status": "already_completed"})
        return {**json.loads(previous["result_json"]), "status": "already_completed"}

    def lookup_completed(self, action):
        """Restart-safe result lookup. It never executes an uncommitted operation."""
        try:
            action = AssignLead.model_validate(action)
            with closing(self._connect(readonly=True)) as conn:
                denied = actor_allowed(conn, self.actor_id)
                return denied or self._completed(conn, action, payload_fingerprint(self.actor_id, action))
        except (ValidationError, ValueError):
            return outcome("forbidden", "invalid_assignment_payload")
        except sqlite3.Error:
            return outcome("temporary_failure", "operation_lookup_unavailable")

    def _fail(self, point):
        if self.fault:
            self.fault(point)

    def execute(self, consent):
        result = self._execute(consent)
        self.trace.event("service_outcome", result=result)
        return result

    def _execute(self, consent):
        """No caller can skip this boundary by choosing a different graph path."""
        try:
            payload, action = self._open(consent, "approved")
        except (ValueError, ValidationError, TypeError, KeyError):
            return outcome("forbidden", "valid_bound_approval_required")
        self.trace.operation(action.request_key)
        committed = False
        try:
            with closing(self._connect()) as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    self.trace.event("transaction_started")
                    denied = actor_allowed(conn, self.actor_id)
                    if denied:
                        return denied
                    prior = self._completed(conn, action, payload["fingerprint"])
                    if prior:
                        return prior  # Only a stored, committed result can take this path.
                    if payload["expires_at"] < time.time():
                        return outcome("forbidden", "approval_expired")
                    if conn.execute("SELECT 1 FROM assignment_audit WHERE approval_nonce=?", (payload["nonce"],)).fetchone():
                        return outcome("forbidden", "approval_replayed")
                    validate_action_evidence(self.retriever, action.model_dump(), payload["evidence"])
                    denied = self._authorize(conn, action, "transaction")
                    if denied:
                        return denied
                    self.trace.event("transaction_revalidated")
                    self._fail("before_mutation")
                    updated_at = datetime.now(timezone.utc).isoformat()
                    cursor = conn.execute("UPDATE leads SET owner_id=?, updated_at=? WHERE id=? AND owner_id IS NULL",
                                          (action.assignee_id, updated_at, action.lead_id))
                    self.trace.event("lead_update_checked", rowcount=cursor.rowcount)
                    if cursor.rowcount != 1:
                        return outcome("conflict", "first_assignment_race_lost")
                    self.trace.event("lead_updated", result={"lead_id": action.lead_id, "owner_id": action.assignee_id})
                    self._fail("after_lead_update")
                    confirmed = conn.execute("SELECT owner_id,updated_at FROM leads WHERE id=?", (action.lead_id,)).fetchone()
                    if confirmed["owner_id"] != action.assignee_id or confirmed["updated_at"] != updated_at:
                        raise RuntimeError("assignment_not_confirmed")
                    conn.execute("""INSERT INTO assignment_audit
                        (request_key,fingerprint,actor_id,lead_id,assignee_id,operation_type,approval_nonce,
                         policy_evidence,rule_version,committed_at) VALUES (?,?,?,?,?,'first_assignment',?,?,?,?)""",
                                 (action.request_key, payload["fingerprint"], self.actor_id, action.lead_id,
                                  action.assignee_id, payload["nonce"], canonical(payload["evidence"]), RULE_VERSION, updated_at))
                    self.trace.event("audit_inserted")
                    self._fail("after_audit")
                    result = outcome("success", lead_id=action.lead_id, previous_owner_id=None,
                                     owner_id=confirmed["owner_id"], updated_at=confirmed["updated_at"],
                                     request_key=action.request_key, actor_id=self.actor_id)
                    conn.execute("INSERT INTO assignment_operations VALUES (?,?,?,?,?)",
                                 (action.request_key, payload["fingerprint"], self.actor_id, canonical(result), updated_at))
                    self.trace.event("idempotency_recorded")
                    conn.commit()
                    committed = True
                    self.trace.event("transaction_committed")
                finally:
                    if conn.in_transaction:
                        conn.rollback()
                        self.trace.event("transaction_rolled_back")
            self._fail("after_commit")
            self.trace.emit("assignment_service", tool="assign_lead", result=result)
            return result
        except PolicyError:
            return outcome("policy_blocked", "policy_action_binding_invalid")
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 255
            return outcome("temporary_failure", "sqlite_busy" if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                           else "sqlite_operation_failed", retry_same_operation=True)
        except Exception:
            # After COMMIT the client must resolve uncertainty using the SAME key.
            # A pre-commit error rolls back all three effects, including triggers.
            return outcome("temporary_failure", "commit_response_unknown" if committed else "assignment_failed",
                           retry_same_operation=True)
