import csv
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from reconciliation.database import SchemaError, open_database
from reconciliation.ingest import InputError, ingest
from reconciliation.reporting import report
from reconciliation.validation import HEADERS, MAX_CENTS, InvalidRow, parse_amount


class LabTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.connection = open_database(self.directory / "lab.sqlite")
        self.addCleanup(self.connection.close)

    def csv_file(self, kind, rows, *, name="input.csv", bom=False):
        path = self.directory / name
        with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS[kind])
            writer.writerows(rows)
        return path

    def state(self):
        return {
            table: [tuple(row) for row in self.connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("invoices", "payments", "batches", "quarantine")
        }

    def invoice(self, identifier="INV-A", amount="10.00", name="Fictional store"):
        return [identifier, "CUST-A", name, "2026-10-01", "2026-10-10", amount]


class ValidationTests(LabTestCase):
    def test_exact_integer_cents_and_maximum(self):
        self.assertEqual(parse_amount("0.01"), 1)
        self.assertEqual(parse_amount("123.45"), 12345)
        self.assertEqual(parse_amount("999999999.99"), MAX_CENTS)
        self.assertIs(type(parse_amount("0.10")), int)

    def test_rejects_noncanonical_amounts_without_rounding(self):
        invalid = [
            "NaN", "nan", "Inf", "Infinity", "-Infinity", "1/2", "1e2", "1E+2",
            "1.001", "1.000", "0.009", "1000000000.00", "0.00", "-1.00",
            "+1.00", "01.00", "1", "1.0", ".50", "1.", " 1.00", "1.00 ",
            "1,00", "١.00", "１.00", "1.00\n", "", "9" * 1000 + ".00",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(InvalidRow):
                parse_amount(value)

    def test_bom_unicode_and_quoted_comma_are_preserved(self):
        name = "Café Açúcar, 東京"
        path = self.csv_file("invoices", [self.invoice(name=name)], bom=True)
        original = path.read_bytes()
        result = ingest(self.connection, "invoices", path)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(report(self.connection)["invoices"][0]["customer_name"], name)
        self.assertEqual(path.read_bytes(), original)

    def test_strict_headers_abort_without_a_batch(self):
        headers = [
            [], list(reversed(HEADERS["payments"])),
            ["payment_id", "invoice_id", "paid_on", "paid_on"],
            ["payment_id", "invoice_id", "paid_on"],
            [*HEADERS["payments"], "extra"],
            ["payment_id ", "invoice_id", "paid_on", "amount"],
        ]
        before = self.state()
        for header in headers:
            with self.subTest(header=header):
                path = self.directory / "header.csv"
                path.write_text(
                    ",".join(header) + "\nPAY-A,INV-A,2026-10-05,1.00\n", encoding="utf-8"
                )
                with self.assertRaises(InputError):
                    ingest(self.connection, "payments", path)
                self.assertEqual(self.state(), before)

    def test_ids_and_dates_are_quarantined_but_valid_rows_continue(self):
        rows = [self.invoice(identifier="A" * 64)]
        for identifier in ("A" * 65, "INV Á", "INV-Á", " INV-A", "", "../INV-A"):
            rows.append(self.invoice(identifier=identifier))
        for bad_date in ("2026-02-29", "2026-02-30", "2026-13-01", "2026-1-01", "0000-01-01"):
            item = self.invoice(identifier=f"DATE-{len(rows)}")
            item[3] = bad_date
            rows.append(item)
        reversed_dates = self.invoice(identifier="REVERSED")
        reversed_dates[4] = "2026-09-01"
        rows.append(reversed_dates)
        leap = self.invoice(identifier="LEAP")
        leap[3:5] = ["2024-02-29", "2024-03-01"]
        rows.append(leap)
        result = ingest(self.connection, "invoices", self.csv_file("invoices", rows))
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["quarantined"], len(rows) - 2)
        reasons = {row[0] for row in self.connection.execute("SELECT reason FROM quarantine")}
        self.assertEqual(reasons, {"invalid_id", "invalid_date", "date_order"})

    def test_blank_short_extra_and_control_character_rows_are_quarantined(self):
        rows = [[], self.invoice()[:-1], [*self.invoice(), "extra"], self.invoice(name="bad\nname")]
        result = ingest(self.connection, "invoices", self.csv_file("invoices", rows))
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["quarantined"], 4)
        stored = self.connection.execute("SELECT row_number, reason, raw_json FROM quarantine ORDER BY 1").fetchall()
        self.assertEqual(stored[0]["row_number"], 2)
        self.assertEqual(json.loads(stored[-1]["raw_json"])[2], "bad\nname")
        self.assertEqual(stored[-1]["reason"], "invalid_name")

    def test_sql_like_text_is_data_not_an_executed_statement(self):
        name = "Fiction'); DROP TABLE invoices; --"
        ingest(self.connection, "invoices", self.csv_file("invoices", [self.invoice(name=name)]))
        self.assertEqual(report(self.connection)["invoices"][0]["customer_name"], name)


