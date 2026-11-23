"""MySQL connection for local Streamlit secrets or cloud MYSQL_* variables."""

import os
from pathlib import Path
import mysql.connector
import streamlit as st


def get_config():
    """Use one complete source; never mix cloud and local database credentials."""
    env_keys = {
        "host": "MYSQL_HOST", "port": "MYSQL_PORT", "user": "MYSQL_USER",
        "password": "MYSQL_PASSWORD", "database": "MYSQL_DATABASE",
    }
    cloud = any(name in os.environ for name in env_keys.values())
    if cloud:
        missing = [name for name in env_keys.values() if not os.environ.get(name)]
        if missing:
            raise RuntimeError("Missing cloud settings: " + ", ".join(missing))
        config = {key: os.environ[name] for key, name in env_keys.items()}
        config.update(
            ssl_ca=os.environ.get("MYSQL_SSL_CA", "ca.pem"),
            ssl_disabled=False,
            ssl_verify_cert=True,
            ssl_verify_identity=True,
        )
    else:
        try:
            config = dict(st.secrets["mysql"])
        except (KeyError, FileNotFoundError) as exc:
            raise RuntimeError(
                "Missing MySQL settings. Configure MYSQL_* cloud variables, or "
                "copy .streamlit/secrets.toml.example to .streamlit/secrets.toml locally."
            ) from exc

    required = ("host", "user", "password", "database")
    missing = [name for name in required if not config.get(name)]
    if missing:
        raise RuntimeError(f"Missing MySQL configuration values: {', '.join(missing)}")

    config.setdefault("port", 3306)
    try:
        config["port"] = int(config["port"])
        if not 1 <= config["port"] <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise RuntimeError("MySQL port must be an integer between 1 and 65535.") from None
    if config.get("ssl_ca"):
        ca_file = Path(config["ssl_ca"])
        if not ca_file.is_absolute():
            ca_file = Path(__file__).resolve().parent / ca_file
        if not ca_file.is_file():
            raise RuntimeError("MySQL CA certificate file is missing. Download the Aiven CA certificate as ca.pem in the project root.")
        config["ssl_ca"] = str(ca_file)
    config.setdefault("connection_timeout", 7)
    config.setdefault("charset", "utf8mb4")
    config.setdefault("autocommit", False)
    return config


def get_connection():
    """Return a new connection. Callers must close it."""
    config = get_config()
    return mysql.connector.connect(**config)
