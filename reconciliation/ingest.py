"""Atomic CSV batches with row quarantine and immutable accepted IDs."""

import csv
import json
import sqlite3
from pathlib import Path

from .database import validate_schema
from .validation import HEADERS, InvalidRow, parse_row

MAX_ROWS = 10_000


class InputError(ValueError):
    """A structural input problem aborts the entire batch."""


def _store(connection: sqlite3.Connection, kind: str, record: dict) -> str:
    identifier = "invoice_id" if kind == "invoices" else "payment_id"
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    existing = connection.execute(
        f"SELECT payload FROM {kind} WHERE {identifier} = ?", (record[identifier],)
    ).fetchone()
    if existing:
        return "duplicate" if existing["payload"] == payload else "conflict"
    fields = [*record, "payload"]
    placeholders = ", ".join("?" for _ in fields)
    connection.execute(
        f"INSERT INTO {kind} ({', '.join(fields)}) VALUES ({placeholders})",
        [*record.values(), payload],
    )
    return "inserted"


def _quarantine(connection, batch_id, kind, number, raw, reason, detail):
    connection.execute(
        "INSERT INTO quarantine (batch_id, kind, row_number, reason, detail, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (batch_id, kind, number, reason, detail, json.dumps(raw, ensure_ascii=False)),
    )


def ingest(connection: sqlite3.Connection, kind: str, source: Path | str) -> dict:
    if kind not in HEADERS:
        raise InputError("kind must be invoices or payments.")
    if connection.in_transaction:
        raise InputError("Import requires a connection without an active transaction.")
    validate_schema(connection)
    source = Path(source)
    summary = {
        "kind": kind, "source": source.name, "rows": 0,
        "inserted": 0, "duplicate": 0, "conflict": 0, "quarantined": 0,
    }
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, None)
            if header != list(HEADERS[kind]):
                raise InputError(f"Expected the exact ordered header: {','.join(HEADERS[kind])}")
            connection.execute("BEGIN IMMEDIATE")
            batch_id = connection.execute(
                "INSERT INTO batches (kind, source_name) VALUES (?, ?)", (kind, source.name)
            ).lastrowid
            for raw in reader:
                summary["rows"] += 1
                if summary["rows"] > MAX_ROWS:
                    raise InputError(f"A batch may contain at most {MAX_ROWS} data rows.")
                try:
                    record = parse_row(kind, raw)
                except InvalidRow as error:
                    summary["quarantined"] += 1
                    _quarantine(connection, batch_id, kind, reader.line_num, raw, error.reason, str(error))
                    continue
                outcome = _store(connection, kind, record)
                summary[outcome] += 1
                if outcome == "conflict":
                    _quarantine(
                        connection, batch_id, kind, reader.line_num, raw,
                        "id_conflict", "Existing ID has a different canonical payload; original preserved.",
                    )
            connection.execute(
                "UPDATE batches SET row_count=?, inserted_count=?, duplicate_count=?, "
                "conflict_count=?, quarantined_count=? WHERE batch_id=?",
                (summary["rows"], summary["inserted"], summary["duplicate"],
                 summary["conflict"], summary["quarantined"], batch_id),
            )
        # Close the input successfully before committing, including close-time IO errors.
        connection.commit()
        return summary
    except (csv.Error, UnicodeError) as error:
        connection.rollback()
        raise InputError("Malformed CSV or invalid UTF-8; the entire batch was rolled back.") from error
    except BaseException:
        connection.rollback()
        raise
