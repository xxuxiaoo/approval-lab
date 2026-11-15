"""Private native-request library and an opt-in, authenticated recording relay."""

import hmac
import json
import logging
import secrets
import uuid

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import Field, ValidationError

from .native import (
    ENDPOINTS,
    HEADER_ALLOWLIST,
    MAX_REQUEST_BYTES,
    AnswerContract,
    NativeRecord,
    NativeRequest,
    as_case,
    digest,
    stress_variants,
    unique_object,
)
from .providers import ProviderClient, redact
from .schemas import StrictModel
from .storage import dumps, now_iso

logger = logging.getLogger(__name__)


def validated(model, value):
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        # Validation errors must never echo a private recorded request.
        raise ValueError("; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"]
                                   for e in exc.errors(include_input=False))[:1500]) from None


async def read_json(request, limit):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "请求超过导入/录制大小限制")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        return json.loads(raw, object_pairs_hook=unique_object), raw
    except (ValueError, UnicodeError):
        raise ValueError("需要有效且没有重复字段的 JSON") from None


class NativeLibrary:
    def __init__(self, store):
        self.store = store

    def get(self, record_id):
        row = self.store.one("SELECT * FROM native_records WHERE id=?", (record_id,))
        if not row:
            raise HTTPException(404, "原生请求记录不存在")
        return NativeRecord.model_validate_json(row["record"])

    def save(self, record, record_id=None):
        record_id = record_id or uuid.uuid4().hex
        original = digest(record.request.body)
        clean = record.model_copy(deep=True)
        # Redact values recursively, never perform a replacement over serialized schema field names.
        for provider in self.store.providers():
            key = self.store.provider(provider["id"], secret=True)["api_key"]
            if key and key != "local":
                def sanitize(value, secret=key):
                    if isinstance(value, dict):
                        return {k: sanitize(v) for k, v in value.items()}
                    if isinstance(value, list):
                        return [sanitize(v) for v in value]
                    if isinstance(value, str) and (len(secret) >= 8 or value == secret):
                        return value.replace(secret, "[REDACTED]")
                    return value

                clean.request.body = sanitize(clean.request.body)
                clean.observed_response = sanitize(clean.observed_response)
                clean.rationale = sanitize(clean.rationale)
                clean.title = sanitize(clean.title)
        clean = NativeRecord.model_validate(clean.model_dump())
        clean.original_body_sha256 = record.original_body_sha256 or original
        clean.redacted = record.redacted or digest(clean.request.body) != original
        timestamp = now_iso()
        self.store.execute(
            "INSERT INTO native_records(id,record,metadata,created_at,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET record=excluded.record,metadata=excluded.metadata,updated_at=excluded.updated_at",
            (record_id, dumps(clean.model_dump()), dumps(clean.metadata()), timestamp, timestamp),
        )
        return record_id

    def listing(self):
        return [{**json.loads(row["metadata"]), "id": row["id"], "created_at": row["created_at"]}
                for row in self.store.query("SELECT id,metadata,created_at FROM native_records ORDER BY created_at DESC")]


class Annotation(StrictModel):
    expected: str | None = None
    label_reviewed: bool = False
    rationale: str = Field(default="", max_length=8000)
    category: str = Field(default="real_workflow", min_length=1, max_length=100)
    light: bool = False
    contract: AnswerContract
    app_version: str = Field(default="", max_length=100)
    harness_version: str = Field(default="", max_length=100)


class StressInput(StrictModel):
    pointer: str = Field(min_length=1, max_length=1000)
    lengths: list[int] = Field(min_length=1, max_length=5)
    positions: list[str] = Field(default_factory=lambda: ["start", "middle", "end"], max_length=3)


class CaptureInput(StrictModel):
    provider_id: int = Field(gt=0)
    source: str = Field(pattern=r"^(codex|claude_code|other)$")
    contract: AnswerContract


