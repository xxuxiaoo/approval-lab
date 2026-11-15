"""Native Windows tray + duplicate startup check, with isolated data and no paid requests.

Run: python -m tests.windows_tray_smoke
The real tray is briefly visible. Browser openings are checked against local health, not launched.
"""

import ctypes
import logging
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx

from approval_lab import desktop
from approval_lab.desktop_service import LocalService

logger = logging.getLogger(__name__)


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for native tray state")


def main():
    if sys.platform != "win32":
        raise SystemExit("This smoke check requires a Windows desktop.")
    from ctypes import wintypes

    workspace = Path(__file__).resolve().parents[1]
    artifacts = workspace / "artifacts"
    artifacts.mkdir(exist_ok=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsMenu.argtypes = [wintypes.HMENU]
    user32.IsMenu.restype = wintypes.BOOL
    errors, opened = [], []
    with tempfile.TemporaryDirectory(prefix="tray-", dir=artifacts) as temporary:
        directory = Path(temporary).resolve()
        assert directory.is_relative_to(artifacts.resolve())
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        service = LocalService(directory, port)
        signal = desktop.DesktopSignal(directory)
        assert signal.owner
        app = desktop.TrayApplication(service, signal)

        def open_browser(url):
            with httpx.Client(trust_env=False, timeout=2) as client:
                assert client.get(url + "/api/health").json()["ok"] is True
                assert client.get(url + "/api/overview").json()["total_runs"] == 0
            opened.append(url)
            return True

        def exercise():
            try:
                wait_for(lambda: len(opened) == 1)
                hwnd = app.icon._hwnd
                assert app.icon.visible and user32.IsWindow(hwnd)
                assert user32.IsMenu(app.icon._menu_handle[0])
                original_thread = service.thread
                environment = os.environ.copy()
                environment["APPROVAL_LAB_DATA_DIR"] = str(directory)
                environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
                # Run the actual batch entry point without a terminal's Python/Conda PATH.
                # Its requested port must not create another instance for the same data directory.
                duplicate = subprocess.run(
                    [os.environ["COMSPEC"], "/d", "/c", str(workspace / "start.bat"),
                     "--port", str(8766 if port != 8766 else 8767)],
                    cwd=workspace, env=environment, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, check=False,
                    timeout=25, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                assert duplicate.returncode == 0, duplicate.stdout + duplicate.stderr
                wait_for(lambda: len(opened) == 2)
                assert service.thread is original_thread and original_thread.is_alive()
                items = tuple(app.icon.menu)
                items[3](app.icon)  # Invoke the native menu's Restart callback.
                wait_for(lambda: len(opened) == 3)
                assert service.thread is not original_thread
                assert not original_thread.is_alive()
                items[6](app.icon)  # Invoke the native menu's Exit callback.
            except Exception as exc:
                logger.exception("Native tray smoke failed")
                errors.append(exc)
                app.dispatch("exit")

        driver = threading.Thread(target=exercise, name="native-tray-smoke")
        try:
            with patch.object(desktop.webbrowser, "open", open_browser), patch.object(
                desktop, "message_box", lambda text, **kwargs: errors.append(AssertionError(text)) or False,
            ):
                driver.start()
                assert app.run() == 0
                driver.join(5)
            assert not driver.is_alive()
            assert not service.running
            assert not user32.IsWindow(app.icon._hwnd)
            if errors:
                raise AssertionError(errors)
            assert opened == [service.url] * 3
            assert not list(directory.glob("desktop-start-*"))
        finally:
            service.stop()
            signal.close()
    print("windows tray smoke: ok; browser_ready_checks=3; duplicate_reused=True; restart=True; exit=True")


if __name__ == "__main__":
    main()
