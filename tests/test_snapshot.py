"""Snapshot contracts against the synthetic example ledger and small inline ledgers."""
import datetime as dt
import tempfile
import textwrap
import unittest
from pathlib import Path

from jobs import snapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "example" / "main.beancount"
GENERATED = "2026-10-24T01:00:00Z"


def build(path, as_of=dt.date(2026, 10, 24)):
    entries, errors, options = snapshot.load(str(path))
    return snapshot.build_snapshot(entries, options, as_of, GENERATED, errors)


def ledger(text):
    handle = tempfile.NamedTemporaryFile("w", suffix=".beancount", delete=False, encoding="utf-8")
    handle.write(textwrap.dedent(text))
    handle.close()
    return Path(handle.name)


class SnapshotTests(unittest.TestCase):
    def test_example_matches_the_panel_schema(self):
        result = build(EXAMPLE)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual((result["as_of"], result["currency"], result["generated_at"]), ("2026-10-24", "CNY", GENERATED))
        for key in ("accounts", "balances", "monthly", "net_worth", "transactions"):
            self.assertIsInstance(result[key], list, key)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["unconverted_currencies"], [])

    def test_balances_are_converted_at_the_latest_price(self):
        balances = {row["account"]: row["amount"] for row in build(EXAMPLE)["balances"]}
        self.assertEqual(balances["Assets:Bank:Checking"], 65613.6)
        self.assertEqual(balances["Assets:Broker:USD"], 6288.38)  # 883.20 USD at 7.12
        self.assertEqual(balances["Liabilities:CreditCard"], -461.5)
        self.assertNotIn("Equity:Opening-Balances", balances)

    def test_monthly_rows_keep_beancount_signs_and_convert_at_the_transaction_date(self):
        rows = {(row["month"], row["account"]): row["amount"] for row in build(EXAMPLE)["monthly"]}
        self.assertEqual(rows[("2026-07", "Income:Salary")], -15000.0)
        self.assertEqual(rows[("2026-08", "Expenses:Travel")], 852.0)  # 120 USD at 7.10
        self.assertEqual(rows[("2026-09", "Income:Interest")], -22.56)  # 3.20 USD at 7.05
        self.assertNotIn(("2026-10", "Expenses:Travel"), rows)

    def test_net_worth_has_month_ends_and_ends_on_as_of(self):
        points = build(EXAMPLE)["net_worth"]
        self.assertEqual([point["date"] for point in points], ["2026-07-31", "2026-08-31", "2026-09-30", "2026-10-24"])
        self.assertEqual(points[0], {"date": "2026-07-31", "assets": 38781.6, "liabilities": 0.0, "net": 38781.6})
        last = points[-1]
        self.assertAlmostEqual(last["net"], last["assets"] + last["liabilities"], places=2)
        self.assertEqual(build(EXAMPLE, dt.date(2026, 9, 30))["net_worth"][-1]["date"], "2026-09-30")

    def test_transactions_are_recent_first_and_only_the_last_90_days(self):
        rows = build(EXAMPLE)["transactions"]
        self.assertEqual(rows[0]["date"], "2026-10-09")
        self.assertEqual(rows[-1]["date"], "2026-08-05")
        self.assertEqual(rows[0]["payee"], "超市")
        self.assertEqual(rows[0]["postings"][0], {"account": "Expenses:Food:Groceries", "amount": 193.5, "currency": "CNY"})
        self.assertEqual(next(row for row in rows if row["narration"] == "出差住宿")["tags"], ["trip-tokyo"])
        self.assertEqual(build(EXAMPLE, dt.date(2026, 7, 31))["transactions"][0]["date"], "2026-07-25")

    def test_entries_after_as_of_are_ignored(self):
        result = build(EXAMPLE, dt.date(2026, 8, 31))
        self.assertNotIn("2026-09", {row["month"] for row in result["monthly"]})
        self.assertEqual(result["net_worth"][-1]["date"], "2026-08-31")

    def test_renamed_roots_read_as_the_english_defaults(self):
        path = ledger("""
            option "operating_currency" "CNY"
            option "name_assets" "Activa"
            option "name_liabilities" "Passiva"
            option "name_equity" "Capital"
            option "name_income" "Revenue"
            option "name_expenses" "Costs"
            2026-01-01 open Activa:Bank CNY
            2026-01-01 open Passiva:Card CNY
            2026-01-01 open Revenue:Salary CNY
            2026-01-01 open Costs:Food CNY
            2026-10-01 * "工资"
              Activa:Bank  1000 CNY
              Revenue:Salary
            2026-10-02 * "晚饭"
              Costs:Food  50 CNY
              Passiva:Card
        """)
        self.addCleanup(path.unlink)
        result = build(path)
        self.assertEqual(result["error_count"], 0)
        self.assertEqual({row["account"] for row in result["balances"]}, {"Assets:Bank", "Liabilities:Card"})
        self.assertEqual({row["account"] for row in result["monthly"]}, {"Income:Salary", "Expenses:Food"})
        self.assertEqual(result["net_worth"][-1]["net"], 950.0)

    def test_currencies_without_a_price_are_reported_not_guessed(self):
        path = ledger("""
            option "operating_currency" "CNY"
            2026-01-01 open Assets:Wallet
            2026-01-01 open Equity:Opening
            2026-10-01 * "coins"
              Assets:Wallet  2 BTC
              Equity:Opening
            2026-10-01 * "cash"
              Assets:Wallet  100 CNY
              Equity:Opening
        """)
        self.addCleanup(path.unlink)
        result = build(path)
        self.assertEqual(result["unconverted_currencies"], ["BTC"])
        self.assertEqual(result["balances"], [{"account": "Assets:Wallet", "amount": 100.0}])

    def test_an_empty_ledger_is_still_valid(self):
        path = ledger('option "operating_currency" "USD"\n')
        self.addCleanup(path.unlink)
        result = build(path)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual((result["balances"], result["monthly"], result["net_worth"], result["transactions"]), ([], [], [], []))


if __name__ == "__main__":
    unittest.main()
