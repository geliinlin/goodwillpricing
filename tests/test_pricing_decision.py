"""Final pricing: validation, SQL locking, atomic writes, and rollback behavior."""
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import Mock
from _goodwill_test_helpers import ScriptedConnection
from item_search import ACTIVE_GUIDELINE_SQL
import pricing_decision as mod


def draft():
    return {
        "staff_id": 5, "branch_id": 3, "item_type_id": 1, "category_id": 2,
        "brand_id": 3, "condition_id": 4, "guideline_id": 11,
        "item_description": "Blue jeans", "item_received_date": datetime.now(mod.TZ).date().isoformat(),
        "suggested_price": "12.00",
    }


def sample_connection(*, staff_id=5, branch_id=3, suggested="12.00", guideline_id=11,
                      lock=1, fail_sql=None):
    staff = {"Staff_Id": staff_id, "Branch_Id": branch_id,
             "Branch_Name": "Mount Pleasant", "Branch_City": "Mount Pleasant"}
    guideline = {"Guideline_Id": guideline_id, "Guideline_Suggested_Price": suggested,
                 "Category_Name": "Clothing", "Brand_Name": "Designer",
                 "Condition_Name": "Good", "Item_Type_Name": "Used"}
    return ScriptedConnection([
        [{"got_lock": lock}], [staff], [guideline],
        [{"next_id": 7}], [{"next_id": 12}], [], [], [{"released": 1}]
    ], fail_sql=fail_sql)


