"""Explicit new-database fictional fixtures. Never backfill facts into existing CRM rows."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from .db import seed_database
from .migrations import migrate_database, setup_demo_identity


def create_demo_database(path):
    path = Path(path).resolve()
    # Exclusive creation prevents an accidental reset, including a concurrent creator.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    seed_database(path)
    migrate_database(path)
    setup_demo_identity(path)
    now = datetime.now(timezone.utc)
    with closing(sqlite3.connect(path)) as conn, conn:
        # These are authored fictional facts for this NEW fixture, not inferred from names.
        conn.execute("""INSERT INTO lead_intake
            SELECT id,'retail',0,1,NULL,?, 'Authored Stage 3 fictional demo intake' FROM leads""", (now.isoformat(),))
        conn.execute("""INSERT INTO agent_roster
            SELECT id,1,1,?, 'Authored fictional roster covering the next two-business-day contact window'
            FROM agents""", ((now + timedelta(days=7)).isoformat(),))
    return path
