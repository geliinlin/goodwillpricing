"""Goodwill Pricing local desktop launcher with first-run browser setup.

The setup page is bound exclusively to 127.0.0.1; it does not expose credentials
on a public website or bundle credentials into the executable. Once saved, the
normal local-only Streamlit server starts and opens in the browser.
"""
from __future__ import annotations

import hmac
import html
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

APP_NAME = "GoodwillPricing"
PORT = 8501
URL = f"http://127.0.0.1:{PORT}"


def user_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / APP_NAME


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def report(title: str, message: str) -> None:
    print(f"{title}: {message}", file=sys.stderr)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
            return
        except Exception:
            pass
    if sys.platform == "darwin":
        try:
            script = 'on run argv\n display dialog (item 2 of argv) with title (item 1 of argv) buttons {"OK"} default button "OK"\nend run'
            subprocess.run(["osascript", "-e", script, title, message], check=False, timeout=15)
        except Exception:
            pass


def team_defaults(root: Path | None = None) -> dict:
    """Load ONLY public non-secret fields included by project organizer."""
    import tomllib
    root = root or resource_root()
    p = root / "template" / "team_profile.toml"
    default = {"host": "", "port": 3306, "database": "Fall2026BIS698TueG3s"}
    if p.is_file():
        try:
            raw = tomllib.loads(p.read_text(encoding="utf-8"))
            mysql = raw.get("mysql", {})
            for field in default:
                if field in mysql:
                    default[field] = mysql[field]
        except (ValueError, OSError):
            pass
    # Credentials must never be read from this profile, even if somebody adds them.
    return default


def prepare_user_config(home: Path) -> tuple[Path, bool]:
    conf_dir = home / ".streamlit"
    conf_dir.mkdir(parents=True, exist_ok=True)
    secret_path = conf_dir / "secrets.toml"
    created = False
    if not secret_path.exists():
        # Existing setup file is kept for compatibility; wizard populates it.
        template = resource_root() / "template" / "secrets.toml.example"
        fd = os.open(str(secret_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(template.read_bytes())
        created = True
    theme = resource_root() / "goodwill_app" / ".streamlit" / "config.toml"
    target_theme = conf_dir / "config.toml"
    if theme.is_file() and not target_theme.exists():
        target_theme.write_bytes(theme.read_bytes())
    return secret_path, created


def settings_appear_valid(path: Path) -> bool:
    try:
        import tomllib
        values = tomllib.loads(path.read_text(encoding="utf-8"))
        mysql = values["mysql"]
        required = ("host", "user", "password", "database")
        if any(not isinstance(mysql.get(k), str) or not mysql[k].strip()
               or mysql[k].startswith("REPLACE_") for k in required):
            return False
        port = int(mysql.get("port", 3306))
        return 1 <= port <= 65535
    except (Exception,):
        return False


def _existing_public_values(path: Path, defaults: dict) -> dict:
    import tomllib
    result = dict(defaults)
    if not path.is_file():
        return result
    try:
        old = tomllib.loads(path.read_text(encoding="utf-8")).get("mysql", {})
        for field in ("host", "port", "database", "user", "ssl_ca"):
            val = old.get(field)
            if val and not (isinstance(val, str) and val.startswith("REPLACE_")):
                result[field] = val
    except (ValueError, OSError):
        pass
    return result


def validate_form(data: dict) -> tuple[dict | None, str | None]:
    values = {key: str(data.get(key, "")).strip() for key in
              ("host", "port", "user", "database", "ssl_ca")}
    # Preserve significant spaces in passwords, but never permit an empty password.
    values["password"] = str(data.get("password", ""))
    try:
        port = int(values["port"])
        if not 1 <= port <= 65535:
            raise ValueError
        values["port"] = port
    except ValueError:
        return None, "Port must be between 1 and 65535."
    if not values["host"] or any(ch.isspace() for ch in values["host"]):
        return None, "Enter the school database hostname or IP address."
    if any(ch in values["host"] for ch in ("/", "@", ":", "\\")):
        return None, "Enter only the database hostname, without http://, port or credentials."
    for key, label in (("user", "database username"), ("password", "database password"),
                       ("database", "database name")):
        if not values[key] or values[key].startswith("REPLACE_"):
            return None, f"Enter a valid {label}."
    if any(len(str(value)) > 1024 for value in values.values()):
        return None, "One or more settings are too long."
    if values["ssl_ca"] and not Path(values["ssl_ca"]).expanduser().is_file():
        return None, "The optional CA certificate path does not exist on this computer."
    if values["ssl_ca"]:
        values["ssl_ca"] = str(Path(values["ssl_ca"]).expanduser().resolve())
    return values, None


def _toml_text(values: dict) -> str:
    # JSON-style quoted basic strings are also valid TOML strings.
    lines = ["# Personal Goodwill settings, generated locally. NEVER share this file.", "[mysql]"]
    for key in ("host", "port", "user", "password", "database", "ssl_ca"):
        if key == "ssl_ca" and not values.get(key):
            continue
        val = values[key]
        lines.append(f"{key} = {val if key == 'port' else json.dumps(val, ensure_ascii=False)}")
    lines += ["", "[app]", f"base_url = {json.dumps(URL)}", ""]
    return "\n".join(lines)


def save_user_config(secret_path: Path, values: dict) -> None:
    import tomllib
    content = _toml_text(values)
    tomllib.loads(content)  # Defensive verification before writing.
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=secret_path.parent,
                                         prefix=".goodwill-config-", delete=False) as file:
            temp = Path(file.name)
            os.chmod(temp, 0o600)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, secret_path)
        os.chmod(secret_path, 0o600)
    finally:
        if temp and temp.exists():
            temp.unlink()


