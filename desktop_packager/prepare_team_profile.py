"""Prepare public-only Goodwill team defaults BEFORE compiling the desktop app.

Examples:
  python desktop_packager/prepare_team_profile.py --from-local
  python desktop_packager/prepare_team_profile.py --host school-db.example.edu
No password, DB username, SMTP token or private key is written or printed.
"""
from __future__ import annotations
import argparse
import json
import tomllib
from pathlib import Path

PROFILE = Path(__file__).resolve().parent / "template" / "team_profile.toml"
DEFAULT_DATABASE = "Fall2026BIS698TueG3s"


def validate_host(host: str) -> str:
    host = host.strip()
    if not host or any(ch.isspace() for ch in host) or any(ch in host for ch in "/:@\\"):
        raise ValueError("Supply a database hostname/IP only (no URL, port or credentials).")
    if len(host) > 255:
        raise ValueError("Hostname is too long.")
    return host


def read_nonsecret_local_config(path: Path) -> tuple[str, int, str]:
    """Parse existing local secrets but RETAIN ONLY host, port and database."""
    if not path.is_file():
        raise ValueError(f"Local config file does not exist: {path}")
    try:
        mysql = tomllib.loads(path.read_text(encoding="utf-8"))["mysql"]
        host = validate_host(str(mysql["host"]))
        port = int(mysql.get("port", 3306))
        database = str(mysql["database"]).strip()
        return host, port, database
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise ValueError("Could not read valid [mysql] host, port and database from local config") from exc


def write_public_profile(host: str, port: int, database: str, path: Path = PROFILE) -> None:
    host = validate_host(host)
    if not 1 <= port <= 65535:
        raise ValueError("Port must be 1..65535")
    database = database.strip()
    if not database or len(database) > 128 or any(x in database for x in ("\n", "\r")):
        raise ValueError("Database name must be nonempty and at most 128 characters")
    profile = ("# Team defaults only. NEVER add passwords or usernames here.\n"
               "[mysql]\n"
               f"host = {json.dumps(host)}\n"
               f"port = {port}\n"
               f"database = {json.dumps(database)}\n\n"
               "[app]\n"
               'base_url = "http://127.0.0.1:8501"\n')
    path.write_text(profile, encoding="utf-8")
    print(f"Prepared non-secret team profile: {path}")
    print("Next: rebuild GoodwillPricing.app/.exe on each target OS. No credentials were copied.")


def main():
    parser = argparse.ArgumentParser(description="Prepare only non-secret shared setup defaults")
    choices = parser.add_mutually_exclusive_group()
    choices.add_argument("--host", help="School VPN database host/IP")
    choices.add_argument("--from-local", action="store_true", help="Read host, port and database from project .streamlit/secrets.toml; requires confirmation")
    parser.add_argument("--source", type=Path, default=Path('.streamlit/secrets.toml'),
                        help="When --from-local, path to existing local secrets (never copied)")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--show", action="store_true", help="Show current non-secret team profile")
    args = parser.parse_args()
    if args.show:
        print(PROFILE.read_text(encoding="utf-8"))
        return
    if args.from_local:
        try:
            host, port, database = read_nonsecret_local_config(args.source)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Host: {host} | Port: {port} | Schema: {database}")
        print("Confirm this is the SCHOOL VPN server, NOT Aiven/public production.")
        if input("Type SCHOOL to use these non-secret settings: ").strip() != "SCHOOL":
            print("Cancelled; no team settings changed.")
            return
    elif args.host:
        host, port, database = args.host, args.port, args.database
    else:
        parser.error("Use --host HOST or --from-local (or --show)")
    try:
        write_public_profile(host, port, database)
    except ValueError as exc:
        parser.error(str(exc))

if __name__ == "__main__":
    main()
