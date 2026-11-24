"""Exercise the real image on a private persistent Linux MySQL server with split identities."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

import mysql.connector
from check_container_deployment import build_release, certificates, docker, ROOT
from check_g3_schema import original_tables, inventory
from mariadb_fixture import HERE, MIGRATIONS, ORDER, seed

OUT=ROOT/'test-results/production-acceptance'
# Runtime privileges by table. The application updates flags/versions instead of deleting business rows.
WRITES={
    'ADMINISTRATOR':'INSERT','USER_ACCOUNT':'INSERT,UPDATE','USER_MODULE_ACCESS':'INSERT,UPDATE',
    'STORE_STAFF':'INSERT,UPDATE','PRICING_GUIDELINE':'INSERT,UPDATE',
    'PRICING_DECISION':'INSERT','ITEM':'INSERT','MASTER_DATA_AUDIT':'INSERT',
    'USER_PASSWORD_TOKEN':'INSERT,UPDATE,DELETE','USER_PASSWORD_STATE':'INSERT,UPDATE',
    **dict.fromkeys(('CATEGORY','ITEM_TYPE','BRAND','ITEM_CONDITION','BRANCH'),'INSERT,UPDATE')}


def ready(endpoint):
    for _ in range(50):
        try:
            with urllib.request.urlopen(endpoint+'/_stcore/health',timeout=1) as response:
                if response.read()==b'ok':
                    return
        except OSError:
            pass
        time.sleep(.25)
    raise RuntimeError('Application health check timed out')


def audit_image(image,directory):
    path=directory/'image.tar'
    docker('image','save','-o',str(path),image)
    count=0
    canary=b'synthetic-container-canary'
    with tarfile.open(path) as archive:
        manifest=json.load(archive.extractfile('manifest.json'))[0]
        assert canary not in archive.extractfile(manifest['Config']).read()
        for name in manifest['Layers']:
            with tarfile.open(fileobj=archive.extractfile(name),mode='r|*') as layer:
                for entry in layer:
                    relative=entry.name.lstrip('./')
                    if relative.startswith('app/'):
                        assert not any(part in relative.split('/') for part in
                                       ('secrets.toml','.env','.git','test-results','.desktop_build'))
                        if entry.isfile():
                            assert canary not in layer.extractfile(entry).read()
                            count+=1
    return {'layers':len(manifest['Layers']),'app_files_inspected':count}


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    suffix=secrets.token_hex(6)
    db,app,net,volume,schema=[f'gw_{k}_{suffix}' for k in ('db','app','net','volume','test')]
    image=f'goodwill-acceptance:{suffix}'
    passwords={u:secrets.token_urlsafe(24) for u in ('root','gw_runtime','gw_previous','gw_migration')}
    login=secrets.token_urlsafe(24)
    evidence={}
    def save():
        (OUT/'linux.json').write_text(json.dumps(evidence,indent=2,default=str)+'\n')
    def client(user,sql,database=schema,quiet=False):
        return docker('exec','-i','-e','MYSQL_PWD',db,'mysql','--batch','--skip-column-names',
                      '--user='+user,database,input=sql,env={**os.environ,'MYSQL_PWD':passwords[user]},
                      stderr=subprocess.DEVNULL if quiet else None)
    def host_config(user):
        port=int(docker('port',db,'3306/tcp').split(':')[-1])
        return dict(host='127.0.0.1',port=port,database=schema,user=user,password=passwords[user],connection_timeout=2)
    with ExitStack() as cleanup:
        path=Path(cleanup.enter_context(tempfile.TemporaryDirectory()))
        certificates(path,'fixture-db')
        (path/'server.key').chmod(0o644)
        wrong=path/'other'; wrong.mkdir()
        certificates(wrong,'fixture-db')
        build_release(image,OUT)
        cleanup.callback(docker,'image','rm',image)
        evidence['artifact']=audit_image(image,path)
        docker('network','create',net)
        cleanup.callback(docker,'network','rm',net)
        docker('volume','create',volume)
        cleanup.callback(docker,'volume','rm',volume)
        docker('run','-d','--name',db,'--network',net,'--network-alias','fixture-db','--network-alias','wrong-name',
               '-p','127.0.0.1::3306','-v',volume+':/var/lib/mysql',
               '-v',f'{path}/server.key:/fixture/key:ro','-v',f'{path}/server.pem:/fixture/cert:ro',
               '-v',f'{path}/ca.pem:/fixture/ca:ro','-e','MYSQL_ROOT_PASSWORD','-e',f'MYSQL_DATABASE={schema}',
               'mysql:8.4','--ssl-key=/fixture/key','--ssl-cert=/fixture/cert','--ssl-ca=/fixture/ca',
               '--require_secure_transport=ON',env={**os.environ,'MYSQL_ROOT_PASSWORD':passwords['root']})
        cleanup.callback(docker,'rm','-f',db)
        def db_ready():
            for _ in range(120):
                try:
                    if client('root','SELECT DATABASE();',quiet=True)==schema and docker('exec',db,'sh','-c','test -S /var/run/mysqld/mysqld.sock && test "$(cat /proc/1/comm)" = mysqld',stderr=subprocess.DEVNULL)=='':
                        return
                except subprocess.CalledProcessError:
                    pass
                time.sleep(.5)
            raise RuntimeError('Isolated MySQL did not become ready')
        db_ready()
        assert client('root','SHOW TABLES;')==''
        for u,location in [('gw_migration','localhost'),('gw_previous','%'),('gw_runtime','%')]:
            tls='' if u=='gw_migration' else ' REQUIRE SSL'
            client('root',f"CREATE USER '{u}'@'{location}' IDENTIFIED BY '{passwords[u]}'{tls};")
        client('root',f"GRANT ALL ON {schema}.* TO 'gw_migration'@'localhost'; GRANT SELECT,INSERT ON {schema}.* TO 'gw_previous'@'%';")
        client('gw_migration',original_tables(Path(os.environ['GW_G3_DDL'])).decode())
        client('gw_migration',"INSERT INTO ITEM_TYPE VALUES(1,'Clothing'); INSERT INTO CATEGORY VALUES(1,'Shirts','Fixture');")
        # Setup via current app account is intentionally impossible at this point.
        for name in ORDER:
            if name.startswith('007'):
                break
            client('gw_migration',(MIGRATIONS/name).read_text())
        # Seed through a temporary schema-scoped fixture loader; retire it before release.
        loader=secrets.token_urlsafe(24)
        client('root',f"CREATE USER 'gw_loader'@'%' IDENTIFIED BY '{loader}' REQUIRE SSL; GRANT SELECT,INSERT ON {schema}.* TO 'gw_loader'@'%';")
        with mysql.connector.connect(**{**host_config('gw_runtime'),'user':'gw_loader','password':loader}) as conn:
            seed(conn,login,branch_snapshot=False,staff_role='Employee')
        client('root',"DROP USER 'gw_loader'@'%';")
        for table,priv in WRITES.items():
            client('root',f"GRANT SELECT,{priv} ON {schema}.`{table}` TO 'gw_runtime'@'%';")
        def launch(name):
            docker('run','-d','--name',name,'--network',net,'-p','127.0.0.1::8501',
                   '-v',f'{path}/ca.pem:/fixture/ca:ro','-v',f'{wrong}/ca.pem:/fixture/wrong:ro',
                   '-e','MYSQL_HOST=fixture-db','-e','MYSQL_PORT=3306','-e','MYSQL_USER=gw_runtime',
                   '-e',f'MYSQL_DATABASE={schema}','-e','MYSQL_PASSWORD','-e','MYSQL_SSL_CA=/fixture/ca',image,
                   env={**os.environ,'MYSQL_PASSWORD':passwords['gw_runtime']})
            cleanup.callback(docker,'rm','-f',name)
        candidate=app+'_pre'
        launch(candidate)
        assert docker('wait',candidate)=='1'
        failure=docker('logs',candidate,stderr=subprocess.STDOUT)
        assert 'migrations 001–008' in failure and not any(p in failure for p in passwords.values())
        evidence['missing_migrations']='Pre-007 candidate exited 1 before serving; migration instructions visible.'
        # A legacy connection can omit the snapshot without an identity fence.
        old=mysql.connector.connect(**host_config('gw_previous'))
        with old.cursor() as cur:
            cur.execute('SELECT CONNECTION_ID()')
            old_id=int(cur.fetchone()[0])
            client('root',"ALTER USER 'gw_previous'@'%' ACCOUNT LOCK;")
            client('root',f'KILL CONNECTION {old_id};')
            try:
                cur.execute('SELECT 1')
            except mysql.connector.Error:
                pass
            else:
                raise AssertionError('Retired session still connected')
        old.close()
        try:
            mysql.connector.connect(**host_config('gw_previous'))
        except mysql.connector.Error as exc:
            assert exc.errno in (3118,1045)
            evidence['old_writer_fence']={'new_connection_errno':exc.errno,'existing_session_terminated':True}
        else:
            raise AssertionError('Retired writer reconnected')
        client('gw_migration',(MIGRATIONS/ORDER[-2]).read_text())
        intermediate=app+'_role'
        launch(intermediate)
        assert docker('wait',intermediate)=='1' and 'migrations 001–008' in docker('logs',intermediate,stderr=subprocess.STDOUT)
        client('gw_migration',(MIGRATIONS/ORDER[-1]).read_text())
        evidence['post_upgrade_roles']=client('root','SELECT User_Type,COUNT(*) FROM USER_ACCOUNT GROUP BY User_Type').splitlines()
        evidence['post_upgrade_checks']=client('root',"SELECT CONSTRAINT_NAME,CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME LIKE '%user_type%'").splitlines()
        save()
        client('gw_migration','INSERT INTO USER_MODULE_ACCESS(User_Id,Can_Manage_Users_Config) VALUES(1,1);')
        launch(app)
        endpoint='http://'+docker('port',app,'8501/tcp')
        try:
            ready(endpoint)
        except RuntimeError:
            details=docker('logs',app,stderr=subprocess.STDOUT)
            assert not any(p in details for p in passwords.values())
            print('Candidate health failure:',details,flush=True)
            raise
        paths=[p for p in ROOT.glob('*.py') if not p.name.startswith('tests_')]
        paths.extend((ROOT/'scripts').glob('*.py'))
        paths.extend((ROOT/'styles').glob('*.css'))
        paths.extend([ROOT/'.streamlit/config.toml',ROOT/'requirements.txt'])
        expected={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        filelist=json.dumps(list(expected))
        measured=json.loads(docker('exec',app,'python','-c',
            "import json,hashlib,pathlib,sys; print(json.dumps({p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in json.loads(sys.argv[1])}))",filelist))
        assert measured==expected
        evidence['artifact'].update(runtime_digest_verified=len(expected),image_id=docker('image','inspect','-f','{{.Id}}',image))
        security=docker('exec',app,'python','-c',"""import os,json,mysql.connector,sys,streamlit
