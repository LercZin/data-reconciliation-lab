-- report: invoices
WITH paid AS (
    SELECT invoice_id, SUM(amount_cents) AS paid_cents
    FROM payments GROUP BY invoice_id
)
SELECT i.invoice_id, i.customer_id, i.customer_name, i.issued_on, i.due_on,
       i.amount_cents, COALESCE(p.paid_cents, 0) AS paid_cents,
       MAX(i.amount_cents - COALESCE(p.paid_cents, 0), 0) AS remaining_cents,
       MAX(COALESCE(p.paid_cents, 0) - i.amount_cents, 0) AS excess_cents,
       CASE WHEN COALESCE(p.paid_cents, 0) >= i.amount_cents
            THEN 'paid' ELSE 'pending' END AS status
FROM invoices i LEFT JOIN paid p ON p.invoice_id = i.invoice_id
ORDER BY i.invoice_id;

-- report: orphan_payments
SELECT p.payment_id, p.invoice_id, p.paid_on, p.amount_cents
FROM payments p LEFT JOIN invoices i ON i.invoice_id = p.invoice_id
WHERE i.invoice_id IS NULL
ORDER BY p.payment_id;

-- report: payment_timeline
SELECT payment_id, invoice_id, paid_on, amount_cents,
       SUM(amount_cents) OVER (
           PARTITION BY invoice_id ORDER BY paid_on, payment_id
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
       ) AS cumulative_paid_cents
FROM payments
ORDER BY invoice_id, paid_on, payment_id;

-- report: review_flags
SELECT p.payment_id, p.invoice_id, 'payment_before_issue' AS reason
FROM payments p JOIN invoices i ON i.invoice_id = p.invoice_id
WHERE p.paid_on < i.issued_on
ORDER BY p.payment_id;

-- report: quality_by_kind
SELECT kind, COUNT(*) AS batches, SUM(row_count) AS rows,
       SUM(inserted_count) AS inserted, SUM(duplicate_count) AS duplicate,
       SUM(conflict_count) AS conflict, SUM(quarantined_count) AS quarantined
FROM batches GROUP BY kind ORDER BY kind;

-- report: quarantine_reasons
SELECT reason, COUNT(*) AS rows
FROM quarantine GROUP BY reason ORDER BY reason;

-- report: totals
SELECT (SELECT COUNT(*) FROM invoices) AS invoices,
       (SELECT COUNT(*) FROM payments) AS payments,
       (SELECT COUNT(*) FROM batches) AS batches,
       (SELECT COUNT(*) FROM quarantine) AS quarantine_rows,
       COALESCE((SELECT SUM(amount_cents) FROM invoices), 0) AS invoiced_cents,
       COALESCE((SELECT SUM(amount_cents) FROM payments), 0) AS payment_cents;
