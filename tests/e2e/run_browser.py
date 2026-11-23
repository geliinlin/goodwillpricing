"""Run the real Streamlit workflow in Chrome against a fresh local MariaDB container.

Requires Docker, the project Python dependencies, Node, and Playwright. Set
GW_E2E_CHROME when using a system Chrome and NODE_PATH if Playwright is external.
Run from the project root: .venv/bin/python tests/e2e/run_browser.py
"""
import json
import os
from decimal import Decimal
from pathlib import Path
import subprocess
import sys

import mysql.connector

from mariadb_fixture import fixture

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parents[1] / 'test-results' / 'staff-workflow'


def run():
    with fixture() as (config, directory):
        env = {**os.environ, 'GW_E2E_DIR': str(directory), 'GW_E2E_PYTHON': sys.executable}
        subprocess.run(['node', '--test', str(HERE / 'staff_workflow.cjs')], env=env, check=True)
        tags = json.loads((RESULTS / 'browser-tags.json').read_text())
        with mysql.connector.connect(**config) as conn, conn.cursor() as cursor:
            cursor.execute('SELECT DATABASE()')
            assert cursor.fetchone() == (config['database'],)
            cursor.execute('SELECT COUNT(*) FROM ITEM')
            assert cursor.fetchone() == (3,), 'Expected two new items and the seed item'
            cursor.execute("""
                SELECT d.Decision_Tag_Number, i.Item_Description, d.Decision_Final_Price,
                       d.Decision_Reason, d.Guideline_Id, d.Staff_Id, g.Guideline_Suggested_Price
                FROM PRICING_DECISION d
                JOIN ITEM i ON i.Item_Id=d.Item_Id
                JOIN PRICING_GUIDELINE g ON g.Guideline_Id=d.Guideline_Id
                ORDER BY d.Decision_Id
            """)
            assert cursor.fetchall() == [
                ('TS26-100001', 'Other fixture item', Decimal('12.50'), None, 1, 3, Decimal('12.50')),
                (tags['acceptedTag'], tags['run'] + '-accept', Decimal('12.50'), None, 1, 2, Decimal('12.50')),
                (tags['overriddenTag'], tags['run'] + '-override', Decimal('9.00'), 'Visible Wear', 1, 2, Decimal('12.50')),
            ], 'Unexpected linked rows after double-click, refresh and reprint'
        print('Database postconditions passed for both browser submissions and the existing seed record.')


if __name__ == '__main__':
    run()
