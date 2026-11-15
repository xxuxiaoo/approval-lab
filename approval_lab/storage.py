import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def now_iso():
    return datetime.now(UTC).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class InstanceLock:
    """OS-released lock: only one paid-request scheduler can own a data directory."""
    def __init__(self, directory):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "instance.lock"
        self.file = None

    def acquire(self):
        self.file = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError("这个数据目录已被另一个 Approval Lab 实例占用，请勿重复启动。") from exc

    def release(self):
        if self.file:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


class Store:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "approval-lab.sqlite3"
        key_path = self.directory / "master.key"
        if not key_path.exists():
            if self.path.exists():
                raise RuntimeError("master.key 丢失；请恢复密钥文件，不要覆盖原数据库。")
            try:
                with key_path.open("xb") as file:
                    file.write(Fernet.generate_key())
                os.chmod(key_path, 0o600)
            except FileExistsError:
                pass
        self.cipher = Fernet(key_path.read_bytes().strip())
        self.initialize()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS providers (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL,
                    protocol TEXT NOT NULL, encrypted_key TEXT NOT NULL, notes TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT, config TEXT NOT NULL, snapshot TEXT NOT NULL,
                    total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '{}', error TEXT, schedule_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    ordinal INTEGER NOT NULL, case_id TEXT NOT NULL, repeat_index INTEGER NOT NULL,
                    case_data TEXT NOT NULL, decision TEXT, reason TEXT, raw_response TEXT,
                    error TEXT, latency_ms REAL NOT NULL, usage TEXT NOT NULL,
                    returned_model TEXT, response_id TEXT, request_id TEXT,
                    UNIQUE(run_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL,
                    enabled INTEGER NOT NULL, next_run_at TEXT NOT NULL, last_run_id TEXT,
                    last_error TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS result_run_idx ON results(run_id);
                CREATE TABLE IF NOT EXISTS review_turns (
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    ordinal INTEGER NOT NULL, case_id TEXT NOT NULL, repeat_index INTEGER NOT NULL,
                    stage TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    request TEXT NOT NULL, response TEXT, usage TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]',
                    error TEXT, latency_ms REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS review_turn_run_idx ON review_turns(run_id,ordinal,id);
                CREATE TABLE IF NOT EXISTS native_records (
                    id TEXT PRIMARY KEY, record TEXT NOT NULL, metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS run_created_idx ON runs(created_at);
                PRAGMA user_version=1;
            """)

    def encrypt(self, key):
        return self.cipher.encrypt(key.encode()).decode()

    def decrypt(self, value):
        try:
            return self.cipher.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("无法解密 API Key，请恢复正确的 master.key 或重新保存 Key。") from exc

    def query(self, sql, params=()):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        with self.connect() as conn:
            cursor = conn.execute(sql, params)
            return cursor.lastrowid

    def provider(self, provider_id, *, secret=False):
        row = self.one("SELECT * FROM providers WHERE id=?", (provider_id,))
        if not row:
            raise ValueError("中转站不存在")
        encrypted = row.pop("encrypted_key")
        if secret:
            row["api_key"] = self.decrypt(encrypted)
        row["has_key"] = bool(encrypted)
        return row

    def providers(self):
        return [self.provider(row["id"]) for row in self.query("SELECT id FROM providers ORDER BY id")]

    def save_provider(self, value, provider_id=None):
        fields = value.model_dump()
        key = fields.pop("api_key")
        if provider_id:
            existing = self.one("SELECT * FROM providers WHERE id=?", (provider_id,))
            if not existing:
                raise ValueError("中转站不存在")
            encrypted = self.encrypt(key) if key else existing["encrypted_key"]
            if not key and fields["base_url"] != existing["base_url"]:
                raise ValueError("更换 API 地址时请重新填写 Key，避免把旧 Key 发往新地址。")
            self.execute(
                "UPDATE providers SET name=?,base_url=?,protocol=?,encrypted_key=?,notes=?,updated_at=? WHERE id=?",
                (fields["name"], fields["base_url"], fields["protocol"], encrypted,
                 fields["notes"], now_iso(), provider_id),
            )
        else:
            if not key:
                raise ValueError("请填写 API Key；本地无鉴权服务可填写 local")
            provider_id = self.execute(
                "INSERT INTO providers(name,base_url,protocol,encrypted_key,notes,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (fields["name"], fields["base_url"], fields["protocol"], self.encrypt(key),
                 fields["notes"], now_iso(), now_iso()),
            )
        return self.provider(provider_id)
