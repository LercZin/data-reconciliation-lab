"""A consistent read snapshot; queries are versioned local SQL, never user input."""

import re
import sqlite3
from pathlib import Path

from .database import validate_schema


def report(connection: sqlite3.Connection) -> dict:
    if connection.in_transaction:
        raise ValueError("Report requires a connection without an active transaction.")
    validate_schema(connection)
    source = Path(__file__).with_name("reports.sql").read_text(encoding="utf-8")
    parts = re.split(r"^-- report: ([a-z_]+)\s*$", source, flags=re.MULTILINE)
    result = {}
    connection.execute("BEGIN")
    try:
        for index in range(1, len(parts), 2):
            result[parts[index]] = [dict(row) for row in connection.execute(parts[index + 1])]
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    result["totals"] = result["totals"][0]
    return result
