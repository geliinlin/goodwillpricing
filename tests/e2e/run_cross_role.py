"""Run the cross-role browser and independent SQL checks on one disposable fixture."""
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import tomllib
from unittest.mock import patch

import mysql.connector
from mariadb_fixture import fixture

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'test-results/cross-role-integration'


def check_denied(operation):
    try:
        operation()
    except PermissionError:
        return True
    raise AssertionError('Protected operation unexpectedly succeeded')


def snapshot(stage):
    directory = Path(os.environ['GW_E2E_DIR'])
    config = tomllib.loads((directory / '.streamlit/secrets.toml').read_text())['mysql']
    if not (config['host'] == '127.0.0.1' and config['user'] == 'gw_fixture'
            and re.fullmatch(r'gw_test_[0-9a-f]{16}', config['database'])):
        raise ValueError('Expected disposable loopback fixture')

    def connect():
        return mysql.connector.connect(**config)

    with connect() as conn, conn.cursor(dictionary=True) as cur:
        cur.execute('SELECT VERSION() AS server, DATABASE() AS db')
        result = cur.fetchone()
        assert result['db'] == config['database']
        cur.execute('''SELECT Guideline_Id AS id, Guideline_Suggested_Price AS price,
                    Guideline_Is_Active AS active, Admin_Id AS admin_id, Category_Name AS category,
                    Item_Type_Name AS item_type, Brand_Name AS brand, Condition_Name AS item_condition
                    FROM PRICING_GUIDELINE JOIN CATEGORY USING(Category_Id)
                    JOIN ITEM_TYPE ON ITEM_TYPE.Item_Type_Id=PRICING_GUIDELINE.Item_Type_Id JOIN BRAND USING(Brand_Id)
                    JOIN ITEM_CONDITION USING(Condition_Id) WHERE Guideline_Id>2 ORDER BY Guideline_Id''')
        result['versions'] = cur.fetchall()
        cur.execute('''SELECT d.Decision_Id AS decision_id,i.Item_Id AS item_id,i.Item_Description AS description,
                    d.Guideline_Id AS guideline_id,d.Staff_Id AS staff_id,CONCAT(u.User_First_Name,' ',u.User_Last_Name) AS staff,
                    b.Branch_Name AS branch,g.Guideline_Suggested_Price AS suggested,d.Decision_Final_Price AS final,
                    d.Decision_Reason AS reason,d.Decision_Tag_Number AS tag,d.Decision_Datetime AS saved_at,
                    DATE_FORMAT(d.Decision_Datetime,'%b %d, %Y · %h:%i %p') AS display_time
                    FROM PRICING_DECISION d JOIN ITEM i USING(Item_Id) JOIN PRICING_GUIDELINE g USING(Guideline_Id)
                    JOIN STORE_STAFF s USING(Staff_Id) JOIN USER_ACCOUNT u ON u.User_Id=s.Staff_Id
                    LEFT JOIN BRANCH b ON b.Branch_Id=d.Decision_Branch_Id ORDER BY d.Decision_Id''')
        result['decisions'] = cur.fetchall()
        cur.execute('''SELECT (SELECT COUNT(*) FROM ITEM) AS items, COUNT(*) AS decisions,
                    COUNT(DISTINCT d.Item_Id) AS priced_items, COUNT(DISTINCT d.Decision_Tag_Number) AS unique_tags,
                    SUM(d.Decision_Final_Price<>g.Guideline_Suggested_Price) AS overrides,
                    (SELECT COUNT(*) FROM USER_ACCOUNT WHERE User_Is_Active=1) AS active_users,
                    (SELECT COUNT(*) FROM PRICING_GUIDELINE WHERE Guideline_Is_Active=1) AS active_guides
                    FROM PRICING_DECISION d JOIN PRICING_GUIDELINE g USING(Guideline_Id)''')
        result['counts'] = {k:int(v) for k,v in cur.fetchone().items()}
        cur.execute('''SELECT u.User_Is_Active AS active, COALESCE(ps.Session_Version,0) AS version
                    FROM USER_ACCOUNT u LEFT JOIN USER_PASSWORD_STATE ps USING(User_Id) WHERE u.User_Id=2''')
        result['employee'] = cur.fetchone()
    if stage == 'account-disabled':
        from pricing_decision import PricingValidationError, save_pricing_decision
        draft = dict(item_type_id=1, category_id=1, brand_id=1, condition_id=1, guideline_id=1,
                     staff_id=2, branch_id=1, suggested_price='12.50', item_received_date=date.today(),
                     item_description='Disabled employee')
        try:
            save_pricing_decision(2, draft, '12.50', None, connect)
        except PricingValidationError as exc:
            assert 'no longer authorized' in str(exc), str(exc)
            result['pricing_denial'] = str(exc)
        else:
            raise AssertionError('Inactive employee saved a decision')
    if stage == 'reauthenticated':
        import auth
        import dashboard_data
        import guidelines
        import reports_data
        import users_service
        with patch.object(auth, 'get_connection', connect):
            actor = auth.authenticate('staff_e2e', (directory/'login-password').read_text())
        assert actor['user_type'] == 'Store Staff'
        with patch.object(guidelines, 'get_connection', connect):
            result['admin_access_denied'] = {
                'dashboard': check_denied(lambda: dashboard_data.get_admin_snapshot(actor, connect)),
                'guideline': check_denied(lambda: guidelines.load_catalog(actor)),
                'users': check_denied(lambda: users_service.load_user_directory(actor, connection_factory=connect)),
                'reports': check_denied(lambda: reports_data.get_report_snapshot(actor, connection_factory=connect)),
            }
    print(json.dumps(result, default=str))


