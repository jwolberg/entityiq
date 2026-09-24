"""pysqlite SAVEPOINT fix for test engines.

pysqlite's default transaction handling defers BEGIN and silently drops
SAVEPOINTs, so ``begin_nested()`` fails with "no such savepoint". This is
SQLAlchemy's documented recipe: turn off the driver's own BEGIN and emit it
ourselves. Needed only where a test binds the session to a Connection with a
transaction already open and the code under test nests inside it.
"""

from sqlalchemy import Engine, event


def enable_sqlite_savepoints(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _no_driver_begin(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _emit_begin(conn):
        conn.exec_driver_sql("BEGIN")
