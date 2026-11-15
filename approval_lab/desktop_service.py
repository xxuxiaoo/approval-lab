"""An owned local server: tray actions never terminate unrelated processes."""

import logging
import socket
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from .app import create_app

logger = logging.getLogger(__name__)


class LocalService:
    def __init__(self, directory: Path, port: int, app_factory=create_app):
        self.directory = Path(directory).resolve()
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.app_factory = app_factory
        self.server = None
        self.thread = None
        self.failure = None

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive() and self.server.started)

    @property
    def active_runs(self):
        engine = getattr(self.server.config.app.state, "engine", None) if self.server else None
        return len(engine.tasks) if engine else 0

    def start(self, timeout=20):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("服务仍在运行或关闭中，请稍后再试。")
        self.failure = None
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", self.port))
        except OSError as exc:
            listener.close()
            raise RuntimeError(
                f"端口 {self.port} 无法使用。请先退出占用端口的程序，"
                "或通过 start.ps1 -Port 8766 指定其他端口。"
            ) from exc
        try:
            self.server = uvicorn.Server(uvicorn.Config(
                self.app_factory(self.directory), host="127.0.0.1", port=self.port,
                access_log=False, log_config=None, ws="none", lifespan="on",
                timeout_graceful_shutdown=5,
            ))

            def serve():
                try:
                    self.server.run(sockets=[listener])
                except BaseException as exc:
                    self.failure = exc
                    logger.exception("Local service failed")
                finally:
                    listener.close()

            self.thread = threading.Thread(target=serve, name="approval-lab-server")
            self.thread.start()
        except BaseException:
            listener.close()
            raise
        try:
            # Check our already-bound server, with proxies disabled, before opening the browser.
            with httpx.Client(trust_env=False, timeout=0.5) as client:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if not self.thread.is_alive():
                        raise RuntimeError(
                            "服务启动失败。请检查日志；若此数据目录已由终端启动，请先在原终端按 Ctrl+C 退出。"
                        ) from self.failure
                    if self.server.started:
                        try:
                            response = client.get(self.url + "/api/health")
                            if response.status_code == 200 and response.json().get("ok") is True:
                                return
                        except (httpx.HTTPError, ValueError):
                            pass
                    time.sleep(0.05)
            raise RuntimeError("服务启动超时，请检查本地日志。")
        except BaseException:
            self.stop()
            raise

    def stop(self, timeout=15):
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise RuntimeError("服务仍在关闭中，请稍候。不会强制终止进程或另开一个服务。")

    def restart(self):
        self.stop()
        self.start()
