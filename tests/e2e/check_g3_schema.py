"""Compare the two base schemas on separate fresh servers, then exercise the G3 upgrade."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import mysql.connector

from mariadb_fixture import HERE, MIGRATIONS, ORDER
from mysql_fixture import mysql_fixture

DIGEST = '3bc367df72f91e32007492b47908bc3d1017d11b778390cbd96ebf9eb5f63506'
OUT = Path(os.environ.get('GW_TEST_RESULTS', HERE.parents[1]/'test-results/final-integrity'))


def original_tables(path):
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != DIGEST:
        raise ValueError('Expected the reviewed eleven-table G3 SQL script')
    ddl = re.findall(rb'(?ms)^CREATE TABLE `[A-Z_]+` \(.*?^\) ENGINE=InnoDB;', source)
    assert len(ddl) == 11
    return b'\n'.join(ddl)


def rows(config, sql, params=()):
    with mysql.connector.connect(**config) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def inventory(config):
    sections = {
        'columns': ('COLUMNS','TABLE_NAME,COLUMN_NAME','ORDINAL_POSITION,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA'),
        'indexes': ('STATISTICS','TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX','COLUMN_NAME,NON_UNIQUE,INDEX_TYPE'),
        'keys': ('KEY_COLUMN_USAGE','TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION',
                 'COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME'),
        'foreign_key_actions': ('REFERENTIAL_CONSTRAINTS','TABLE_NAME,CONSTRAINT_NAME','UPDATE_RULE,DELETE_RULE'),
        'checks': ('CHECK_CONSTRAINTS','CONSTRAINT_NAME','CHECK_CLAUSE'),
    }
    result = {}
    for section,(catalog,keys,fields) in sections.items():
        scope = 'TABLE_SCHEMA' if section in ('columns','indexes','keys') else 'CONSTRAINT_SCHEMA'
        data = rows(config,f'SELECT {keys},{fields} FROM information_schema.{catalog} WHERE {scope}=DATABASE() ORDER BY {keys}')
        n = len(keys.split(','))
        result[section] = {'/'.join(map(str,row[:n])): list(row[n:]) for row in data}
    return result


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    source = Path(os.environ['GW_G3_DDL'])
    evidence = {'source_sha256':DIGEST}
    baseline = {}
    def reconstructed(config, execute):
        execute((HERE/'workflow_base.sql.in').read_bytes())
        baseline.update(inventory(config))
    with mysql_fixture(initialize=reconstructed):
        pass

    def original(config, execute):
        execute(original_tables(source))
        schema = inventory(config)
        evidence['server'] = rows(config,'SELECT VERSION(),DATABASE()')[0]
        evidence['comparison'] = {}
        for section in schema:
            left, right = baseline[section], schema[section]
            evidence['comparison'][section] = {
                key:{'reconstructed':left.get(key),'g3':right.get(key)}
                for key in sorted(left.keys() | right.keys()) if left.get(key) != right.get(key)}
        assert {k.split('/')[0] for k in schema['columns']} == {k.split('/')[0] for k in baseline['columns']}
        assert len({k.split('/')[0] for k in schema['columns']})==11
        assert baseline['columns'].keys()==schema['columns'].keys()
        # Populate only auth and subtype rows. The original has no catalog seed.
        execute(b"""INSERT INTO USER_ACCOUNT (User_Id,User_First_Name,User_Last_Name,User_Username,
                    User_Password_Hash,User_Email,User_Type) VALUES
                    (1,'A','Admin','a','unused','admin@example.invalid','Administrator'),
                    (2,'B','Employee','b','unused','staff@example.invalid','Employee');
                    INSERT INTO ADMINISTRATOR VALUES(1);
                    INSERT INTO BRANCH VALUES(1,'Test','1 Test St','Test','MI','48800');
                    INSERT INTO STORE_STAFF VALUES(2,1);""")
        with mysql.connector.connect(**config) as conn, conn.cursor() as cur:
            try:
                cur.execute("UPDATE USER_ACCOUNT SET User_Type='Store Staff' WHERE User_Id=2")
            except mysql.connector.Error as err:
                assert err.errno == 3819
                evidence['original_rejects_application_role_errno'] = err.errno
            else:
                raise AssertionError('Original did not enforce its role CHECK')
            conn.rollback()
        for name in ORDER:
            if name == '004_category_item_type.sql':
                # An actual unassigned legacy category still needs its fallback.
                execute(b"INSERT INTO CATEGORY(Category_Id,Category_Name) VALUES (9001,'Unassigned fixture');")
                try:
                    execute((MIGRATIONS/name).read_bytes())
                except subprocess.CalledProcessError:
                    assert not rows(config, """SELECT COLUMN_NAME FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='CATEGORY' AND COLUMN_NAME='Item_Type_Id'""")
                    evidence['unresolved_category_stops_before_ddl'] = True
                else:
                    raise AssertionError('Migration accepted an unresolved legacy category')
                execute(b'DELETE FROM CATEGORY WHERE Category_Id=9001;')
            execute((MIGRATIONS/name).read_bytes())
            if name.startswith('006'):
                evidence['role_after_006'] = rows(config,'SELECT User_Type FROM USER_ACCOUNT WHERE User_Id=2')[0][0]
                assert evidence['role_after_006'] == 'Employee'
        assert rows(config,'SELECT User_Id,User_Type FROM USER_ACCOUNT ORDER BY User_Id') == [(1,'Administrator'),(2,'Store Staff')], schema['checks']
        assert rows(config,'SELECT Staff_Id,Branch_Id FROM STORE_STAFF') == [(2,1)]
        evidence['migrations'] = list(ORDER)
        for name in ORDER:
            execute((MIGRATIONS/name).read_bytes())
        evidence['final'] = inventory(config)
        evidence['catalog_after_replay'] = rows(config,'SELECT (SELECT COUNT(*) FROM ITEM_TYPE),COUNT(*) FROM CATEGORY')[0]
        evidence['accounts_after_replay'] = rows(config,'SELECT User_Id,User_Type FROM USER_ACCOUNT ORDER BY User_Id')
        # Simulate the valid intermediate state after an interrupted 008.
        execute(b"""ALTER TABLE USER_ACCOUNT DROP CONSTRAINT chk_user_type,
                    ADD CONSTRAINT chk_gw_role_transition CHECK (User_Type IN ('Administrator','Employee','Store Staff'));
                    UPDATE USER_ACCOUNT SET User_Type='Employee' WHERE User_Id=2;""")
        execute((MIGRATIONS/'008_align_g3_staff_role.sql').read_bytes())
        assert inventory(config) == evidence['final']
        assert rows(config,'SELECT User_Id,User_Type FROM USER_ACCOUNT ORDER BY User_Id') == evidence['accounts_after_replay']
        evidence['interrupted_008_recovery'] = True
        with mysql.connector.connect(**config) as conn, conn.cursor() as cur:
            try:
                cur.execute("UPDATE USER_ACCOUNT SET User_Type='Employee' WHERE User_Id=2")
            except mysql.connector.Error as err:
                assert err.errno == 3819
                evidence['final_rejects_legacy_role_errno'] = err.errno
            else:
                raise AssertionError('Final G3 role constraint failed to enforce the new vocabulary')
            conn.rollback()
    with mysql_fixture(initialize=original):
        pass
    (OUT/'g3-comparison.json').write_text(json.dumps(evidence,indent=2,default=str)+'\n')
    print('PASS: original G3 comparison, migration, replay and interrupted-role recovery')


if __name__=='__main__':
    run()
