"""Private, task-routed model configuration for the local report platform.

API keys never leave this module in an API response. The encrypted configuration
and its local master key are created with owner-only permissions. For deployments
with a managed secret, set MODEL_CONFIG_FERNET_KEY instead of using a key file.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pymupdf
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


Task = Literal["extraction", "writing", "ocr", "vision", "embedding", "image_generation", "document_parse"]
Protocol = Literal["chat", "vision", "embedding", "image", "mineru"]
TASK_PROTOCOLS: dict[str, set[str]] = {
    "extraction": {"chat"}, "writing": {"chat"}, "ocr": {"mineru", "vision"},
    "vision": {"vision"}, "embedding": {"embedding"},
    "image_generation": {"image"}, "document_parse": {"mineru"},
}
TASK_LABELS = {"extraction": "事实抽取", "writing": "章节起草", "ocr": "扫描页识别",
               "vision": "图片理解", "embedding": "向量生成", "image_generation": "图片生成",
               "document_parse": "文档解析"}
PROTOCOL_LABELS = {"chat": "文本对话", "vision": "图片理解 / OCR", "embedding": "向量",
                   "image": "图片生成", "mineru": "MinerU 文件解析"}
CONFIG_DIR = Path(os.getenv("MODEL_SETTINGS_DIR", Path(__file__).resolve().parents[2] / "storage" / "model-settings"))

# Connection examples only. Replace the loopback endpoint in the UI; saved settings are unchanged.
PRESETS = [
    {"name": "Qwen3.8-27B-FP8", "model": "Qwen3.8-27B-FP8", "endpoint": "http://127.0.0.1:9000/v1", "protocol": "chat", "auth": "bearer"},
    {"name": "Qwen3-VL-32B-Instruct-AWQ", "model": "Qwen3-VL-32B-Instruct-AWQ", "endpoint": "http://127.0.0.1:9001/v1", "protocol": "vision", "auth": "bearer"},
    {"name": "Qwen3-Embedding-0.6B", "model": "Qwen3-Embedding-0.6B", "endpoint": "http://127.0.0.1:9002/v1", "protocol": "embedding", "auth": "bearer"},
    {"name": "Qwen/Qwen-Image-2.1", "model": "Qwen/Qwen-Image-2.1", "endpoint": "http://127.0.0.1:9003/v1/images/generations", "protocol": "image", "auth": "bearer"},
    {"name": "DotsMOCR", "model": "model", "endpoint": "http://127.0.0.1:9004/v1", "protocol": "vision", "auth": "none"},
    {"name": "MinerU2.5-2509-1.2B", "model": "OpenDataLab/MinerU2.5-2509-1.2B", "endpoint": "http://127.0.0.1:9005/file_parse", "protocol": "mineru", "auth": "none"},
]


class ModelInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=180)
    endpoint: str = Field(min_length=7, max_length=500)
    protocol: Protocol
    auth: Literal["bearer", "none"] = "bearer"
    # Validate manually so FastAPI's default 422 response cannot echo a malformed
    # API-key value back to the browser.
    api_key: Any = None
    clear_api_key: bool = False
    timeout_seconds: int = Field(default=90, ge=3, le=300)


class RouteInput(BaseModel):
    task: Task
    model_id: str | None = None


@dataclass(frozen=True)
class RuntimeModel:
    id: str
    name: str
    model: str
    endpoint: str
    api_key: str
    protocol: str
    timeout_seconds: int
    source: str = "saved"


class ChatResult(dict):
    """Parsed model JSON with trusted, credential-free call metadata."""

    def __init__(self, body: dict, model_call: dict):
        super().__init__(body)
        self.model_call = model_call


def _validated_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("接口地址须为不含账号、查询参数的 HTTP(S) 地址")
    return endpoint.strip().rstrip("/")


def _owner_only(path: Path) -> None:
    if path.stat().st_mode & 0o077:
        raise ValueError("模型密钥文件权限过宽；请限制为当前用户可读写")


class ModelSettingsStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or CONFIG_DIR
        self._lock = RLock()

    def _prepare(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.stat().st_mode & 0o077:
            # Existing directories can come from earlier local runs. Restrict them
            # before any secret is written rather than relying on the process umask.
            self.directory.chmod(0o700)

    def _cipher(self) -> Fernet:
        configured = os.getenv("MODEL_CONFIG_FERNET_KEY")
        if configured:
            try:
                return Fernet(configured.encode("ascii"))
            except (ValueError, UnicodeError) as exc:
                raise ValueError("MODEL_CONFIG_FERNET_KEY 无效") from exc
        self._prepare()
        path = self.directory / "master.key"
        if not path.exists():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as file:
                    file.write(Fernet.generate_key())
                    file.flush()
                    os.fsync(file.fileno())
            except FileExistsError:
                pass
        _owner_only(path)
        return Fernet(path.read_bytes())

    def _read(self) -> dict:
        self._prepare()
        path = self.directory / "models.json"
        if not path.exists():
            return {"models": [], "routes": {}}
        _owner_only(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("models"), list) or not isinstance(data.get("routes"), dict):
            raise ValueError("模型配置文件损坏")
        return data

    def _write(self, data: dict) -> None:
        self._prepare()
        fd, filename = tempfile.mkstemp(prefix=".models-", suffix=".tmp", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, separators=(",", ":"))
                file.flush()
                os.fsync(file.fileno())
            os.replace(filename, self.directory / "models.json")
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def public(self) -> dict:
        with self._lock:
            data = self._read()
        models = []
        for record in data["models"]:
            models.append({key: value for key, value in record.items() if key != "encrypted_api_key"} |
                          {"has_api_key": bool(record.get("encrypted_api_key")),
                           "api_key_masked": "••••••" if record.get("encrypted_api_key") else ""})
        configured = bool(os.getenv("LLM_BASE_URL") and os.getenv("LLM_API_KEY"))
        return {"models": models, "routes": data["routes"], "task_labels": TASK_LABELS,
                "protocol_labels": PROTOCOL_LABELS, "presets": PRESETS,
                "legacy_environment": {"configured": configured,
                                       "model": os.getenv("LLM_MODEL", "deepseek-v4-flash-0731") if configured else ""}}

    def save(self, body: ModelInput, model_id: str | None = None) -> dict:
        endpoint = _validated_endpoint(body.endpoint)
        if body.api_key is not None and not isinstance(body.api_key, str):
            raise ValueError("API Key 格式无效")
        if body.api_key is not None and ("\n" in body.api_key or "\r" in body.api_key):
            raise ValueError("API Key 格式无效")
        if body.auth == "bearer" and body.clear_api_key and not body.api_key:
            raise ValueError("Bearer 模型需要 API Key")
        with self._lock:
            data = self._read()
            old = next((item for item in data["models"] if item["id"] == model_id), None)
            if model_id and old is None:
                raise KeyError(model_id)
            token = old.get("encrypted_api_key", "") if old else ""
            if body.clear_api_key:
                token = ""
            if body.api_key:
                token = self._cipher().encrypt(body.api_key.encode("utf-8")).decode("ascii")
            if body.auth == "bearer" and not token:
                raise ValueError("请填写 API Key")
            unchanged_connection = bool(old and not body.api_key and not body.clear_api_key and
                                        old["model"] == body.model.strip() and old["endpoint"] == endpoint and
                                        old["protocol"] == body.protocol and old["auth"] == body.auth)
            record = {"id": model_id or str(uuid4()), "name": body.name.strip(), "model": body.model.strip(),
                      "endpoint": endpoint, "protocol": body.protocol, "auth": body.auth,
                      "encrypted_api_key": token if body.auth == "bearer" else "",
                      "timeout_seconds": body.timeout_seconds,
                      "last_test": old.get("last_test") if unchanged_connection else None,
                      "source": old.get("source") if old else "manual"}
            if old:
                data["models"] = [record if item["id"] == model_id else item for item in data["models"]]
            else:
                data["models"].append(record)
            # A protocol edit can invalidate an existing task assignment.
            data["routes"] = {task: identifier for task, identifier in data["routes"].items()
                              if identifier != record["id"] or record["protocol"] in TASK_PROTOCOLS.get(task, set())}
            self._write(data)
        return self._public_record(record)

    @staticmethod
    def _public_record(record: dict) -> dict:
        return {key: value for key, value in record.items() if key != "encrypted_api_key"} | {
            "has_api_key": bool(record.get("encrypted_api_key")),
            "api_key_masked": "••••••" if record.get("encrypted_api_key") else ""}

    def delete(self, model_id: str) -> None:
        with self._lock:
            data = self._read()
            if not any(item["id"] == model_id for item in data["models"]):
                raise KeyError(model_id)
            data["models"] = [item for item in data["models"] if item["id"] != model_id]
            data["routes"] = {task: identifier for task, identifier in data["routes"].items() if identifier != model_id}
            self._write(data)

    def route(self, task: str, model_id: str | None) -> dict:
        if task not in TASK_PROTOCOLS:
            raise ValueError("不支持的任务")
        with self._lock:
            data = self._read()
            if model_id:
                record = next((item for item in data["models"] if item["id"] == model_id), None)
                if record is None:
                    raise KeyError(model_id)
                if record["protocol"] not in TASK_PROTOCOLS[task]:
                    raise ValueError("该模型接口与任务用途不匹配")
                data["routes"][task] = model_id
            else:
                data["routes"].pop(task, None)
            self._write(data)
            return dict(data["routes"])

    def import_environment(self) -> dict:
        """Copy the legacy DeepSeek runtime setting into protected local storage."""
        endpoint, key = os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_API_KEY", "")
        if not endpoint or not key:
            raise ValueError("当前运行环境没有可导入的文本模型")
        endpoint = _validated_endpoint(endpoint)
        model = os.getenv("LLM_MODEL", "deepseek-v4-flash-0731")
        with self._lock:
            data = self._read()
            old = next((item for item in data["models"] if item.get("source") == "environment-import"), None)
            record = {"id": old["id"] if old else str(uuid4()), "name": "DeepSeek · 内网",
                      "model": model, "endpoint": endpoint, "protocol": "chat", "auth": "bearer",
                      "encrypted_api_key": self._cipher().encrypt(key.encode("utf-8")).decode("ascii"),
                      "timeout_seconds": 90, "last_test": None,
                      "source": "environment-import"}
            if old:
                data["models"] = [record if item["id"] == old["id"] else item for item in data["models"]]
            else:
                data["models"].append(record)
            data["routes"]["extraction"] = record["id"]
            data["routes"]["writing"] = record["id"]
            self._write(data)
            return self._public_record(record)

    def runtime(self, task: str) -> RuntimeModel:
        if task not in TASK_PROTOCOLS:
            raise ValueError("不支持的任务")
        with self._lock:
            data = self._read()
            model_id = data["routes"].get(task)
            record = next((item for item in data["models"] if item["id"] == model_id), None)
        if model_id and record is None:
            raise ValueError("当前任务的模型配置不存在")
        if record:
            if record["protocol"] not in TASK_PROTOCOLS[task]:
                raise ValueError("当前任务的模型接口不匹配")
            encrypted = record.get("encrypted_api_key")
            try:
                key = self._cipher().decrypt(encrypted.encode("ascii")).decode("utf-8") if encrypted else ""
            except (InvalidToken, ValueError) as exc:
                raise ValueError("模型密钥无法解密，请重新设置") from exc
            return RuntimeModel(record["id"], record["name"], record["model"], record["endpoint"],
                                key, record["protocol"], record["timeout_seconds"])
        if task in ("extraction", "writing"):
            endpoint, key = os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_API_KEY", "")
            if endpoint and key:
                return RuntimeModel("environment-llm", "环境模型", os.getenv("LLM_MODEL", "deepseek-v4-flash-0731"),
                                    _validated_endpoint(endpoint), key, "chat", 90, "environment")
        raise ValueError(f"请先在模型配置中为{TASK_LABELS[task]}指定可用模型")

    def runtime_by_id(self, model_id: str) -> RuntimeModel:
        with self._lock:
            record = next((item for item in self._read()["models"] if item["id"] == model_id), None)
        if record is None:
            raise KeyError(model_id)
        encrypted = record.get("encrypted_api_key")
        try:
            key = self._cipher().decrypt(encrypted.encode("ascii")).decode("utf-8") if encrypted else ""
        except (InvalidToken, ValueError) as exc:
            raise ValueError("模型密钥无法解密，请重新设置") from exc
        return RuntimeModel(record["id"], record["name"], record["model"], record["endpoint"],
                            key, record["protocol"], record["timeout_seconds"])

    def test_result(self, model_id: str, ok: bool, error: str = "") -> dict:
        with self._lock:
            data = self._read()
            record = next((item for item in data["models"] if item["id"] == model_id), None)
            if record is None:
                raise KeyError(model_id)
            record["last_test"] = {"status": "normal" if ok else "error",
                                   "at": datetime.now(timezone.utc).isoformat(), "error": error}
            self._write(data)
            return self._public_record(record)


store = ModelSettingsStore()
router = APIRouter(prefix="/api/models", tags=["models"])


def resolve_model(task: str) -> RuntimeModel:
    """Get the configured model for a task, with legacy DeepSeek env fallback."""
    return store.runtime(task)


def is_configured(task: str) -> bool:
    try:
        resolve_model(task)
        return True
    except ValueError:
        return False


def _endpoint(model: RuntimeModel) -> str:
    path = {"chat": "/chat/completions", "vision": "/chat/completions",
            "embedding": "/embeddings", "image": "/images/generations"}.get(model.protocol)
    if model.protocol == "mineru":
        return model.endpoint if model.endpoint.endswith("/file_parse") else model.endpoint.rstrip("/") + "/file_parse"
    if not path:
        raise ValueError("不支持的模型接口")
    if model.endpoint.endswith(path):
        return model.endpoint
    base = model.endpoint.rstrip("/")
    if not re.search(r"/v\d+$", base):
        base += "/v1"
    return base + path


def _headers(model: RuntimeModel) -> dict[str, str]:
    return {"Authorization": f"Bearer {model.api_key}"} if model.api_key else {}


def _safe_model_error(response: httpx.Response) -> ValueError:
    return ValueError(f"模型服务返回 HTTP {response.status_code}；请检查接口、模型和鉴权")


def chat_json(task: Literal["extraction", "writing"], messages: list[dict], max_tokens: int) -> dict:
    """Call a task-routed JSON chat model; never return raw service error bodies."""
    model = resolve_model(task)
    try:
        with httpx.Client(timeout=model.timeout_seconds, trust_env=False) as client:
            response = client.post(_endpoint(model), headers=_headers(model), json={
                "model": model.model, "messages": messages, "temperature": 0,
                "max_tokens": max_tokens, "response_format": {"type": "json_object"}})
        if response.is_error:
            raise _safe_model_error(response)
        content = response.json()["choices"][0]["message"]["content"]
        body = json.loads(content)
        if not isinstance(body, dict):
            raise ValueError("模型结果不是 JSON 对象")
        return ChatResult(body, {
            "model_id": model.id, "model": model.model, "model_version": response.json().get("model") or None,
            "source": model.source, "protocol": model.protocol, "temperature": 0,
            "max_tokens": max_tokens, "response_id": response.json().get("id") or None,
        })
    except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("模型服务请求或结果解析失败；没有修改项目事实或报告") from exc


def _test_image_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=220, height=80)
    page.insert_text((20, 48), "OCR TEST 123", fontsize=18)
    image = page.get_pixmap(matrix=pymupdf.Matrix(1, 1))
    result = image.tobytes("png")
    document.close()
    return result


def probe_model(model: RuntimeModel) -> None:
    """Send one minimal request matching the declared API capability."""
    try:
        with httpx.Client(timeout=min(model.timeout_seconds, 60), trust_env=False) as client:
            if model.protocol == "chat":
                response = client.post(_endpoint(model), headers=_headers(model), json={
                    "model": model.model, "messages": [{"role": "user", "content": "只回答：正常"}],
                    "max_tokens": 32, "temperature": 0})
                valid = lambda body: bool(body["choices"][0]["message"]["content"])
            elif model.protocol in ("vision", "image"):
                image_url = "data:image/png;base64," + base64.b64encode(_test_image_bytes()).decode("ascii")
                if model.protocol == "vision":
                    response = client.post(_endpoint(model), headers=_headers(model), json={
                        "model": model.model, "messages": [{"role": "user", "content": [
                            {"type": "text", "text": "请识别图片中的文字"},
                            {"type": "image_url", "image_url": {"url": image_url}}]}], "max_tokens": 96})
                    valid = lambda body: "123" in str(body["choices"][0]["message"]["content"])
                else:
                    response = client.post(_endpoint(model), headers=_headers(model), json={
                        "model": model.model, "prompt": "一枚简单的蓝色圆形图标，白色背景", "size": "256x256", "n": 1})
                    valid = lambda body: bool(body["data"])
            elif model.protocol == "embedding":
                response = client.post(_endpoint(model), headers=_headers(model), json={"model": model.model, "input": "连接测试"})
                valid = lambda body: bool(body["data"][0]["embedding"])
            elif model.protocol == "mineru":
                document = pymupdf.open()
                page = document.new_page(width=595, height=842)
                for index, line in enumerate((
                    "Environmental impact assessment report", "Project overview and construction details",
                    "Site area: 8606.63 square meters", "Production capacity: 300000 units per year",
                    "This document is for OCR connection testing.",
                )):
                    page.insert_text((50, 70 + index * 35), line, fontsize=16)
                content = document.tobytes()
                document.close()
                response = client.post(_endpoint(model), headers=_headers(model),
                                       files={"files": ("test.pdf", content, "application/pdf")},
                                       data={"start_page_id": "0", "end_page_id": "0", "return_md": "true"})
                valid = lambda body: _mineru_result(body) is not None
            else:
                raise ValueError("不支持的模型接口")
        if response.is_error:
            raise _safe_model_error(response)
        body = response.json()
        if not valid(body):
            raise ValueError("模型返回空结果或结果格式不符")
    except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("模型连接测试失败，请检查地址、模型与服务状态") from exc


def _mineru_result(body: object) -> str | None:
    """Accept Markdown-bearing MinerU responses; reject error/empty envelopes."""
    if not isinstance(body, dict) or body.get("success") is False or body.get("code") not in (None, 0, 200, "0", "200"):
        return None
    values = [body]
    for key in ("data", "result", "results"):
        nested = body.get(key)
        if isinstance(nested, dict):
            values.append(nested)
        elif isinstance(nested, list):
            values.extend(item for item in nested if isinstance(item, dict))
    for item in values:
        for key in ("md_content", "markdown", "md", "content", "text"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _vision_page_image(data: bytes, page: int) -> bytes:
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        if page > len(pdf):
            raise ValueError("页码超出原件范围")
        source = pdf[page - 1]
        # Keep the request bounded while retaining enough resolution for OCR.
        zoom = min(1.7, 2400 / max(source.rect.width, source.rect.height))
        return source.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")


def parse_document_page(data: bytes, page: int) -> dict:
    """OCR one physical PDF page with MinerU or an OpenAI-style vision model."""
    model = resolve_model("ocr")
    if page < 1:
        raise ValueError("页码必须从 1 开始")
    try:
        with httpx.Client(timeout=model.timeout_seconds, trust_env=False) as client:
            if model.protocol == "mineru":
                response = client.post(_endpoint(model), headers=_headers(model),
                                       files={"files": ("source.pdf", data, "application/pdf")},
                                       data={"start_page_id": str(page - 1), "end_page_id": str(page - 1),
                                             "return_md": "true"})
            elif model.protocol == "vision":
                image_url = "data:image/png;base64," + base64.b64encode(_vision_page_image(data, page)).decode("ascii")
                response = client.post(_endpoint(model), headers=_headers(model), json={
                    "model": model.model, "messages": [{"role": "user", "content": [
                        {"type": "text", "text": "识别这页报告的全部可见文字，保持阅读顺序和表格行列。只输出识别结果。"},
                        {"type": "image_url", "image_url": {"url": image_url}}]}], "max_tokens": 4000})
            else:
                raise ValueError("当前 OCR 任务的模型接口不匹配")
        if response.is_error:
            raise _safe_model_error(response)
        body = response.json()
        if model.protocol == "mineru":
            text = _mineru_result(body)
        else:
            content = body["choices"][0]["message"]["content"]
            text = content if isinstance(content, str) else "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict)) if isinstance(content, list) else ""
        if not text:
            raise ValueError("OCR 未返回可定位正文")
        return {"text": text, "provider": model.protocol, "model": model.model,
                "metadata": {"model_id": model.id, "page": page}}
    except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("OCR 服务请求失败；原件和事实未改变") from exc


@router.get("")
def model_list():
    return store.public()


@router.post("/import-environment")
def model_import_environment():
    try:
        return store.import_environment()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("", status_code=201)
def model_create(body: ModelInput):
    try:
        return store.save(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/{model_id}")
def model_update(model_id: str, body: ModelInput):
    try:
        return store.save(body, model_id)
    except KeyError as exc:
        raise HTTPException(404, "模型配置不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{model_id}")
def model_delete(model_id: str):
    try:
        store.delete(model_id)
    except KeyError as exc:
        raise HTTPException(404, "模型配置不存在") from exc
    return {"deleted": True}


@router.put("/routes/{task}")
def model_route(task: Task, body: RouteInput):
    if body.task != task:
        raise HTTPException(400, "任务类型不一致")
    try:
        return {"routes": store.route(task, body.model_id)}
    except KeyError as exc:
        raise HTTPException(404, "模型配置不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{model_id}/test")
def model_test(model_id: str):
    try:
        model = store.runtime_by_id(model_id)
        probe_model(model)
        return {"ok": True, "model": store.test_result(model_id, True)}
    except KeyError as exc:
        raise HTTPException(404, "模型配置不存在") from exc
    except ValueError as exc:
        message = str(exc)
        # No response body or exception representation from the remote service is exposed.
        return {"ok": False, "error": message, "model": store.test_result(model_id, False, message)}
