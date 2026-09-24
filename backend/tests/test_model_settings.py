"""Model configuration tests do not touch the shared PostgreSQL test database."""

from __future__ import annotations

import json
import os

import httpx
import pytest
import pymupdf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import model_settings as settings


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_CONFIG_FERNET_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    instance = settings.ModelSettingsStore(tmp_path / "protected")
    monkeypatch.setattr(settings, "store", instance)
    app = FastAPI()
    app.include_router(settings.router)
    return instance, TestClient(app)


def test_config_api_never_returns_or_stores_plaintext_key(local_store):
    store, client = local_store
    key = "test-secret-should-never-appear"
    created = client.post("/api/models", json={
        "name": "测试文本模型", "model": "test-model", "endpoint": "http://127.0.0.1:19000/v1",
        "protocol": "chat", "auth": "bearer", "api_key": key,
    })
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    assert key not in created.text
    assert created.json()["has_api_key"] is True
    assert key not in client.get("/api/models").text
    assert key not in (store.directory / "models.json").read_text()
    assert (store.directory / "models.json").stat().st_mode & 0o077 == 0
    assert (store.directory / "master.key").stat().st_mode & 0o077 == 0
    assert store.runtime_by_id(identifier).api_key == key
    store.test_result(identifier, True)

    update = client.put(f"/api/models/{identifier}", json={
        "name": "改名", "model": "test-model", "endpoint": "http://127.0.0.1:19000/v1",
        "protocol": "chat", "auth": "bearer", "api_key": None,
    })
    assert update.status_code == 200
    assert store.runtime_by_id(identifier).api_key == key
    assert key not in update.text
    assert update.json()["last_test"]["status"] == "normal"

    changed = client.put(f"/api/models/{identifier}", json={
        "name": "改名", "model": "new-model", "endpoint": "http://127.0.0.1:19000/v1",
        "protocol": "chat", "auth": "bearer", "api_key": None,
    })
    assert changed.status_code == 200
    assert changed.json()["last_test"] is None


def test_route_validation_and_environment_fallback(local_store, monkeypatch):
    store, client = local_store
    model = client.post("/api/models", json={
        "name": "解析器", "model": "model", "endpoint": "http://127.0.0.1:19001/file_parse",
        "protocol": "mineru", "auth": "none",
    }).json()
    rejected = client.put("/api/models/routes/extraction", json={"task": "extraction", "model_id": model["id"]})
    assert rejected.status_code == 400
    assigned = client.put("/api/models/routes/ocr", json={"task": "ocr", "model_id": model["id"]})
    assert assigned.status_code == 200
    assert settings.resolve_model("ocr").protocol == "mineru"
    assert settings.is_configured("ocr") is True
    assert settings.is_configured("extraction") is False

    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:19002")
    monkeypatch.setenv("LLM_API_KEY", "env-secret")
    monkeypatch.setenv("LLM_MODEL", "deepseek-test")
    assert settings.resolve_model("extraction").source == "environment"
    assert settings.resolve_model("writing").model == "deepseek-test"
    assert "env-secret" not in client.get("/api/models").text
    client.delete(f"/api/models/{model['id']}")
    assert settings.is_configured("ocr") is False
    assert store.public()["routes"] == {}


def test_legacy_environment_import_is_encrypted_and_idempotent(local_store, monkeypatch):
    store, client = local_store
    key = "import-only-secret"
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:19002")
    monkeypatch.setenv("LLM_API_KEY", key)
    monkeypatch.setenv("LLM_MODEL", "deepseek-test")
    first = client.post("/api/models/import-environment")
    second = client.post("/api/models/import-environment")
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(store.public()["models"]) == 1
    assert store.public()["routes"] == {"extraction": first.json()["id"], "writing": first.json()["id"]}
    assert settings.resolve_model("writing").source == "saved"
    assert key not in (store.directory / "models.json").read_text()
    assert key not in client.get("/api/models").text


