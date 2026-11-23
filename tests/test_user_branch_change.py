"""Branch reassignment: corporate admins have no Branch_Id; staff do."""
import unittest
from unittest.mock import patch

from _goodwill_test_helpers import FakeStreamlit, ScriptedConnection
import users_page as page
import users_service as svc

ADMIN = {'user_id': 1, 'user_type': 'Administrator',
         'can_manage_users_config': True}
STAFF = {'user_id': 2, 'user_type': 'Store Staff',
         'can_manage_users_config': True}
STAFF_ROW = {'User_Id': 10, 'User_Type': 'Store Staff',
             'Branch_Id': 3, 'Branch_Name': 'Mount Pleasant'}
BRANCHES = [{'Branch_Id': 3, 'Branch_Name': 'Mount Pleasant'},
            {'Branch_Id': 4, 'Branch_Name': 'Lansing'}]


def admin_reply():
    return {'User_Type': 'Administrator', 'User_Is_Active': 1,
            'Admin_Id': 1, 'Staff_Id': None, 'can_manage': 1}


def staff_target():
    return {'User_Id': 10, 'User_Type': 'Store Staff',
            'User_Is_Active': 1, 'can_manage': 0}


class BranchServiceTests(unittest.TestCase):
    def test_change_branch_commits_and_revokes_staff_sessions(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [admin_reply()], [staff_target()],
            [{'Branch_Id': 3}], [{'Branch_Id': 4}], [], [], [{'released': 1}],
        ])
        res = svc.change_staff_branch(ADMIN, 10, 4, expected_branch_id=3,
                                      connection_factory=lambda: con)
        self.assertEqual(res, {'branch_changed': True, 'branch_id': 4})
        self.assertEqual(con.commits, 1)
        self.assertEqual(con.rollbacks, 0)
        sql = '\n'.join(stmt for stmt, _ in con.statements)
        self.assertIn('UPDATE STORE_STAFF', sql)
        self.assertIn('Session_Version=Session_Version+1', sql)
        self.assertTrue(con.closed)

    def test_stale_branch_is_rejected_without_update(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [admin_reply()], [staff_target()],
            [{'Branch_Id': 6}], [{'released': 1}],
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'changed'):
            svc.change_staff_branch(ADMIN, 10, 4, expected_branch_id=3,
                                    connection_factory=lambda: con)
        self.assertEqual(con.rollbacks, 1)
        self.assertFalse(any(sql.startswith('UPDATE STORE_STAFF') for sql, _ in con.statements))

    def test_inactive_or_missing_branch_is_blocked(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [admin_reply()], [staff_target()],
            [{'Branch_Id': 3}], [], [{'released': 1}],
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'inactive or does not exist'):
            svc.change_staff_branch(ADMIN, 10, 4, expected_branch_id=3,
                                    connection_factory=lambda: con)
        self.assertEqual(con.commits, 0)

    def test_administrator_account_cannot_be_assigned(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [admin_reply()],
            [{'User_Id': 10, 'User_Type': 'Administrator',
              'User_Is_Active': 1, 'can_manage': 1}],
            [{'released': 1}],
        ])
        with self.assertRaisesRegex(svc.UserManagementError, 'Store Staff only'):
            svc.change_staff_branch(ADMIN, 10, 4, expected_branch_id=None,
                                    connection_factory=lambda: con)
        self.assertEqual(con.commits, 0)

    def test_forged_staff_role_cannot_open_db(self):
        with patch.object(svc, 'get_connection') as db:
            with self.assertRaises(svc.UserManagementPermissionError):
                svc.change_staff_branch(STAFF, 10, 4, expected_branch_id=3)
            db.assert_not_called()

    def test_noop_has_no_write_and_commits(self):
        con = ScriptedConnection([
            [{'acquired': 1}], [admin_reply()], [staff_target()],
            [{'Branch_Id': 3}], [{'Branch_Id': 3}], [{'released': 1}],
        ])
        res = svc.change_staff_branch(ADMIN, 10, 3, expected_branch_id=3,
                                      connection_factory=lambda: con)
        self.assertFalse(res['branch_changed'])
        self.assertFalse(any('UPDATE STORE_STAFF' in sql for sql, _ in con.statements))
        self.assertEqual(con.commits, 1)


class BranchPageTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeStreamlit()
        self.patcher = patch.object(page, 'st', self.ui)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_standalone_change_branch_removed(self):
        import users_page

        self.assertFalse(
            hasattr(users_page, "_change_branch_panel")
        )

        self.assertFalse(
            hasattr(users_page, "change_staff_branch")
        )

if __name__ == '__main__':
    unittest.main()
