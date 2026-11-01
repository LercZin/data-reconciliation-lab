import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reconciliation.validation import HEADERS

ROOT = Path(__file__).resolve().parent.parent


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lab-cli-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "space ç"
        self.directory.mkdir()

    def command(self, *arguments, status=0):
        process = subprocess.run(
            [sys.executable, "-m", "reconciliation", *map(str, arguments)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(process.returncode, status, process.stderr)
        if status == 0:
            self.assertEqual(process.stderr, "")
            return json.loads(process.stdout)
        self.assertEqual(process.stdout, "")
        self.assertNotIn("Traceback", process.stderr)
        return json.loads(process.stderr)

    def csv_file(self, name, kind, rows):
        source = self.directory / name
        with source.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS[kind])
            writer.writerows(rows)
        return source

    def test_demo_is_deterministic_and_exercises_quality_cases(self):
        first = self.command("demo")
        self.assertEqual(first, self.command("demo"))
        self.assertEqual(first["report"]["totals"], {
            "invoices": 4, "payments": 6, "batches": 2, "quarantine_rows": 5,
            "invoiced_cents": 25000, "payment_cents": 23500,
        })
        self.assertEqual(first["imports"][0]["conflict"], 1)
        self.assertEqual(first["imports"][1]["duplicate"], 1)
        self.assertEqual([row["invoice_id"] for row in first["report"]["invoices"] if row["status"] == "pending"], ["INV-002"])
        self.assertEqual(first["report"]["orphan_payments"][0]["payment_id"], "PAY-005")
        self.assertEqual(first["report"]["invoices"][2]["excess_cents"], 1000)

    def test_real_import_replay_conflict_and_report_keep_input_unchanged(self):
        database = self.directory / "state.sqlite"
        invoices = self.csv_file("Unicode invoices.csv", "invoices", [
            ["INV-A", "CUST-A", "Café 東京", "2026-10-01", "2026-10-10", "10.00"],
        ])
        payments = self.csv_file("payments.csv", "payments", [
            ["PAY-A", "INV-A", "2026-10-05", "4.00"],
            ["PAY-A", "INV-A", "2026-10-05", "5.00"],
        ])
        original = (invoices.read_bytes(), payments.read_bytes())
        self.assertEqual(self.command("import", "invoices", invoices, "--database", database)["inserted"], 1)
        self.assertEqual(self.command("import", "invoices", invoices, "--database", database)["duplicate"], 1)
        self.assertEqual(self.command("import", "payments", payments, "--database", database)["conflict"], 1)
        result = self.command("report", "--database", database)
        self.assertEqual(result["invoices"][0]["remaining_cents"], 600)
        self.assertEqual(result["invoices"][0]["customer_name"], "Café 東京")
        self.assertEqual((invoices.read_bytes(), payments.read_bytes()), original)

    def test_persistent_demo_requires_a_new_directory(self):
        destination = self.directory / "new-demo"
        result = self.command("demo", "--directory", destination)
        self.assertTrue((destination / "invoices.csv").is_file())
        self.assertEqual(self.command("report", "--database", destination / "lab.sqlite"), result["report"])
        original = (destination / "invoices.csv").read_bytes()
        self.assertIn("error", self.command("demo", "--directory", destination, status=1))
        self.assertEqual((destination / "invoices.csv").read_bytes(), original)

    def test_import_never_uses_input_or_its_hardlink_as_database(self):
        source = self.csv_file("input.csv", "payments", [["PAY-A", "INV-A", "2026-10-05", "1.00"]])
        original = source.read_bytes()
        self.command("import", "payments", source, "--database", source, status=1)
        self.assertEqual(source.read_bytes(), original)
        alias = self.directory / "alias.sqlite"
        try:
            os.link(source, alias)
        except OSError:
            self.skipTest("This filesystem does not permit hardlinks.")
        self.command("import", "payments", source, "--database", alias, status=1)
        self.assertEqual(source.read_bytes(), original)

    def test_missing_input_and_report_do_not_create_a_database(self):
        missing_database = self.directory / "missing.sqlite"
        self.command("report", "--database", missing_database, status=1)
        self.command("import", "payments", self.directory / "missing.csv", "--database", missing_database, status=1)
        self.assertFalse(missing_database.exists())

    def test_report_rejects_an_empty_existing_file_without_initializing_it(self):
        database = self.directory / "empty.sqlite"
        database.touch()
        self.command("report", "--database", database, status=1)
        self.assertEqual(database.read_bytes(), b"")

    def test_failed_cli_import_preserves_previously_committed_state(self):
        destination = self.directory / "demo"
        before = self.command("demo", "--directory", destination)["report"]
        bad = self.directory / "bad.csv"
        bad.write_text(
            ",".join(HEADERS["payments"]) + '\nPAY-NEW,INV-001,2026-10-06,1.00\n"broken\n', encoding="utf-8"
        )
        self.command("import", "payments", bad, "--database", destination / "lab.sqlite", status=1)
        self.assertEqual(self.command("report", "--database", destination / "lab.sqlite"), before)


if __name__ == "__main__":
    unittest.main()
