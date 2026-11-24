"""Compare the MariaDB baseline to the MySQL metadata captured by check_mysql_deployment."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re

import mysql.connector
from mariadb_fixture import fixture

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'test-results/deployment-readiness'


def inventory():
    # Include dynamic SQL construction sites. Counts refer to source locations, not query coverage.
    locations = {}
    for file in sorted([*ROOT.glob('*.py'), *(ROOT/'scripts').glob('*.py')]):
        if file.name.startswith('test'):
            continue
        tree = ast.parse(file.read_text())
        sites = set()
        for node in ast.walk(tree):
            call = isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('execute','executemany')
            sql = isinstance(node,ast.Constant) and isinstance(node.value,str) and re.match(
                r'^\s*(SELECT|INSERT|UPDATE|DELETE|SHOW|ALTER|CREATE|DROP)\s',node.value,re.I)
            if call or sql:
                sites.add(node.lineno)
        if sites:
            locations[str(file.relative_to(ROOT))] = sorted(sites)
    return locations


if __name__ == '__main__':
    if os.environ.get('GW_TEST_ENGINE','mariadb') != 'mariadb':
        raise SystemExit('Comparison requires the disposable MariaDB baseline')
    with fixture() as (config, _), mysql.connector.connect(**config) as conn, conn.cursor(dictionary=True) as cur:
        cur.execute('''SELECT VERSION() version, @@sql_mode sql_mode, @@tx_isolation isolation_level,
            @@lower_case_table_names identifier_mode, @@character_set_database charset_name, @@collation_database collation_name''')
        server = cur.fetchone()
        cur.execute("""SELECT DATA_TYPE type_name FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
                    AND TABLE_NAME='MASTER_DATA_AUDIT' AND COLUMN_NAME='Before_Json'""")
        server['audit_json_type'] = cur.fetchone()['type_name']
    oracle = json.loads((OUT/'mysql-verification.json').read_text())
    mysql_summary = dict(oracle['server'])
    mysql_summary['audit_json_type'] = next(r['COLUMN_TYPE'] for r in oracle['columns'] if r['COLUMN_NAME']=='Before_Json')
    evidence = dict(mysql=mysql_summary,mariadb=server,sql_source_lines=inventory(),
                    migration_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'migrations').glob('*.sql'))})
    (OUT/'engine-comparison.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print('Compared',server['version'],'and',mysql_summary['version'])
