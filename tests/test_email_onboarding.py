"""Offline email + one-time link checks. NEVER contacts a real SMTP/MySQL server."""
from pathlib import Path
from unittest.mock import Mock, patch
import os
import unittest

from _goodwill_test_helpers import ScriptedConnection
import password_recovery as rec
import email_delivery as mail
import users_service as users

ROOT = Path(__file__).resolve().parents[1]
ADMIN = {'user_id': 1, 'user_type': 'Administrator', 'can_manage_users_config': True}


class RecoveryTests(unittest.TestCase):
    def _factory(self, *connections):
        items = iter(connections)
        return lambda: next(items)

    def test_send_invite_only_activates_after_email_success(self):
        first = ScriptedConnection([
            [{'User_Id':55,'User_Email':'sam@example.org',
              'User_First_Name':'Sam','User_Is_Active':1}], []])
        second = ScriptedConnection([[], []])
        send = Mock()
        ok = rec.send_new_user_invitation(55, 'temp-secret',
            connection_factory=self._factory(first, second), email_sender=send)
        self.assertTrue(ok)
        self.assertEqual(first.commits, 1)
        self.assertEqual(second.commits, 1)
        self.assertNotIn('temp-secret', str(first.statements))
        self.assertIn('temp-secret', send.call_args.kwargs['temporary_password'])
        self.assertEqual(send.call_args.kwargs['purpose'], 'invite')
        self.assertIn('Sent_At=UTC_TIMESTAMP()', second.statements[-1][0])

    def test_send_failure_does_not_invalidate_prior_links(self):
        first = ScriptedConnection([
            [{'User_Id':55,'User_Email':'sam@example.org',
              'User_First_Name':'Sam','User_Is_Active':1}], []])
        cleanup = ScriptedConnection([[]])
        send = Mock(side_effect=RuntimeError('synthetic SMTP error'))
        with self.assertLogs(rec.LOG.name, 'ERROR'):
            ok = rec.send_new_user_invitation(55, 'temp-secret',
                connection_factory=self._factory(first,cleanup), email_sender=send)
        self.assertFalse(ok)
        self.assertEqual(cleanup.commits, 1)
        self.assertFalse(any('Consumed_At=' in sql for sql,_ in cleanup.statements))

    def test_unknown_email_is_silent_and_no_email_sent(self):
        conn=ScriptedConnection([[]]);send=Mock()
        self.assertIsNone(rec.request_password_reset('not-found@example.com',
            connection_factory=lambda:conn,email_sender=send))
        send.assert_not_called()
        self.assertTrue(conn.closed)

    def test_inactive_email_is_not_returned_to_sender(self):
        conn=ScriptedConnection([[]]);send=Mock()
        rec.request_password_reset('inactive@example.com',
            connection_factory=lambda:conn,email_sender=send)
        send.assert_not_called()

    def test_rate_limited_request_does_not_send_again(self):
        lookup=ScriptedConnection([[{'User_Id':55}]])
        start=ScriptedConnection([[{'User_Id':55,'User_Email':'a@example.com',
            'User_First_Name':'A','User_Is_Active':1}],[{'recent':1}]])
        send=Mock()
        rec.request_password_reset('a@example.com',
            connection_factory=self._factory(lookup,start),email_sender=send)
        send.assert_not_called()
        self.assertEqual(start.rollbacks,1)

    @patch.object(rec,'hash_password',return_value='hashed')
    def test_complete_reset_updates_password_and_burns_tokens(self, hashing):
        conn=ScriptedConnection([[{'User_Id':55}],[],[],[]])
        rec.complete_password_reset('long-valid-raw-token-012345678901', 'newpassword',
                                    connection_factory=lambda:conn)
        self.assertEqual(conn.commits,1)
        updates='\n'.join(sql for sql,_ in conn.statements)
        self.assertIn('User_Password_Hash',updates)
        self.assertIn('Consumed_At=UTC_TIMESTAMP()', updates)
        self.assertIn('Must_Change_Password=0', updates)
        self.assertIn('Session_Version=Session_Version+1', updates)
        hashing.assert_called_once_with('newpassword')
        self.assertNotIn('long-valid-raw-token',str(conn.statements))

    @patch.object(rec,'hash_password',return_value='hashed')
    def test_consumed_token_cannot_be_reused(self, hashing):
        conn=ScriptedConnection([[]])
        with self.assertRaisesRegex(rec.PasswordRecoveryError,'invalid, expired'):
            rec.complete_password_reset('long-valid-raw-token-012345678901','newpw',
                                        connection_factory=lambda:conn)
        self.assertEqual(conn.commits,0)
        self.assertEqual(conn.rollbacks,1)
        self.assertFalse(any('UPDATE USER_ACCOUNT' in sql for sql,_ in conn.statements))

    def test_blank_password_rejected_before_database(self):
        with patch.object(rec,'get_connection') as opening:
            for password in ('','  ', '界'*25):
                with self.subTest(password=password), self.assertRaises(rec.PasswordRecoveryError):
                    rec.complete_password_reset('long-valid-raw-token-012345678901',password)
            opening.assert_not_called()

    @patch.object(rec,'hash_password',return_value='hashed')
    @patch.object(rec.bcrypt,'checkpw',return_value=True)
    def test_temporary_login_requires_password_change(self, verify, hashing):
        conn=ScriptedConnection([[{'User_Password_Hash':'oldhash'}],[],[],[]])
        rec.change_temporary_password(55,'oldpw','mynewpw', connection_factory=lambda:conn)
        verify.assert_called_once_with(b'oldpw',b'oldhash')
        self.assertEqual(conn.commits,1)
        self.assertTrue(any('Must_Change_Password=0' in sql for sql,_ in conn.statements))
        self.assertTrue(any('Session_Version=Session_Version+1' in sql for sql,_ in conn.statements))

    @patch.object(rec,'hash_password',return_value='hashed')
    @patch.object(rec.bcrypt,'checkpw',return_value=False)
    def test_wrong_initial_password_never_changes_database(self, verify, hashing):
        conn=ScriptedConnection([[{'User_Password_Hash':'oldhash'}]])
        with self.assertRaises(rec.PasswordRecoveryError):
            rec.change_temporary_password(55,'wrong','newpw', connection_factory=lambda:conn)
        self.assertEqual(conn.commits,0)
        self.assertEqual(conn.rollbacks,1)

    def test_admin_resend_requires_live_rights(self):
        with patch.object(rec,'get_connection') as opening:
            with self.assertRaises(PermissionError):
                rec.send_admin_reset_link({'user_id':1,'user_type':'Store Staff',
                     'can_manage_users_config':True},55)
            opening.assert_not_called()

    def test_create_wrapper_never_returns_plaintext_password(self):
        with patch.object(users,'create_user_account',return_value={
               'user_id':55,'username':'sample','temporary_password':'private'}) as create, \
             patch.object(rec,'send_new_user_invitation',return_value=True) as send:
            result=users.create_and_invite_user(ADMIN,first_name='A',last_name='B',
                username='sample',email='sample@example.org',user_type='Store Staff',
                branch_id=3)
        self.assertTrue(result['email_sent'])
        self.assertNotIn('temporary_password',result)
        self.assertTrue(create.call_args.kwargs['require_password_change'])
        send.assert_called_once()

    def test_inactive_account_not_sent_an_invitation(self):
        with patch.object(users,'create_user_account',return_value={
               'user_id':55,'username':'sample','temporary_password':'private'}), \
             patch.object(rec,'send_new_user_invitation') as send:
            result=users.create_and_invite_user(ADMIN,active=False)
        self.assertFalse(result['email_sent'])
        send.assert_not_called()

    def test_session_metadata_marks_initial_password_and_version(self):
        import auth
        from datetime import datetime, timedelta, timezone
        row = {'User_Id':55,'User_First_Name':'Sam','User_Last_Name':'Sample',
            'User_Username':'sample','User_Is_Active':1,'User_Type':'Store Staff',
            'Admin_Id':None,'Staff_Id':55,'Branch_Id':2,'Branch_Name':'Branch',
            'Must_Change_Password':1,'Password_Version':2,
            'Temp_Password_Expires_At':datetime.now(timezone.utc)+timedelta(hours=2)}
        user=auth._to_session_user(row)
        self.assertTrue(user['must_change_password'])
        self.assertEqual(user['password_version'],2)
        row['Temp_Password_Expires_At']=datetime.now(timezone.utc)-timedelta(seconds=1)
        self.assertIsNone(auth._to_session_user(row))

    def test_email_config_refuses_plaintext_or_untrusted_base_urls(self):
        cfg={'EMAIL_SMTP_HOST':'smtp.example.org','EMAIL_SMTP_USER':'sender',
             'EMAIL_SMTP_PASSWORD':'secret','EMAIL_FROM_ADDRESS':'sender@example.org',
             'APP_BASE_URL':'http://bad.example.org'}
        with patch.dict(os.environ,cfg,clear=True):
            with self.assertRaises(mail.EmailConfigurationError):
                mail._email_config()
        cfg['APP_BASE_URL']='https://goodwill.example.org'
        cfg['EMAIL_SMTP_SECURITY']='none'
        with patch.dict(os.environ,cfg,clear=True):
            with self.assertRaises(mail.EmailConfigurationError):
                mail._email_config()

    def test_email_builder_constructs_single_use_url_and_temp_password(self):
        cfg={'host':'smtp.example.org','port':465,'username':'sender','password':'pw',
             'from_address':'sender@example.org','security':'ssl','base_url':'https://goodwill.example.org'}
        client=Mock(); client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False)
        with patch.object(mail,'_email_config',return_value=cfg), \
             patch.object(mail.smtplib,'SMTP_SSL',return_value=client):
            mail.send_account_email(to_email='sam@example.org',first_name='Sam',
                token='one-time-token', purpose='invite',temporary_password='temp-123')
        sent=client.send_message.call_args.args[0]
        self.assertIn('reset_token=one-time-token', sent.get_content())
        self.assertIn('temp-123',sent.get_content())
        self.assertIn('SSL' if False else 'goodwill',sent['Subject'].lower())

    def test_migration_and_pages_wired_without_plaintext_display(self):
        migration=(ROOT/'migrations/002_account_email_recovery.sql').read_text()
        app=(ROOT/'app.py').read_text()
        page=(ROOT/'users_page.py').read_text()
        for name in ('USER_PASSWORD_TOKEN','USER_PASSWORD_STATE','Token_Hash','Consumed_At'):
            self.assertIn(name,migration)
        self.assertIn("st.query_params.get('reset_token')",app)
        self.assertIn('show_forced_password_change',app)
        self.assertIn('gw_password_version',app)
        self.assertIn('complete_password_reset',app)
        self.assertIn('create_and_invite_user(',page)
        self.assertIn('Send Password Reset Email',page)
        self.assertNotIn("st.code(receipt['temporary_password'])",page)


if __name__=='__main__': unittest.main()