from database import get_config,get_connection
from scripts.check_release_schema import check_schema
with get_connection() as c,c.cursor() as q:
 q.execute("SELECT VERSION(),CURRENT_USER(),@@collation_database"); version,user,collation=q.fetchone()
 q.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'"); cipher=q.fetchone()[1]
 q.execute("SHOW GRANTS"); grants=[r[0] for r in q.fetchall()]
 assert cipher and user.startswith('gw_runtime@')
for changes in ({'host':'wrong-name'},{'ssl_ca':'/fixture/wrong'}):
 try: mysql.connector.connect(**{**get_config(),**changes})
 except mysql.connector.Error as e: assert e.errno==2026,e.errno
 else: raise AssertionError('invalid TLS accepted')
print(json.dumps(dict(mysql=version,python=sys.version.split()[0],streamlit=streamlit.__version__,uid=os.getuid(),cipher=cipher,grants=grants,collation=collation,invalid_tls_rejected=2)))""")
        evidence['runtime']=json.loads(security)
        assert evidence['runtime']['uid']==10001
        with mysql.connector.connect(**host_config('gw_runtime')) as conn,conn.cursor() as cur:
            for statement in ('CREATE TABLE fixture_denied (id INT)','UPDATE PRICING_DECISION SET Decision_Final_Price=0 WHERE 1=0'):
                try:
                    cur.execute(statement)
                except mysql.connector.Error as exc:
                    assert exc.errno==1142
                else:
                    raise AssertionError('Excess runtime privilege')
        evidence['migrated_metadata']=inventory(host_config('gw_runtime'))
        save()
        env={**os.environ,'GW_ACCEPTANCE_ENDPOINT':endpoint,'GW_ACCEPTANCE_LOGIN':login,
             'GW_ACCEPTANCE_RESULTS':str(OUT),'GW_ACCEPTANCE_METRICS':json.dumps(['2','$15.25','0.0%','2'])}
        with (OUT/'browser.log').open('w') as log:
            subprocess.run(['node','--test',HERE/'admin_workflow.cjs'],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        tag=(OUT/'browser-tag.txt').read_text().strip()
        env['GW_EXPECT_TAG']=tag
        def saved():
            return client('root','SELECT Decision_Id,Decision_Tag_Number,Decision_Final_Price,Guideline_Id,Staff_Id,Decision_Branch_Id FROM PRICING_DECISION ORDER BY Decision_Id;')
        before=saved()
        assert before.splitlines()==['1\tTS26-100001\t12.50\t1\t3\tNULL',f'2\t{tag}\t18.00\t4\t2\t1']
        evidence['browser_rows']=before.splitlines()
        evidence['guidelines']=client('root','SELECT Guideline_Id,Guideline_Suggested_Price,Guideline_Is_Active FROM PRICING_GUIDELINE ORDER BY Guideline_Id').splitlines()
        assert evidence['guidelines'][-2:]==['3\t15.00\t0','4\t18.00\t0']
        def browser_check(mode):
            subprocess.run(['node',HERE/'acceptance_login.cjs',mode],env={**env,'GW_ACCEPTANCE_ENDPOINT':endpoint},check=True)
        save()
        start=time.perf_counter()
        docker('stop','-t','8',db)
        browser_check('outage')
        docker('start',db)
        db_ready()
        browser_check('recover')
        assert saved()==before
        evidence['outage_to_verified_relogin_seconds']=round(time.perf_counter()-start,3)
        # Abort a staged identical candidate before its port becomes the service endpoint.
        staged=app+'_next'
        launch(staged)
        docker('kill',staged)
        browser_check('recover')
        assert saved()==before
        evidence['aborted_staged_candidate_old_endpoint_served_history']=True
        start=time.perf_counter()
        docker('restart',app)
        endpoint='http://'+docker('port',app,'8501/tcp')
        ready(endpoint)
        browser_check('recover')
        assert saved()==before
        evidence['app_restart_to_verified_history_seconds']=round(time.perf_counter()-start,3)
        logs=docker('logs',app,stderr=subprocess.STDOUT)
        assert not any(s in logs for s in [*passwords.values(),login,loader,'fixture-db'])
        evidence['inspected_app_log_contains_configured_secrets_or_db_hostname']=False
        evidence['docker_resources']=json.loads(docker('info','--format','{"logical_cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}}}'))
        save()
    print('PASS deployed cross-role workflow, release contents, TLS, cutover, outage and restart')


if __name__=='__main__':
    run()
