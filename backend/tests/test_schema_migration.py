"""Schema upgrades run only against the isolated test database."""

import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

import pytest
from sqlalchemy import text

from app import db


def test_schema_marker_and_failed_ddl_rollback(monkeypatch):
    assert db.engine.url.database == "report_platform_test"
    db.init_db()
    with db.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM schema_migrations WHERE version='20260927_next28_40'")) == 1

    original = db.Base.metadata.create_all

    def fail_after_ddl(connection):
        original(connection)
        connection.execute(text("CREATE TABLE migration_failure_probe (id integer)"))
        raise RuntimeError("simulated upgrade failure")

    monkeypatch.setattr(db.Base.metadata, "create_all", fail_after_ddl)
    with pytest.raises(RuntimeError, match="simulated"):
        db.init_db()
    with db.engine.connect() as connection:
        assert connection.scalar(text("SELECT to_regclass('migration_failure_probe')")) is None