def check_temporary_session_expiry(config):
    import auth
    import users_service
    from password_recovery import change_temporary_password

    def connect():
        return mysql.connector.connect(**config)

    admin = dict(user_id=1, user_type='Administrator', can_manage_users_config=True)
    factory = dict(connection_factory=connect)
    user_id = 3
    temporary = users_service.reset_account_password(admin, user_id, **factory)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE USER_PASSWORD_STATE SET Temp_Password_Expires_At=UTC_TIMESTAMP()+INTERVAL 5 SECOND
                       WHERE User_Id=%s AND Must_Change_Password=1""", (user_id,))
        assert cur.rowcount == 1
        cur.execute('SELECT Temp_Password_Expires_At FROM USER_PASSWORD_STATE WHERE User_Id=%s', (user_id,))
        expiry = cur.fetchone()[0].replace(tzinfo=timezone.utc)
        conn.commit()
    with patch.object(auth, 'get_connection', connect):
        assert auth.authenticate('other_e2e', temporary)['must_change_password']
        assert auth.get_active_user(user_id)['must_change_password']
        time.sleep(max(0, (expiry - datetime.now(timezone.utc)).total_seconds()) + 0.1)
        assert auth.get_active_user(user_id) is None
        assert auth.authenticate('other_e2e', temporary) is None
        fresh = users_service.reset_account_password(admin, user_id, **factory)
        password = secrets.token_urlsafe()
        change_temporary_password(user_id, fresh, password, **factory)
        assert auth.authenticate('other_e2e', password)['must_change_password'] is False
    (OUT / 'temporary-session.json').write_text(json.dumps({
        'staff_id': user_id, 'expires_at_utc': expiry.isoformat(),
        'session_valid_before_expiry': True, 'session_denied_after_expiry': True,
        'expired_credential_denied': True, 'reauthenticated_after_reset': True}, indent=2)+'\n')
    print('PASS: temporary session expired and renewed credentials authenticated.')


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with fixture() as (config, directory):
        with mysql.connector.connect(**config) as conn, conn.cursor() as cur:
            cur.execute('INSERT INTO USER_MODULE_ACCESS(User_Id,Can_Manage_Users_Config) VALUES(1,1)')
            conn.commit()
        env = {**os.environ, 'GW_E2E_DIR': str(directory), 'GW_E2E_PYTHON': sys.executable}
        subprocess.run(['node', '--test', str(HERE/'cross_role.cjs')], env=env, check=True)
        check_temporary_session_expiry(config)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'snapshot':
        snapshot(sys.argv[2])
    else:
        run()
