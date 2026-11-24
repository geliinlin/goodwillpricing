"""Run the existing suites on Oracle MySQL, keeping previous MariaDB evidence intact."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get('GW_TEST_RESULTS', ROOT / 'test-results/deployment-readiness'))


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gw-regression-') as scratch:
        work = Path(scratch)
        for p in ROOT.glob('*.py'):
            shutil.copy2(p, work/p.name)
        for directory in ('styles', 'migrations', 'tests', 'scripts'):
            shutil.copytree(ROOT/directory, work/directory, ignore=shutil.ignore_patterns('__pycache__'))
        (work/'.streamlit').mkdir()
        shutil.copy2(ROOT/'.streamlit/config.toml', work/'.streamlit/config.toml')
        env = {**os.environ, 'GW_TEST_ENGINE':'mysql'}
        env.pop('PYTHONPATH', None)
        pdf_python = os.environ.get('GW_PDF_PYTHON', sys.executable)
        for filename in ('run_mariadb', 'run_admin_mariadb', 'run_browser', 'run_admin_browser',
                         'run_cross_role', 'check_printed_tags', 'check_admin_export', 'check_cross_role_tag'):
            pdf = filename.startswith('check_')
            process = subprocess.run([pdf_python if pdf else sys.executable, f'tests/e2e/{filename}.py'],
                                     cwd=work, env=({**env, 'PYTHONPATH':os.environ.get('GW_DECODER_PATH','')}
                                                    if pdf else env),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            print(filename, process.returncode, flush=True)
            (OUT/(filename+'.log')).write_text(process.stdout)
            if process.returncode:
                raise SystemExit(f'{filename} failed; see saved log')
        for directory, name in [('admin-workflow','service-sql.json'),('admin-workflow','browser-sql.json'),
                                ('cross-role-integration','sql-checkpoints.json'),
                                ('staff-workflow','browser-tags.json'),
                                ('cross-role-integration','temporary-session.json')]:
            shutil.copy2(work/'test-results'/directory/name, OUT/name)


if __name__ == '__main__':
    run()
