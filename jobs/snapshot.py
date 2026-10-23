"""Build the ledger snapshot the panel accepts (schema 1) from a beancount file.

Amounts are converted to the first operating currency with the ledger's own
prices. Income and expense rows keep beancount's signs (income negative,
expenses positive). Account roots are renamed to the defaults, so a ledger that
sets option "name_assets" "Activa" still reads as Assets in the panel.
"""
from __future__ import annotations

import collections
import datetime as dt
from decimal import Decimal
from typing import Any, Iterable

from beancount import loader
from beancount.core import convert, getters, inventory, prices
from beancount.core.data import Transaction

SCHEMA_VERSION = 1
TRANSACTION_DAYS = 90
TRANSACTION_LIMIT = 5000
ROOTS = (
    ("name_assets", "Assets"),
    ("name_liabilities", "Liabilities"),
    ("name_equity", "Equity"),
    ("name_income", "Income"),
    ("name_expenses", "Expenses"),
)
ZERO = Decimal(0)


def money(value: Decimal) -> float:
    return float(round(value, 2)) + 0.0


def month_end(day: dt.date) -> dt.date:
    following = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return following - dt.timedelta(days=1)


def load(path: str) -> tuple[list, list, dict]:
    entries, errors, options = loader.load_file(path)
    return entries, errors, options


class Ledger:
    """Converts positions to the operating currency and renames account roots."""

    def __init__(self, entries: list, options: dict):
        self.currency = (options.get("operating_currency") or ["CNY"])[0]
        self.roots = {options.get(key) or name: name for key, name in ROOTS}
        self.price_map = prices.build_price_map(entries)
        self.unconverted: set[str] = set()

    def account(self, name: str) -> str:
        root, _, rest = name.partition(":")
        root = self.roots.get(root, root)
        return f"{root}:{rest}" if rest else root

    def root(self, name: str) -> str | None:
        return self.roots.get(name.partition(":")[0])

    def value(self, holdings: inventory.Inventory, date: dt.date) -> Decimal:
        total = ZERO
        for position in holdings:
            converted = convert.convert_position(position, self.currency, self.price_map, date)
            if converted.currency == self.currency:
                total += converted.number
            else:
                self.unconverted.add(position.units.currency)
        return total

    def weight(self, posting, date: dt.date) -> Decimal | None:
        amount = convert.convert_amount(convert.get_weight(posting), self.currency, self.price_map, date)
        if amount.currency != self.currency:
            self.unconverted.add(amount.currency)
            return None
        return amount.number


def _transaction_row(entry: Transaction, ledger: Ledger) -> dict[str, Any]:
    return {
        "date": entry.date.isoformat(),
        "flag": entry.flag,
        "payee": entry.payee or None,
        "narration": entry.narration or "",
        "tags": sorted(entry.tags or ()),
        "postings": [
            {"account": ledger.account(posting.account), "amount": float(posting.units.number), "currency": posting.units.currency}
            for posting in entry.postings
            if posting.units is not None and posting.units.number is not None
        ],
    }


def build_snapshot(
    entries: list,
    options: dict,
    as_of: dt.date,
    generated_at: str,
    errors: Iterable = (),
) -> dict[str, Any]:
    ledger = Ledger(entries, options)
    transactions = [entry for entry in entries if isinstance(entry, Transaction) and entry.date <= as_of]
    holdings: dict[str, inventory.Inventory] = collections.defaultdict(inventory.Inventory)
    monthly: dict[tuple[str, str], Decimal] = collections.defaultdict(Decimal)
    net_worth: list[dict[str, Any]] = []

    def worth(date: dt.date) -> dict[str, Any]:
        totals = {"Assets": ZERO, "Liabilities": ZERO}
        for name, held in holdings.items():
            totals[ledger.root(name)] += ledger.value(held, date)
        assets, liabilities = totals["Assets"], totals["Liabilities"]
        return {"date": date.isoformat(), "assets": money(assets), "liabilities": money(liabilities), "net": money(assets + liabilities)}

    boundary = month_end(transactions[0].date) if transactions else None
    for entry in transactions:
        while boundary and entry.date > boundary:
            net_worth.append(worth(boundary))
            boundary = month_end(boundary + dt.timedelta(days=1))
        month = entry.date.strftime("%Y-%m")
        for posting in entry.postings:
            root = ledger.root(posting.account)
            if root in ("Assets", "Liabilities"):
                holdings[posting.account].add_position(posting)
            elif root in ("Income", "Expenses"):
                amount = ledger.weight(posting, entry.date)
                if amount is not None:
                    monthly[(month, ledger.account(posting.account))] += amount
    while boundary and boundary < as_of:
        net_worth.append(worth(boundary))
        boundary = month_end(boundary + dt.timedelta(days=1))
    if transactions:
        net_worth.append(worth(as_of))

    balances = []
    for name in sorted(holdings):
        amount = ledger.value(holdings[name], as_of)
        if abs(amount) >= Decimal("0.005"):
            balances.append({"account": ledger.account(name), "amount": money(amount)})

    accounts = []
    for name, (opened, closed) in sorted(getters.get_account_open_close(entries).items()):
        row = {"account": ledger.account(name), "open": opened.date.isoformat() if opened else None}
        if closed:
            row["close"] = closed.date.isoformat()
        accounts.append(row)

    since = as_of - dt.timedelta(days=TRANSACTION_DAYS)
    recent = [_transaction_row(entry, ledger) for entry in reversed(transactions) if entry.date > since][:TRANSACTION_LIMIT]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "as_of": as_of.isoformat(),
        "currency": ledger.currency,
        "accounts": accounts,
        "balances": balances,
        "monthly": [
            {"month": month, "account": account, "amount": money(amount)}
            for (month, account), amount in sorted(monthly.items())
            if amount != ZERO
        ],
        "net_worth": net_worth,
        "transactions": recent,
        "unconverted_currencies": sorted(ledger.unconverted),
        "error_count": len(list(errors)),
    }
