"""Number and date reading must refuse what it cannot read unambiguously."""
import datetime as dt
import unittest
from decimal import Decimal

from inbox_radar import text as T


class Amounts(unittest.TestCase):
    def values(self, s, fmt="1,234.56"):
        return [(v, c) for _p, v, c in T.amounts(s, fmt)]

    def test_us_and_european_formats(self):
        self.assertEqual(self.values("New balance $1,234.56"), [(Decimal("1234.56"), "USD")])
        self.assertEqual(self.values("Total: 1.234,56 €", "1.234,56"), [(Decimal("1234.56"), "EUR")])
        self.assertEqual(self.values("USD 54.32 due"), [(Decimal("54.32"), "USD")])
        self.assertEqual(self.values("23,45 EUR", "1.234,56"), [(Decimal("23.45"), "EUR")])

    def test_a_number_in_the_wrong_format_is_not_reinterpreted(self):
        self.assertEqual(self.values("Total: 1.234,56 €"), [])         # read as US: rejected
        self.assertEqual(self.values("$1,234.56", "1.234,56"), [])

    def test_bare_numbers_are_not_amounts(self):
        for s in ("card ending in 1234", "reference 2026", "call 555 0100", "12/05/2026"):
            self.assertEqual(self.values(s), [], s)

    def test_whole_amounts_and_large_ones(self):
        self.assertEqual(self.values("$1,200"), [(Decimal("1200.00"), "USD")])
        self.assertEqual(self.values("$12,345,678.90"), [(Decimal("12345678.90"), "USD")])


class Dates(unittest.TestCase):
    def dates(self, s, order="MDY"):
        return [d for _p, d in T.dates(s, order)]

    def test_named_months_in_two_languages(self):
        d = dt.date(2026, 6, 3)
        for s in ("3 June 2026", "June 3, 2026", "Jun 3, 2026", "3 de junio de 2026", "3 jun 2026"):
            self.assertEqual(self.dates(s), [d], s)

    def test_numeric_order_comes_from_the_rule(self):
        self.assertEqual(self.dates("03/06/2026", "DMY"), [dt.date(2026, 6, 3)])
        self.assertEqual(self.dates("03/06/2026", "MDY"), [dt.date(2026, 3, 6)])

    def test_impossible_dates_are_dropped(self):
        self.assertEqual(self.dates("31/02/2026", "DMY"), [])
        self.assertEqual(self.dates("13/25/2026", "MDY"), [])

    def test_accents_and_case_do_not_matter(self):
        self.assertEqual(self.dates("3 DE JUNIO DE 2026"), [dt.date(2026, 6, 3)])
        self.assertEqual(T.fold("Fecha LÍMITE"), "fecha limite")
        self.assertEqual(len(T.fold("Ñandú ﬁ")), len("Ñandú ﬁ"))


class Misc(unittest.TestCase):
    def test_last_four(self):
        self.assertEqual(T.last_four("card ending in 1234"), "1234")
        self.assertEqual(T.last_four("tarjeta terminada en 0000"), "0000")
        self.assertEqual(T.last_four("Card ending in ****1234"), "1234")
        self.assertIsNone(T.last_four("no card here 1234"))
        self.assertIsNone(T.last_four("Statement period ending 2026-05-31"))
        self.assertIsNone(T.last_four("Your card statement period ending 2026-05-31"))
        self.assertIsNone(T.last_four("ending in 1234"))                     # no card or account named

    def test_money(self):
        self.assertEqual(T.money(Decimal("1234.56"), "USD"), "$1,234.56")
        self.assertEqual(T.money(Decimal("1234.5"), "EUR"), "1.234,50 €")
