import sqlite3
from contextlib import closing

import pytest

from sales_copilot import db
from sales_copilot.db import CRM, seed_database
from sales_copilot.schemas import GetLeadDetails, SearchLeads

from conftest import call
from test_stage1 import snapshot


def legacy_database(crm, path, *, nullable=False):
    """Recreate the original 20-row schema, preserving edited owner/timestamps."""
    rows = [list(row) for row in snapshot(crm)[:20]]
    rows[0][8] = None if nullable else 4
    rows[0][-1] = "2026-06-01T12:00:00+00:00"
    schema = db.LEADS_SCHEMA if nullable else db.LEADS_SCHEMA.replace(
        "owner_id INTEGER REFERENCES", "owner_id INTEGER NOT NULL REFERENCES")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(db.AGENTS_SCHEMA)
        conn.execute(schema)
        conn.executemany("INSERT INTO agents VALUES (?, ?, ?)", db.AGENTS)
        conn.executemany("INSERT INTO leads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.execute("CREATE INDEX leads_city ON leads(city)")
        conn.execute("CREATE TABLE observed_updates (lead_id INTEGER)")
        conn.execute("""CREATE TRIGGER observe_owner AFTER UPDATE OF owner_id ON leads
                        BEGIN INSERT INTO observed_updates VALUES (NEW.id); END""")
    return CRM(path)


def test_legacy_upgrade_adds_a_null_owner_and_preserves_every_existing_row_and_schema_object(crm, tmp_path):
    old = legacy_database(crm, tmp_path / "legacy.sqlite3")
    before = snapshot(old)
    assert old.search_leads(SearchLeads(city="Chennai", unassigned_only=True)) == []
    assert seed_database(old.path) is True
    after = snapshot(old)
    assert len(after) == 21 and after[:20] == before
    matching = old.search_leads(SearchLeads(city="chennai", status="new", unassigned_only=True))
    assert [lead["id"] for lead in matching] == [21]
    assert matching[0]["email"] == db.UNASSIGNED_SEED_EMAIL
    details = old.get_lead_details(GetLeadDetails(lead_id=21))
    assert details["owner_id"] is None and details["owner_name"] is None
    with closing(old._connect(readonly=False)) as conn, conn:
        assert next(r for r in conn.execute("PRAGMA table_info(leads)") if r["name"] == "owner_id")["notnull"] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='leads_city'").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE leads SET owner_id=999 WHERE id=1")
        conn.execute("UPDATE leads SET owner_id=2 WHERE id=21")
        assert conn.execute("SELECT lead_id FROM observed_updates").fetchone()[0] == 21


def test_seed_upgrade_rolls_back_schema_and_data_on_failure(crm, tmp_path, monkeypatch):
    old = legacy_database(crm, tmp_path / "rollback.sqlite3")
    before = snapshot(old)
    original = db._allow_unassigned_owners

    def fail_after_rebuild(conn):
        original(conn)
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(db, "_allow_unassigned_owners", fail_after_rebuild)
    with pytest.raises(RuntimeError, match="migration failure"):
        seed_database(old.path)
    assert snapshot(old) == before
    with closing(old._connect()) as conn:
        assert next(r for r in conn.execute("PRAGMA table_info(leads)") if r["name"] == "owner_id")["notnull"] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='observe_owner'").fetchone()


def test_existing_matching_unassigned_record_does_not_cause_an_extra_fixture(crm, tmp_path):
    old = legacy_database(crm, tmp_path / "already_matching.sqlite3", nullable=True)
    before = snapshot(old)
    assert seed_database(old.path) is False
    assert snapshot(old) == before


def test_assignment_of_unassigned_fixture_requires_approval_and_reseed_preserves_it(crm, make_copilot):
    action = {"lead_id": 21, "assignee_id": 1, "request_key": "assign-unassigned-21"}
    before = snapshot(crm)
    bot, _ = make_copilot(call("assign_lead", action))
    paused = bot.chat("Assign lead 21 to agent 1")
    assert paused["__interrupt__"] and snapshot(crm) == before
    result = bot.resume({"approved": True})
    assert result["results"][-1]["result"]["previous_owner_id"] is None
    assert crm.get_lead_details(GetLeadDetails(lead_id=21))["owner_id"] == 1
    assigned = snapshot(crm)
    assert seed_database(crm.path) is False
    assert snapshot(crm) == assigned
    assert crm.search_leads(SearchLeads(city="Chennai", unassigned_only=True)) == []


def test_search_returns_all_21_records_and_owner_status_city_filters_remain_anded(crm):
    assert len(crm.search_leads(SearchLeads())) == 21
    assert [r["id"] for r in crm.search_leads(SearchLeads(city="Chennai", status="new", owner_id=1))] == [1]
    assert crm.search_leads(SearchLeads(city="Bangalore", unassigned_only=True)) == []
    assert crm.search_leads(SearchLeads(city="Chennai", status="won", unassigned_only=True)) == []
