"""Read-only check for Goodwill Configuration migration 003.
Run from the project root: python -m scripts.check_configuration_schema
"""
from __future__ import annotations

from database import get_connection

REQUIRED_COLUMNS = {
    'ITEM_TYPE': 'Item_Type_Is_Active',
    'CATEGORY': 'Category_Is_Active',
    'BRAND': 'Brand_Is_Active',
    'ITEM_CONDITION': 'Condition_Is_Active',
    'BRANCH': 'Branch_Is_Active',
}


def main():
    conn = get_connection()
    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute('SELECT DATABASE() AS db')
        schema = cur.fetchone()['db']
        if not schema:
            raise RuntimeError('No MySQL schema is selected.')
        print('Connected schema:', schema)
        missing = []
        for table, column in REQUIRED_COLUMNS.items():
            cur.execute('''SELECT COUNT(*) AS n FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s''',
                (schema, table, column))
            found = cur.fetchone()['n'] > 0
            print(f'{table}.{column}: {"OK" if found else "MISSING"}')
            if not found:
                missing.append(f'{table}.{column}')
        cur.execute('''SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME='MASTER_DATA_AUDIT' ''', (schema,))
        audit_ok = cur.fetchone()['n'] > 0
        print('MASTER_DATA_AUDIT:', 'OK' if audit_ok else 'MISSING')
        if not audit_ok:
            missing.append('MASTER_DATA_AUDIT')
        if missing:
            raise RuntimeError('Run migrations/003_master_data_management.sql before starting Configuration.')
        print('Configuration schema: OK (read-only)')
    finally:
        if cur is not None:
            cur.close()
        conn.close()


if __name__ == '__main__':
    main()
