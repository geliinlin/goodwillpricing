"""Build an isolated macOS app from current sources and inspect its packaged resources."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'test-results/deployment-readiness'
sys.path.insert(0,str(ROOT))
from desktop_packager.build_portable import stage_project

if __name__ == '__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gw-mac-build-') as scratch:
        work = Path(scratch)/'project'
        stage_project(ROOT,work)
        shutil.copytree(ROOT/'desktop_packager',work/'desktop_packager')
        canary = work/'desktop_packager/template/secrets.toml'
        canary.write_text('[mysql]\npassword="synthetic-staging-canary"\n')
        with (OUT/'mac-build.log').open('w') as log:
            subprocess.run([sys.executable,str(work/'desktop_packager/build_portable.py'),'--project',str(work)],
                           env={**os.environ,"PYINSTALLER_CONFIG_DIR":str(Path(scratch)/"cache")},
                           stdout=log,stderr=subprocess.STDOUT,check=True)
        app = work/'.desktop_build/dist/GoodwillPricing.app'
        resources = app/'Contents/Resources'
        assert (app/'Contents/MacOS/GoodwillPricing').is_file()
        for source in ('database.py','item_search_page.py','pricing_decision.py'):
            assert (resources/'goodwill_app'/source).read_bytes() == (ROOT/source).read_bytes()
        assert not (resources/'goodwill_app/test-results').exists()
        assert not list(app.rglob('secrets.toml'))
        assert (resources/'template/secrets.toml.example').is_file()
        codesign = subprocess.run(['codesign','--verify','--deep','--strict',str(app)], capture_output=True)
        evidence = dict(build=True,current_application_sources=True,canary_excluded=True,
                        evidence_directory_excluded=True,code_signature_verified=codesign.returncode==0)
        (OUT/'mac-build.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(evidence)
