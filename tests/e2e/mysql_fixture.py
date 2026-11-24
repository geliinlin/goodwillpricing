"""Private Oracle MySQL instance for deployment tests. Never opens an existing datadir."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time

import mysql.connector


@contextmanager
def mysql_fixture(tls_options=(), *, migrate_branch=True, initialize=None):
    from mariadb_fixture import HERE, MIGRATIONS, ORDER, seed

    server = os.environ.get('GW_TEST_MYSQLD') or shutil.which('mysqld')
    if not server:
        raise RuntimeError('Set GW_TEST_MYSQLD to the Oracle MySQL server executable')
    server = Path(server).resolve()
    client = server.with_name('mysql')
    version = subprocess.check_output([server, '--no-defaults', '--version'], text=True)
    if 'MySQL' not in version or 'MariaDB' in version:
        raise RuntimeError('Expected an Oracle MySQL executable')

    with tempfile.TemporaryDirectory(prefix='gwsql-', dir='/tmp') as scratch:
        root = Path(scratch).resolve()
        data = root / 'data'
        schema = 'gw_test_' + secrets.token_hex(8)
        password = secrets.token_urlsafe(24)
        application_password = secrets.token_urlsafe(18)
        sock = root / 'mysql.sock'
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        base = [server, '--no-defaults', f'--basedir={server.parent.parent}',
                f'--datadir={data}', f'--log-error={root / "server.log"}']
        subprocess.run([*base, '--initialize-insecure'], check=True)
        # mysqld runs the private bootstrap before accepting client traffic.
        init_file = root / 'bootstrap.sql'
        init_file.write_text(f"""ALTER USER 'root'@'localhost' IDENTIFIED BY '{secrets.token_urlsafe(32)}';
CREATE DATABASE {schema} CHARACTER SET utf8mb4;
CREATE USER 'gw_fixture'@'127.0.0.1' IDENTIFIED BY '{password}';
GRANT ALL PRIVILEGES ON {schema}.* TO 'gw_fixture'@'127.0.0.1';
""")
        init_file.chmod(0o600)
        proc = subprocess.Popen([*base, '--mysqlx=OFF', '--bind-address=127.0.0.1',
                                 f'--port={port}', f'--socket={sock}', f'--pid-file={root / "pid"}',
                                 f'--init-file={init_file}', '--skip-log-bin', '--skip-name-resolve', *tls_options])
        try:
            config = dict(host='127.0.0.1', port=port, user='gw_fixture', password=password,
                          database=schema, connection_timeout=3, ssl_disabled=False)
            for _ in range(80):
                if proc.poll() is not None:
                    raise RuntimeError('Disposable mysqld exited during startup')
                try:
                    conn = mysql.connector.connect(**config)
                    break
                except mysql.connector.Error as exc:
                    if exc.errno not in (2002, 2003, 2013):
                        raise RuntimeError(f"Disposable connection failed (errno={exc.errno}): {exc.msg}") from None
                    time.sleep(.25)
            else:
                raise RuntimeError('Disposable MySQL did not accept the fixture credentials')
            with conn, conn.cursor() as cur:
                cur.execute('SELECT @@datadir,DATABASE(),@@version_comment,@@port')
                location, selected, edition, actual_port = cur.fetchone()
                if (Path(location).resolve() != data or selected != schema or 'MySQL' not in edition
                        or actual_port != port):
                    raise RuntimeError('Disposable MySQL identity mismatch')
                cur.execute('SHOW TABLES')
                if cur.fetchall():
                    raise RuntimeError('Expected empty fixture schema')
            init_file.unlink()
            # The CLI handles stored procedures and DELIMITER exactly as released.
            def execute_sql(sql):
                subprocess.run([client, '--no-defaults', '--protocol=TCP', '--host=127.0.0.1', f'--port={port}',
                                '--user=gw_fixture', f'--database={schema}'], input=sql,
                               env={**os.environ, 'MYSQL_PWD':password}, stdout=subprocess.DEVNULL, check=True)
            if initialize:
                initialize(config, execute_sql)
            else:
                migrations = [name for name in ORDER if migrate_branch or not name.startswith(('007_', '008_'))]
                original = os.environ.get('GW_G3_DDL')
                if original:
                    from check_g3_schema import original_tables
                    execute_sql(original_tables(Path(original)))
                    execute_sql(b"INSERT INTO ITEM_TYPE VALUES (1,'Clothing'); "
                                b"INSERT INTO CATEGORY VALUES (1,'Shirts','Tops worn on the upper body');")
                else:
                    execute_sql((HERE/'workflow_base.sql.in').read_bytes())
                for name in migrations:
                    execute_sql((MIGRATIONS/name).read_bytes())
                with mysql.connector.connect(**config) as conn:
                    seed(conn, application_password, branch_snapshot=migrate_branch,
                         staff_role='Employee' if original and not migrate_branch else 'Store Staff')
            settings = root / '.streamlit'
            settings.mkdir(mode=0o700)
            (settings / 'secrets.toml').write_text('[mysql]\n' + ''.join(
                f'{k} = {json.dumps(v)}\n' for k,v in config.items()))
            (root / 'login-password').write_text(application_password)
            yield config, root
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=45)
