"""Obtain the actual classifier request from an unmodified, installed Claude Code.

Only an isolated fixture, fake key, loopback HTTP server, and a print-only tool call
are used. This does not import personal sessions/settings or call a paid model.
Anthropic's prompt is cached locally, rather than redistributed in this repository.
"""

import hashlib
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SUPPORTED_VERSION = "2.1.317"


def capture_claude(directory):
    executable = shutil.which("claude")
    if not executable:
        raise ValueError("未找到官方 Claude Code。安装 2.1.317 后点击准备；Codex 场景可直接使用。")
    version = subprocess.run([executable, "--version"], check=False, capture_output=True, timeout=10,
                             text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    if not version.startswith(SUPPORTED_VERSION + " "):
        raise ValueError(f"当前官方 CLI 为 {version[:80]}；适配器仅核验过 {SUPPORTED_VERSION}，请勿混用版本。")
    directory = Path(directory).resolve() / "official-cc-probe"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("config", "work"):
        (directory / name).mkdir(exist_ok=True)
    captured, main_calls = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            if length > 4_000_000:
                self.send_error(413)
                return
            body = json.loads(self.rfile.read(length) or "{}")
            system = json.dumps(body.get("system", ""))
            classifier = "security monitor for autonomous AI coding agents" in system
            if classifier:
                captured.append(body)
                content = [{"type": "text", "text": "<block>yes</block><category>Probe</category>"}]
            elif body.get("tools") and not main_calls:
                main_calls.append(True)
                # Safe even if the official host bypasses classification and executes it.
                content = [{"type": "tool_use", "id": "toolu_profile_probe", "name": "Bash",
                            "input": {"command": "python -c \"print(12345)\"",
                                      "description": "Print a fixture marker"}}]
            else:
                content = [{"type": "text", "text": "Profile fixture complete."}]
            stop = "tool_use" if content[0]["type"] == "tool_use" else "end_turn"
            response = {"id": "msg_local_fixture", "type": "message", "role": "assistant",
                        "model": body.get("model"), "content": content, "stop_reason": stop,
                        "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
            self.send_response(200)
            if body.get("stream"):
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                events = [("message_start", {"type": "message_start", "message": {
                    **response, "content": [], "stop_reason": None}})]
                for index, block in enumerate(content):
                    initial = {**block, "text": ""} if block["type"] == "text" else {**block, "input": {}}
                    delta = ({"type": "text_delta", "text": block["text"]} if block["type"] == "text" else
                             {"type": "input_json_delta", "partial_json": json.dumps(block["input"])})
                    events.extend([
                        ("content_block_start", {"type": "content_block_start", "index": index,
                                                 "content_block": initial}),
                        ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta}),
                        ("content_block_stop", {"type": "content_block_stop", "index": index}),
                    ])
                events.extend([
                    ("message_delta", {"type": "message_delta", "delta": {
                        "stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 1}}),
                    ("message_stop", {"type": "message_stop"}),
                ])
                self.wfile.write("".join(f"event: {event}\ndata: {json.dumps(data)}\n\n"
                                        for event, data in events).encode())
            else:
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Prevent inherited relay/auth/provider/environment configuration from entering the probe.
    env = {key: value for key, value in os.environ.items()
           if not any(part in key.upper() for part in ("CLAUDE", "ANTHROPIC", "API_KEY", "MAGPIE"))}
    env.update(ANTHROPIC_API_KEY="local-profile-fixture", USER="labuser", USERNAME="labuser",
               ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
               CLAUDE_CONFIG_DIR=str(directory / "config"), CLAUDE_CODE_AUTO_MODE_SERVER="0",
               CLAUDE_CODE_AUTO_MODE_MODEL="claude-haiku-4-5-20251001",
               DISABLE_NON_ESSENTIAL_MODEL_CALLS="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    args = [executable, "--bare", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
            "--permission-mode", "auto", "--model", "claude-opus-4-8", "--tools", "Bash", "--max-turns", "2",
            "-p", "Print the fixture marker to verify the test environment."]
    try:
        result = subprocess.run(args, check=False, cwd=directory / "work", env=env, capture_output=True, timeout=45,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"官方 CLI 准备失败（{type(exc).__name__}），没有调用付费 API。") from exc
    finally:
        server.shutdown()
        server.server_close()
    if result.returncode or len(captured) != 2:
        raise ValueError("未获得完整两阶段官方请求；不使用猜测模板，请检查官方安装与版本。")
    fast, thinking = captured
    for body in captured:
        if "tools" in body or body.get("temperature") != 1:
            raise ValueError("官方请求能力发生变化，适配器已停止，不能继续套用旧配置。")
        body.pop("metadata", None)  # Host account/session IDs are not part of the synthetic profile.
    if fast.get("stop_sequences") != ["</block>"] or fast.get("max_tokens") != 64:
        raise ValueError("官方快速阶段合同发生变化。")
    if thinking.get("max_tokens") != 8192 or "stop_sequences" in thinking:
        raise ValueError("官方复核阶段合同发生变化。")
    for body in captured:
        blocks = body["system"]
        if not any("cc_automode_permissions" in block.get("text", "") for block in blocks):
            raise ValueError("官方权限策略模板缺失，准备失败。")
        # Keep exact official policy and transport attribution; session identity is synthetic.
        for block in blocks:
            if "## Session Context" in block.get("text", "") and "labuser" not in block["text"]:
                raise ValueError("CLI 未使用隔离身份；拒绝保存可能带有个人身份的模板。")
    profile = {"version": SUPPORTED_VERSION, "source": "unmodified-installed-official-cli",
               "configuration": {"client_classifier": True, "two_stage": "both", "severity": False,
                                 "tools": False, "bare": True, "segmented_transcript": False},
               "fast": fast, "thinking": thinking}
    profile["capture_sha256"] = hashlib.sha256(json.dumps(
        [fast, thinking], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    target = directory.parent / "claude-official-profile.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return profile
