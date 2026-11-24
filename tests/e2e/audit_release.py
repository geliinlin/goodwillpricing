"""Read-only, value-redacted audit of reachable Git blobs and supplied release archives.

Uses a Python with PyInstaller for inspecting embedded CArchive and PYZ entries.
Only reports paths/counts, never the values used for matching.
"""
import argparse
from collections import Counter
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import sysconfig
import tomllib
import zipfile

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'test-results/deployment-readiness'


def audit(archives):
    values = set()
    private = ROOT/'.streamlit/secrets.toml'
    if private.is_file():
        config = tomllib.loads(private.read_text())
        for section in ('mysql','email'):
            value = config.get(section,{}).get('password')
            if value:
                values.add(str(value).encode())
    for key in ('MYSQL_PASSWORD','EMAIL_SMTP_PASSWORD'):
        if os.environ.get(key):
            values.add(os.environ[key].encode())
    counts = Counter()
    findings = set()
    compared = {}
    library_matches = set()
    libraries = Path(sysconfig.get_paths()['purelib'])
    def installed_reference(name, kind):
        member = name.split('!/')[-1]
        if kind == 'embedded Python modules' and re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9.]*', member):
            return libraries.joinpath(*member.split('.')).with_suffix('.py')
        for prefix in ('/Contents/Resources/', '/Contents/Frameworks/'):
            if prefix in member:
                relative = Path(member.split(prefix,1)[1].replace('__dot__','.'))
                if '..' not in relative.parts and not relative.is_absolute():
                    return libraries/relative
        return None
    key_pattern = re.compile(rb'(?m)^-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{32,}')
    def check(name, data, kind):
        counts[kind] += 1
        matches = [value for value in values if value in data]
        if matches:
            baseline = installed_reference(name, kind)
            if baseline and baseline.is_file() and all(value in baseline.read_bytes() for value in matches):
                library_matches.add(name)
            else:
                findings.add(('configured password match',name))
        if key_pattern.search(data):
            findings.add(('private key material',name))
        leaf = Path(name).name.lower()
        if leaf == 'secrets.toml' or leaf == '.env' or (leaf.startswith('.env.') and leaf!='.env.example'):
            findings.add(('private configuration filename',name))
        if name.endswith('/goodwill_app/database.py'):
            compared[name] = data == (ROOT/'database.py').read_bytes()
        if data.startswith(b'PK\x03\x04') and zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for entry in archive.infolist():
                    if not entry.is_dir():
                        check(name+'!/'+entry.filename, archive.read(entry), 'zip entries')
        elif b'MEI\x0c\x0b\x0a\x0b\x0e' in data[-100000:] and (
                leaf.endswith('.pkg') or leaf=='goodwillpricing'):
            with tempfile.NamedTemporaryFile() as tmp:
                tmp.write(data)
                tmp.flush()
                c = CArchiveReader(tmp.name)
                for entry, meta in c.toc.items():
                    if meta[-1]=='z':
                        nested = c.open_embedded_archive(entry)
                        for module in nested.toc:
                            content = nested.extract(module,raw=True)
                            if content:
                                check(name+'!/'+module,content,'embedded Python modules')
                    else:
                        check(name+'!/'+entry,c.extract(entry),'embedded executable entries')
        elif data.startswith(b'PYZ\x00') and leaf.endswith('.pyz'):
            with tempfile.NamedTemporaryFile() as tmp:
                tmp.write(data)
                tmp.flush()
                nested = ZlibArchiveReader(tmp.name)
                for module in nested.toc:
                    content = nested.extract(module,raw=True)
                    if content:
                        check(name+'!/'+module,content,'embedded Python modules')
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=ROOT)
    for line in git('rev-list','--objects','--all').decode().splitlines():
        oid, _, name = line.partition(' ')
        if name and git('cat-file','-t',oid).strip()==b'blob':
            check('git/'+name,git('cat-file','blob',oid),'Git blobs')
    for archive in archives:
        check(archive.name,archive.read_bytes(),'distribution archives')
    result = dict(reachable_commits=int(git('rev-list','--all','--count')),scanned=dict(counts),
                  comparisons_available=bool(values),findings=sorted(findings),
                  substring_matches_also_in_installed_dependencies=len(library_matches),
                  archives=[p.name for p in archives],
                  packaged_database_module_matches_workspace=compared)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'release-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Scanned',dict(counts),'findings',len(findings))
    return bool(findings)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives',type=Path,nargs='*')
    raise SystemExit(audit(parser.parse_args().archives))
