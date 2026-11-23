"""Build a native .app (macOS) or .exe (Windows) from the CURRENT repo.

Place desktop_packager/ inside the goodwillpricing repo. Run from same OS
as intended recipients, in a venv with all project dependencies installed.
Never bundles secrets.toml, .env, databases, or private keys.
"""
from __future__ import annotations
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

SKIP_DIRS = {".git", ".idea", ".venv", "venv", "__pycache__", "tests", "scripts",
             "migrations", ".desktop_build", "dist", "build", "desktop_packager", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", ".codex", "node_modules"}
SKIP_SUFFIX = {".db", ".sqlite", ".sqlite3", ".key", ".p12", ".pfx", ".sql",
               ".log", ".zip", ".tar", ".gz", ".bak", ".pyc", ".xlsx", ".xls", ".csv"}
SKIP_FILES = {"secrets.toml", ".env", "ca.key", "id_rsa", "id_ed25519", "credentials.json"}
KEEP_SUFFIX = {".py", ".css", ".html", ".htm", ".svg", ".png", ".jpg", ".jpeg",
               ".gif", ".ico", ".icns", ".json", ".toml", ".txt", ".md", ".woff2"}


def safe_files(project: Path):
    for path in project.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(project)
        if any(part in SKIP_DIRS for part in relative.parts[:-1]):
            continue
        if any(part.startswith(".") and part != ".streamlit" for part in relative.parts[:-1]):
            continue
        if relative.name.lower() in SKIP_FILES or "secret" in relative.name.lower():
            continue
        # Exclude development/maintenance tools from the shipped executable.
        if len(relative.parts) == 1 and relative.suffix == ".py" and (
            relative.stem.startswith(("test_", "tests_", "apply_", "check_", "reset_", "run_"))
            or relative.name == "create_user.py"
            or not relative.stem.isidentifier()
        ):
            continue
        if relative.suffix.lower() not in KEEP_SUFFIX:
            if relative.name != "ca.pem":
                continue
        if relative.name.endswith(".pem") and relative.name != "ca.pem":
            continue
        if relative.parts[0] == ".streamlit" and relative.as_posix() != ".streamlit/config.toml":
            continue
        if path.stat().st_size > 20_000_000:
            continue
        yield path, relative


def stage_project(project: Path, dest: Path) -> int:
    if not (project / "app.py").is_file():
        raise SystemExit(f"Expected app.py at project root: {project}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    count = 0
    for source, rel in safe_files(project):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    if any(p.name in SKIP_FILES or "secret" in p.name.lower() for p in dest.rglob("*")):
        raise SystemExit("Safety check failed: credential file appeared in bundle")
    return count


def installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def validate_team_profile(packager: Path) -> None:
    """Prevent credentials from leaking into the shared team defaults."""
    path = packager / "template" / "team_profile.toml"
    if not path.is_file():
        raise SystemExit(f"Missing non-secret team profile: {path}")
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise SystemExit("Invalid team_profile.toml") from exc
    if set(parsed) - {"mysql", "app"}:
        raise SystemExit("Team profile cannot include additional sections")
    mysql = parsed.get("mysql", {})
    app = parsed.get("app", {})
    if not isinstance(mysql, dict) or not isinstance(app, dict):
        raise SystemExit("Team profile must use [mysql] and [app] tables")
    if set(mysql) - {"host", "port", "database"} or set(app) - {"base_url"}:
        raise SystemExit("Team profile may contain host, port, database and base_url only; no credentials")
    if app.get("base_url", "http://127.0.0.1:8501") != "http://127.0.0.1:8501":
        raise SystemExit("Desktop reset links must remain local only")


def build(project: Path, inspect_only: bool = False) -> None:
    packager = Path(__file__).resolve().parent
    validate_team_profile(packager)
    work = project / ".desktop_build"
    stage = work / "goodwill_app"
    count = stage_project(project, stage)
    print(f"Staged {count} application files without secrets in: {stage}")
    print("Builds must be made on the recipients' OS; macOS .app and Windows .exe are separate.")
    if inspect_only:
        return
    if not installed("PyInstaller"):
        raise SystemExit("PyInstaller not installed. Run: python -m pip install pyinstaller")
    if not installed("streamlit"):
        raise SystemExit("Streamlit missing from this Python. Activate the project's .venv first.")
    sep = ";" if os.name == "nt" else ":"
    data_args = ["--add-data", f"{stage}{sep}goodwill_app",
                 "--add-data", f"{packager / 'template'}{sep}template"]
    opts = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", "GoodwillPricing", "--distpath", str(work / "dist"),
            "--workpath", str(work / "pyinstaller_work"), "--specpath", str(work / "spec"),
            "--paths", str(stage), "--collect-all", "streamlit"]
    for lib in ["altair", "pyarrow", "reportlab", "PIL", "pydeck", "mysql", "certifi"]:
        if installed(lib):
            opts += ["--collect-all", lib]
    # Local imports are not statically visible in launcher.py: ask PyInstaller
    # to scan every local module and follow its dependency graph.
    for file in sorted(stage.glob("*.py")):
        if file.stem.isidentifier() and file.stem != "__init__":
            opts += ["--hidden-import", file.stem]
    opts += data_args + [str(packager / "launcher.py")]
    print("Building GoodwillPricing for:", sys.platform)
    subprocess.run(opts, cwd=project, check=True)
    dist = work / "dist"
    if sys.platform == "darwin":
        print(f"Build output (Mac): {dist / 'GoodwillPricing.app'}")
    elif os.name == "nt":
        print(f"Build output (Windows): {dist / 'GoodwillPricing' / 'GoodwillPricing.exe'}")
        print("Distribute the ENTIRE GoodwillPricing folder, not only the .exe")
    else:
        print(f"Build output: {dist / 'GoodwillPricing'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--inspect-only", action="store_true", help="Stage and audit files, without PyInstaller")
    args = parser.parse_args()
    build(args.project.resolve(), args.inspect_only)
