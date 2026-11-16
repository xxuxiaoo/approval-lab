import json
import socket
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from approval_lab import desktop
from approval_lab.app import create_app
from approval_lab.desktop_service import LocalService


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for desktop state")


def test_occupied_port_never_stops_or_reuses_an_unrelated_server(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.listen()
        service = LocalService(tmp_path, port)
        with pytest.raises(RuntimeError, match="端口"):
            service.start()
        service.stop()
        assert listener.getsockname()[1] == port
        assert not (tmp_path / "master.key").exists()


def test_same_data_directory_cannot_run_a_second_scheduler_on_another_port(tmp_path):
    first, second = LocalService(tmp_path, free_port()), LocalService(tmp_path, free_port())
    first.start()
    try:
        with pytest.raises(RuntimeError, match="启动失败"):
            second.start()
        assert not second.running
        with httpx.Client(trust_env=False) as client:
            assert client.get(first.url + "/api/health").json()["ok"] is True
    finally:
        second.stop()
        first.stop()


class FakeSignal:
    def __init__(self):
        self.event = threading.Event()

    def wait(self):
        ready = self.event.wait(0.02)
        # Preserve a signal arriving just after the wait timed out. Clearing it
        # unconditionally can lose the simulated duplicate-launch notification.
        if ready:
            self.event.clear()
        return ready


class FakeIcon:
    def __init__(self, name, image, title, menu):
        self.title, self.menu = title, menu
        self.stopped = threading.Event()

    def run(self, setup):
        setup(self)
        assert self.stopped.wait(15)

    def stop(self):
        self.stopped.set()

    def update_menu(self):
        pass


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


@pytest.fixture
def fake_tray(monkeypatch):
    monkeypatch.setitem(sys.modules, "pystray", SimpleNamespace(
        Icon=FakeIcon, Menu=FakeMenu, MenuItem=lambda text, action, **kwargs: action,
    ))
    monkeypatch.setattr(desktop, "make_icon", lambda: None)


def test_tray_ready_browser_repeat_launch_restart_and_exit(tmp_path, monkeypatch, fake_tray):
    # Only the OS tray is faked. Readiness, shutdown, restart and HTTP requests are real and offline.
    opened, errors = [], []

    def open_browser(url):
        with httpx.Client(trust_env=False, timeout=2) as client:
            assert client.get(url + "/api/health").json()["ok"] is True
        opened.append(url)
        return True

    monkeypatch.setattr(desktop.webbrowser, "open", open_browser)
    monkeypatch.setattr(desktop, "message_box", lambda text, **kwargs: errors.append(text))
    service = LocalService(tmp_path, free_port(), app_factory=lambda directory: create_app(directory, scheduler=False))
    signal = FakeSignal()
    startup = tmp_path / "started.json"
    app = desktop.TrayApplication(service, signal, startup)
    thread = threading.Thread(target=app.run)
    thread.start()
    try:
        wait_for(lambda: len(opened) == 1)
        assert json.loads(startup.read_text())["status"] == "ready"
        original_thread = service.thread
        original_key = (tmp_path / "master.key").read_bytes()
        signal.event.set()  # Simulate a second script launch.
        wait_for(lambda: len(opened) == 2)
        assert service.thread is original_thread
        # Invoke the actual callbacks wired to the tray menu.
        app.icon.menu.items[3]()  # Restart.
        wait_for(lambda: len(opened) == 3)
        assert service.thread is not original_thread
        assert not original_thread.is_alive()
        assert (tmp_path / "master.key").read_bytes() == original_key
        app.icon.menu.items[6]()  # Exit.
        thread.join(8)
        assert not thread.is_alive()
        assert not service.running
        assert not errors
        # Shutdown released the data lock and listening port.
        service.start()
        service.stop()
    finally:
        if thread.is_alive():
            app.dispatch("exit")
            thread.join(8)
        service.stop()


def test_failed_start_reports_reason_without_opening_browser_or_leaving_a_service(
    tmp_path, monkeypatch, fake_tray,
):
    opened, errors = [], []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(desktop, "message_box", lambda text, **kwargs: errors.append(text))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        service = LocalService(tmp_path, listener.getsockname()[1])
        startup = tmp_path / "startup.json"
        app = desktop.TrayApplication(service, FakeSignal(), startup)
        assert app.run() == 1
        status = json.loads(startup.read_text())
        assert status["status"] == "error" and "端口" in status["message"]
        assert errors and not opened
        assert app.icon.stopped.is_set()
        assert not service.running


def test_confirmation_keeps_an_active_test_running_when_cancelled(monkeypatch):
    app = desktop.TrayApplication.__new__(desktop.TrayApplication)
    app.service = SimpleNamespace(active_runs=1)
    prompts = []
    monkeypatch.setattr(desktop, "message_box", lambda text, **kwargs: prompts.append((text, kwargs)) or False)
    assert app.confirm_stop() is False
    assert "已计费" in prompts[0][0]
    assert prompts[0][1]["confirm"] is True
    app.service.active_runs = 0
    assert app.confirm_stop() is True
    assert len(prompts) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named desktop event")
def test_native_signal_reuses_existing_instance_and_is_released_after_exit(tmp_path):
    first = desktop.DesktopSignal(tmp_path)
    second = desktop.DesktopSignal(tmp_path / ".")
    try:
        assert first.owner
        assert not second.owner
        second.request_open()
        assert first.wait()
        assert not first.wait()
    finally:
        second.close()
        first.close()
    third = desktop.DesktopSignal(tmp_path)
    try:
        assert third.owner
    finally:
        third.close()