def probe_connection(values: dict) -> bool:
    """Optional read-only check; no schema changes or test rows."""
    try:
        import mysql.connector
        opts = {k: values[k] for k in ("host", "port", "user", "password", "database")}
        if values.get("ssl_ca"):
            opts.update(ssl_ca=values["ssl_ca"], ssl_verify_cert=True, ssl_verify_identity=True)
        conn = mysql.connector.connect(**opts, connection_timeout=6)
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                cursor.close()
        finally:
            conn.close()
        return True
    except Exception:
        # Do not show detailed connector errors that may contain hostnames/credentials.
        return False


def _page(values: dict, token: str, feedback: str = "", error: bool = False) -> bytes:
    def esc(key):
        return html.escape(str(values.get(key, "")), quote=True)
    alert = (f'<div class="alert {"bad" if error else "good"}">{html.escape(feedback)}</div>'
             if feedback else "")
    markup = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Goodwill Setup</title>
<style>body{{margin:0;background:#edf3f7;color:#18364f;font:16px system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:580px;margin:6vh auto;padding:30px;background:#fff;border-radius:18px;box-shadow:0 7px 24px #18364f20}}
h1{{margin:0 0 5px;font-size:29px}}p{{color:#526b80;line-height:1.5}}label{{display:block;margin:17px 0 5px;font-weight:600}}
input{{box-sizing:border-box;width:100%;padding:12px;font-size:16px;border:1px solid #c4d2df;border-radius:8px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}button{{background:#18364f;border:0;color:white;padding:12px 19px;border-radius:8px;font-weight:650;cursor:pointer;margin:22px 8px 0 0}}
button.secondary{{background:#e4ecf3;color:#18364f}}.note{{font-size:13px}}.alert{{padding:12px;border-radius:8px;margin-top:15px}}.bad{{background:#fff0f0;color:#8d2020}}.good{{background:#e8f6eb;color:#145f38}}
@media(max-width:640px){{main{{margin:0;padding:22px;border-radius:0}}.grid{{grid-template-columns:1fr}}}}</style></head><body>
<main><h1>Goodwill Pricing — First-time Setup</h1><p>Connect to your school VPN, then enter your authorized database credentials. These settings stay on <strong>this computer</strong>.</p>
{alert}
<form method="post" action="/setup/{html.escape(token,quote=True)}" autocomplete="off">
<input type="hidden" name="csrf" value="{html.escape(token,quote=True)}">
<label for="host">School database host / IP</label><input id="host" name="host" value="{esc('host')}" required maxlength="255">
<div class="grid"><div><label for="port">Port</label><input id="port" type="number" min="1" max="65535" name="port" value="{esc('port')}" required></div>
<div><label for="database">Database / Schema</label><input id="database" name="database" value="{esc('database')}" required></div></div>
<label for="user">Your database username</label><input id="user" name="user" value="{esc('user')}" required>
<label for="password">Your database password</label><input id="password" name="password" type="password" value="" required autocomplete="new-password">
<label for="ssl_ca">CA certificate path (only if required)</label><input id="ssl_ca" name="ssl_ca" value="{esc('ssl_ca')}" placeholder="Optional absolute path">
<p class="note">These are MySQL/MariaDB credentials, not your Goodwill Staff login. No password is included in the shared APP.</p>
<button name="action" value="save" type="submit">Save & Open Goodwill</button><button name="action" value="test" type="submit" class="secondary">Test VPN / Database Connection</button>
</form></main></body></html>'''
    return markup.encode("utf-8")


def run_setup_wizard(secret_path: Path, defaults: dict, *, timeout_seconds: int = 900,
                     open_browser=webbrowser.open) -> bool:
    """Run a one-time, token-protected LOCAL browser setup, then exit server."""
    state = {"done": False, "values": _existing_public_values(secret_path, defaults)}
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # Never log POST body or user credentials.
            pass

        def _respond(self, status: int, body: bytes):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self):
            return (self.path == f"/setup/{token}"
                    and self.headers.get("Host", "") in
                    {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"})

        def do_GET(self):
            if not self._allowed():
                self._respond(404, b"Not Found")
                return
            self._respond(200, _page(state["values"], token))

        def do_POST(self):
            if not self._allowed():
                self._respond(404, b"Not Found")
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 8192:
                    raise ValueError
                data = urllib.parse.parse_qs(self.rfile.read(size).decode("utf-8"),
                                             keep_blank_values=True, max_num_fields=12)
                received = {k: v[0] for k, v in data.items() if len(v) == 1}
                if not hmac.compare_digest(received.get("csrf", ""), token):
                    self._respond(403, b"Forbidden")
                    return
                action = received.get("action", "")
                if action not in {"save", "test"}:
                    raise ValueError
                raw = {k: received.get(k, "") for k in
                       ("host", "port", "user", "password", "database", "ssl_ca")}
                # Don't retain the submitted password in the setup server's state.
                state["values"] = {k: raw[k] for k in raw if k != "password"}
                valid, message = validate_form(raw)
                if message:
                    self._respond(200, _page(state["values"], token, message, error=True))
                    return
                if action == "test":
                    result = probe_connection(valid)
                    self._respond(200, _page(state["values"], token,
                        "Connection test passed (read-only). Enter password again to save." if result else
                        "Connection failed. Check VPN, database host, credentials and optional CA path.",
                        error=not result))
                    return
                save_user_config(secret_path, valid)
                state["done"] = True
                success = b'''<!doctype html><html><head><meta charset="utf-8"><title>Goodwill Setup Complete</title><meta http-equiv="Cache-Control" content="no-store"></head><body style="font:17px system-ui;padding:3em;color:#18364f"><h2>Connection settings saved on this computer.</h2><p>Goodwill will open in a new browser tab. Keep your school VPN connected.</p><p>You may close this setup tab.</p></body></html>'''
                self._respond(200, success)
            except Exception:
                self._respond(400, _page(state["values"], token,
                                         "Unable to process these settings. Please check the fields.", error=True))

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1.0
        address = f"http://127.0.0.1:{server.server_port}/setup/{token}"
        print("Opening local Goodwill first-run setup page (no credentials shown)...")
        if not open_browser(address):
            report("Goodwill Setup", "Open this local setup address in your browser:\n" + address)
        deadline = time.monotonic() + timeout_seconds
        while not state["done"] and time.monotonic() < deadline:
            server.handle_request()
        return state["done"]


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn:
        try:
            conn.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def check_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/_stcore/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def serve() -> None:
    project = resource_root() / "goodwill_app"
    script = project / "app.py"
    if not script.is_file():
        raise RuntimeError(f"Bundled app.py not found: {script}")
    sys.path.insert(0, str(project))
    os.chdir(user_dir())
    for key in list(os.environ):
        if key.startswith("MYSQL_"):
            os.environ.pop(key, None)
    # PyInstaller can make Streamlit misdetect this packaged app as its own
    # development environment, where explicit server.port is forbidden.
    # Set the environment before importing Streamlit; the CLI flag ensures
    # the same setting is honored when configuration is parsed again.
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
    from streamlit.web.cli import main
    sys.argv = ["streamlit", "run", str(script),
                "--global.developmentMode=false",
                "--server.address=127.0.0.1", f"--server.port={PORT}",
                "--server.headless=true", "--server.fileWatcherType=none",
                "--browser.gatherUsageStats=false", "--server.enableCORS=true",
                "--server.enableXsrfProtection=true"]
    main()


def run_controller(child: subprocess.Popen, url: str) -> None:
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title("Goodwill Pricing — Local App")
        root.geometry("440x190")
        root.resizable(False, False)
        tk.Label(root, text="Goodwill Pricing", font=("Arial", 18, "bold"), fg="#18364F").pack(pady=(20, 8))
        tk.Label(root, text="Running locally · VPN required for school database").pack()
        tk.Button(root, text="Open Goodwill in Browser", command=lambda: webbrowser.open(url)).pack(pady=12)
        def stop():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
            root.destroy()
        tk.Button(root, text="Stop App", command=stop).pack()
        root.protocol("WM_DELETE_WINDOW", stop)
        def poll():
            if child.poll() is not None:
                root.destroy()
            else:
                root.after(1500, poll)
        poll()
        root.mainloop()
    except Exception:
        try:
            child.wait()
        except KeyboardInterrupt:
            child.terminate()


def launch() -> int:
    home = user_dir()
    home.mkdir(parents=True, exist_ok=True)
    secret_path, first_run = prepare_user_config(home)
    if first_run or not settings_appear_valid(secret_path):
        if not run_setup_wizard(secret_path, team_defaults()):
            report("Goodwill Setup", "Database configuration was not saved. Reopen Goodwill to try again.")
            return 2
    if not port_available(PORT):
        report("Goodwill Already Running", f"Local port {PORT} is in use. Close the existing Goodwill/Streamlit process before launching again.")
        return 3
    log_file = home / "goodwill_runtime.log"
    with log_file.open("ab") as logs:
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--serve"]
        else:
            command = [sys.executable, str(Path(__file__).resolve()), "--serve"]
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("MYSQL_"):
                env.pop(key, None)
        child = subprocess.Popen(command, cwd=home, stdout=logs, stderr=subprocess.STDOUT, env=env)
        for _ in range(120):
            if child.poll() is not None:
                report("Goodwill Start Error", f"The app stopped during startup. Review the local log:\n{log_file}")
                return 4
            if check_ready(URL):
                webbrowser.open(URL)
                run_controller(child, URL)
                return 0
            time.sleep(0.5)
        child.terminate()
        report("Goodwill Start Timeout", f"Could not start the local website. See:\n{log_file}")
        return 5


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        raise SystemExit(launch())
