"""Logical backup, fresh restore, interrupted upgrade and measured service operations."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

import mysql.connector
from check_g3_schema import inventory, original_tables
from mariadb_fixture import HERE, MIGRATIONS, ORDER, seed
from mysql_fixture import mysql_fixture

ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
import dashboard_data
import item_search
import pricing_decision as pricing
import reports_data
from scripts.check_release_schema import check_schema

OUT = ROOT/'test-results/production-acceptance'
ADMIN = {'user_type':'Administrator','user_id':1}
TIMER = time.perf_counter


def query(config, sql, params=()):
    with mysql.connector.connect(**config) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fingerprint(config, columns=None):
    table_columns = columns or {name:[row[0] for row in query(config, f'SHOW COLUMNS FROM `{name}`')]
                                for name, in query(config,'SHOW TABLES')}
    counts, digests = {}, {}
    for table, fields in table_columns.items():
        data = query(config,f"SELECT {','.join(fields)} FROM `{table}` ORDER BY 1")
        counts[table] = len(data)
        digests[table] = hashlib.sha256(json.dumps(data,default=str).encode()).hexdigest()
    return table_columns, counts, digests


def populate(config, execute):
    execute(original_tables(Path(os.environ['GW_G3_DDL'])))
    execute(b"INSERT INTO ITEM_TYPE VALUES(1,'Clothing'); INSERT INTO CATEGORY VALUES(1,'Shirts','Synthetic');")
    with mysql.connector.connect(**config) as conn:
        seed(conn, os.urandom(18).hex(),branch_snapshot=False,staff_role='Employee')
        with conn.cursor() as cur:
            for i in range(2,65):
                cur.execute('INSERT INTO ITEM VALUES (%s,%s,CURDATE(),1,1,1,1)',(i,f'Fixture item {i}'))
                cur.execute('INSERT INTO PRICING_DECISION VALUES (%s,NOW(),NULL,12.50,%s,1,%s,%s)',
                            (i,f'LEG-{i:06}',2+i%2,i))
        conn.commit()


def draft(staff, description):
    return dict(staff_id=staff,branch_id=staff-1,item_type_id=1,category_id=1,brand_id=1,
                condition_id=1,guideline_id=1,suggested_price='12.50',
                item_received_date=date.today(),item_description=description)


def counts(config):
    return query(config,'SELECT (SELECT COUNT(*) FROM ITEM),COUNT(*),COUNT(DISTINCT Item_Id),'
                 'COUNT(DISTINCT Decision_Tag_Number) FROM PRICING_DECISION')[0]


def verify_links(config):
    # Read the actual FK definitions, including composite keys.
    rows=query(config,"""SELECT TABLE_NAME,CONSTRAINT_NAME,REFERENCED_TABLE_NAME,COLUMN_NAME,REFERENCED_COLUMN_NAME
                  FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
                  ORDER BY TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION""")
    groups={}
    for child,key,parent,col,target in rows:
        groups.setdefault((child,key,parent),[]).append((col,target))
    for (child,key,parent),pairs in groups.items():
        on=' AND '.join(f'c.`{col}`=p.`{dest}`' for col,dest in pairs)
        populated=' AND '.join(f'c.`{col}` IS NOT NULL' for col,_ in pairs)
        assert query(config,f'SELECT COUNT(*) FROM `{child}` c LEFT JOIN `{parent}` p ON {on} '
                     f'WHERE {populated} AND p.`{pairs[0][1]}` IS NULL')[0][0]==0,key
    assert len(set(counts(config)))==1
    return len(groups)


def failures(config):
    connect=lambda: mysql.connector.connect(**config)
    result={}
    for committed in (True,False):
        class LostReply:
            def __init__(self):
                self.connection=connect()
            def __getattr__(self,name):
                return getattr(self.connection,name)
            def commit(self):
                if committed:
                    self.connection.commit()
                self.connection.close()
                raise ConnectionError('synthetic lost response')
        before=counts(config)[0]
        try:
            pricing.save_pricing_decision(2,draft(2,'Uncertain commit'),'12.50',None,LostReply)
        except pricing.PricingSaveError as exc:
            assert exc.uncertain_tag
            found=pricing.submitted_tag_exists(exc.uncertain_tag,connect)
            assert found==committed and counts(config)[0]==before+int(committed)
        else:
            raise AssertionError('Expected lost acknowledgement')
        if not found:
            pricing.save_pricing_decision(2,draft(2,'Uncertain commit'),'12.50',None,connect)
        assert counts(config)[0]==before+1
        result['ack_lost_after_commit' if committed else 'disconnect_before_commit']={
            'candidate_found_on_writer':found,'retry_count':int(not found),'net_decisions':1}
    with connect() as holder, holder.cursor() as cur:
        cur.execute('SELECT Staff_Id FROM STORE_STAFF WHERE Staff_Id=2 FOR UPDATE')
        cur.fetchall()
        def short_wait():
            conn=connect()
            with conn.cursor() as c:
                c.execute('SET SESSION innodb_lock_wait_timeout=1')
            return conn
        before=counts(config)
        start=TIMER()
        try:
            pricing.save_pricing_decision(2,draft(2,'Blocked write'),'12.50',None,short_wait)
        except pricing.PricingSaveError as exc:
            assert exc.errno==1205
            result['row_lock_timeout_ms']=round((TIMER()-start)*1000,2)
        else:
            raise AssertionError('Expected row lock timeout')
        assert counts(config)==before
        holder.rollback()
    pricing.save_pricing_decision(2,draft(2,'After lock timeout'),'12.50',None,connect)
    with connect() as victim, victim.cursor() as cur:
        cur.execute('SELECT CONNECTION_ID()')
        ident=int(cur.fetchone()[0])
        cur.execute("UPDATE ITEM SET Item_Description='transaction interrupted' WHERE Item_Id=1")
        with connect() as supervisor, supervisor.cursor() as killer:
            killer.execute(f'KILL CONNECTION {ident}')
        try:
            victim.commit()
        except Exception as exc:
            # The C extension raises MySQLInterfaceError directly from commit().
            assert getattr(exc,'errno',None) in (2006,2013,2055),type(exc).__name__
        else:
            raise AssertionError('Terminated transaction committed')
    assert query(config,'SELECT Item_Description FROM ITEM WHERE Item_Id=1')==[('Other fixture item',)]
    result['terminated_transaction_rolled_back']=True
    # Time the production submission lock separately from end-to-end save latency.
    with connect() as owner, owner.cursor() as held:
        held.execute('SELECT GET_LOCK(%s,0)',(pricing.LOCK_NAME,))
        assert held.fetchone()[0]==1
        waiting=threading.Event()
        def wait_on_writer():
            with connect() as waiter, waiter.cursor() as cur:
                start=TIMER()
                waiting.set()
                cur.execute('SELECT GET_LOCK(%s,5)',(pricing.LOCK_NAME,))
                assert cur.fetchone()[0]==1
                return round((TIMER()-start)*1000,2)
        with ThreadPoolExecutor(1) as pool:
            pending=pool.submit(wait_on_writer)
            assert waiting.wait(3)
            time.sleep(.25)
            held.execute('SELECT RELEASE_LOCK(%s)',(pricing.LOCK_NAME,))
            assert held.fetchone()[0]==1
            result['controlled_submission_lock_wait_ms']=pending.result()
    with closing(socket.socket()) as listener:
        listener.bind(('127.0.0.1',0))
        listener.listen(1)
        with ThreadPoolExecutor(1) as pool:
            def stall():
                with listener.accept()[0] as accepted:
                    accepted.settimeout(4)
                    try:
                        accepted.recv(1)
                    except TimeoutError:
                        pass
            future=pool.submit(stall)
            start=TIMER()
            try:
                mysql.connector.connect(**{**config,'port':listener.getsockname()[1],'connection_timeout':1})
            except mysql.connector.Error as exc:
                result['handshake_timeout']={'errno':exc.errno,'elapsed_ms':round((TIMER()-start)*1000,2)}
                assert TIMER()-start < 3.8
            else:
                raise AssertionError('Stalled connection unexpectedly succeeded')
            future.result()
    return result


def benchmark(config):
    connect=lambda:mysql.connector.connect(**config)
    records=[]
    for target,guides in ((100,10),(1000,100),(10000,1000)):
        n=counts(config)[0]
        last=query(config,'SELECT MAX(Guideline_Id) FROM PRICING_GUIDELINE')[0][0]
        with connect() as conn, conn.cursor() as cur:
            cur.executemany('''INSERT INTO PRICING_GUIDELINE (Guideline_Id,Guideline_Suggested_Price,
                        Guideline_Last_Updated,Guideline_Is_Active,Category_Id,Brand_Id,Condition_Id,Admin_Id,Item_Type_Id)
                        VALUES(%s,12.50,NOW(),0,1,1,1,1,1)''',[(i,) for i in range(last+1,guides+1)])
            for first in range(n+1,target+1,500):
                ids=range(first,min(first+500,target+1))
                cur.executemany('INSERT INTO ITEM VALUES(%s,%s,CURDATE(),1,1,1,1)',[(i,f'Volume {i}') for i in ids])
                cur.executemany('INSERT INTO PRICING_DECISION VALUES(%s,NOW(),NULL,12.50,%s,1,%s,%s,%s)',
                                [(i,f'VOL-{i}',2+i%2,i,1+i%2) for i in ids])
            conn.commit()
        def submit(i):
            staff=2+i%2
            return pricing.save_pricing_decision(staff,draft(staff,f'Load {i}'),'12.50',None,connect)
        operations={
            'guideline_search':lambda:item_search.get_active_price_guideline(1,1,1,1,connect),
            'history_branch':lambda:dashboard_data.get_filtered_history(ADMIN,branch_id=1,factory=connect),
            'dashboard':lambda:dashboard_data.get_admin_snapshot(ADMIN,connect),
            'report':lambda:reports_data.get_report_snapshot(ADMIN,branch_id=1,connection_factory=connect),
            'single_submit':lambda:submit(0)}
        timings={}
        for name,operation in operations.items():
            times=[]
            for _ in range(7):
                start=TIMER()
                operation()
                times.append(round((TIMER()-start)*1000,2))
            timings[name]={'median_ms':statistics.median(times),'max_ms':max(times),'samples_ms':times}
        gate=threading.Barrier(2)
        def concurrent(i):
            gate.wait(timeout=5)
            start=TIMER()
            receipt=submit(i)
            return receipt.tag_number,round((TIMER()-start)*1000,2)
        with ThreadPoolExecutor(2) as pool:
            tags,times=zip(*pool.map(concurrent,(0,1)))
        assert len(set(tags))==2 and counts(config)==(target+9,)*4
        plans={}
        statements={'active_guideline':(item_search.ACTIVE_GUIDELINE_SQL,(1,1,1,1)),
                    'filtered_history':('SELECT Decision_Id FROM PRICING_DECISION WHERE Decision_Branch_Id=%s ORDER BY Decision_Datetime DESC,Decision_Id DESC LIMIT 200',(1,)),
                    'tag_lookup':('SELECT Item_Id FROM PRICING_DECISION WHERE Decision_Tag_Number=%s',(tags[0],))}
        for key,(sql,params) in statements.items():
            plans[key]=json.loads(query(config,'EXPLAIN FORMAT=JSON '+sql,params)[0][0])
        records.append(dict(initial_decisions=target,guidelines=guides,timings=timings,
                            concurrent_pair_ms=times,query_plans=plans))
    return records


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    result={}
    def checkpoint():
        (OUT/'recovery.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
    with tempfile.TemporaryDirectory() as tmp:
        backup=Path(tmp)/'synthetic.sql'
        with mysql_fixture(initialize=populate) as (config,_):
            cols,original_counts,hashes=fingerprint(config)
            ddl=inventory(config)
            prices=query(config,'SELECT * FROM PRICING_DECISION ORDER BY Decision_Id')
            users=query(config,'SELECT * FROM USER_ACCOUNT ORDER BY User_Id')
            bin_dir=Path(os.environ.get('GW_TEST_MYSQLD') or shutil.which('mysqld')).resolve().parent
            start=TIMER()
            with backup.open('wb') as sink:
                subprocess.run([bin_dir/'mysqldump','--no-defaults','--protocol=TCP','-h127.0.0.1',
                                f"-P{config['port']}",'-ugw_fixture','--single-transaction','--routines','--events',
                                '--triggers','--hex-blob','--no-tablespaces','--set-gtid-purged=OFF',config['database']],
                               env={**os.environ,'MYSQL_PWD':config['password']},stdout=sink,check=True)
            result['backup']={'seconds':round(TIMER()-start,3),'sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),
                              'bytes':backup.stat().st_size,'source_counts':original_counts,'table_sha256':hashes}
            with mysql.connector.connect(**config) as conn,conn.cursor() as cur:
                cur.execute("INSERT INTO ITEM VALUES(65,'After backup',CURDATE(),1,1,1,1)")
                cur.execute("INSERT INTO PRICING_DECISION VALUES(65,NOW(),NULL,12.50,'POST-BACKUP-65',1,2,65)")
                conn.commit()
            assert counts(config)==(65,)*4
        image=backup.read_bytes()
        assert hashlib.sha256(image).hexdigest()==result['backup']['sha256']
        def restore(config,execute):
            start=TIMER()
            execute(image)
            assert fingerprint(config,cols)[1:]==(original_counts,hashes)
            assert inventory(config)==ddl
            result.setdefault('restores',[]).append({'seconds':round(TIMER()-start,3),'all_core_rows_and_schema_equal':True})
        def broken(config,execute):
            restore(config,execute)
            for name in ORDER:
                if name.startswith('007'):
                    execute(b'ALTER TABLE PRICING_DECISION ADD Decision_Branch_Id BIGINT NULL;')
                    try:
                        execute((MIGRATIONS/name).read_bytes())
                    except subprocess.CalledProcessError:
                        assert query(config,'SELECT * FROM PRICING_DECISION ORDER BY Decision_Id')==[r+(None,) for r in prices]
                        result['forced_007_failure_preserved_history']=True
                        return
                    raise AssertionError('Malformed column was accepted')
                execute((MIGRATIONS/name).read_bytes())
        with mysql_fixture(initialize=broken):
            pass
        checkpoint()
        recovery_start=TIMER()
        def recover(config,execute):
            restore(config,execute)
            start=TIMER()
            for name in ORDER:
                execute((MIGRATIONS/name).read_bytes())
            first=inventory(config)
            for name in ORDER:
                execute((MIGRATIONS/name).read_bytes())
            assert inventory(config)==first
            result['migrate_and_replay_seconds']=round(TIMER()-start,3)
        with mysql_fixture(initialize=recover) as (config,_):
            assert query(config,'SELECT * FROM PRICING_DECISION ORDER BY Decision_Id')==[r+(None,) for r in prices]
            assert counts(config)==(64,)*4 and not query(config,"SELECT 1 FROM PRICING_DECISION WHERE Decision_Tag_Number='POST-BACKUP-65'")
            result['post_snapshot_commits_missing_at_restore']=1
            assert query(config,'SELECT * FROM USER_ACCOUNT ORDER BY User_Id')==[
                tuple('Store Staff' if v=='Employee' else v for v in r) for r in users]
            for table in ('STORE_STAFF','ADMINISTRATOR','BRANCH','BRAND','ITEM_CONDITION','ITEM','PRICING_GUIDELINE'):
                assert fingerprint(config,{table:cols[table]})[2][table]==hashes[table]
            assert query(config,'SELECT COUNT(*) FROM USER_MODULE_ACCESS')==[(0,)]
            result['foreign_keys_checked']=verify_links(config)
            with mysql.connector.connect(**config) as conn:
                check_schema(conn)
            result['verified_recovery_seconds']=round(TIMER()-recovery_start,3)
            result['restored_decisions_checked']=len(prices)
            result['lost_or_changed_decisions_at_backup_boundary']=0
            result['mysql_settings']=query(config,'SELECT VERSION(),@@collation_database,@@transaction_isolation,@@innodb_buffer_pool_size')[0]
            result['host']={'architecture':platform.machine(),'system':platform.system(),'logical_cpu':os.cpu_count(),
                            'ram_bytes':int(subprocess.check_output(['sysctl','-n','hw.memsize'],text=True))}
            checkpoint()
            result['failure_probes']=failures(config)
            checkpoint()
            result['performance']=benchmark(config)
            result['final_fk_count']=verify_links(config)
            checkpoint()
    print('PASS logical restore, migration recovery, failure injection and three load baselines')


if __name__=='__main__':
    run()
