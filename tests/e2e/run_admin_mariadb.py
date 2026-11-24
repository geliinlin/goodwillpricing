"""Administrator regressions against an independently provisioned MariaDB fixture."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import sys
from threading import Barrier
import unittest
from unittest.mock import patch

import mysql.connector
from mariadb_fixture import fixture

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import auth
from item_search import get_active_price_guideline
import configuration_service as config
import dashboard_data as dashboard
import guidelines as guides
import password_recovery as recovery
import reports_data as reports
import users_service as users

OUT = ROOT / 'test-results/admin-workflow'
ADMIN = dict(user_id=1, user_type='Administrator', can_manage_users_config=True)


class AdminWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, path = cls.enterClassContext(fixture())
        cls.login_password = (path / 'login-password').read_text()
        cls.evidence = {}
        with cls.connect() as conn, conn.cursor() as cur:
            cur.execute('INSERT INTO USER_MODULE_ACCESS (User_Id,Can_Manage_Users_Config) VALUES(1,1)')
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT/'service-sql.json').write_text(json.dumps(cls.evidence, default=str, indent=2)+'\n')

    @classmethod
    def connect(cls):
        return mysql.connector.connect(**cls.db)

    def setUp(self):
        self.enterContext(patch.object(auth, 'get_connection', self.connect))
        self.enterContext(patch.object(guides, 'get_connection', self.connect))
        self.factory = dict(connection_factory=self.connect)

    def sql(self, statement, params=()):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall() if cur.with_rows else []
            conn.commit()
            return rows

    def test_login_and_protected_services(self):
        for identifier in ['admin_e2e','admin_e2e@example.invalid']:
            self.assertTrue(auth.authenticate(identifier,self.login_password)['can_manage_users_config'])
        self.assertIsNone(auth.authenticate('admin_e2e',secrets.token_urlsafe()))
        self.sql('UPDATE USER_ACCOUNT SET User_Is_Active=0 WHERE User_Id=1')
        self.addCleanup(self.sql,'UPDATE USER_ACCOUNT SET User_Is_Active=1 WHERE User_Id=1')
        self.assertIsNone(auth.authenticate('admin_e2e',self.login_password))
        for actor in (ADMIN,dict(ADMIN,user_id=2)):
            for call in (lambda: guides.load_catalog(actor),
                         lambda: reports.get_report_snapshot(actor,**self.factory),
                         lambda: users.load_user_directory(actor,**self.factory),
                         lambda: config.load_audit(actor,**self.factory),
                         lambda: dashboard.get_admin_snapshot(actor,self.connect)):
                with self.assertRaises(PermissionError): call()

    def test_staff_denied_administrator_operations(self):
        # Supply an admin-looking session with the ID of a real, active employee.
        actor = dict(ADMIN, user_id=2)
        fields = dict(first_name='Test', last_name='Denied', username='qa_denied',
                      email='denied@example.invalid', user_type='Store Staff', branch_id=1)
        profile = dict(User_First_Name='Fixture', User_Last_Name='other_e2e',
                       User_Username='other_e2e', User_Email='other_e2e@example.invalid', Branch_Id=2)
        attrs = dict(Item_Type_Id=1, Category_Id=1, Brand_Id=1, Condition_Id=1)
        brand = dict(Brand_Name='QA denied')
        operations = {
            'catalog': lambda: guides.load_catalog(actor),
            'save guideline': lambda: guides.save_guideline(actor, attrs, '9'),
            'toggle guideline': lambda: guides.set_guideline_active(actor, 1, False, None),
            'admin dashboard': lambda: dashboard.get_admin_snapshot(actor, self.connect),
            'report': lambda: reports.get_report_snapshot(actor, **self.factory),
            'report filters': lambda: reports.get_report_filter_options(actor, **self.factory),
            'directory': lambda: users.load_user_directory(actor, **self.factory),
            'create account': lambda: users.create_user_account(actor, **fields, **self.factory),
            'edit profile': lambda: users.update_user_profile(actor, 3, first_name='Test', last_name='Edited',
                username='qa_edited', email='edited@example.invalid', branch_id=1, expected_profile=profile, **self.factory),
            'account status': lambda: users.set_account_active(actor, 3, False, **self.factory),
            'permission': lambda: users.set_management_access(actor, 1, False, **self.factory),
            'transfer': lambda: users.change_staff_branch(actor, 3, 1, expected_branch_id=2, **self.factory),
            'temporary password': lambda: users.reset_account_password(actor, 3, **self.factory),
            'send reset link': lambda: recovery.send_admin_reset_link(actor, 3, email_sender=lambda **kw: None, **self.factory),
            'master directory': lambda: config.load_master_data(actor, 'brands', **self.factory),
            'create master': lambda: config.create_master_data(actor, 'brands', brand, **self.factory),
            'edit master': lambda: config.update_master_data(actor, 'brands', 1, brand, '0'*64, **self.factory),
            'toggle master': lambda: config.set_master_active(actor, 'brands', 1, False, '0'*64, **self.factory),
            'audit': lambda: config.load_audit(actor, **self.factory),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name), self.assertRaises(PermissionError):
                operation()
        self.evidence['staff_denied_operations'] = list(operations)

    def test_version_replacement_preserves_existing_decision(self):
        original = next(r for r in guides.load_catalog(ADMIN)[1] if r['Guideline_Id'] == 1)
        attrs = {key: original[key] for key in guides.ATTRIBUTE_FIELDS}
        statement = """SELECT d.Guideline_Id, g.Guideline_Suggested_Price, d.Decision_Final_Price,
                              d.Staff_Id, d.Item_Id, d.Decision_Tag_Number
                       FROM PRICING_DECISION d JOIN PRICING_GUIDELINE g USING(Guideline_Id)
                       WHERE d.Decision_Id=1"""
        before = self.sql(statement)
        self.assertEqual(len(before), 1)
        new_id = guides.save_guideline(ADMIN, attrs, '17', guideline_id=1,
                                       expected_token=guides.snapshot_token(original))
        for active in (False, True):
            current = next(r for r in guides.load_catalog(ADMIN)[1] if r['Guideline_Id'] == new_id)
            guides.set_guideline_active(ADMIN, new_id, active, guides.snapshot_token(current))
            match = get_active_price_guideline(1, 1, 1, 1, self.connect)
            self.assertEqual(match.status, 'matched' if active else 'not_found')
            if active:
                self.assertEqual((match.guideline_id, str(match.suggested_price)), (new_id, '17.00'))
            self.assertEqual(self.sql(statement), before)
        self.evidence['historical_decision_after_replacement'] = before

    def test_temporary_password_blocks_protected_admin_services(self):
        self.sql("INSERT INTO USER_PASSWORD_STATE(User_Id,Must_Change_Password) VALUES(1,1) ON DUPLICATE KEY UPDATE Must_Change_Password=1")
        self.addCleanup(self.sql,"UPDATE USER_PASSWORD_STATE SET Must_Change_Password=0 WHERE User_Id=1")
        for name, operation in (
            ('guidelines', lambda: guides.load_catalog(ADMIN)),
            ('history', lambda: dashboard.get_filtered_history(ADMIN,factory=self.connect)),
            ('reports', lambda: reports.get_report_snapshot(ADMIN,**self.factory)),
            ('users', lambda: users.load_user_directory(ADMIN,**self.factory)),
            ('configuration', lambda: config.load_audit(ADMIN,**self.factory))):
            with self.subTest(service=name), self.assertRaises(PermissionError):
                operation()

    def test_concurrent_edits_and_rollback(self):
        chair,kind = self.sql("SELECT Category_Id,Item_Type_Id FROM CATEGORY WHERE Category_Name='Chairs'")[0]
        attrs=dict(Category_Id=chair,Item_Type_Id=kind,Brand_Id=1,Condition_Id=1)
        gid=guides.save_guideline(ADMIN,attrs,'15')
        token=guides.snapshot_token(next(r for r in guides.load_catalog(ADMIN)[1] if r['Guideline_Id']==gid))
        def edit(price):
            return guides.save_guideline(ADMIN,attrs,price,guideline_id=gid,expected_token=token)
        self.sql("""CREATE TRIGGER qa_guideline_insert BEFORE INSERT ON PRICING_GUIDELINE
                    FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test insert failure'""")
        try:
            with self.assertRaises(mysql.connector.Error): edit('18')
            self.assertEqual(self.sql('SELECT Guideline_Is_Active FROM PRICING_GUIDELINE WHERE Guideline_Id=%s',(gid,)),[(1,)])
        finally:
            self.sql('DROP TRIGGER qa_guideline_insert')
        gate=Barrier(2)
        def submit(price):
            gate.wait(10)
            try: return edit(price)
            except guides.GuidelineError: return None
        with ThreadPoolExecutor(2) as pool:
            ids=list(pool.map(submit,['18','19']))
        self.assertEqual(sum(i is not None for i in ids),1)
        rows=self.sql('SELECT Guideline_Id,Guideline_Suggested_Price,Guideline_Is_Active FROM PRICING_GUIDELINE WHERE Category_Id=%s ORDER BY Guideline_Id',(chair,))
        self.assertEqual([r[2] for r in rows],[0,1])
        active=next(r for r in guides.load_catalog(ADMIN)[1] if r['Guideline_Id']==max(filter(None,ids)))
        for desired in (False,True):
            guides.set_guideline_active(ADMIN,active['Guideline_Id'],desired,guides.snapshot_token(active))
            active=next(r for r in guides.load_catalog(ADMIN)[1] if r['Guideline_Id']==active['Guideline_Id'])
            self.assertEqual(bool(active['Guideline_Is_Active']),desired)
        for invalid in ({},dict(attrs,Item_Type_Id=1),dict(attrs,Brand_Id=999),dict(attrs,Condition_Id=999)):
            with self.assertRaises(guides.GuidelineError): guides.save_guideline(ADMIN,invalid,'15')
        for price in (None,'no','-3','0',str(active['Guideline_Suggested_Price']),'20'):
            with self.assertRaises(guides.GuidelineError): guides.save_guideline(ADMIN,attrs,price)
        self.sql('UPDATE BRAND SET Brand_Is_Active=0 WHERE Brand_Id=1')
        self.addCleanup(self.sql,'UPDATE BRAND SET Brand_Is_Active=1 WHERE Brand_Id=1')
        with self.assertRaises(guides.GuidelineError): guides.save_guideline(ADMIN,attrs,'21')
        self.evidence['concurrent_versions']=rows

    def test_configuration_lifecycle_and_audit(self):
        for kind,spec in config.SPECS.items():
            fields={spec.name:'QA '+kind}
            if kind=='categories': fields['Item_Type_Id']=1
            if kind=='branches': fields.update(Branch_Street='1 Test Rd',Branch_City='Fixture',Branch_State='MI',Branch_Zip='48800')
            uid=config.create_master_data(ADMIN,kind,fields,**self.factory)
            def snapshot():
                row=next(r for r in config.load_master_data(ADMIN,kind,**self.factory) if r[spec.key]==uid)
                return config.snapshot_for_row(kind,row)
            fields[spec.name]+=' revised'
            config.update_master_data(ADMIN,kind,uid,fields,snapshot(),**self.factory)
            for desired in (False,True):
                config.set_master_active(ADMIN,kind,uid,desired,snapshot(),**self.factory)
                self.assertEqual(self.sql(f'SELECT {spec.name},{spec.active} FROM {spec.table} WHERE {spec.key}=%s',(uid,)),[(fields[spec.name],int(desired))])
        actions=self.sql('SELECT Action_Type,COUNT(*) FROM MASTER_DATA_AUDIT GROUP BY Action_Type')
        self.assertEqual(dict(actions),dict(CREATE=5,UPDATE=5,ACTIVATE=5,DEACTIVATE=5))
        self.assertEqual(len(config.load_audit(ADMIN,**self.factory)),20)
        self.evidence['audit_counts']=actions

    def test_users_profiles_access_and_recovery(self):
        captured={}
        def deliver(**message): captured.update(message)
        for role,branch in [('Administrator',None),('Store Staff',1)]:
            name='qa_admin' if branch is None else 'qa_staff'
            fields=dict(first_name='QA',last_name=name,username=name,email=name+'@example.invalid',user_type=role,branch_id=branch)
            receipt=users.create_and_invite_user(ADMIN,email_sender=deliver,**fields,**self.factory)
            uid=receipt['user_id']
            self.assertTrue(receipt['email_sent'])
            self.assertEqual(self.sql('SELECT a.Admin_Id,s.Branch_Id FROM USER_ACCOUNT u LEFT JOIN ADMINISTRATOR a ON a.Admin_Id=u.User_Id LEFT JOIN STORE_STAFF s ON s.Staff_Id=u.User_Id WHERE u.User_Id=%s',(uid,)),[(uid if branch is None else None,branch)])
            recovery.complete_password_reset(captured['token'],secrets.token_urlsafe(),**self.factory)
            with self.assertRaises(recovery.PasswordRecoveryError): recovery.complete_password_reset(captured['token'],secrets.token_urlsafe(),**self.factory)
            for changes in (dict(email='fresh@example.invalid'),dict(username='different'),dict(user_type='invalid')):
                with self.assertRaises(users.UserManagementError): users.create_user_account(ADMIN,**(fields|changes),**self.factory)
            if branch is None:
                subject=dict(ADMIN,user_id=uid)
                with self.assertRaises(PermissionError): users.load_user_directory(subject,**self.factory)
                for grant in (True,False):
                    users.set_management_access(ADMIN,uid,grant,**self.factory)
                    self.assertEqual(self.sql('SELECT Can_Manage_Users_Config FROM USER_MODULE_ACCESS WHERE User_Id=%s',(uid,)),[(int(grant),)])
                    if grant: self.assertTrue(users.load_user_directory(subject,**self.factory)['users'])
            else:
                users.change_staff_branch(ADMIN,uid,2,expected_branch_id=1,**self.factory)
                self.assertEqual(self.sql('SELECT Branch_Id FROM STORE_STAFF WHERE Staff_Id=%s',(uid,)),[(2,)])
                old=next(r for r in users.load_user_directory(ADMIN,**self.factory)['users'] if r['User_Id']==uid)
                users.update_user_profile(ADMIN,uid,first_name='Changed',last_name=name,username=name,email='changed@example.invalid',branch_id=2,expected_profile=old,**self.factory)
                self.assertEqual(self.sql('SELECT User_First_Name,User_Email FROM USER_ACCOUNT WHERE User_Id=%s',(uid,)),[('Changed','changed@example.invalid')])
            temporary=users.reset_account_password(ADMIN,uid,**self.factory)
            password=secrets.token_urlsafe()
            recovery.change_temporary_password(uid,temporary,password,**self.factory)
            original=auth.authenticate(name,password)
            self.assertIsNotNone(original)
            self.sql('UPDATE USER_PASSWORD_TOKEN SET Sent_At=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 DAY) WHERE User_Id=%s',(uid,))
            self.assertTrue(recovery.send_admin_reset_link(ADMIN,uid,email_sender=deliver,**self.factory))
            pending_token = captured['token']
            for active in (False,True):
                users.set_account_active(ADMIN,uid,active,**self.factory)
                self.assertEqual(self.sql('SELECT User_Is_Active FROM USER_ACCOUNT WHERE User_Id=%s',(uid,)),[(int(active),)])
                self.assertEqual(auth.get_active_user(uid) is not None,active)
            self.assertGreater(auth.get_active_user(uid)['password_version'],original['password_version'],
                               'Deactivating and reactivating must invalidate existing sessions')
            with self.assertRaises(recovery.PasswordRecoveryError):
                recovery.complete_password_reset(pending_token, secrets.token_urlsafe(), **self.factory)
            self.evidence.setdefault('account_reactivation', {})[role] = {
                'user_id': uid, 'previous_version': original['password_version'],
                'new_version': auth.get_active_user(uid)['password_version'], 'pending_reset_rejected': True}
            # Exercise the admin reset flow after clearing the fixture cooldown.
            self.sql('UPDATE USER_PASSWORD_TOKEN SET Sent_At=DATE_SUB(UTC_TIMESTAMP(),INTERVAL 1 DAY) WHERE User_Id=%s',(uid,))
            self.assertTrue(recovery.send_admin_reset_link(ADMIN,uid,email_sender=deliver,**self.factory))
            recovery.complete_password_reset(captured['token'],secrets.token_urlsafe(),**self.factory)
            self.assertEqual(self.sql('SELECT COUNT(*) FROM USER_PASSWORD_TOKEN WHERE User_Id=%s AND Consumed_At IS NULL',(uid,)),[(0,)])

    def test_metrics_and_filtered_history_against_sql(self):
        self.sql("INSERT INTO ITEM VALUES(21,'QA report',CURDATE(),1,1,1,1),(22,'QA report',CURDATE(),1,1,1,1)")
        self.sql("""INSERT INTO PRICING_DECISION VALUES
                 (21,NOW(),'Wear',8.50,'QA-21',1,2,21,1),(22,NOW(),'Rare',16.50,'QA-22',1,2,22,1)""")
        metrics=dashboard.get_admin_snapshot(ADMIN,self.connect)['metrics']
        expected=self.sql("""SELECT (SELECT COUNT(*) FROM PRICING_GUIDELINE WHERE Guideline_Is_Active=1),
            (SELECT COUNT(*) FROM USER_ACCOUNT WHERE User_Is_Active=1),COUNT(*),
            SUM(d.Decision_Final_Price<>g.Guideline_Suggested_Price)
            FROM PRICING_DECISION d JOIN PRICING_GUIDELINE g USING(Guideline_Id)""")[0]
        self.assertEqual(tuple(metrics[k] for k in ['active_guidelines','active_users','decisions','overrides']),expected)
        # Compare each category, reason and staff group, as well as all KPIs.
        start,end=reports.report_window(30)
        detail = """ FROM PRICING_DECISION d
            JOIN ITEM i USING(Item_Id) JOIN CATEGORY c ON c.Category_Id=i.Category_Id
            JOIN PRICING_GUIDELINE g ON g.Guideline_Id=d.Guideline_Id
            WHERE d.Decision_Datetime >= %s AND d.Decision_Datetime < %s"""
        window = (start, end)
        categories = self.sql('SELECT c.Category_Name,COUNT(*),AVG(d.Decision_Final_Price)' +
                              detail + ' GROUP BY c.Category_Id,c.Category_Name', window)
        staff = self.sql('SELECT d.Staff_Id,COUNT(*),AVG(d.Decision_Final_Price),'
                         'SUM(d.Decision_Final_Price<>g.Guideline_Suggested_Price)' +
                         detail + ' GROUP BY d.Staff_Id', window)
        reasons = self.sql('SELECT d.Decision_Reason,COUNT(*)' + detail +
                           ' AND d.Decision_Final_Price<>g.Guideline_Suggested_Price'
                           ' GROUP BY d.Decision_Reason', window)
        for adjustment in ('all','overrides','unchanged','increased','decreased'):
            op={'all':'1=1','overrides':'f<>p','unchanged':'f=p','increased':'f>p','decreased':'f<p'}[adjustment]
            source=f"""FROM (SELECT d.Decision_Final_Price f,g.Guideline_Suggested_Price p,
                 u.User_Id staff,u.User_Is_Active active FROM PRICING_DECISION d JOIN PRICING_GUIDELINE g USING(Guideline_Id)
                 JOIN USER_ACCOUNT u ON u.User_Id=d.Staff_Id WHERE d.Decision_Datetime >= %s AND d.Decision_Datetime < %s) q WHERE {op}"""
            count,avg,overrides,active=self.sql('SELECT COUNT(*),COALESCE(AVG(f),0),COALESCE(SUM(f<>p),0),COUNT(DISTINCT IF(active,staff,NULL)) '+source,(start,end))[0]
            result=reports.get_report_snapshot(ADMIN,adjustment=adjustment,**self.factory)
            self.assertEqual(result['metrics'],dict(items_priced=count,average_final_price=avg,override_count=overrides,
                            override_rate=int(overrides)/count if count else 0,active_staff_with_activity=active))
            if adjustment=='all':
                self.evidence['dashboard_metrics']=metrics
                self.evidence['report_metrics']=result['metrics']
                self.assertCountEqual([(a['category_name'], a['items_priced'], a['average_final_price'])
                                       for a in result['categories']], categories)
                self.assertCountEqual([(a['staff_id'], a['items_priced'], a['average_final_price'], a['override_count'])
                                       for a in result['staff']], staff)
                self.assertCountEqual([(r['reason'], r['count'], r['share']) for r in result['reasons']],
                                      [(name, n, n / int(overrides)) for name, n in reasons])
                self.evidence.update(category_groups=categories, staff_groups=staff, override_reasons=reasons)
        for branch,total in [(1,2),(2,0)]:
            result=reports.get_report_snapshot(ADMIN,staff_id=2,branch_id=branch,category_id=1,item_type_id=1,**self.factory)
            self.assertEqual(result['metrics']['items_priced'],total)
        history=dashboard.get_filtered_history(ADMIN,staff_id=2,branch_id=1,category_id=1,price_change='Override',
                                               keyword='QA',days=7,limit=1,factory=self.connect)
        self.assertEqual([(r['tag_number'],r['override_reason']) for r in history],[('QA-22','Rare')])

if __name__=='__main__':
    unittest.main(verbosity=2)
