"""Read-only release preflight used before the Docker application serves traffic."""
import sys

import mysql.connector

from database import get_connection


def check_schema(conn):
    # Verify runtime columns from the feature migrations without reading user rows.
    with conn.cursor() as cur:
        cur.execute("""SELECT a.Can_Manage_Users_Config, p.Session_Version, t.Consumed_At,
                   m.Action_Type, c.Item_Type_Id, c.Category_Is_Active, i.Item_Type_Description,
                   b.Branch_Description, d.Decision_Branch_Id
                   FROM USER_MODULE_ACCESS a, USER_PASSWORD_STATE p, USER_PASSWORD_TOKEN t,
                   MASTER_DATA_AUDIT m, CATEGORY c, ITEM_TYPE i, BRANCH b, PRICING_DECISION d LIMIT 0""")
        cur.fetchall()
        cur.execute("""SELECT k.REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE k
                   WHERE k.TABLE_SCHEMA=DATABASE() AND k.TABLE_NAME='PRICING_DECISION'
                   AND k.COLUMN_NAME='Decision_Branch_Id' AND k.REFERENCED_TABLE_SCHEMA=DATABASE()
                   AND k.REFERENCED_TABLE_NAME='BRANCH' AND k.REFERENCED_COLUMN_NAME='Branch_Id'""")
        if not cur.fetchall():
            raise ValueError('Missing decision branch constraint')
        cur.execute("""SELECT c.CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS c
                   JOIN information_schema.TABLE_CONSTRAINTS t USING(CONSTRAINT_SCHEMA,CONSTRAINT_NAME)
                   WHERE t.TABLE_SCHEMA=DATABASE() AND t.TABLE_NAME='USER_ACCOUNT'
                   AND t.CONSTRAINT_TYPE='CHECK' AND t.CONSTRAINT_NAME IN ('chk_user_type','chk_gw_role_transition')""")
        if any('employee' in clause.lower() for (clause,) in cur.fetchall()):
            raise ValueError('Legacy employee role constraint')


def main():
    try:
        with get_connection() as conn:
            check_schema(conn)
    except (ValueError, mysql.connector.Error, RuntimeError, OSError) as exc:
        if isinstance(exc, ValueError) or getattr(exc, 'errno', None) in (1054, 1146):
            message = 'Schema is incompatible. Verify the selected database and complete migrations 001–008 before starting.'
        else:
            message = 'Database preflight failed. Verify runtime grants, network, TLS and external connection settings.'
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