def observed_text(content, protocol, stream):
    """Keep observations for human labeling; they are never an answer key."""
    texts = []
    try:
        if not stream:
            events = [json.loads(content)]
        else:
            events = []
            for line in content.decode("utf-8").splitlines():
                if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                    events.append(json.loads(line[5:]))
        for event in events:
            if protocol == "chat":
                for choice in event.get("choices", []):
                    text = choice.get("delta", choice.get("message", {})).get("content")
                    if isinstance(text, str):
                        texts.append(text)
            elif protocol == "anthropic":
                if event.get("type") == "content_block_delta":
                    texts.append(event.get("delta", {}).get("text", ""))
                else:
                    texts.extend(part.get("text", "") for part in event.get("content", [])
                                 if part.get("type") == "text")
            else:
                if event.get("type") == "response.output_text.delta":
                    texts.append(event.get("delta", ""))
                elif not stream:
                    texts.extend(part.get("text", "") for item in event.get("output", [])
                                 for part in item.get("content", []) if part.get("type") == "output_text")
        return "".join(texts)[:100000]
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return ""


def register_native_routes(app):
    sessions = {}

    def library():
        return NativeLibrary(app.state.store)

    @app.get("/api/native")
    async def list_native():
        return library().listing()

    @app.get("/api/native/export")
    async def export_native():
        records = [json.loads(row["record"]) for row in app.state.store.query(
            "SELECT record FROM native_records ORDER BY created_at,id")]
        return Response(dumps({"version": "native.v1", "records": records}), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="approval-lab-native-private.json"'})

    @app.post("/api/native/import")
    async def import_native(request: Request):
        value, _ = await read_json(request, 64_000_000)
        rows = value.get("records") if isinstance(value, dict) else value
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise ValueError("导入需要包含 1–500 个原生请求记录的数组或 native.v1 文件")
        records = [validated(NativeRecord, row) for row in rows]
        ids = []
        for record in records:
            # An uploaded file is declared provenance, not proof of a live capture.
            if record.origin == "captured":
                record.origin = "imported"
            ids.append(library().save(record))
        return {"ids": ids, "count": len(ids)}

    @app.get("/api/native/{record_id}")
    async def native_detail(record_id: str):
        return library().get(record_id).model_dump()

    @app.put("/api/native/{record_id}")
    async def annotate_native(record_id: str, value: Annotation):
        record = library().get(record_id)
        updated = {**record.model_dump(), **value.model_dump()}
        record = validated(NativeRecord, updated)
        if record.label_reviewed and (record.expected is None or not record.rationale.strip()):
            raise ValueError("人工标注需要同时填写期望判定和依据")
        if record.label_reviewed:
            as_case(record_id, record)
        library().save(record, record_id)
        return {"id": record_id}

    @app.delete("/api/native/{record_id}")
    async def delete_native(record_id: str):
        library().get(record_id)
        app.state.store.execute("DELETE FROM native_records WHERE id=?", (record_id,))
        return {"ok": True}

    @app.post("/api/native/{record_id}/stress")
    async def make_stress(record_id: str, value: StressInput):
        try:
            variants = stress_variants(record_id, library().get(record_id), value.pointer,
                                       value.lengths, value.positions)
        except TypeError as exc:
            raise ValueError(str(exc)) from None
        return {"ids": [library().save(variant) for variant in variants], "count": len(variants)}

    @app.post("/api/capture")
    async def start_capture(value: CaptureInput, request: Request):
        if len(sessions) >= 10:
            raise ValueError("录制入口已达 10 个；请结束不用的入口或重启应用")
        provider = app.state.store.provider(value.provider_id, secret=True)
        session_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        sessions[session_id] = {"provider": provider, "token": token, "source": value.source,
                                "contract": value.contract, "recording": True, "count": 0, "last_error": None}
        return {"id": session_id, "base_url": str(request.base_url).rstrip("/") + f"/record/{session_id}/v1",
                "api_key": token, "protocol": provider["protocol"],
                "note": "仅手动接入审核模型；保持 Magpie 原有路由/翻译。停止录制后仍透传，重启应用后入口失效。"}

    @app.get("/api/capture")
    async def capture_status():
        return [{"id": key, "recording": value["recording"], "source": value["source"],
                 "provider": value["provider"]["name"], "count": value["count"], "last_error": value["last_error"]}
                for key, value in sessions.items()]

    @app.post("/api/capture/{session_id}/stop")
    async def stop_capture(session_id: str):
        if session_id not in sessions:
            raise HTTPException(404, "录制入口不存在")
        sessions[session_id]["recording"] = False
        return {"ok": True, "forwarding_continues": True}

    @app.api_route("/record/{session_id}/v1/{endpoint:path}", methods=["POST", "GET"])
    async def record_proxy(session_id: str, endpoint: str, request: Request):
        session = sessions.get(session_id)
        supplied = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if not session or not hmac.compare_digest(supplied.encode(), session["token"].encode()):
            raise HTTPException(401, "录制入口需要专用本地 Key")
        if (request.headers.get("origin") and request.headers["origin"] != str(request.base_url).rstrip("/")) or (
            request.headers.get("sec-fetch-site") == "cross-site"
        ):
            raise HTTPException(403, "拒绝跨站录制请求")
        provider = session["provider"]
        if not app.state.store.one("SELECT id FROM providers WHERE id=?", (provider["id"],)):
            raise HTTPException(410, "上游中转站已删除，请恢复原来的审核路由")
        if not (request.method == "GET" and endpoint == "models") and not (
            request.method == "POST" and endpoint == ENDPOINTS[provider["protocol"]]
        ):
            raise HTTPException(404, "录制入口只提供所选协议的模型和生成接口")
        body, raw = (None, None) if request.method == "GET" else await read_json(request, MAX_REQUEST_BYTES)
        headers = {k: v for k, v in request.headers.items() if k.lower() in HEADER_ALLOWLIST}
        record_id = None
        if session["recording"] and body is not None:
            try:
                record_id = library().save(NativeRecord(
                    title=f"{session['source']} · {now_iso()}", source=session["source"], origin="captured",
                    request=NativeRequest(protocol=provider["protocol"], body=body, headers=headers),
                    contract=session["contract"],
                ))
                session["count"] += 1
                session["last_error"] = None
            except (ValueError, TypeError):
                session["last_error"] = (
                    "本次请求缺少可移植的完整上下文，或包含服务器工具 / 不支持的字段；"
                    "未录为可测用例，原审核请求仍继续转发。"
                )
            except Exception as exc:  # noqa: BLE001 - recorder failure must not change the live review request
                session["last_error"] = "本次请求无法保存为独立回放用例；原审核请求仍继续转发。"
                logger.warning("Capture skipped (%s); request and exception bodies omitted", type(exc).__name__)
        client = ProviderClient(provider)
        # Authentication always comes from the selected saved provider, never the capture client.
        try:
            upstream = client.client.build_request(request.method, f"{client.base_url}/{endpoint}",
                                                  content=raw, headers=headers, timeout=600)
            response = await client.client.send(upstream, stream=True)
        except (TimeoutError, httpx.HTTPError):
            await client.client.aclose()
            return JSONResponse({"error": {"message": "录制转发连接失败；没有重试"}}, status_code=502)

        async def forward():
            collected, size = [], 0
            try:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size <= 4_000_000:
                        collected.append(chunk)
                    yield chunk
            finally:
                await response.aclose()
                await client.client.aclose()
                if record_id and size <= 4_000_000 and response.status_code < 400:
                    try:
                        record = library().get(record_id)
                        record.observed_response = redact(observed_text(
                            b"".join(collected), provider["protocol"], bool(body.get("stream"))), provider["api_key"])
                        library().save(record, record_id)
                    except HTTPException:
                        pass  # The record may have been deliberately deleted while streaming.
                    except Exception as exc:  # noqa: BLE001 - never break live stream over observation storage
                        session["last_error"] = "回复已转发，但本机未能保存观察回复；请核查语料。"
                        logger.warning("Capture observation not saved (%s); bodies omitted", type(exc).__name__)

        response_headers = {key: value for key, value in response.headers.items()
                            if key.lower() in {"content-type", "x-request-id", "request-id"}}
        return StreamingResponse(forward(), status_code=response.status_code, headers=response_headers)
