"""Canonical CSV values; monetary input never passes through floating point."""

import re
import unicodedata
from datetime import date

HEADERS = {
    "invoices": ("invoice_id", "customer_id", "customer_name", "issued_on", "due_on", "amount"),
    "payments": ("payment_id", "invoice_id", "paid_on", "amount"),
}
MAX_CENTS = 99_999_999_999
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
AMOUNT = re.compile(r"(?:0|[1-9][0-9]{0,8})\.[0-9]{2}\Z")
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


class InvalidRow(ValueError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason


def parse_amount(value: str) -> int:
    if not AMOUNT.fullmatch(value):
        raise InvalidRow("invalid_amount", "amount must use canonical digits and exactly two decimal places.")
    whole, fraction = value.split(".")
    cents = int(whole) * 100 + int(fraction)
    if not 1 <= cents <= MAX_CENTS:
        raise InvalidRow("invalid_amount", "amount must be between 0.01 and 999999999.99.")
    return cents


def parse_date(value: str, field: str) -> date:
    if not ISO_DATE.fullmatch(value):
        raise InvalidRow("invalid_date", f"{field} must use YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise InvalidRow("invalid_date", f"{field} is not a calendar date.") from error


def parse_row(kind: str, row: list[str]) -> dict[str, str | int]:
    if len(row) != len(HEADERS[kind]):
        raise InvalidRow("column_count", "row has a different number of columns than the header.")
    values = dict(zip(HEADERS[kind], row, strict=True))
    for field, value in values.items():
        if field.endswith("_id") and not IDENTIFIER.fullmatch(value):
            raise InvalidRow("invalid_id", f"{field} must be 1-64 ASCII letters/digits, underscores or hyphens.")
    if kind == "invoices":
        name = values["customer_name"]
        if not name or name != name.strip() or len(name) > 120 or any(
            unicodedata.category(character).startswith("C") for character in name
        ):
            raise InvalidRow("invalid_name", "customer_name must be 1-120 visible characters without edge spaces.")
        issued = parse_date(values["issued_on"], "issued_on")
        due = parse_date(values["due_on"], "due_on")
        if due < issued:
            raise InvalidRow("date_order", "due_on must not precede issued_on.")
    else:
        parse_date(values["paid_on"], "paid_on")
    amount = parse_amount(values.pop("amount"))
    return {**values, "amount_cents": amount}
