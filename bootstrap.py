"""One-command local setup. Dependencies are checked locally before any network access."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def runtime_pins(root):
    import tomllib

    pins = []
    for line in (root / "requirements.lock").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        requirement, _, marker = line.partition(";")
        # The lock file's only platform marker is for the Windows tray dependencies.
        if marker:
            if marker.strip() != 'sys_platform == "win32"':
                raise ValueError("Unsupported requirements.lock platform marker: " + marker)
            if sys.platform != "win32":
                continue
        pins.append(requirement.strip().split("==", 1))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return pins + [[project["name"], project["version"]]]


def launch_desktop(root, python, port, timeout=35):
    directory = Path(os.environ.get("APPROVAL_LAB_DATA_DIR", "data"))
    directory = (directory if directory.is_absolute() else root / directory).resolve()
    logs = directory / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="desktop-start-", suffix=".json", dir=directory, delete=False) as file:
        status_file = Path(file.name)
    try:
        with (logs / "launcher.log").open("ab") as output:
            child = subprocess.Popen(
                [str(python.with_name("pythonw.exe")), "-m", "approval_lab.desktop", "--port", str(port),
                 "--startup-file", str(status_file)],
                cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if status_file.stat().st_size:
                status = json.loads(status_file.read_text(encoding="utf-8"))
                if status["status"] == "ready":
                    print("Approval Lab is running in the Windows system tray.", flush=True)
                    return 0
                print("Approval Lab startup failed: " + status.get("message", "") + f"\nLogs: {logs}",
                      file=sys.stderr)
                return 1
            if child.poll() is not None:
                print(f"Approval Lab exited before startup completed. Logs: {logs}", file=sys.stderr)
                return 1
            time.sleep(0.1)
        print(f"Approval Lab startup timed out. Check the system tray and logs: {logs}", file=sys.stderr)
        return 1
    finally:
        status_file.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Start Approval Lab with its isolated environment")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--install-only", action="store_true")
    parser.add_argument("--foreground", action="store_true", help="Run in this terminal instead of the Windows tray")
    args = parser.parse_args()
    if sys.version_info < (3, 11):  # noqa: UP036 - bootstrap runs before the project's Python requirement check
        parser.error("Python 3.11 or newer is required.")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    root = Path(__file__).resolve().parent
    environment = (root / ".venv").resolve()
    if not environment.is_relative_to(root):
        parser.error(".venv must resolve inside the project directory.")
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        subprocess.check_call([sys.executable, "-m", "venv", str(environment)], cwd=root)
    pins = runtime_pins(root)
    check = subprocess.run([
        str(python), "-c",
        ("import importlib.metadata as m,json,sys; "
         "sys.exit(0 if all(m.version(n)==v for n,v in json.loads(sys.argv[1])) else 1)"),
        json.dumps(pins),
    ], cwd=root, capture_output=True, check=False)
    if check.returncode:
        print("Installing Approval Lab dependencies in .venv ...", flush=True)
        subprocess.check_call([
            str(python), "-m", "pip", "install", "--disable-pip-version-check", "--timeout", "60",
            "-r", "requirements.lock", "-e", ".",
        ], cwd=root)
    if args.install_only:
        print("Approval Lab dependencies are ready.")
        return 0
    if sys.platform == "win32" and not args.foreground:
        return launch_desktop(root, python, args.port)
    print(f"Approval Lab: http://127.0.0.1:{args.port}", flush=True)
    try:
        return subprocess.call([str(python), "-m", "approval_lab", "--port", str(args.port)], cwd=root)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