def test_chat_embedding_and_mineru_capability_requests(local_store, monkeypatch):
    store, client = local_store
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"facts":[]}'}}]})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        if request.url.path.endswith("/file_parse"):
            return httpx.Response(200, json={"data": {"md_content": "# OCR TEST 123"}})
        return httpx.Response(404)

    original_client = httpx.Client
    monkeypatch.setattr(settings.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))

    chat = client.post("/api/models", json={"name": "文本", "model": "chat-model",
        "endpoint": "http://127.0.0.1:19000/v1", "protocol": "chat", "auth": "none"}).json()
    embed = client.post("/api/models", json={"name": "向量", "model": "embed-model",
        "endpoint": "http://127.0.0.1:19000/v1", "protocol": "embedding", "auth": "none"}).json()
    mineru = client.post("/api/models", json={"name": "OCR", "model": "model",
        "endpoint": "http://127.0.0.1:19000/file_parse", "protocol": "mineru", "auth": "none"}).json()
    assert client.put("/api/models/routes/extraction", json={"task": "extraction", "model_id": chat["id"]}).status_code == 200
    assert client.put("/api/models/routes/ocr", json={"task": "ocr", "model_id": mineru["id"]}).status_code == 200
    chat_result = settings.chat_json("extraction", [{"role": "user", "content": "test"}], 32)
    assert chat_result == {"facts": []}
    assert chat_result.model_call["model"] == "chat-model"
    assert chat_result.model_call["max_tokens"] == 32
    assert "api_key" not in chat_result.model_call
    assert seen[-1].url.path == "/v1/chat/completions"
    assert json.loads(seen[-1].content)["model"] == "chat-model"
    assert client.post(f"/api/models/{embed['id']}/test").json()["ok"] is True
    assert seen[-1].url.path == "/v1/embeddings"
    assert settings.parse_document_page(b"%PDF-test", 2)["text"] == "# OCR TEST 123"
    assert seen[-1].url.path == "/file_parse"
    assert b'name="files"' in seen[-1].content
    assert b'name="start_page_id"' in seen[-1].content
    assert b"\r\n1\r\n" in seen[-1].content
    assert store.public()["routes"]["ocr"] == mineru["id"]


def test_remote_failure_is_sanitized(local_store, monkeypatch):
    _, client = local_store
    key = "test-secret-in-remote-error"
    model = client.post("/api/models", json={"name": "失败模型", "model": "wrong-model",
        "endpoint": "http://127.0.0.1:19000/v1", "protocol": "chat", "auth": "bearer", "api_key": key}).json()
    original_client = httpx.Client
    monkeypatch.setattr(settings.httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"error": key}))))
    result = client.post(f"/api/models/{model['id']}/test")
    assert result.status_code == 200
    assert result.json()["ok"] is False
    assert "HTTP 401" in result.json()["error"]
    assert key not in result.text
    assert key not in client.get("/api/models").text


def test_vision_ocr_renders_only_selected_pdf_page(local_store, monkeypatch):
    _, client = local_store
    model = client.post("/api/models", json={"name": "视觉 OCR", "model": "model",
        "endpoint": "http://127.0.0.1:19000/v1", "protocol": "vision", "auth": "none"}).json()
    assert client.put("/api/models/routes/ocr", json={"task": "ocr", "model_id": model["id"]}).status_code == 200
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "第二页文字 123"}}]})

    original_client = httpx.Client
    monkeypatch.setattr(settings.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    pdf = pymupdf.open()
    pdf.new_page().insert_text((30, 40), "PAGE ONE")
    pdf.new_page().insert_text((30, 40), "PAGE TWO")
    result = settings.parse_document_page(pdf.tobytes(), 2)
    pdf.close()
    assert result["text"] == "第二页文字 123"
    assert result["provider"] == "vision"
    assert result["metadata"]["page"] == 2
    assert seen[0]["model"] == "model"
    assert seen[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_invalid_endpoint_does_not_save(local_store):
    store, client = local_store
    response = client.post("/api/models", json={"name": "无效", "model": "model",
        "endpoint": "http://user:secret@example.com/v1", "protocol": "chat", "auth": "none"})
    assert response.status_code == 400
    assert store.public()["models"] == []
    malformed = client.post("/api/models", json={"name": "无效", "model": "model",
        "endpoint": "http://127.0.0.1:19000/v1", "protocol": "chat", "auth": "bearer",
        "api_key": ["secret-in-malformed-key"]})
    assert malformed.status_code == 400
    assert "secret-in-malformed-key" not in malformed.text
