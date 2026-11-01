PRAGMA application_id = 1146244162;
PRAGMA user_version = 1;

CREATE TABLE batches (
    batch_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('invoices', 'payments')),
    source_name TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    quarantined_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    issued_on TEXT NOT NULL,
    due_on TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents BETWEEN 1 AND 99999999999),
    payload TEXT NOT NULL
);

-- No FK to invoices: unmatched payments must remain visible for reconciliation.
CREATE TABLE payments (
    payment_id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL,
    paid_on TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents BETWEEN 1 AND 99999999999),
    payload TEXT NOT NULL
);
CREATE INDEX payments_invoice_date ON payments(invoice_id, paid_on, payment_id);

CREATE TABLE quarantine (
    quarantine_id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(batch_id),
    row_number INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('invoices', 'payments')),
    reason TEXT NOT NULL,
    detail TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
