# Data Reconciliation Lab

**Python standard library · SQLite · SQL · deterministic CLI**

[![Checks](https://github.com/LercZin/data-reconciliation-lab/actions/workflows/checks.yml/badge.svg)](https://github.com/LercZin/data-reconciliation-lab/actions/workflows/checks.yml)

[Portfolio](https://github.com/LercZin/portfolio) · [Engineering map](https://github.com/LercZin/portfolio/blob/main/docs/engineering-map.md)

A small data engineering lab that validates CSV invoices and payments, preserves
rejected rows, and reconciles the accepted records with SQL. Created in November
2026 for this portfolio with fictional data. This is a new lab, not a previous
customer deployment or an accounting, tax or banking product.

The interesting parts are the failure boundaries: an invalid row can be reviewed
without discarding its valid neighbors, but a broken file or database operation
must not commit only the beginning of an import.

## Run

Requirements: Python **3.13 or 3.14**, with its standard `sqlite3` module and
SQLite 3.25+ for window functions. No package installation or runtime dependency.
Run these commands from the repository root.

```bash
python -m reconciliation demo
```

This one command copies the fictional samples into a temporary directory,
creates a database, imports both files, prints JSON, and removes its temporary
files. It uses no network, credentials or current date. Repeated fresh runs
produce the same JSON values and ordering. Non-ASCII JSON characters are escaped
for consistent output on terminals with different encodings.

To retain the generated inputs and database, select a **new** directory:

```bash
python -m reconciliation demo --directory demo-output
```

The command refuses an existing destination. It never overwrites supplied CSV
files. The regular import/report workflow is:

```bash
python -m reconciliation import invoices samples/invoices.csv --database local.sqlite
python -m reconciliation import payments samples/payments.csv --database local.sqlite
python -m reconciliation report --database local.sqlite
```

Import creates an empty lab database when necessary; its parent directory must
exist. An existing database must have the lab application ID, supported version
and expected table/column names. Report requires an existing compatible database
and does not initialize an empty file. Input/database failures return exit code 1
and JSON on stderr; success returns exit code 0 and JSON on stdout. Invalid
command usage follows argparse's standard help text and exit code 2.

## CSV contract

The exact ordered headers are:

```csv
invoice_id,customer_id,customer_name,issued_on,due_on,amount
```

```csv
payment_id,invoice_id,paid_on,amount
```

- UTF-8, with an optional initial BOM; quoted commas and Unicode names work.
- IDs are case-sensitive ASCII letters/digits, `_` or `-`, 1–64 characters,
  beginning with a letter or digit. No trimming or Unicode ID normalization.
- Names contain 1–120 visible characters, without leading/trailing whitespace
  or Unicode control/format characters. They are not normalized into another spelling.
- Dates use exact `YYYY-MM-DD` calendar dates; invoice due date cannot precede issue date.
- Amounts use a dot and **exactly two decimal places**, from `0.01` through
  `999999999.99`. No sign, grouping, leading zero, whitespace, fraction, exponent,
  NaN, infinity or rounding. Parsing uses digits and integer arithmetic throughout.
- Each batch contains at most 10,000 data rows. The CSV parser also retains its
  standard field-size limit. Blank, short and extra-column rows are quarantined.

## Ingestion decisions

`validation.py` defines canonical row values; `ingest.py` owns one explicit
`BEGIN IMMEDIATE` transaction per file. Accepted records, the batch counters and
quarantine rows commit together after the input closes successfully. Parser,
encoding, header, row-limit, database and read/close I/O errors roll back that
batch, preserving previous committed imports. A failed first import may leave
an initialized but empty lab database, with no partial ingestion.

A malformed value produces a quarantine row with source batch, physical CSV
line number, reason, detail and original parsed fields. A multiline record's
number is its ending physical line. Header/schema failures abort the batch and
do not create quarantine records for an interpretation the program cannot trust.

Accepted IDs are immutable. Same ID and canonical payload means `duplicate`;
different content means `conflict`, with the existing record preserved and the
incoming row retained in quarantine. Parameterized statements keep values as data.
There is no automatic correction, replacement or fuzzy match.

Idempotency applies to canonical invoice/payment state. Every successful import
still records a new audit batch, including repeated duplicates. In summaries,
`quarantined` counts **invalid rows**, while `conflict` counts conflicting rows.
Both are stored in the `quarantine` table: `quarantine_rows` includes their sum.
For each batch, `rows = inserted + duplicate + conflict + quarantined`.

## SQL reconciliation

All report queries are visible in [reports.sql](reconciliation/reports.sql).
They execute inside one read transaction for a consistent snapshot:

- Invoice status is `paid` when matching payment cents reach the invoice amount;
  otherwise it is `pending`. Remaining and excess amounts are separate fields.
- Orphan payments stay accepted without an invoice foreign key, so a later
  invoice import can resolve the mismatch without rewriting the payment.
- Quality aggregates separate inserted, duplicate, conflict and invalid rows,
  and group quarantine reasons.
- Payments before invoice issue are review flags, not automatically invalid:
  an advance payment can be legitimate. This lab makes no accounting conclusion.
- `SUM(amount_cents) OVER (...)` shows cumulative payments per invoice, ordered
  by payment date and payment ID with an explicit `ROWS` frame. The tie-break
  makes same-day payments deterministic and helps inspect when a balance was covered.

Money is stored and summed as integer cents. SQLite integer `SUM` raises on a
64-bit overflow instead of silently changing these queries to floating point.
That error is surfaced; this lab has no arbitrary-precision SQL aggregation.
Reports include all accepted payments, without a current-date cutoff.

## Verification and observed result

```bash
python -m unittest discover -s tests -v
```

Locally verified on **Windows / Python 3.14**: **27 tests passed**. The suite
includes real CLI subprocesses and temporary databases, UTF-8/BOM/Unicode,
strict numeric/date/header boundaries, same-batch and repeated-import conflicts,
orphan resolution, SQL window ordering, forced database failure, malformed CSV,
read and close failures, foreign/schema rejection, and input/hardlink protection.
The tests verify rollback of accepted rows, quarantine and batch metadata together.

Observed `demo` result:

| Measure | Result |
| --- | ---: |
| Accepted invoices / payments | 4 / 6 |
| Duplicate rows / conflicts | 2 / 2 |
| Invalid rows / total quarantine rows | 3 / 5 |
| Paid / pending invoices | 3 / 1 |
| Invoice / payment totals in cents | 25000 / 23500 |
| Remaining / excess invoice cents | 5000 / 1000 |
| Orphan payment cents | 2500 |
| Payment-before-issue review flags | 1 |

[CI](.github/workflows/checks.yml) is configured for Python 3.13 and 3.14 on
Ubuntu using pinned official actions. The local run does not establish a CI
result or a Python 3.13 execution; those results require the actual workflow run.

## Boundaries

The database is a local disposable lab, without access control, encryption,
multi-currency conversion, invoice cancellation, refunds, fuzzy matching,
production migration tooling or deployment. Row identity does not prove an
authentic payment. Schema checks identify a compatible layout, not malicious
tampering of a database controlled by another process. No large-volume benchmark,
distributed processing, production SLA or real customer result is claimed.

The code is split into a small CLI, validation, ingestion, database initialization
and SQL reporting; there is no service framework or ORM to hide the transaction
and query behavior. MIT license, copyright Lucas Emanuel Ramos Carvalho.
