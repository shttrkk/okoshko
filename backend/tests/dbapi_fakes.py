"""Strict synchronous DBAPI fakes: real QueuePool/dialect, fake wire transport."""
from threading import RLock, get_ident
from types import SimpleNamespace

import psycopg
from psycopg.pq import TransactionStatus


class FakeDatabase:
    def __init__(self):
        self.connections = []
        self.calls = []
        self.values = {}
        self.fail_commit = False
        self.lose_commit_response = False
        self.fail_rollback = False
        self.fail_reset = False
        self.fail_copy_after = None
        self.query_handler = None
        self.lock = RLock()

    def connect(self, *args, **kwargs):
        with self.lock:
            connection = FakeConnection(self, len(self.connections)+1)
            self.connections.append(connection)
            return connection


class FakeConnection:
    def __init__(self, database, identity):
        self.database, self.identity = database, identity
        self.closed = self.broken = False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.pending = {}
        self.temp_tables = set()
        self.commits = self.rollbacks = self.closes = self.context_entries = 0
        self.owner = None
        self._autocommit = False

    def _check_open(self):
        if self.closed:
            raise psycopg.OperationalError("Synthetic closed connection")

    @property
    def autocommit(self):
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._check_open()
        if self.info.transaction_status != TransactionStatus.IDLE:
            raise psycopg.ProgrammingError("Synthetic active transaction")
        self._autocommit = value

    def __enter__(self):
        self._check_open()
        self.context_entries += 1
        return self

    def __exit__(self, error, *_):
        try:
            self.rollback() if error else self.commit()
        finally:
            self.close()

    def cursor(self):
        self._check_open()
        return FakeCursor(self)

    def execute(self, query, params=None):
        cursor = self.cursor()
        cursor.execute(query, params)
        return cursor

    def commit(self):
        self._check_open()
        self.commits += 1
        if self.database.fail_commit:
            raise RuntimeError("Synthetic transaction failure")
        with self.database.lock:
            self.database.values.update(self.pending)
        self._finish()
        if self.database.lose_commit_response:
            raise psycopg.OperationalError("Synthetic lost commit acknowledgment")

    def rollback(self):
        self._check_open()
        self.rollbacks += 1
        if self.database.fail_rollback or self.database.fail_reset and self.info.transaction_status == TransactionStatus.IDLE:
            raise RuntimeError("Synthetic rollback failure")
        self._finish()

    def _finish(self):
        self.pending.clear()
        self.temp_tables.clear()
        self.owner = None
        self.info.transaction_status = TransactionStatus.IDLE

    def close(self):
        if not self.closed:
            self.closes += 1
            self._finish()
            self.closed = True
            self.info.transaction_status = TransactionStatus.UNKNOWN


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.closed = False
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def execute(self, query, params=None):
        connection = self.connection
        connection._check_open()
        if self.closed:
            raise psycopg.InterfaceError("Synthetic closed cursor")
        query = query.as_string() if hasattr(query, "as_string") else query
        if connection.info.transaction_status == TransactionStatus.INERROR:
            raise psycopg.errors.InFailedSqlTransaction("Synthetic failed transaction")
        if not connection.autocommit:
            assert connection.owner in {None, get_ident()}, "Physical connection shared by concurrent transactions"
            connection.owner = get_ident()
            connection.info.transaction_status = TransactionStatus.INTRANS
        with connection.database.lock:
            connection.database.calls.append((query, params))
        if query == "SELECT 1/0":
            connection.info.transaction_status = TransactionStatus.INERROR
            raise psycopg.errors.DivisionByZero("Synthetic SQL failure")
        if query.startswith("INSERT INTO pool_probe"):
            target = connection.database.values if connection.autocommit else connection.pending
            target[params[0]] = params[1]
        elif query.startswith("SELECT value FROM pool_probe"):
            self.rows = [(connection.pending.get(params[0], connection.database.values.get(params[0])),)]
        elif query.startswith("CREATE TEMP TABLE"):
            name = query.split()[3]
            if name in connection.temp_tables:
                connection.info.transaction_status = TransactionStatus.INERROR
                raise psycopg.errors.DuplicateTable("Synthetic temporary table leaked")
            connection.temp_tables.add(name)
        elif query == "SELECT 1":
            self.rows = [(1,)]
        elif query == ";":
            self.rows = []
        elif connection.database.query_handler:
            self.rows = connection.database.query_handler(query, params)
        return self

    def executemany(self, query, rows):
        for params in rows:
            self.execute(query, params)

    def copy(self, query):
        return FakeCopy(self.connection)

    def __iter__(self):
        return iter(self.rows)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self):
        self.closed = True


class FakeCopy:
    def __init__(self, connection):
        self.connection = connection
        self.rows = 0

    def __enter__(self):
        return self

    def __exit__(self, error, *_):
        if error:
            self.connection.info.transaction_status = TransactionStatus.INERROR

    def write_row(self, row):
        if self.connection.database.fail_copy_after == self.rows:
            raise RuntimeError("Synthetic COPY failure")
        self.rows += 1
