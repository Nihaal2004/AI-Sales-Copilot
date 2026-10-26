"""Small parameterized SQLite repository. Reads use a read-only connection."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .schemas import ApprovedAssignment, GetLeadDetails, SearchLeads
from .ranking import LeadRanker, RankingConfig

AGENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL
)
"""
LEADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    company TEXT NOT NULL,
    city TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('new','contacted','qualified','won','lost')),
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
    priority TEXT NOT NULL CHECK(priority IN ('low','medium','high')),
    owner_id INTEGER REFERENCES agents(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
SCHEMA = AGENTS_SCHEMA + ";\n" + LEADS_SCHEMA + ";\n"

UNASSIGNED_SEED_EMAIL = "kavya.iyer@marinaretail.example.test"

AGENTS = [
    (1, "Anika Demo", "Chennai"),
    (2, "Ravi Example", "Bangalore"),
    (3, "Meera Sample", "Hyderabad"),
    (4, "Dev Fiction", "Chennai"),
]


def _allow_unassigned_owners(conn: sqlite3.Connection) -> bool:
    """Upgrade the original Stage 1 NOT NULL owner column without losing rows."""
    owner = next(row for row in conn.execute("PRAGMA table_info(leads)") if row[1] == "owner_id")
    if not owner[3]:
        return False
    # SQLite requires a table rebuild to remove NOT NULL. Recreate any existing
    # indexes/triggers after copying all rows; the caller owns the transaction.
    objects = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name = 'leads' "
                           "AND type IN ('index', 'trigger') AND sql IS NOT NULL").fetchall()
    conn.execute(LEADS_SCHEMA.replace("IF NOT EXISTS leads", "leads_nullable", 1))
    conn.execute("INSERT INTO leads_nullable SELECT * FROM leads")
    conn.execute("DROP TABLE leads")
    conn.execute("ALTER TABLE leads_nullable RENAME TO leads")
    for (sql,) in objects:
        conn.execute(sql)
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Foreign key validation failed during owner migration.")
    return True


def seed_database(path: str | Path) -> bool:
    """Seed 20 original leads plus an unassigned Chennai example; preserve edits.

    Existing Stage 1 databases are upgraded transactionally. Re-running never
    resets an owner or duplicates/recreates the example after it is assigned.
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(AGENTS_SCHEMA)
        conn.execute(LEADS_SCHEMA)
        counts = (conn.execute("SELECT count(*) FROM agents").fetchone()[0],
                  conn.execute("SELECT count(*) FROM leads").fetchone()[0])
        if counts != (0, 0) and (counts[0] != 4 or counts[1] < 20):
            raise ValueError("Refusing to seed a nonempty CRM with unexpected row counts.")
        changed = _allow_unassigned_owners(conn)
        cities = ["Chennai", "Bangalore", "Hyderabad"]
        statuses = ["new", "contacted", "qualified", "won", "lost"]
        rows = []
        for i in range(1, 21):
            score = (i * 17) % 101
            timestamp = f"2026-01-{i:02d}T09:00:00+00:00"
            rows.append((i, f"Fictional Lead {i:02d}", f"lead{i:02d}@example.test",
                         f"Demo Company {i:02d}", cities[(i - 1) % 3],
                         statuses[(i - 1) % 5], score,
                         "high" if score >= 70 else "medium" if score >= 40 else "low",
                         (i - 1) % 4 + 1, timestamp, timestamp))
        if counts == (0, 0):
            conn.executemany("INSERT INTO agents VALUES (?, ?, ?)", AGENTS)
            conn.executemany("INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            changed = True
        existing_example = conn.execute("SELECT id FROM leads WHERE email = ?", (UNASSIGNED_SEED_EMAIL,)).fetchone()
        matching_lead = conn.execute("SELECT id FROM leads WHERE city = ? COLLATE NOCASE AND owner_id IS NULL",
                                     ("Chennai",)).fetchone()
        if existing_example is None and matching_lead is None:
            timestamp = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (None, "Kavya Iyer (Fictional)", UNASSIGNED_SEED_EMAIL,
                          "Marina Retail Labs (Fictional)", "Chennai", "new", 82, "high", None,
                          timestamp, timestamp))
            changed = True
    return changed


class CRM:
    def __init__(self, path: str | Path, *, ranking_config: RankingConfig | None = None):
        self.path = Path(path).resolve()
        self.ranker = LeadRanker(ranking_config)

    def _connect(self, *, readonly: bool = True) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def list_agents(self) -> list[dict]:
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM agents ORDER BY id")]

    def search_leads(self, args: SearchLeads) -> list[dict]:
        clauses, values = [], []
        for column in ("city", "status", "owner_id"):
            value = getattr(args, column)
            if value is not None:
                # Column names come exclusively from the fixed Pydantic schema.
                clauses.append(f"{column} = ? COLLATE NOCASE")
                values.append(value)
        if args.unassigned_only:
            clauses.append("owner_id IS NULL")
        sql = "SELECT * FROM leads" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        with closing(self._connect()) as conn:
            leads = [dict(row) for row in conn.execute(sql + " ORDER BY id", values)]
        return self.ranker.order(leads, sort_by=args.sort_by, sort_order=args.sort_order)

    def get_lead_details(self, args: GetLeadDetails) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT leads.*, agents.name AS owner_name FROM leads "
                "LEFT JOIN agents ON agents.id = leads.owner_id WHERE leads.id = ?", (args.lead_id,)
            ).fetchone()
            return dict(row) if row else None

    def assign_lead(self, approved: ApprovedAssignment, *, assignment_service=None) -> dict:
        """Compatibility facade; all writes use the authoritative Stage 3 service."""
        if not isinstance(approved, ApprovedAssignment):
            raise TypeError("An ApprovedAssignment is required.")
        approved = ApprovedAssignment.model_validate(approved.model_dump())
        if assignment_service is None:
            return {"ok": False, "status": "forbidden", "error": "assignment_service_required"}
        consent = approved.consent
        if (not consent or consent.get("payload", {}).get("action") != approved.action.model_dump()
                or assignment_service.crm.path != self.path):
            return {"ok": False, "status": "forbidden", "error": "valid_bound_approval_required"}
        return assignment_service.execute(consent)
