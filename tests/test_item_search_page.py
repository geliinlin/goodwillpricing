"""Staff pricing page interactions, guideline states and receipt lifecycle."""
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch
from _goodwill_test_helpers import ensure_streamlit_importable, FakeStreamlit

ensure_streamlit_importable()
import item_search_page as page

STAFF = {"User_Id": 5, "Staff_Id": 5, "Branch_Id": 3, "Branch_Name": "Mount Pleasant"}
LOOKUPS = {
    "Item_Type_Id": {1: "Used"},
    "Category_Id": {2: "Clothing"},
    "Category_Item_Type": {2: 1},
    "Brand_Id": {3: "Designer"},
    "Condition_Id": {4: "Good"},
}


def matched():
    return SimpleNamespace(status="matched", guideline_id=11,
                           suggested_price=Decimal("12.00"), conflicting_ids=())


class ItemSearchPageTests(unittest.TestCase):
    def test_barcode_preview_failure_preserves_receipt_and_available_pdf(self):
        receipt = dict(tag_number="GW26-100012", branch_id=3, category="Books",
                       brand="Basic", condition="Good", final_price="5.00")
        self.ui.session_state[page.RECEIPT_KEY] = receipt
        with patch.object(page, "barcode_svg", side_effect=ValueError("internal detail")), \
             patch.object(page, "printable_label_pdf", return_value=b"test PDF"), \
             patch.object(page, "save_pricing_decision") as save, \
             self.assertLogs(page.LOG, level="ERROR"):
            page._render_receipt(receipt, STAFF)
        self.assertEqual(self.ui.errors, ["Unable to render barcode. Saved decision is unaffected."])
        self.assertEqual(self.ui.downloads[0][1]["data"], b"test PDF")
        self.assertEqual(self.ui.session_state[page.RECEIPT_KEY], receipt)
        save.assert_not_called()

    def setUp(self):
        self.ui = FakeStreamlit(session={"user_id": 5})
        self.p = patch.object(page, "st", self.ui)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.styles_patch = patch.object(page, "load_styles")
        self.styles = self.styles_patch.start()
        self.addCleanup(self.styles_patch.stop)

    def test_pricing_page_requires_logged_in_id(self):
        self.ui.session_state.clear()
        with patch.object(page, "get_authenticated_store_staff") as check:
            page.render_item_search_page(show_header=False)
        check.assert_not_called()
        self.assertTrue(self.ui.warnings)

    def test_staff_recheck_rejects_inactive_employee(self):
        with patch.object(page, "get_authenticated_store_staff", return_value=None):
            page.render_item_search_page(show_header=False)
        self.assertTrue(self.ui.errors)
        self.assertFalse(any(x == "module3_topbar" for x in self.ui.containers))

    def test_router_owned_header_prevents_duplicate_topbar(self):
        with patch.object(page, "get_authenticated_store_staff", return_value=STAFF), \
             patch.object(page, "get_lookup_options", return_value=LOOKUPS), \
             patch.object(page, "_item_form") as form:
            page.render_item_search_page(show_header=False)
        form.assert_called_once()
        self.assertNotIn("module3_topbar", self.ui.containers)
        self.styles.assert_called_once_with("pricing.css")

    def test_description_optional_search_success(self):
        with patch.object(page, "get_active_price_guideline", return_value=matched()) as query:
            page._search_guidelines(STAFF, (1, 2, 3, 4), "")
        query.assert_called_once_with(1, 2, 3, 4)
        draft = self.ui.session_state[page.DRAFT_KEY]
        self.assertEqual(draft["staff_id"], 5)
        self.assertEqual(draft["item_description"], "")
        self.assertEqual(self.ui.session_state["module3_final_price"], "$12.00")
        self.assertEqual(self.ui.session_state[page.RESULT_KEY]["guideline_id"], 11)

    def test_incomplete_four_attributes_are_blocked_before_query(self):
        with patch.object(page, "get_active_price_guideline") as query:
            page._search_guidelines(STAFF, (1, None, 3, 4), "jacket")
        query.assert_not_called()
        self.assertNotIn(page.DRAFT_KEY, self.ui.session_state)
        self.assertEqual(self.ui.session_state[page.SEARCH_ERROR_KEY][0], "warning")

    def test_missing_guideline_shows_warning_without_price(self):
        record = SimpleNamespace(status="not_found")
        with patch.object(page, "get_active_price_guideline", return_value=record):
            page._search_guidelines(STAFF, (1, 2, 3, 4), "jeans")
        self.assertNotIn(page.DRAFT_KEY, self.ui.session_state)
        self.assertIn("No active guideline", self.ui.session_state[page.SEARCH_ERROR_KEY][1])

    def test_conflicting_guidelines_fail_closed(self):
        record = SimpleNamespace(status="conflict", conflicting_ids=(20, 21))
        with patch.object(page, "get_active_price_guideline", return_value=record):
            page._search_guidelines(STAFF, (1, 2, 3, 4), "jeans")
        self.assertEqual(self.ui.session_state[page.SEARCH_ERROR_KEY][0], "error")
        self.assertNotIn(page.DRAFT_KEY, self.ui.session_state)

    def test_query_exception_is_reported_without_db_details(self):
        for failure in (RuntimeError, ValueError):
            with self.subTest(failure=failure), patch.object(page, "get_active_price_guideline",
                                                            side_effect=failure("credentials")):
                page._search_guidelines(STAFF, (1, 2, 3, 4), "jeans")
                error = self.ui.session_state[page.SEARCH_ERROR_KEY][1]
                self.assertNotIn("credentials", error)
                self.assertIn("Could not search MySQL", error)

    def test_categories_require_parent_type_and_reset_after_type_changes(self):
        self.ui.session_state["module3_Category_Id"] = 3
        options = dict(LOOKUPS, Category_Id={2: "Shirts", 3: "Books"}, Category_Item_Type={2: 1, 3: 9})
        with patch.object(self.ui, "selectbox", wraps=self.ui.selectbox) as select:
            self.assertIsNone(page._select("Category_Id", "Category", options))
            self.assertEqual(select.call_args.kwargs["options"], [None])
            self.assertTrue(select.call_args.kwargs["disabled"])
            self.ui.session_state["module3_Category_Id"] = 3
            self.assertIsNone(page._select("Category_Id", "Category", options, 1))
            self.assertEqual(select.call_args.kwargs["options"], [None, 2])

    def test_workflow_progress_is_present_on_both_screens(self):
        page._steps(complete=False)
        page._steps(complete=True)
        self.assertEqual(len(self.ui.markdowns), 2)
        for text in self.ui.markdowns:
            for word in ("Item details", "Generate tag", "Print"):
                self.assertIn(word, text)
        self.assertIn("gw-step-current", self.ui.markdowns[-1])

    def test_saved_receipt_is_shown_without_refetching_lookup_lists(self):
        self.ui.session_state[page.RECEIPT_KEY] = {"staff_id": 5, "tag_number": "MP26-1001"}
        with patch.object(page, "get_authenticated_store_staff", return_value=STAFF), \
             patch.object(page, "get_lookup_options") as lookups, \
             patch.object(page, "_render_receipt") as render:
            page.render_item_search_page(show_header=False)
        lookups.assert_not_called()
        render.assert_called_once()

    def test_receipt_from_different_staff_is_discarded(self):
        self.ui.session_state[page.RECEIPT_KEY] = {"staff_id": 9, "tag_number": "OLD"}
        with patch.object(page, "get_authenticated_store_staff", return_value=STAFF), \
             patch.object(page, "get_lookup_options", return_value=LOOKUPS), \
             patch.object(page, "_item_form") as form:
            page.render_item_search_page(show_header=False)
        self.assertNotIn(page.RECEIPT_KEY, self.ui.session_state)
        form.assert_called_once()

    def prepare_submission(self, description):
        self.ui.selects.update(module3_Item_Type_Id=1, module3_Category_Id=2,
                               module3_Brand_Id=3, module3_Condition_Id=4)
        self.ui.inputs["module3_description"] = description
        with patch.object(page, "get_active_price_guideline", return_value=matched()):
            page._search_guidelines(STAFF, (1, 2, 3, 4), description)
        self.ui.buttons["module3_submit"] = True

    def test_submit_generates_receipt_and_removes_stale_draft(self):
        self.prepare_submission("blue jeans")
        receipt = SimpleNamespace(as_dict=lambda: {"staff_id": 5, "tag_number": "MP26-100012"})
        with patch.object(page, "save_pricing_decision", return_value=receipt) as save:
            page._item_form(STAFF, LOOKUPS, 5)
            with patch.object(page, "get_authenticated_store_staff", return_value=STAFF), \
                 patch.object(page, "_render_receipt") as render:
                page.render_item_search_page(show_header=False)
            render.assert_called_once()
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args[0], 5)
        self.assertEqual(save.call_args.args[2], "$12.00")
        self.assertEqual(self.ui.session_state[page.RECEIPT_KEY]["tag_number"], "MP26-100012")
        self.assertNotIn(page.DRAFT_KEY, self.ui.session_state)
        self.assertNotIn(page.RESULT_KEY, self.ui.session_state)
        self.assertGreater(self.ui.reruns, 0)

    def test_price_override_without_reason_blocks_submit(self):
        self.prepare_submission("jacket")
        self.ui.session_state["module3_final_price"] = "$10.00"
        self.ui.buttons["module3_submit"] = True
        with patch.object(page, "save_pricing_decision") as save:
            page._item_form(STAFF, LOOKUPS, 5)
        save.assert_not_called()
        self.assertTrue(self.ui.warnings)

    def test_repeated_submit_before_first_save_returns_writes_once(self):
        self.prepare_submission("coat")
        receipt = SimpleNamespace(as_dict=lambda: {"staff_id": 5, "tag_number": "MP26-100012"})

        def save_while_another_click_arrives(*args):
            self.assertNotIn(page.DRAFT_KEY, self.ui.session_state)
            page._item_form(STAFF, LOOKUPS, 5)
            return receipt

        with patch.object(page, "save_pricing_decision", side_effect=save_while_another_click_arrives) as save:
            page._item_form(STAFF, LOOKUPS, 5)
            save.assert_called_once()
        self.assertEqual(self.ui.session_state[page.RECEIPT_KEY]["tag_number"], "MP26-100012")

    def test_known_rollback_leaves_draft_available_for_retry(self):
        self.prepare_submission("coat")
        failure = page.PricingSaveError("inserting ITEM", RuntimeError())
        with patch.object(page, "save_pricing_decision", side_effect=failure):
            page._item_form(STAFF, LOOKUPS, 5)
        self.assertIn(page.DRAFT_KEY, self.ui.session_state)
        self.assertTrue(self.ui.errors)

    def test_stale_draft_after_navigation_or_branch_change_cannot_be_submitted(self):
        for change in ("branch", "attributes"):
            self.prepare_submission("coat")
            employee = dict(STAFF)
            if change == "branch":
                employee["Branch_Id"] = 4
            else:
                self.ui.selects.clear()
            with self.subTest(change=change), patch.object(page, "save_pricing_decision") as save:
                page._item_form(employee, LOOKUPS, 5)
                save.assert_not_called()
                self.assertNotIn(page.DRAFT_KEY, self.ui.session_state)

    def test_lost_commit_confirmation_clears_draft_and_identifies_tag_in_history(self):
        self.prepare_submission("coat")
        failure = page.PricingSaveError("committing transaction", RuntimeError(), uncertain_tag="MP26-100012")
        with patch.object(page, "save_pricing_decision", side_effect=failure) as save:
            page._item_form(STAFF, LOOKUPS, 5)
            page._item_form(STAFF, LOOKUPS, 5)
            save.assert_called_once()
        self.assertNotIn(page.DRAFT_KEY, self.ui.session_state)
        self.assertIn("Pricing History", self.ui.warnings[-1])
        self.assertIn("MP26-100012", self.ui.warnings[-1])

    def test_search_button_uses_selected_attributes(self):
        self.ui.buttons["module3_search"] = True
        self.ui.selects.update(module3_Item_Type_Id=1, module3_Category_Id=2,
                               module3_Brand_Id=3, module3_Condition_Id=4)
        with patch.object(page, "_search_guidelines") as query:
            page._item_form(STAFF, LOOKUPS, 5)
        self.assertEqual(query.call_args.args[1], (1, 2, 3, 4))
        self.assertEqual(self.ui.reruns, 1)


if __name__ == "__main__":
    unittest.main()