class PricingDecisionTests(unittest.TestCase):
    def test_money_parses_dollars_commas_and_two_places(self):
        for raw, value in (("$1,234.50", "1234.50"), ("12", "12.00"), ("  4.9 ", "4.90")):
            with self.subTest(raw=raw):
                self.assertEqual(mod.money(raw), Decimal(value))

    def test_reject_invalid_nonfinite_or_out_of_range_amounts(self):
        for raw in ("", "abc", "NaN", "Infinity", "0", "-2", "1000000", "12.345", "12,50", "$1,,000"):
            with self.subTest(raw=raw), self.assertRaises(mod.PricingValidationError):
                mod.money(raw)

    def test_override_reason_only_required_when_price_differs(self):
        self.assertIsNone(mod.validate_override(Decimal("12"), Decimal("12"), "unused"))
        with self.assertRaises(mod.PricingValidationError):
            mod.validate_override(Decimal("10"), Decimal("12"), None)
        self.assertEqual(mod.validate_override(Decimal("10"), Decimal("12"), " Visible Wear "), "Visible Wear")
        with self.assertRaises(mod.PricingValidationError):
            mod.validate_override(Decimal("10"), Decimal("12"), "r" * 256)

    def test_invalid_or_stale_draft_aborts_before_connection(self):
        attempts = []
        for uid, data, price in ((0, draft(), "12.00"), (5, {}, "12.00"), (5, draft(), "-1"),
                                 (5, dict(draft(), category_id=2.9), "12"),
                                 (5, dict(draft(), item_type_id=True), "12")):
            with self.subTest(uid=uid, data=data), self.assertRaises(mod.PricingValidationError):
                mod.save_pricing_decision(uid, data, price, None, lambda: attempts.append(1))
        self.assertEqual(attempts, [])

    def test_success_writes_item_and_decision_once_then_commits(self):
        con = sample_connection()
        receipt = mod.save_pricing_decision(5, draft(), "$12.00", None, lambda: con)
        self.assertEqual(receipt.item_id, 7)
        self.assertEqual(receipt.decision_id, 12)
        self.assertTrue(receipt.tag_number.startswith("MP"))
        self.assertEqual(receipt.final_price, "12.00")
        self.assertEqual(con.commits, 1)
        self.assertEqual(con.rollbacks, 0)
        self.assertEqual(con.started, 1)
        self.assertEqual(len([sql for sql, _ in con.statements if "INSERT INTO ITEM" in sql]), 1)
        self.assertEqual(len([sql for sql, _ in con.statements if "INSERT INTO PRICING_DECISION" in sql]), 1)
        self.assertIn((ACTIVE_GUIDELINE_SQL + " FOR UPDATE", (1, 2, 3, 4)), con.statements)
        self.assertIn("b.Branch_Is_Active = 1", con.statements[1][0])
        self.assertTrue(con.cursors[0].options["buffered"])
        item_args = next(params for sql, params in con.statements if "INSERT INTO ITEM" in sql)
        decision_args = next(params for sql, params in con.statements if "INSERT INTO PRICING_DECISION" in sql)
        self.assertEqual(item_args, (7, "Blue jeans", datetime.now(mod.TZ).date(), 2, 3, 4, 1))
        self.assertEqual(decision_args[2:], (None, Decimal("12.00"), receipt.tag_number, 11, 5, item_args[0], 3))
        self.assertTrue(any("RELEASE_LOCK" in sql for sql, _ in con.statements))
        self.assertTrue(con.closed)
        self.assertTrue(all(c.closed for c in con.cursors))

    def test_valid_override_is_stored_with_reason(self):
        con = sample_connection()
        result = mod.save_pricing_decision(5, draft(), "10.00", "Visible Wear", lambda: con)
        self.assertEqual(result.override_reason, "Visible Wear")
        insert = [params for sql, params in con.statements if "INSERT INTO PRICING_DECISION" in sql][0]
        self.assertIn("Visible Wear", insert)
        self.assertIn(Decimal("10.00"), insert)

    def test_override_without_reason_does_not_connect(self):
        attempts = []
        with self.assertRaises(mod.PricingValidationError):
            mod.save_pricing_decision(5, draft(), "10.00", None, lambda: attempts.append(1))
        self.assertEqual(attempts, [])

    def test_staff_identity_mismatch_rolls_back_without_inserts(self):
        con = sample_connection(staff_id=99)
        with self.assertRaises(mod.PricingValidationError):
            mod.save_pricing_decision(5, draft(), "12", None, lambda: con)
        self.assertEqual(con.commits, 0)
        self.assertEqual(con.rollbacks, 1)
        self.assertFalse(any("INSERT INTO" in sql for sql, _ in con.statements))
        self.assertTrue(con.closed)

    def test_staff_branch_change_prevents_write(self):
        con = sample_connection(branch_id=7)
        with self.assertRaisesRegex(mod.PricingValidationError, "assigned store"):
            mod.save_pricing_decision(5, draft(), "12", None, lambda: con)
        self.assertEqual(con.commits, 0)

    def test_stale_guideline_price_prevents_write(self):
        con = sample_connection(suggested="15.00")
        with self.assertRaisesRegex(mod.PricingValidationError, "suggested price has changed"):
            mod.save_pricing_decision(5, draft(), "12", None, lambda: con)
        self.assertEqual(con.commits, 0)
        self.assertFalse(any("INSERT INTO" in sql for sql, _ in con.statements))

    def test_guideline_id_mismatch_prevents_write(self):
        con = sample_connection(guideline_id=500)
        with self.assertRaises(mod.PricingValidationError):
            mod.save_pricing_decision(5, draft(), "12", None, lambda: con)
        self.assertEqual(con.rollbacks, 1)

    def test_lock_not_acquired_prevents_writes(self):
        con = sample_connection(lock=0)
        with self.assertRaisesRegex(mod.PricingValidationError, "Another item"):
            mod.save_pricing_decision(5, draft(), "12", None, lambda: con)
        self.assertEqual(con.rollbacks, 1)
        self.assertEqual(con.commits, 0)
        self.assertEqual(len(con.statements), 1)
        self.assertTrue(con.closed)

    def test_database_write_error_rolls_back_and_releases_lock(self):
        con = sample_connection(fail_sql="INSERT INTO PRICING_DECISION")
        with self.assertRaises(mod.PricingSaveError) as result:
            mod.save_pricing_decision(5, draft(), "12", None, lambda: con)
        self.assertEqual(result.exception.stage, "inserting PRICING_DECISION")
        self.assertEqual(con.commits, 0)
        self.assertEqual(con.rollbacks, 1)
        self.assertTrue(any("RELEASE_LOCK" in sql for sql, _ in con.statements))
        self.assertTrue(con.closed)

    def test_database_connection_error_uses_public_safe_exception(self):
        def raise_error():
            raise RuntimeError("secret host password")
        with self.assertRaises(mod.PricingSaveError) as result:
            mod.save_pricing_decision(5, draft(), "12", None, raise_error)
        self.assertNotIn("secret host password", str(result.exception))
        self.assertEqual(result.exception.stage, "database connection")

    def test_commit_failure_exposes_candidate_tag_for_history_check(self):
        con = sample_connection()
        con.commit = Mock(side_effect=ConnectionError("lost acknowledgement"))
        with self.assertRaises(mod.PricingSaveError) as result:
            mod.save_pricing_decision(5, draft(), "12", None, lambda: con)
        decision = next(params for sql, params in con.statements if "INSERT INTO PRICING_DECISION" in sql)
        self.assertEqual(result.exception.uncertain_tag, decision[4])
        self.assertTrue(con.closed)

    def test_disconnect_after_successful_commit_preserves_receipt(self):
        con = sample_connection()
        con.close = Mock(side_effect=ConnectionError("disconnect"))
        result = mod.save_pricing_decision(5, draft(), "12", None, lambda: con)
        self.assertEqual(result.decision_id, 12)
        self.assertEqual(con.commits, 1)


if __name__ == "__main__":
    unittest.main()