class BatchTests(LabTestCase):
    def test_reimports_and_conflicts_preserve_canonical_invoices_and_payments(self):
        cases = [
            ("invoices", self.invoice()),
            ("payments", ["PAY-A", "INV-A", "2026-10-05", "10.00"]),
        ]
        for kind, original in cases:
            with self.subTest(kind=kind):
                path = self.csv_file(kind, [original], name=f"{kind}.csv")
                self.assertEqual(ingest(self.connection, kind, path)["inserted"], 1)
                stored = tuple(self.connection.execute(f"SELECT * FROM {kind}").fetchone())
                self.assertEqual(ingest(self.connection, kind, path)["duplicate"], 1)
                changed = [*original[:-1], "11.00"]
                conflict = ingest(self.connection, kind, self.csv_file(kind, [changed, original]))
                self.assertEqual((conflict["conflict"], conflict["duplicate"]), (1, 1))
                self.assertEqual(self.connection.execute(f"SELECT COUNT(*) FROM {kind}").fetchone()[0], 1)
                self.assertEqual(tuple(self.connection.execute(f"SELECT * FROM {kind}").fetchone()), stored)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM quarantine WHERE reason='id_conflict'").fetchone()[0], 2
        )

    def test_duplicates_and_conflicts_in_the_same_batch_are_classified(self):
        rows = [self.invoice(), self.invoice(), self.invoice(amount="11.00")]
        result = ingest(self.connection, "invoices", self.csv_file("invoices", rows))
        self.assertEqual(
            {key: result[key] for key in ("rows", "inserted", "duplicate", "conflict", "quarantined")},
            {"rows": 3, "inserted": 1, "duplicate": 1, "conflict": 1, "quarantined": 0},
        )
        self.assertEqual(report(self.connection)["totals"]["quarantine_rows"], 1)

    def test_malformed_csv_after_a_valid_row_rolls_back_the_entire_batch(self):
        path = self.directory / "broken.csv"
        path.write_text(
            ",".join(HEADERS["payments"]) + '\nPAY-A,INV-A,2026-10-05,1.00\n"unterminated\n',
            encoding="utf-8",
        )
        before = self.state()
        with self.assertRaises(InputError):
            ingest(self.connection, "payments", path)
        self.assertEqual(self.state(), before)
        self.assertFalse(self.connection.in_transaction)

    def test_invalid_utf8_aborts_without_partial_acceptance(self):
        path = self.directory / "invalid.csv"
        path.write_bytes(
            (",".join(HEADERS["payments"]) + "\nPAY-A,INV-A,2026-10-05,1.00\n").encode() + b"\xff\n"
        )
        before = self.state()
        with self.assertRaises(InputError):
            ingest(self.connection, "payments", path)
        self.assertEqual(self.state(), before)

    def test_database_failure_rolls_back_valid_rows_quarantine_and_batch(self):
        ingest(self.connection, "invoices", self.csv_file("invoices", [self.invoice()]))
        self.connection.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON payments "
            "WHEN NEW.payment_id = 'PAY-B' BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
        )
        path = self.csv_file("payments", [
            ["PAY-A", "INV-A", "2026-10-05", "1.00"],
            ["PAY-INVALID", "INV-A", "2026-10-05", "NaN"],
            ["PAY-B", "INV-A", "2026-10-05", "1.00"],
        ])
        before = self.state()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "forced failure"):
            ingest(self.connection, "payments", path)
        self.assertEqual(self.state(), before)

    def test_read_and_close_io_failures_roll_back(self):
        class FailingInput(io.StringIO):
            def __init__(self, *, fail_close):
                super().__init__(",".join(HEADERS["payments"]) + "\nPAY-A,INV-A,2026-10-05,1.00\n")
                self.lines = 0
                self.fail_close = fail_close

            def __next__(self):
                self.lines += 1
                if self.lines == 3 and not self.fail_close:
                    raise OSError("forced read failure")
                return super().__next__()

            def __exit__(self, *arguments):
                super().__exit__(*arguments)
                if self.fail_close:
                    raise OSError("forced close failure")

        before = self.state()
        for fail_close in (False, True):
            with self.subTest(fail_close=fail_close):
                with patch("pathlib.Path.open", return_value=FailingInput(fail_close=fail_close)):
                    with self.assertRaises(OSError):
                        ingest(self.connection, "payments", self.directory / "simulated.csv")
                self.assertEqual(self.state(), before)

    def test_batch_limit_aborts_instead_of_committing_a_prefix(self):
        rows = [[f"PAY-{number}", "INV-A", "2026-10-05", "1.00"] for number in range(3)]
        before = self.state()
        with patch("reconciliation.ingest.MAX_ROWS", 2), self.assertRaises(InputError):
            ingest(self.connection, "payments", self.csv_file("payments", rows))
        self.assertEqual(self.state(), before)

    def test_incompatible_schema_is_rejected_before_ingestion(self):
        self.connection.execute("ALTER TABLE payments RENAME COLUMN paid_on TO paid_date")
        before = self.state()
        with self.assertRaises(SchemaError):
            ingest(self.connection, "payments", self.csv_file("payments", [["PAY-A", "INV-A", "2026-10-05", "1.00"]]))
        self.assertEqual(self.state(), before)

    def test_foreign_database_is_not_modified(self):
        foreign_path = self.directory / "foreign.sqlite"
        with closing(sqlite3.connect(foreign_path)) as foreign:
            with foreign:
                foreign.execute("CREATE TABLE unrelated_table (value TEXT)")
                foreign.execute("INSERT INTO unrelated_table VALUES ('synthetic fixture')")
        before = foreign_path.read_bytes()
        with self.assertRaises(SchemaError):
            open_database(foreign_path)
        self.assertEqual(foreign_path.read_bytes(), before)

    def test_caller_transaction_is_never_implicitly_committed(self):
        self.connection.execute("BEGIN")
        with self.assertRaises(InputError):
            ingest(self.connection, "payments", self.directory / "unused.csv")
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()


