"""Release inputs and local TLS policy. All credentials here are invented."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import database
from desktop_packager import build_portable


class ReleaseInputsTests(unittest.TestCase):
    def test_staging_excludes_evidence_and_env_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('app.py','test-results/log.json','.env.production.json','.streamlit/secrets.toml'):
                p = root/name
                p.parent.mkdir(exist_ok=True)
                p.write_text('synthetic')
            self.assertEqual([rel.as_posix() for _,rel in build_portable.safe_files(root)], ['app.py'])

    def test_personalized_template_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'template'
            folder.mkdir()
            (folder/'team_profile.toml').write_text('[mysql]\nport=3306')
            (folder/'secrets.toml.example').write_text('[mysql]\nuser="fixture"\npassword="synthetic"')
            with self.assertRaises(SystemExit):
                build_portable.validate_team_profile(folder.parent)

    def test_private_key_mislabeled_as_ca_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'ca.pem').write_text('-----BEGIN PRIVATE KEY-----\nsynthetic')
            with self.assertRaises(SystemExit):
                list(build_portable.safe_files(root))

    def test_local_ca_enforces_identity_and_trust(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{},clear=True):
            ca = Path(tmp)/'ca.pem'
            ca.write_text('fixture')
            settings = dict(host='localhost',user='fixture',password='synthetic',database='fixture',
                            ssl_ca=str(ca),ssl_disabled=True)
            with patch.object(database.st,'secrets',{'mysql':settings}):
                config = database.get_config()
            self.assertFalse(config['ssl_disabled'])
            self.assertTrue(config['ssl_verify_identity'] and config['ssl_verify_cert'])
