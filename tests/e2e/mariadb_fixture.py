"""Disposable MariaDB 10.11 for workflow tests. Requires Docker and project dependencies.

Use as a context manager, or run this file for a browser fixture until Enter is pressed.
All credentials are freshly generated; existing project DB settings are never read.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import secrets
import subprocess
import tempfile
import time

import bcrypt
import mysql.connector

HERE = Path(__file__).resolve().parent
MIGRATIONS = HERE.parents[1] / 'migrations'
ORDER = (
    '001_user_management_access.sql', '002_account_email_recovery.sql',
    '003_master_data_management.sql', '004_preflight_read_only.sql',
    '004_category_item_type.sql', '005_seed_item_types_categories.sql',
    '006_master_descriptions.sql', '007_add_decision_branch.sql', '008_align_g3_staff_role.sql',
)


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, text=True,
                          stdout=subprocess.PIPE, **kwargs).stdout.strip()


def seed(conn, password, *, branch_snapshot=True, staff_role='Store Staff'):
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode()
    with conn.cursor() as cur:
        for uid, role, username, active in [
            (1, 'Administrator', 'admin_e2e', 1), (2, staff_role, 'staff_e2e', 1),
            (3, staff_role, 'other_e2e', 1), (4, staff_role, 'inactive_e2e', 0)
        ]:
            cur.execute('''INSERT INTO USER_ACCOUNT
                        (User_Id,User_First_Name,User_Last_Name,User_Username,User_Email,
                         User_Password_Hash,User_Is_Active,User_Type) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (uid, 'Fixture', username, username, f'{username}@example.invalid',
                         password_hash, active, role))
        cur.execute('INSERT INTO ADMINISTRATOR VALUES (1)')
        cur.execute("""INSERT INTO BRANCH (Branch_Id,Branch_Name,Branch_City,Branch_Street,Branch_State,Branch_Zip)
                       VALUES (1,'Test North','Test North','1 Test St','MI','48801'),
                              (2,'Test South','Test South','2 Test St','MI','48802')""")
        cur.execute('INSERT INTO STORE_STAFF VALUES (2,1),(3,2),(4,1)')
        cur.execute("""INSERT INTO BRAND (Brand_Id,Brand_Name,Brand_Description)
                       VALUES (1,'Generic','Standard brand tier')""")
        cur.execute("""INSERT INTO ITEM_CONDITION (Condition_Id,Condition_Name,Condition_Description)
                       VALUES (1,'Good','Gently used')""")
        cur.execute("""INSERT INTO PRICING_GUIDELINE (Guideline_Id,Item_Type_Id,Category_Id,Brand_Id,Condition_Id,
                       Guideline_Suggested_Price,Guideline_Is_Active,Guideline_Last_Updated,Admin_Id) VALUES
                       (1,1,1,1,1,12.50,1,NOW(),1),(2,1,1,1,1,4.50,0,NOW(),1)""")
        cur.execute("""INSERT INTO ITEM VALUES (1,'Other fixture item',CURDATE(),1,1,1,1)""")
        columns = 'Decision_Id, Decision_Datetime, Decision_Reason, Decision_Final_Price, Decision_Tag_Number, Guideline_Id, Staff_Id, Item_Id'
        values = "1,NOW(),NULL,12.50,'TS26-100001',1,3,1"
        if branch_snapshot:
            columns += ', Decision_Branch_Id'
            values += ', 2'
        cur.execute(f'INSERT INTO PRICING_DECISION ({columns}) VALUES ({values})')
    conn.commit()


@contextmanager
def fixture():
    if os.environ.get('GW_TEST_ENGINE') == 'mysql':
        from mysql_fixture import mysql_fixture
        with mysql_fixture() as isolated:
            yield isolated
        return
    schema = 'gw_test_' + secrets.token_hex(8)
    name = schema.replace('_', '-')
    password = secrets.token_urlsafe(24)
    app_password = secrets.token_urlsafe(18)
    docker('run', '--detach', '--rm', '--name', name, '--publish', '127.0.0.1::3306',
           '--tmpfs', '/var/lib/mysql', '--env', 'MARIADB_RANDOM_ROOT_PASSWORD=1',
           '--env', 'MARIADB_USER=gw_fixture', '--env', 'MARIADB_PASSWORD',
           '--env', 'MARIADB_DATABASE=' + schema, 'mariadb:10.11',
           env={**os.environ, 'MARIADB_PASSWORD': password})
    try:
        address, port = docker('port', name, '3306/tcp').split(':')
        if address != '127.0.0.1':
            raise RuntimeError('Expected a loopback-only fixture')
        config = dict(host=address, port=int(port), user='gw_fixture', password=password,
                      database=schema, connection_timeout=3, ssl_disabled=True)
        for _ in range(60):
            try:
                conn = mysql.connector.connect(**config)
                break
            except mysql.connector.Error:
                time.sleep(1)
        else:
            raise RuntimeError('Disposable MariaDB did not start')
        with conn:
            with conn.cursor() as cur:
                cur.execute('SELECT DATABASE(),VERSION(),CURRENT_USER()')
                selected, version, account = cur.fetchone()
                cur.execute('SHOW TABLES')
                if (selected != schema or 'MariaDB' not in version or
                        not account.startswith('gw_fixture@') or cur.fetchall()):
                    raise RuntimeError('Expected empty isolated MariaDB schema')
            for file in [HERE / 'workflow_base.sql.in', *(MIGRATIONS / n for n in ORDER)]:
                docker('exec', '-i', name, 'sh', '-c',
                       'export MYSQL_PWD="$MARIADB_PASSWORD"; exec mariadb --user="$MARIADB_USER" --database="$MARIADB_DATABASE"',
                       input=file.read_text())
            conn.rollback()  # leave the initial read transaction before seeding
            seed(conn, app_password)
        with tempfile.TemporaryDirectory(prefix='goodwill-e2e-') as scratch:
            path = Path(scratch)
            private = path / '.streamlit'
            private.mkdir(mode=0o700)
            settings = private / 'secrets.toml'
            settings.write_text('[mysql]\n' + ''.join(f'{k} = {json.dumps(v)}\n' for k, v in config.items()))
            settings.chmod(0o600)
            login = path / 'login-password'
            login.write_text(app_password)
            login.chmod(0o600)
            yield config, path
    finally:
        docker('stop', '--time', '2', name)


if __name__ == '__main__':
    with fixture() as (_, path):
        print('Browser fixture directory:', path, flush=True)
        input('Press Enter when finished to discard the fixture.\n')
