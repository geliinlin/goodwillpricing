"""Read-only item dropdowns, guideline matching and staff authorization."""
import unittest
from datetime import date, timedelta
from decimal import Decimal
from _goodwill_test_helpers import ScriptedConnection
import item_search as mod


class ItemSearchTests(unittest.TestCase):
    def test_four_lookup_tables_return_id_to_name_maps(self):
        con = ScriptedConnection([
            [{"id": 1, "name": "Used"}],
            [{"id": 4, "name": "Clothing"}],
            [{"id": 9, "name": "Designer"}],
            [{"id": 2, "name": "Good"}],
        ])
        options = mod.get_lookup_options(lambda: con)
        self.assertEqual(options["Item_Type_Id"], {1: "Used"})
        self.assertEqual(options["Category_Id"], {4: "Clothing"})
        self.assertEqual(options["Condition_Id"], {2: "Good"})
        self.assertEqual(len(con.statements), 4)
        self.assertTrue(con.closed)
        self.assertTrue(all(c.closed for c in con.cursors))

    def test_lookup_query_uses_fixed_table_identifiers(self):
        con = ScriptedConnection([[], [], [], []])
        mod.get_lookup_options(lambda: con)
        queried = "\n".join(stmt[0] for stmt in con.statements)
        for table in ("`ITEM_TYPE`", "`CATEGORY`", "`BRAND`", "`ITEM_CONDITION`"):
            self.assertIn(table, queried)

    def test_exact_active_guideline_match(self):
        record = {"Guideline_Id": 30, "Guideline_Suggested_Price": "19.95", "Guideline_Last_Updated": "2026-11-22"}
        con = ScriptedConnection([[record]])
        result = mod.get_active_price_guideline(1, 4, 9, 2, lambda: con)
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.guideline_id, 30)
        self.assertEqual(result.suggested_price, Decimal("19.95"))
        self.assertIn("Guideline_Is_Active = 1", con.statements[0][0])
        for column in ("Item_Type_Is_Active", "Category_Is_Active", "Brand_Is_Active", "Condition_Is_Active"):
            self.assertIn(column + " = 1", con.statements[0][0])
        self.assertIn("c.Item_Type_Id = g.Item_Type_Id", con.statements[0][0])
        self.assertEqual(con.statements[0][1], (1, 4, 9, 2))

    def test_not_found_does_not_invent_price(self):
        result = mod.get_active_price_guideline(1, 4, 9, 2, lambda: ScriptedConnection([[]]))
        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.suggested_price)

    def test_multiple_active_matches_fail_closed(self):
        con = ScriptedConnection([[{"Guideline_Id": 3}, {"Guideline_Id": 4}]])
        result = mod.get_active_price_guideline(1, 4, 9, 2, lambda: con)
        self.assertEqual(result.status, "conflict")
        self.assertIsNone(result.guideline_id)
        self.assertEqual(result.conflicting_ids, (3, 4))

    def test_invalid_nonpositive_bool_or_float_ids_rejected_without_sql(self):
        for ids in ((0, 4, 9, 2), (True, 4, 9, 2), (1.5, 4, 9, 2), (-1, 4, 9, 2)):
            with self.subTest(ids=ids):
                with self.assertRaises(ValueError):
                    mod.get_active_price_guideline(*ids, connection_factory=lambda: self.fail("unexpected db"))

    def test_corrupt_database_price_rejected(self):
        for value in ("0.00", "-2.00", "NaN", None, "invalid", "9999999.00", "1.234"):
            con = ScriptedConnection([[{"Guideline_Id": 1, "Guideline_Suggested_Price": value, "Guideline_Last_Updated": "x"}]])
            with self.subTest(value=value), self.assertRaises(ValueError):
                mod.get_active_price_guideline(1, 2, 3, 4, lambda: con)

    def test_staff_must_have_active_account_staff_and_branch(self):
        con = ScriptedConnection([[{"User_Id": 5, "Staff_Id": 5, "Branch_Id": 2, "Branch_Name": "Mount Pleasant"}]])
        staff = mod.get_authenticated_store_staff(5, lambda: con)
        self.assertEqual(staff["Staff_Id"], 5)
        sql, params = con.statements[0]
        self.assertIn("User_Is_Active = 1", sql)
        self.assertIn("b.Branch_Is_Active = 1", sql)
        self.assertIn("'Employee', 'Store Staff'", sql)
        self.assertIn("INNER JOIN STORE_STAFF", sql)
        self.assertEqual(params, (5,))

    def test_missing_staff_returns_none(self):
        self.assertIsNone(mod.get_authenticated_store_staff(5, lambda: ScriptedConnection([[]])))

    def test_invalid_staff_user_id_short_circuits(self):
        for uid in (-2, 0, True, "2", None):
            with self.subTest(uid=uid):
                self.assertIsNone(mod.get_authenticated_store_staff(uid, lambda: self.fail("unexpected db")))

    def test_optional_description_accepts_blank_or_none(self):
        now = date.today()
        mod.validate_item_entry(None, now, now)
        mod.validate_item_entry("", now, now)
        mod.validate_item_entry(" " * 20, now, now)
        mod.validate_item_entry("x" * 255, now, now)

    def test_description_length_and_future_date_rejected(self):
        today = date.today()
        with self.assertRaises(ValueError):
            mod.validate_item_entry("X" * 256, today, today)
        with self.assertRaises(ValueError):
            mod.validate_item_entry("Okay", today + timedelta(days=1), today)
        with self.assertRaises(ValueError):
            mod.validate_item_entry(45, today, today)


if __name__ == "__main__":
    unittest.main()
