"""Database configuration smoke tests; credentials are dummy and no network is used."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import database


class DatabaseConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ca = Path(self.temp.name) / "ca.pem"
        self.ca.write_text("test-only fixture, not a real CA certificate")
        self.env = {
            "MYSQL_HOST": "db.example.invalid", "MYSQL_PORT": "12691",
            "MYSQL_USER": "test_user", "MYSQL_PASSWORD": "dummy-not-a-secret",
            "MYSQL_DATABASE": "course_schema", "MYSQL_SSL_CA": str(self.ca),
        }

    def test_cloud_config_has_tls_cert_and_identity_verification(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(database.st, "secrets", {}):
            config = database.get_config()
        self.assertEqual(config["database"], "course_schema")
        self.assertEqual(config["port"], 12691)
        self.assertTrue(config["ssl_verify_cert"])
        self.assertTrue(config["ssl_verify_identity"])
        self.assertFalse(config["ssl_disabled"])
        self.assertEqual(config["ssl_ca"], str(self.ca))
        self.assertEqual(config["connection_timeout"], 7)
        self.assertEqual(config["charset"], "utf8mb4")
        self.assertFalse(config["autocommit"])

    def test_partial_cloud_env_fails_closed_instead_of_using_local_secrets(self):
        with patch.dict(os.environ, {"MYSQL_HOST": "partial.invalid"}, clear=True), \
             patch.object(database.st, "secrets", {"mysql": {"password": "local"}}):
            with self.assertRaisesRegex(RuntimeError, "Missing cloud settings"):
                database.get_config()

    def test_missing_ca_certificate_rejected(self):
        with patch.dict(os.environ, {**self.env, "MYSQL_SSL_CA": str(self.ca.parent / "missing.pem")}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "certificate file is missing"):
                database.get_config()

    def test_invalid_ports_are_rejected(self):
        for port in ("bad", "0", "65536"):
            with self.subTest(port=port), patch.dict(os.environ, {**self.env, "MYSQL_PORT": port}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "port must be"):
                    database.get_config()

    def test_local_streamlit_secrets_remain_supported(self):
        secrets = {"mysql": {"host": "localhost", "user": "local",
                             "password": "dummy", "database": "Fall2026BIS698TueG3s"}}
        with patch.dict(os.environ, {}, clear=True), patch.object(database.st, "secrets", secrets):
            config = database.get_config()
        self.assertEqual(config["database"], "Fall2026BIS698TueG3s")
        self.assertEqual(config["port"], 3306)
        self.assertEqual(config["password"], "dummy")

    def test_no_local_or_cloud_configuration_is_an_error(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(database.st, "secrets", {}):
            with self.assertRaisesRegex(RuntimeError, "Missing MySQL settings"):
                database.get_config()

    def test_connection_factory_receives_configuration_without_real_connection(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch("database.mysql.connector.connect") as connection:
            actual = database.get_connection()
        self.assertIs(actual, connection.return_value)
        self.assertEqual(connection.call_args.kwargs["host"], "db.example.invalid")
        self.assertTrue(connection.call_args.kwargs["ssl_verify_identity"])

    def test_cloud_config_does_not_accidentally_merge_local_database_name(self):
        local = {"mysql": {"database": "local_db", "user": "local", "password": "localpass"}}
        with patch.dict(os.environ, self.env, clear=True), patch.object(database.st, "secrets", local):
            config = database.get_config()
        self.assertEqual(config["database"], "course_schema")
        self.assertNotEqual(config["user"], "local")


if __name__ == "__main__":
    unittest.main()
