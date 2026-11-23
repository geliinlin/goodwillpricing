"""Offline coverage for the new ITEM_TYPE admin action; never opens real MySQL."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

import mysql.connector
import guidelines as service
from _goodwill_test_helpers import FakeStreamlit, ensure_streamlit_importable

ensure_streamlit_importable()
import guideline_page as page

ADMIN = {"user_id": 7, "user_type": "Administrator", "full_name": "Morgan Lee"}
STAFF = {"user_id": 8, "user_type": "Store Staff", "branch_id": 1}
LOOKUPS = {
    "Category_Id": {1: "Accessories"},
    "Brand_Id": {2: "Generic"},
    "Condition_Id": {3: "Good"},
    "Item_Type_Id": {4: "Clothing"},
}


class ItemTypeServiceTests(unittest.TestCase):
    """The retired Guidelines entrypoint cannot bypass Configuration permissions."""

    def test_regular_admin_is_denied_without_configuration_access(self):
        with self.assertRaises(PermissionError):
            service.create_item_type(ADMIN, 'Furniture')

    def test_staff_is_denied(self):
        with self.assertRaises(PermissionError):
            service.create_item_type(STAFF, 'Furniture')

    def test_authorized_admin_delegates_to_configuration_service(self):
        authorized = {**ADMIN, 'can_manage_users_config': True}
        with patch('configuration_service.create_master_data', return_value=17) as create:
            result = service.create_item_type(authorized, 'Furniture')
        self.assertEqual(result, 17)
        create.assert_called_once_with(authorized, 'item_types', {'Item_Type_Name': 'Furniture'})

    def test_duplicate_errors_have_user_facing_message(self):
        from configuration_service import ConfigurationError
        authorized = {**ADMIN, 'can_manage_users_config': True}
        with patch('configuration_service.create_master_data', side_effect=ConfigurationError('already exists')):
            with self.assertRaisesRegex(service.GuidelineError, 'already exists'):
                service.create_item_type(authorized, 'Furniture')


class ItemTypePageTests(unittest.TestCase):
    """Master-data service remains available; old Guidelines UI is retired."""

    def setUp(self):
        self.ui = FakeStreamlit()
        self.st_patch = patch.object(page, "st", self.ui)
        self.st_patch.start()
        self.addCleanup(self.st_patch.stop)
        self.styles_patch = patch.object(page, "load_styles")
        self.styles_patch.start()
        self.addCleanup(self.styles_patch.stop)

    def test_guidelines_does_not_expose_add_item_type_button(self):
        with patch.object(page, "load_catalog", return_value=(deepcopy(LOOKUPS), [])):
            page.show_guideline_management(ADMIN, show_header=False)
        self.assertNotIn("gw_add_item_type", [opts.get('key') for _, opts in self.ui.button_calls])
        self.assertNotIn('gw_item_type_editor_open', self.ui.session_state)

    def test_master_data_ui_moved_out_of_guidelines(self):
        self.assertFalse(hasattr(page, '_item_type_editor'))
        self.assertFalse(hasattr(page, 'create_item_type'))
        self.assertTrue(callable(service.create_item_type))

    def test_guideline_editor_does_not_consume_stale_new_type_id(self):
        self.ui.session_state.update({"gw_guideline_editor": "new", "gw_selected_item_type_id": 15})
        self.ui.inputs["gw_form_new_0_price"] = "24.99"
        self.ui.buttons["Save Guideline"] = True
        lookups = deepcopy(LOOKUPS)
        lookups["Item_Type_Id"][15] = "Furniture"
        with patch.object(page, 'save_guideline', return_value=26) as save:
            page._editor(ADMIN, lookups, [])
        self.assertEqual(save.call_args.args[1]['Item_Type_Id'], 4)
        self.assertEqual(save.call_args.args[2], '24.99')


if __name__ == "__main__":
    unittest.main()
