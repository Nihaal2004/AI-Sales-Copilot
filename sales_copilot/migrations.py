"""Additive Stage 3 schema. Migration never supplies business facts or permissions."""

from contextlib import closing
import sqlite3

STAGE3_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS crm_actors (
        actor_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        can_assign INTEGER NOT NULL CHECK(can_assign IN (0,1)),
        simulated INTEGER NOT NULL CHECK(simulated IN (0,1)), source TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS lead_intake (
        lead_id INTEGER PRIMARY KEY REFERENCES leads(id), industry TEXT NOT NULL,
        strategic INTEGER NOT NULL CHECK(strategic IN (0,1)),
        territory_confirmed INTEGER NOT NULL CHECK(territory_confirmed IN (0,1)),
        compliance_review_until TEXT, verified_at TEXT NOT NULL, source TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS agent_roster (
        agent_id INTEGER PRIMARY KEY REFERENCES agents(id),
        available INTEGER NOT NULL CHECK(available IN (0,1)),
        response_window_confirmed INTEGER NOT NULL CHECK(response_window_confirmed IN (0,1)),
        valid_until TEXT NOT NULL, source TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS agent_certifications (
        agent_id INTEGER NOT NULL REFERENCES agents(id), industry TEXT NOT NULL,
        scheme TEXT NOT NULL, issuer TEXT NOT NULL, valid_from TEXT NOT NULL,
        valid_until TEXT NOT NULL, suspended INTEGER NOT NULL CHECK(suspended IN (0,1)),
        source TEXT NOT NULL, PRIMARY KEY(agent_id, industry))""",
    """CREATE TABLE IF NOT EXISTS assignment_audit (
        event_id INTEGER PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
        fingerprint TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES crm_actors(actor_id),
        lead_id INTEGER NOT NULL REFERENCES leads(id), assignee_id INTEGER NOT NULL REFERENCES agents(id),
        operation_type TEXT NOT NULL CHECK(operation_type = 'first_assignment'),
        approval_nonce TEXT NOT NULL UNIQUE, policy_evidence TEXT NOT NULL,
        rule_version TEXT NOT NULL, committed_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS assignment_operations (
        request_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
        actor_id TEXT NOT NULL REFERENCES crm_actors(actor_id), result_json TEXT NOT NULL,
        committed_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS leads_owner_status ON leads(owner_id, status)",
    """CREATE TABLE IF NOT EXISTS crm_migrations (
        version INTEGER PRIMARY KEY, description TEXT NOT NULL)""",
)


def migrate_database(path):
    """Transactional, repeatable migration; existing lead/agent rows stay untouched."""
    with closing(sqlite3.connect(path, timeout=3)) as conn, conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 3000")
        conn.execute("BEGIN IMMEDIATE")
        for statement in STAGE3_SCHEMA:
            conn.execute(statement)
        conn.execute("INSERT OR IGNORE INTO crm_migrations VALUES (3, 'first-assignment safeguards')")


def setup_demo_identity(path):
    """Explicit opt-in provisioning of one simulated dispatcher, never a manager."""
    migrate_database(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO crm_actors VALUES (?,1,1,1,?)",
                     ("demo-dispatcher", "Explicit fictional CLI demo provisioning"))
    # Do not re-enable a revoked account or add metadata to existing CRM records.
