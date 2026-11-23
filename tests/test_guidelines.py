"""Guideline versioning, authorization and transaction safety in current guidelines.py.

SQL and MySQL advisory locks are simulated; the database is never contacted.
"""
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import mysql.connector
import guidelines as mod

ADMIN = {"user_id": 7, "user_type": "Administrator"}
STAFF = {"user_id": 2, "user_type": "Store Staff"}
ATTRS = {"Category_Id": 1, "Brand_Id": 2, "Condition_Id": 3, "Item_Type_Id": 4}


def guideline(**changes):
    row = {"Guideline_Id": 8, "Guideline_Suggested_Price": Decimal("12.99"),
           "Guideline_Last_Updated": datetime(2026, 11, 17, 12),
           "Guideline_Is_Active": 1, "Admin_Id": 7, **ATTRS}
    row.update(changes)
    return row


class GuidelineServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value
        self.db = patch.object(mod, "get_connection", return_value=self.connection)
        self.db.start()
        self.addCleanup(self.db.stop)

    def writable(self, *responses, authorized=True, acquired=1):
        # DB name -> GET_LOCK -> recheck admin -> application queries -> RELEASE_LOCK
        self.cursor.fetchone.side_effect = [
            {"db_name": "test_goodwill"}, {"acquired": acquired},
            *([{"User_Id": 7} if authorized else None] if acquired == 1 else []),
            *responses, {"released": 1},
        ]

    def readonly(self, *responses, authorized=True):
        self.cursor.fetchone.side_effect = [({"User_Id": 7} if authorized else None), *responses]

    def queries(self, word):
        return [call.args for call in self.cursor.execute.call_args_list
                if call.args[0].strip().upper().startswith(word.upper())]

    @staticmethod
    def attribute_rows():
        return [{field: value} for field, value in ATTRS.items()]

    def test_staff_cannot_read_or_modify_admin_guidelines(self):
        for operation in (lambda: mod.load_catalog(STAFF),
                          lambda: mod.save_guideline(STAFF, ATTRS, "4.99"),
                          lambda: mod.set_guideline_active(STAFF, 8, False, "x")):
            with self.subTest(operation=operation), self.assertRaises(PermissionError):
                operation()
        self.connection.cursor.assert_not_called()

    def test_load_catalog_reads_lookup_tables_and_active_history(self):
        self.readonly()
        self.cursor.fetchall.side_effect = [
            [{"Category_Id": 1, "Category_Name": "Clothing"}],
            [{"Brand_Id": 2, "Brand_Name": "Levi's"}],
            [{"Condition_Id": 3, "Condition_Name": "Good"}],
            [{"Item_Type_Id": 4, "Item_Type_Name": "Jeans"}],
            [guideline()],
        ]
        lookups, rows = mod.load_catalog(ADMIN)
        self.assertEqual(lookups["Category_Id"], {1: "Clothing"})
        self.assertEqual(lookups["Item_Type_Id"], {4: "Jeans"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(self.queries("SELECT")), 6)  # auth + 4 lookups + catalog
        self.assertFalse(self.queries("INSERT"))
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once()

    def test_revoked_admin_must_not_write(self):
        self.writable(authorized=False)
        with self.assertRaises(PermissionError):
            mod.save_guideline(ADMIN, ATTRS, "4.99")
        self.assertFalse(self.queries("INSERT"))
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once()

    def test_create_unique_active_guideline_allocates_id_and_commits(self):
        self.writable(*self.attribute_rows(), None, None, {"next_id": 9})
        self.assertEqual(mod.save_guideline(ADMIN, ATTRS, "4.99"), 9)
        params = self.queries("INSERT")[0][1]
        self.assertEqual(params, (9, Decimal("4.99"), 1, 1, 2, 3, 4, 7))
        self.connection.commit.assert_called_once()
        self.assertTrue(any("GET_LOCK" in s[0] for s in self.queries("SELECT")))
        self.assertTrue(any("RELEASE_LOCK" in s[0] for s in self.queries("SELECT")))
        self.connection.close.assert_called_once()

    def test_active_duplicate_rejected_before_insert(self):
        self.writable(*self.attribute_rows(), None, {"Guideline_Id": 8})
        with self.assertRaisesRegex(mod.GuidelineError, "already uses"):
            mod.save_guideline(ADMIN, ATTRS, "8.99")
        self.assertFalse(self.queries("INSERT"))
        self.connection.commit.assert_not_called()

    def test_inactive_guideline_can_share_attributes(self):
        self.writable(*self.attribute_rows(), None, {"next_id": 9})
        self.assertEqual(mod.save_guideline(ADMIN, ATTRS, "8.99", False), 9)
        self.assertEqual(self.queries("INSERT")[0][1][2], 0)

    def test_exact_duplicate_active_rejected_with_guideline_id_and_price(self):
        self.writable(*self.attribute_rows(),
                      {"Guideline_Id": 337, "Guideline_Is_Active": 1})
        with self.assertRaises(mod.GuidelineError) as ctx:
            mod.save_guideline(ADMIN, ATTRS, "12.99", True)
        message = str(ctx.exception)
        self.assertIn("Guideline #337 already exists (Active)", message)
        self.assertIn("$12.99", message)
        self.assertIn("No new guideline was created", message)
        self.assertFalse(self.queries("INSERT"))
        self.assertFalse(any("MAX(Guideline_Id)" in sql for sql, *_ in self.queries("SELECT")))
        self.connection.commit.assert_not_called()
        self.assertTrue(self.connection.rollback.called)

    def test_exact_duplicate_inactive_is_detected_even_when_creating_inactive(self):
        self.writable(*self.attribute_rows(),
                      {"Guideline_Id": 330, "Guideline_Is_Active": 0})
        with self.assertRaisesRegex(mod.GuidelineError,
                                    r"Guideline #330 already exists \(Inactive\)"):
            mod.save_guideline(ADMIN, ATTRS, "4.99", False)
        self.assertFalse(self.queries("INSERT"))

    def test_exact_duplicate_inactive_is_detected_when_creating_active(self):
        self.writable(*self.attribute_rows(),
                      {"Guideline_Id": 330, "Guideline_Is_Active": 0})
        with self.assertRaisesRegex(mod.GuidelineError, r"Guideline #330"):
            mod.save_guideline(ADMIN, ATTRS, "4.99", True)
        self.assertFalse(self.queries("INSERT"))

    def test_full_duplicate_query_uses_both_attributes_and_decimal_price(self):
        self.writable(*self.attribute_rows(), None, None, {"next_id": 9})
        self.assertEqual(mod.save_guideline(ADMIN, ATTRS, "12.90", True), 9)
        matches = [call.args for call in self.cursor.execute.call_args_list
                   if "Guideline_Suggested_Price = %s" in call.args[0]]
        self.assertEqual(len(matches), 1)
        sql, params = matches[0]
        self.assertIn("Guideline_Id, Guideline_Is_Active", sql)
        self.assertNotIn("Guideline_Is_Active = 1", sql)
        self.assertIn("FOR UPDATE", sql)
        self.assertEqual(params, (1, 2, 3, 4, Decimal("12.90")))

    def test_different_price_inactive_can_be_created_with_same_attributes(self):
        self.writable(*self.attribute_rows(), None, {"next_id": 9})
        self.assertEqual(mod.save_guideline(ADMIN, ATTRS, "8.99", False), 9)
        self.assertTrue(self.queries("INSERT"))

    def test_versioned_edit_does_not_flag_its_existing_row_as_duplicate(self):
        existing = guideline()
        self.writable(*self.attribute_rows(), existing, None)
        self.assertEqual(mod.save_guideline(ADMIN, ATTRS, "12.99", True,
                       guideline_id=8, expected_token=mod.snapshot_token(existing)), 8)
        self.assertFalse(self.queries("INSERT"))
        self.assertFalse(self.queries("UPDATE"))

    def test_unknown_lookup_is_rejected(self):
        self.writable(None)
        with self.assertRaisesRegex(mod.GuidelineError, "no longer available"):
            mod.save_guideline(ADMIN, ATTRS, "8.99")
        self.assertFalse(self.queries("INSERT"))

    def test_edit_inserts_new_version_and_preserves_previous_price(self):
        old = guideline()
        self.writable(*self.attribute_rows(), old, None, {"next_id": 9})
        actual = mod.save_guideline(ADMIN, ATTRS, "15.99", guideline_id=8,
                                    expected_token=mod.snapshot_token(old))
        self.assertEqual(actual, 9)
        update, params = self.queries("UPDATE")[0]
        self.assertNotIn("Guideline_Suggested_Price", update)
        self.assertNotIn("Category_Id", update)
        self.assertEqual(params, (8,))
        self.assertEqual(self.queries("INSERT")[0][1][1], Decimal("15.99"))
        self.assertFalse(self.queries("DELETE"))

    def test_write_failure_rolls_back_old_version_deactivation(self):
        old = guideline()
        self.writable(*self.attribute_rows(), old, None, {"next_id": 9})
        def explode(sql, params=None):
            if sql.strip().startswith("INSERT"):
                raise mysql.connector.IntegrityError("simulated failure")
        self.cursor.execute.side_effect = explode
        with self.assertRaises(mysql.connector.IntegrityError):
            mod.save_guideline(ADMIN, ATTRS, "15.99", guideline_id=8,
                               expected_token=mod.snapshot_token(old))
        self.assertTrue(self.queries("UPDATE"))
        self.assertFalse(self.queries("DELETE"))
        self.connection.commit.assert_not_called()
        self.assertEqual(self.connection.rollback.call_count, 2)  # lock setup and error
        self.connection.close.assert_called_once()

    def test_stale_guideline_rejected_without_data_change(self):
        old = guideline()
        current = guideline(Guideline_Suggested_Price=Decimal("16.99"))
        self.writable(*self.attribute_rows(), current, None)
        with self.assertRaisesRegex(mod.GuidelineError, "changed after"):
            mod.save_guideline(ADMIN, ATTRS, "15.99", guideline_id=8,
                               expected_token=mod.snapshot_token(old))
        self.assertFalse(self.queries("UPDATE"))
        self.assertFalse(self.queries("INSERT"))

    def test_status_toggle_deactivates_without_rewriting_price(self):
        old = guideline()
        self.writable(old)
        mod.set_guideline_active(ADMIN, 8, False, mod.snapshot_token(old))
        update, params = self.queries("UPDATE")[0]
        self.assertEqual(params, (0, 8))
        self.assertNotIn("Guideline_Suggested_Price", update)
        self.assertFalse(self.queries("DELETE"))
        self.connection.commit.assert_called_once()

    def test_reactivate_checks_for_another_active_guideline(self):
        old = guideline(Guideline_Is_Active=0)
        self.writable(old, *self.attribute_rows(), {"Guideline_Id": 9})
        with self.assertRaisesRegex(mod.GuidelineError, "already uses"):
            mod.set_guideline_active(ADMIN, 8, True, mod.snapshot_token(old))
        self.assertFalse(self.queries("UPDATE"))

    def test_lock_timeout_does_not_write(self):
        self.writable(acquired=0)
        with self.assertRaisesRegex(mod.GuidelineError, "Another administrator"):
            mod.save_guideline(ADMIN, ATTRS, "4.99")
        self.assertFalse(self.queries("INSERT"))
        self.connection.commit.assert_not_called()
        self.connection.close.assert_called_once()

    def test_expected_version_token_includes_price_and_status(self):
        original = guideline()
        for change in (dict(Guideline_Suggested_Price=Decimal("19.99")),
                       dict(Guideline_Is_Active=0), dict(Brand_Id=9)):
            self.assertNotEqual(mod.snapshot_token(original), mod.snapshot_token(guideline(**change)))


class PriceValidationTests(unittest.TestCase):
    def test_valid_prices(self):
        for value in ("0.01", "12.99", "999999.99", " 8.5 "):
            with self.subTest(value=value):
                self.assertEqual(mod.parse_price(value), Decimal(value.strip()))

    def test_invalid_or_high_precision_prices(self):
        for value in ("", "NaN", "Infinity", "-1", "0", "1000000", "1.999", "text", True, None):
            with self.subTest(value=value), self.assertRaises(mod.GuidelineError):
                mod.parse_price(value)

    def test_active_must_be_actual_bool(self):
        with self.assertRaises(mod.GuidelineError):
            mod.save_guideline(ADMIN, ATTRS, "4.99", active=1)


if __name__ == "__main__":
    unittest.main()
