"""Run with python -m reconciliation; inputs are always opened read-only."""

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from .database import open_database
from .ingest import ingest
from .reporting import report
from .validation import HEADERS


def _demo(directory: Path) -> dict:
    samples = Path(__file__).resolve().parent.parent / "samples"
    for kind in HEADERS:
        shutil.copyfile(samples / f"{kind}.csv", directory / f"{kind}.csv")
    with closing(open_database(directory / "lab.sqlite")) as connection:
        imports = [ingest(connection, kind, directory / f"{kind}.csv") for kind in HEADERS]
        return {"imports": imports, "report": report(connection)}


def _different_files(database: Path, source: Path) -> None:
    if database.resolve() == source.resolve() or (
        database.exists() and source.exists() and database.samefile(source)
    ):
        raise ValueError("The database and CSV input must be different files.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Reconcile fictional invoices and payments in integer cents.")
    commands = result.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Generate and import fictional samples, then print JSON.")
    demo.add_argument("--directory", type=Path, help="New directory to preserve fixtures and database; must not exist.")
    importing = commands.add_parser("import", help="Import one CSV as one atomic batch.")
    importing.add_argument("kind", choices=tuple(HEADERS))
    importing.add_argument("source", type=Path)
    importing.add_argument("--database", type=Path, required=True)
    reporting = commands.add_parser("report", help="Print SQL reports from an existing lab database.")
    reporting.add_argument("--database", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "demo":
            if arguments.directory:
                arguments.directory.mkdir()  # Exclusive: never overwrite existing files.
                output = _demo(arguments.directory)
            else:
                with tempfile.TemporaryDirectory(prefix="reconciliation-lab-") as temporary:
                    output = _demo(Path(temporary))
        else:
            if arguments.command == "import":
                _different_files(arguments.database, arguments.source)
                if not arguments.source.is_file():
                    raise ValueError("CSV input must be an existing file.")
            elif not arguments.database.is_file():
                raise ValueError("Report requires an existing lab database.")
            with closing(open_database(arguments.database, create=arguments.command == "import")) as connection:
                output = (
                    ingest(connection, arguments.kind, arguments.source)
                    if arguments.command == "import" else report(connection)
                )
        print(json.dumps(output, ensure_ascii=True, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1