class ReportTests(LabTestCase):
    def test_empty_database_and_unpaid_invoice_have_explicit_results(self):
        empty = report(self.connection)
        self.assertEqual(empty["invoices"], [])
        self.assertEqual(empty["totals"], {
            "invoices": 0, "payments": 0, "batches": 0, "quarantine_rows": 0,
            "invoiced_cents": 0, "payment_cents": 0,
        })
        ingest(self.connection, "invoices", self.csv_file("invoices", [self.invoice(amount="999999999.99")]))
        invoice = report(self.connection)["invoices"][0]
        self.assertEqual(invoice["status"], "pending")
        self.assertEqual((invoice["paid_cents"], invoice["remaining_cents"]), (0, MAX_CENTS))

    def test_paid_pending_excess_orphan_quality_and_stable_window_order(self):
        invoices = [self.invoice(), self.invoice(identifier="INV-B"), self.invoice(identifier="INV-C")]
        ingest(self.connection, "invoices", self.csv_file("invoices", invoices))
        payments = [
            ["PAY-Z", "INV-A", "2026-10-05", "6.00"],
            ["PAY-A", "INV-A", "2026-10-05", "4.00"],
            ["PAY-B", "INV-B", "2026-10-06", "2.00"],
            ["PAY-C", "INV-C", "2026-10-06", "11.00"],
            ["PAY-O", "MISSING", "2026-10-06", "3.00"],
            ["PAY-INVALID", "INV-A", "2026-10-06", "Inf"],
        ]
        ingest(self.connection, "payments", self.csv_file("payments", payments))
        result = report(self.connection)
        self.assertEqual(
            [(row["status"], row["remaining_cents"], row["excess_cents"]) for row in result["invoices"]],
            [("paid", 0, 0), ("pending", 800, 0), ("paid", 0, 100)],
        )
        self.assertEqual(result["orphan_payments"][0]["payment_id"], "PAY-O")
        timeline = [row for row in result["payment_timeline"] if row["invoice_id"] == "INV-A"]
        self.assertEqual([(row["payment_id"], row["cumulative_paid_cents"]) for row in timeline], [("PAY-A", 400), ("PAY-Z", 1000)])
        self.assertEqual(result["quarantine_reasons"], [{"reason": "invalid_amount", "rows": 1}])
        self.assertEqual(result["totals"]["payment_cents"], 2600)
        self.assertEqual(result, report(self.connection))

    def test_orphan_can_be_resolved_by_later_invoice_without_reimporting_payment(self):
        ingest(self.connection, "payments", self.csv_file("payments", [["PAY-A", "INV-A", "2026-09-30", "10.00"]]))
        self.assertEqual(len(report(self.connection)["orphan_payments"]), 1)
        ingest(self.connection, "invoices", self.csv_file("invoices", [self.invoice()]))
        result = report(self.connection)
        self.assertEqual(result["orphan_payments"], [])
        self.assertEqual(result["invoices"][0]["status"], "paid")
        self.assertEqual(result["review_flags"], [{"payment_id": "PAY-A", "invoice_id": "INV-A", "reason": "payment_before_issue"}])


if __name__ == "__main__":
    unittest.main()
