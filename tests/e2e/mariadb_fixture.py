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
    '006_master_descriptions.sql',
)


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, text=True,
                          stdout=subprocess.PIPE, **kwargs).stdout.strip()


def seed(conn, password):
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode()
    with conn.cursor() as cur:
        for uid, role, username, active in [
            (1, 'Administrator', 'admin_e2e', 1), (2, 'Store Staff', 'staff_e2e', 1),
            (3, 'Store Staff', 'other_e2e', 1), (4, 'Store Staff', 'inactive_e2e', 0)
        ]:
            cur.execute('INSERT INTO USER_ACCOUNT VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                        (uid, 'Fixture', username, username, f'{username}@example.invalid',
                         password_hash, active, role))
        cur.execute('INSERT INTO ADMINISTRATOR VALUES (1)')
        cur.execute("""INSERT INTO BRANCH (Branch_Id,Branch_Name,Branch_City)
                       VALUES (1,'Test North','Test North'),(2,'Test South','Test South')""")
        cur.execute('INSERT INTO STORE_STAFF VALUES (2,1),(3,2),(4,1)')
        cur.execute("""INSERT INTO BRAND (Brand_Id,Brand_Name,Brand_Description)
                       VALUES (1,'Generic','Standard brand tier')""")
        cur.execute("""INSERT INTO ITEM_CONDITION (Condition_Id,Condition_Name,Condition_Description)
                       VALUES (1,'Good','Gently used')""")
        cur.execute("""INSERT INTO PRICING_GUIDELINE VALUES
                       (1,1,1,1,1,12.50,1,NOW(),1),(2,1,1,1,1,4.50,0,NOW(),1)""")
        cur.execute("""INSERT INTO ITEM VALUES (1,'Other fixture item',CURDATE(),1,1,1,1)""")
        cur.execute("""INSERT INTO PRICING_DECISION VALUES
                       (1,NOW(),NULL,12.50,'TS26-100001',1,3,1)""")
    conn.commit()


@contextmanager
def fixture():
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
            for file in [HERE / 'workflow_base.sql', *(MIGRATIONS / n for n in ORDER)]:
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
