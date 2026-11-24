"""Build the release image and exercise its real CMD with verified TLS to isolated Linux MySQL."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.request

import mysql.connector
from check_mysql_deployment import certificates, OUT, ROOT
from mariadb_fixture import HERE, MIGRATIONS, ORDER, seed


def docker(*args, **kwargs):
    return subprocess.check_output(['docker',*args],text=True,**kwargs).strip()


def build_release(image, output):
    # Exercise the release rules with synthetic canaries, never with a developer's private settings.
    with tempfile.TemporaryDirectory() as tmp:
        context = Path(tmp)
        for source in [*ROOT.glob('*.py'), ROOT/'requirements.txt', ROOT/'Dockerfile', ROOT/'.dockerignore']:
            shutil.copy2(source,context/source.name)
        for directory in ('styles','scripts'):
            shutil.copytree(ROOT/directory,context/directory,ignore=shutil.ignore_patterns('__pycache__'))
        (context/'.streamlit').mkdir()
        shutil.copy2(ROOT/'.streamlit/config.toml',context/'.streamlit/config.toml')
        for name in ('.streamlit/secrets.toml','.env','.desktop_build/private.json','test-results/private.json'):
            dest = context/name
            dest.parent.mkdir(exist_ok=True)
            dest.write_text('synthetic-container-canary')
        with (output/'docker-build.log').open('w') as log:
            subprocess.run(['docker','build','--tag',image,'.'],cwd=context,stdout=log,stderr=log,check=True)


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    suffix = secrets.token_hex(6)
    schema, db, app, network = ['gw_'+k+'_'+suffix for k in ('test','db','app','net')]
    image = 'goodwill-readiness-local:validation'
    build_release(image, OUT)
    secret = secrets.token_urlsafe(25)
    login = secrets.token_urlsafe(20)
    with ExitStack() as cleanup:
        path = Path(cleanup.enter_context(tempfile.TemporaryDirectory()))
        certificates(path,'fixture-db')
        # The private parent is mode 0700; allow the container uid to read the bind-mounted leaf.
        (path/'server.key').chmod(0o644)
        docker('network','create',network)
        cleanup.callback(docker,'network','rm',network)
        docker('run','-d','--rm','--name',db,'--network',network,'--network-alias','fixture-db',
               '-p','127.0.0.1::3306','--tmpfs','/var/lib/mysql',
               '-v',f'{path}/server.key:/fixture/key:ro','-v',f'{path}/server.pem:/fixture/cert:ro',
               '-v',f'{path}/ca.pem:/fixture/ca:ro','-e','MYSQL_RANDOM_ROOT_PASSWORD=1',
               '-e','MYSQL_PASSWORD','-e','MYSQL_USER=gw_fixture','-e',f'MYSQL_DATABASE={schema}',
               'mysql:8.4','--ssl-key=/fixture/key','--ssl-cert=/fixture/cert','--ssl-ca=/fixture/ca',
               env={**os.environ,'MYSQL_PASSWORD':secret})
        cleanup.callback(docker,'stop','-t','2',db)
        port = int(docker('port',db,'3306/tcp').split(':')[-1])
        conf = dict(host='127.0.0.1',port=port,database=schema,user='gw_fixture',password=secret,connection_timeout=3)
        for _ in range(90):
            try:
                conn = mysql.connector.connect(**conf)
                break
            except mysql.connector.Error:
                time.sleep(1)
        else:
            raise RuntimeError('MySQL container failed to become ready')
        with conn,conn.cursor() as cur:
            cur.execute('SELECT VERSION(),DATABASE(),@@lower_case_table_names,@@sql_mode')
            version,selected,case_mode,sql_mode = cur.fetchone()
            assert selected==schema and version.startswith('8.4.')
            cur.execute('SHOW TABLES')
            assert not cur.fetchall()
        for script in [HERE/'workflow_base.sql.in',*(MIGRATIONS/name for name in ORDER)]:
            docker('exec','-i','-e','MYSQL_PWD',db,'mysql','--user=gw_fixture',schema,
                   input=script.read_text(),env={**os.environ,'MYSQL_PWD':secret})
        with mysql.connector.connect(**conf) as conn:
            seed(conn,login)
        docker('run','-d','--rm','--name',app,'--network',network,'-p','127.0.0.1::8501',
               '-v',f'{path}/ca.pem:/fixture/ca:ro','-e','MYSQL_HOST=fixture-db','-e','MYSQL_PORT=3306',
               '-e','MYSQL_USER=gw_fixture','-e',f'MYSQL_DATABASE={schema}','-e','MYSQL_PASSWORD',
               '-e','MYSQL_SSL_CA=/fixture/ca',image,env={**os.environ,'MYSQL_PASSWORD':secret})
        cleanup.callback(docker,'stop','-t','2',app)
        endpoint = 'http://'+docker('port',app,'8501/tcp')
        for _ in range(45):
            try:
                with urllib.request.urlopen(endpoint+'/_stcore/health',timeout=1) as response:
                    if response.read()==b'ok': break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError('Release image did not start')
        checked = docker('exec',app,'python','-c',"""import database, json, os, pathlib, sys, streamlit
root = pathlib.Path('/app')
assert not any((root/p).exists() for p in ('.git','.desktop_build','.streamlit/secrets.toml','test-results','.venv','desktop_packager','.env'))
with database.get_connection() as conn, conn.cursor() as cur:
    cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
    cipher = cur.fetchone()[1]
    assert cipher and os.getuid() == 10001
    print(json.dumps(dict(uid=os.getuid(),python=sys.version.split()[0],streamlit=streamlit.__version__,cipher=cipher)))""")
        docker('exec',app,'python','-m','pip','check')
        for script in ('check_database_connection','check_configuration_schema','check_guideline_schema'):
            docker('exec',app,'python','-m','scripts.'+script)
        js = """const {chromium}=require('playwright');
(async()=>{const b=await chromium.launch({executablePath:process.env.GW_E2E_CHROME});
try {const p=await b.newPage(); await p.goto(process.env.GW_ENDPOINT);
await p.getByLabel('Email or Username',{exact:true}).fill('admin_e2e');
await p.getByLabel('Password',{exact:true}).fill(process.env.GW_LOGIN);
await p.getByRole('button',{name:'Login',exact:true}).click();
await p.getByRole('button',{name:'Logout',exact:true}).waitFor();
await p.getByRole('button',{name:'Guidelines',exact:true}).click();
await p.locator('[data-testid="stDataFrame"]').first().waitFor(); }
finally {await b.close()}})().catch(e=>{console.error(e.message);process.exitCode=1});"""
        subprocess.run(['node','-e',js],env={**os.environ,'GW_ENDPOINT':endpoint,'GW_LOGIN':login},check=True)
        result = dict(mysql=version,lower_case_table_names=case_mode,sql_mode=sql_mode,
                      migrations=list(ORDER),docker_image=docker('image','inspect','-f','{{.Id}}',image),
                      authenticated_browser=True,**json.loads(checked))
        (OUT/'container.json').write_text(json.dumps(result,indent=2)+'\n')
        print('PASS Docker release image, verified TLS, schema probes, Chrome login and guideline grid',version)


if __name__ == '__main__':
    run()
