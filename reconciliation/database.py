"""Create only an empty database; refuse foreign or incompatible schemas."""

import sqlite3
from pathlib import Path

APPLICATION_ID = 1146244162
SCHEMA_VERSION = 1
TABLE_COLUMNS = {
    "batches": (
        "batch_id", "kind", "source_name", "row_count", "inserted_count",
        "duplicate_count", "conflict_count", "quarantined_count",
    ),
    "invoices": (
        "invoice_id", "customer_id", "customer_name", "issued_on", "due_on",
        "amount_cents", "payload",
    ),
    "payments": ("payment_id", "invoice_id", "paid_on", "amount_cents", "payload"),
    "quarantine": (
        "quarantine_id", "batch_id", "row_number", "kind", "reason", "detail", "raw_json",
    ),
}


class SchemaError(ValueError):
    """The selected file is not a compatible lab database."""


def validate_schema(connection: sqlite3.Connection) -> None:
    application = connection.execute("PRAGMA application_id").fetchone()[0]
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if application != APPLICATION_ID or version != SCHEMA_VERSION or tables != set(TABLE_COLUMNS):
        raise SchemaError("Expected a reconciliation lab database with schema version 1.")
    for table, columns in TABLE_COLUMNS.items():
        actual = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        if actual != columns:
            raise SchemaError(f"Incompatible columns in {table}.")


def open_database(path: Path | str, *, create: bool = True) -> sqlite3.Connection:
    if not create and not Path(path).is_file():
        raise SchemaError("Report requires an existing lab database.")
    connection = sqlite3.connect(path, isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if create and not tables and application == 0 and version == 0:
            source = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
            connection.executescript("BEGIN IMMEDIATE;\n" + source + "\nCOMMIT;")
        validate_schema(connection)
        return connection
    except BaseException:
        connection.rollback()
        connection.close()
        raise
