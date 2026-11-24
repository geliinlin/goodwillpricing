"""Run schema, recovery, TLS and historical-attribution probes on a private Oracle server."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import mysql.connector
from mariadb_fixture import MIGRATIONS, ORDER
from mysql_fixture import mysql_fixture

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import dashboard_data
import database
import reports_data
import users_service
from desktop_packager.launcher import probe_connection
from scripts import check_configuration_schema, check_database_connection, check_guideline_schema

OUT = Path(os.environ.get('GW_TEST_RESULTS', ROOT / 'test-results/deployment-readiness'))
ADMIN = dict(user_id=1, user_type='Administrator', can_manage_users_config=True)


def certificates(path, hostname="localhost"):
    def openssl(*args):
        subprocess.run(['openssl', *args], cwd=path, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    openssl('req','-x509','-newkey','rsa:2048','-nodes','-keyout','ca.key','-out','ca.pem',
            '-subj','/CN=Fixture CA','-days','1')
    openssl('req','-newkey','rsa:2048','-nodes','-keyout','server.key','-out','server.csr',
            '-subj',f'/CN={hostname}')
    (path/'extensions').write_text(f'subjectAltName=DNS:{hostname}\nextendedKeyUsage=serverAuth\n')
    openssl('x509','-req','-in','server.csr','-CA','ca.pem','-CAkey','ca.key','-CAcreateserial',
            '-out','server.pem','-days','1','-extfile','extensions')


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tls = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        certificates(cls.tls)
        cls.config, cls.workspace = cls.enterClassContext(mysql_fixture([
            f'--ssl-ca={cls.tls}/ca.pem', f'--ssl-cert={cls.tls}/server.pem', f'--ssl-key={cls.tls}/server.key']))
        cls.evidence = {}
        OUT.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        (OUT/'mysql-verification.json').write_text(json.dumps(cls.evidence, indent=2, default=str)+'\n')

    def connect(self):
        return mysql.connector.connect(**self.config)

    def rows(self, sql):
        with self.connect() as conn, conn.cursor(dictionary=True) as cur:
            cur.execute(sql)
            return cur.fetchall()

    def test_schema(self):
        self.evidence['server'] = self.rows("""SELECT VERSION() version, @@version_comment edition,
                @@sql_mode sql_mode, @@transaction_isolation isolation_level, @@lower_case_table_names identifier_mode,
                @@character_set_database charset_name, @@collation_database collation_name""")[0]
        for name, fields, source, order in [
            ('tables','table_name, engine','tables','table_name'),
            ('columns','table_name,column_name,column_type,is_nullable,column_default,extra','columns','table_name,ordinal_position'),
            ('keys','table_name,constraint_name,column_name,referenced_table_name,referenced_column_name',
             'key_column_usage','table_name,constraint_name,ordinal_position'),
            ('indexes','table_name,index_name,non_unique,seq_in_index,column_name','statistics','table_name,index_name,seq_in_index'),
            ('checks','constraint_name,check_clause','check_constraints','constraint_name')]:
            scope = 'constraint_schema' if name=='checks' else 'table_schema'
            self.evidence[name] = self.rows(f'SELECT {fields} FROM information_schema.{source} WHERE {scope}=DATABASE() ORDER BY {order}')
        tables = {r['TABLE_NAME'].upper() for r in self.evidence['tables']}
        self.assertEqual(len(tables),15)
        self.assertEqual({r['ENGINE'] for r in self.evidence['tables']},{'InnoDB'})
        self.assertEqual(tables,{r['TABLE_NAME'].upper() for r in self.evidence['keys'] if r['CONSTRAINT_NAME']=='PRIMARY'})
        self.evidence['explain'] = {
            'tag_lookup': self.rows("EXPLAIN SELECT Decision_Id FROM PRICING_DECISION WHERE Decision_Tag_Number='TS26-100001'"),
            'recent_history': self.rows("EXPLAIN SELECT Decision_Id FROM PRICING_DECISION ORDER BY Decision_Datetime DESC, Decision_Id DESC LIMIT 7"),
            'attribute_lookup': self.rows("EXPLAIN SELECT Guideline_Id FROM PRICING_GUIDELINE WHERE Item_Type_Id=1 AND Category_Id=1 AND Brand_Id=1 AND Condition_Id=1 AND Guideline_Is_Active=1"),
        }
        self.assertEqual(self.evidence['explain']['tag_lookup'][0]['type'], 'const')
        for module in [check_database_connection, check_configuration_schema, check_guideline_schema]:
            with patch.object(module,'get_connection',self.connect), redirect_stdout(io.StringIO()):
                module.main()

    def test_constraint_enforcement(self):
        attempts = {
            'primary': ('INSERT INTO ADMINISTRATOR VALUES(1)',1062),
            'decision_branch': ('UPDATE PRICING_DECISION SET Decision_Branch_Id=99999 WHERE Decision_Id=1',1452),
            'foreign_key': ('INSERT INTO ADMINISTRATOR VALUES(99999)',1452),
            'unique_tag': ("""INSERT INTO PRICING_DECISION SELECT 999,Decision_Datetime,Decision_Reason,
                           Decision_Final_Price,Decision_Tag_Number,Guideline_Id,Staff_Id,Item_Id,Decision_Branch_Id
                           FROM PRICING_DECISION LIMIT 1""",1062),
            'check_flag': ('INSERT INTO USER_MODULE_ACCESS(User_Id,Can_Manage_Users_Config) VALUES(1,2)',3819),
            'decimal_overflow': ('UPDATE PRICING_GUIDELINE SET Guideline_Suggested_Price=1000000 WHERE Guideline_Id=1',1264),
            'category_type': ('UPDATE ITEM SET Item_Type_Id=2 WHERE Item_Id=1',1452),
        }
        result = {}
        for key,(sql,errno) in attempts.items():
            with self.subTest(key=key), self.connect() as conn, conn.cursor() as cur:
                with self.assertRaises(mysql.connector.Error) as err:
                    cur.execute(sql)
                self.assertEqual(err.exception.errno,errno)
                result[key] = errno
                conn.rollback()
        self.evidence['rejected_invalid_writes_errno'] = result

    def test_disconnect_rolls_back_and_next_request_recovers(self):
        with patch.dict(os.environ,{},clear=True), patch.object(database.st,'secrets',{'mysql':self.config}):
            conn = database.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute('SELECT CONNECTION_ID()')
                    connection_id = int(cur.fetchone()[0])
                    cur.execute("UPDATE ITEM SET Item_Description='uncommitted disconnect' WHERE Item_Id=1")
                    with self.connect() as killer, killer.cursor() as other:
                        other.execute(f'KILL CONNECTION {connection_id}')
                    with self.assertRaises(mysql.connector.Error):
                        cur.execute('SELECT 1')
            finally:
                conn.close()
            with socket.socket() as closed_port:
                closed_port.bind(('127.0.0.1',0))
                invalid = dict(self.config,port=closed_port.getsockname()[1])
                with patch.object(database.st,'secrets',{'mysql':invalid}), self.assertRaises(mysql.connector.Error):
                    database.get_connection()
            with database.get_connection() as recovered, recovered.cursor() as cur:
                cur.execute('SELECT Item_Description FROM ITEM WHERE Item_Id=1')
                self.assertEqual(cur.fetchone()[0],'Other fixture item')
        self.evidence['recovery'] = 'Lost connection rolled back pending write; refused endpoint failed; next request opened a new connection.'

    def test_branch_transfer(self):
        employee = dict(user_id=3,user_type='Store Staff')
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('INSERT INTO USER_MODULE_ACCESS(User_Id,Can_Manage_Users_Config) VALUES(1,1)')
            conn.commit()
        def view():
            row = dashboard_data.get_history(ADMIN,factory=self.connect)[0]
            printed = dashboard_data.get_reprint_receipt(employee,row['tag_number'],self.connect)
            per_branch = [reports_data.get_report_snapshot(ADMIN,branch_id=i,connection_factory=self.connect)
                          ['metrics']['items_priced'] for i in (1,2)]
            return dict(tag=row['tag_number'],branch=row['branch_name'],reprint_branch=printed['branch_id'],
                        final=row['final_price'],north_south=per_branch)
        before = view()
        users_service.change_staff_branch(ADMIN,3,1,expected_branch_id=2,connection_factory=self.connect)
        after = view()
        self.assertEqual([before['reprint_branch'],after['reprint_branch']],[2,2])
        self.assertEqual([before['north_south'],after['north_south']],[[0,1],[0,1]])
        self.assertEqual(before['tag'],after['tag'])
        self.evidence['historical_attribution'] = dict(before=before,after=after)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute('DELETE FROM USER_MODULE_ACCESS WHERE User_Id=1')
            conn.commit()

    def test_migration_replay(self):
        from shutil import which
        client = Path(os.environ.get('GW_TEST_MYSQLD') or which('mysqld')).resolve().with_name('mysql')
        counts = 'SELECT (SELECT COUNT(*) FROM CATEGORY) categories,(SELECT COUNT(*) FROM ITEM_TYPE) types_count'
        before = self.rows(counts)
        for filename in ORDER:
            subprocess.run([client,'--no-defaults','--protocol=TCP','--host=127.0.0.1',
                            f"--port={self.config['port']}",'--user=gw_fixture',self.config['database']],
                           env={**os.environ,'MYSQL_PWD':self.config['password']},
                           input=(MIGRATIONS/filename).read_bytes(),stdout=subprocess.DEVNULL,check=True)
        self.assertEqual(self.rows(counts),before)
        self.evidence['migration_replay'] = dict(files=ORDER,catalog_counts=before)

    def test_tls(self):
        settings = dict(self.config,host='localhost',ssl_ca=str(self.tls/'ca.pem'))
        env = {f'MYSQL_{k.upper()}':str(settings[k]) for k in ('host','port','user','password','database','ssl_ca')}
        with patch.dict(os.environ,env,clear=True), database.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            cipher = cur.fetchone()[1]
            self.assertTrue(cipher)
        # Make an unrelated test CA for the negative trust check on all platforms.
        unrelated = self.tls/'unrelated'
        unrelated.mkdir()
        certificates(unrelated)
        invalid = dict(settings,ssl_ca=str(unrelated/'ca.pem'))
        with patch.dict(os.environ,{},clear=True), patch.object(database.st,'secrets',{'mysql':invalid}):
            with self.assertRaises(mysql.connector.Error):
                database.get_connection()
        self.assertFalse(probe_connection(invalid))
        self.assertTrue(probe_connection(settings))
        wrong_name = dict(settings,host='127.0.0.1')
        with patch.dict(os.environ,{},clear=True), patch.object(database.st,'secrets',{'mysql':wrong_name}):
            with self.assertRaises(mysql.connector.Error):
                database.get_connection()
        self.evidence['tls'] = dict(cloud_verified_cipher=cipher,untrusted_ca_rejected=True,wrong_hostname_rejected=True,desktop_verified=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
