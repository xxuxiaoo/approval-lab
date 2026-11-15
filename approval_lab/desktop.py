"""Windows tray launcher. The web/CLI/Docker entry points remain independent."""

import argparse
import ctypes
import hashlib
import json
import logging
import os
import queue
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .desktop_service import LocalService

logger = logging.getLogger(__name__)


class DesktopSignal:
    """An OS-released, per-data-directory event also lets a second launch open the first."""

    def __init__(self, directory):
        from ctypes import wintypes

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.SetEvent.argtypes = [wintypes.HANDLE]
        self.kernel.SetEvent.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        identity = os.path.normcase(str(Path(directory).resolve())).encode("utf-8")
        name = "Local\\ApprovalLab.Open." + hashlib.sha256(identity).hexdigest()
        ctypes.set_last_error(0)
        self.handle = self.kernel.CreateEventW(None, False, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.owner = ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS

    def request_open(self):
        if not self.kernel.SetEvent(self.handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait(self):
        result = self.kernel.WaitForSingleObject(self.handle, 250)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def write_startup(path, status, message=""):
    if path:
        # The parent reads only a complete JSON document.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"status": status, "message": message}), encoding="utf-8")
        temporary.replace(path)


def message_box(text, *, confirm=False):
    return ctypes.windll.user32.MessageBoxW(
        None, text, "Approval Lab · 审核模型实验室", 0x10000 | (0x31 if confirm else 0x10)
    ) == 1


def make_icon():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, 63, 63), radius=17, fill="#23483d")
    draw.line([(16, 32), (27, 43), (46, 22)], fill="#eef2df", width=7, joint="curve")
    draw.ellipse((45, 45, 55, 55), fill="#dd9259")
    return image


class TrayApplication:
    def __init__(self, service, signal, startup_file=None):
        import pystray

        self.service, self.signal, self.startup_file = service, signal, startup_file
        self.closed = threading.Event()
        self.commands = queue.Queue()
        self.status = "正在启动…"
        self.busy = True
        self.pending = set()
        self.pending_lock = threading.Lock()
        self.result = 0
        self.icon = pystray.Icon("approval-lab", make_icon(), "Approval Lab · 正在启动", pystray.Menu(
            pystray.MenuItem(lambda _: self.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开网页", lambda *_: self.dispatch("open"), default=True),
            pystray.MenuItem("重启服务", lambda *_: self.dispatch("restart"), enabled=lambda _: not self.busy),
            pystray.MenuItem("打开日志目录", lambda *_: self.dispatch("logs")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出 Approval Lab", lambda *_: self.dispatch("exit")),
        ))

    def dispatch(self, action):
        with self.pending_lock:
            if action in self.pending or self.closed.is_set():
                return
            self.pending.add(action)
        self.commands.put(action)

    def set_status(self, status, busy=False):
        self.status, self.busy = status, busy
        self.icon.title = "Approval Lab · " + status
        self.icon.update_menu()

    def open_website(self):
        if self.service.running:
            logger.info("Opening web UI: %s", self.service.url)
            if not webbrowser.open(self.service.url):
                logger.warning("The default browser could not be opened")
                message_box("服务已启动，但无法打开默认浏览器。请手动访问：\n" + self.service.url)
        else:
            message_box("服务已停止，请在托盘菜单选择“重启服务”。")

    def confirm_stop(self):
        count = self.service.active_runs
        return not count or message_box(
            f"还有 {count} 轮测试正在运行或排队。重启或退出会中断测试，"
            "在途请求可能已计费。建议先在网页停止测试并等待完成。\n\n仍要继续吗？", confirm=True,
        )

    def watch_launches(self):
        while not self.closed.is_set():
            if self.signal.wait():
                self.dispatch("open")

    def work(self):
        while not self.closed.is_set():
            try:
                action = self.commands.get(timeout=0.5)
            except queue.Empty:
                if not self.busy and self.status.startswith("运行中") and not self.service.running:
                    self.set_status("服务已停止，可重启")
                    message_box("后台服务意外停止，请查看日志后从托盘重启。")
                continue
            try:
                if action in ("start", "restart"):
                    if action == "restart" and not self.confirm_stop():
                        continue
                    self.set_status("正在启动…" if action == "start" else "正在重启…", busy=True)
                    self.service.start() if action == "start" else self.service.restart()
                    self.set_status(f"运行中 · 127.0.0.1:{self.service.port}")
                    write_startup(self.startup_file, "ready")
                    self.startup_file = None
                    self.open_website()
                elif action == "open":
                    self.open_website()
                elif action == "logs":
                    os.startfile(str(self.service.directory / "logs"))
                elif action == "exit" and self.confirm_stop():
                    self.set_status("正在退出…", busy=True)
                    self.service.stop()
                    self.closed.set()
                    self.icon.stop()
            except Exception as exc:
                logger.exception("Tray action failed: %s", action)
                self.set_status("操作失败，请查看日志")
                write_startup(self.startup_file, "error", str(exc))
                message_box(str(exc) + "\n\n日志目录：" + str(self.service.directory / "logs"))
                if action == "start":
                    self.result = 1
                    self.closed.set()
                    self.icon.stop()
            finally:
                with self.pending_lock:
                    self.pending.discard(action)

    def run(self):
        worker = threading.Thread(target=self.work, name="approval-lab-tray-actions")
        watcher = threading.Thread(target=self.watch_launches, name="approval-lab-launch-signal")

        def setup(icon):
            try:
                icon.visible = True
                worker.start()
                watcher.start()
                self.dispatch("start")
            except Exception as exc:
                logger.exception("Could not initialize the system tray")
                self.result = 1
                write_startup(self.startup_file, "error", str(exc))
                message_box("无法创建系统托盘图标：" + str(exc))
                self.closed.set()
                icon.stop()

        try:
            self.icon.run(setup)
        finally:
            self.closed.set()
            for thread in (worker, watcher):
                if thread.ident:
                    thread.join()
            self.service.stop()
        return self.result


def main():
    parser = argparse.ArgumentParser(description="Approval Lab Windows tray")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--startup-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("The tray launcher requires Windows. Use python -m approval_lab on other systems.")
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    directory = Path(os.environ.get("APPROVAL_LAB_DATA_DIR", "data")).resolve()
    signal = None
    try:
        signal = DesktopSignal(directory)
        if not signal.owner:
            signal.request_open()
            write_startup(args.startup_file, "ready", "Opened the existing desktop instance.")
            return 0
        logs = directory / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(logs / "desktop.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        return TrayApplication(LocalService(directory, args.port), signal, args.startup_file).run()
    except Exception as exc:
        logger.exception("Desktop startup failed")
        write_startup(args.startup_file, "error", str(exc))
        message_box("启动失败：" + str(exc))
        return 1
    finally:
        if signal:
            signal.close()


if __name__ == "__main__":
    raise SystemExit(main())
