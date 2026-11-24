"""Exercise migration 007 and a browser-driven transfer on a private MySQL server."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

import mysql.connector

from mariadb_fixture import HERE, MIGRATIONS
from mysql_fixture import mysql_fixture

OUT = Path(os.environ.get('GW_TEST_RESULTS', HERE.parents[1] / 'test-results/final-integrity')).resolve()
GROUP_SQL = """SELECT COUNT(*) AS items, ROUND(AVG(d.Decision_Final_Price),2) AS avg,
    ROUND(100*AVG(d.Decision_Final_Price<>g.Guideline_Suggested_Price),1) AS pct,
    COUNT(DISTINCT d.Staff_Id) AS staff
    FROM PRICING_DECISION d JOIN PRICING_GUIDELINE g USING(Guideline_Id)"""


def snapshot():
    directory = Path(os.environ['GW_E2E_DIR'])
    config = tomllib.loads((directory/'.streamlit/secrets.toml').read_text())['mysql']
    assert config['host'] == '127.0.0.1' and config['user'] == 'gw_fixture'
    assert re.fullmatch(r'gw_test_[0-9a-f]{16}', config['database'])
    with mysql.connector.connect(**config) as conn, conn.cursor(dictionary=True) as cur:
        cur.execute('SELECT Branch_Id AS current_branch FROM STORE_STAFF WHERE Staff_Id=2')
        state = cur.fetchone()
        cur.execute("""SELECT d.Decision_Tag_Number AS tag, d.Decision_Final_Price AS price,
                    d.Staff_Id AS staff, d.Decision_Branch_Id AS branch_id,
                    COALESCE(b.Branch_Name,'Unknown') AS branch FROM PRICING_DECISION d
                    LEFT JOIN BRANCH b ON b.Branch_Id=d.Decision_Branch_Id ORDER BY d.Decision_Id""")
        state['decisions'] = cur.fetchall()
        cur.execute("""SELECT (SELECT COUNT(*) FROM PRICING_GUIDELINE WHERE Guideline_Is_Active=1) AS guides,
                    (SELECT COUNT(*) FROM USER_ACCOUNT WHERE User_Is_Active=1) AS users,
                    COUNT(*) AS decisions, SUM(d.Decision_Final_Price<>g.Guideline_Suggested_Price) AS overrides
                    FROM PRICING_DECISION d JOIN PRICING_GUIDELINE g USING(Guideline_Id)""")
        row = cur.fetchone()
        state['dashboard'] = [int(row[k]) for k in ('guides','users','decisions','overrides')]
        state['reports'] = []
        for label, query, params in [('Test North',' WHERE d.Decision_Branch_Id=%s',(1,)),
                                     ('Test South',' WHERE d.Decision_Branch_Id=%s',(2,)),
                                     ('Unknown',' WHERE d.Decision_Branch_Id IS NULL',()),
                                     ('All branches','',())]:
            cur.execute(GROUP_SQL + query, params)
            report = cur.fetchone()
            if report['items']:
                report.update(branch=label, rate=f"{report.pop('pct'):.1f}%")
                state['reports'].append(report)
    return state


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    script = MIGRATIONS/'007_add_decision_branch.sql'
    server = Path(os.environ.get('GW_TEST_MYSQLD') or shutil.which('mysqld')).resolve()
    with mysql_fixture(migrate_branch=False) as (config, directory):
        def read(query):
            with mysql.connector.connect(**config) as conn, conn.cursor(dictionary=True) as cur:
                cur.execute(query)
                return cur.fetchall()

        query = 'SELECT * FROM PRICING_DECISION ORDER BY Decision_Id'
        before = read(query)
        assert len(before) == 1 and 'Decision_Branch_Id' not in before[0]
        audit = {'server': read('SELECT VERSION() AS version, DATABASE() AS isolated_schema')[0],
                 'before': before}
        def apply(migration):
            subprocess.run([server.with_name('mysql'),'--no-defaults','--protocol=TCP','--host=127.0.0.1',
                            f"--port={config['port']}",'--user=gw_fixture', f"--database={config['database']}"],
                           input=migration.read_bytes(), env={**os.environ, 'MYSQL_PWD':config['password']}, check=True)

        for stage in ('first_application', 'repeat_application'):
            apply(script)
            rows = read(query)
            assert rows == [dict(row, Decision_Branch_Id=None) for row in before]
            audit[stage] = rows
        apply(MIGRATIONS/'008_align_g3_staff_role.sql')
        with mysql.connector.connect(**config) as conn, conn.cursor() as cur:
            cur.execute('INSERT INTO USER_MODULE_ACCESS(User_Id,Can_Manage_Users_Config) VALUES(1,1)')
            conn.commit()
        env = {**os.environ,'GW_E2E_DIR':str(directory),'GW_E2E_PYTHON':sys.executable,'GW_TEST_RESULTS':str(OUT)}
        subprocess.run(['node','--test',str(HERE/'branch_integrity.cjs')],env=env,check=True)
        # Reapplying after new writes must also preserve known snapshots and all tag values.
        populated = read(query)
        apply(script)
        assert read(query) == populated and populated[0] == audit['first_application'][0]
        audit['replay_after_transfer'] = populated
        (OUT/'migration-replay.json').write_text(json.dumps(audit, indent=2, default=str)+'\n')


if __name__ == '__main__':
    if sys.argv[1:] == ['snapshot']:
        print(json.dumps(snapshot(),default=str))
    else:
        run()
