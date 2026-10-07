import copy
import json
import stat

import httpx
import pytest
from sqlalchemy import select

from backend.app import ai_settings
from backend.app.ai_client import AIClient, parse_response
from backend.app.advice import advice_job, learn
from backend.app.db import SessionLocal
from backend.app.entities import AdviceMessage, Job, Prediction, Snapshot, TradingPreference
from backend.app.worker import execute_job
from .test_imports import stock


CONFIG = {
    "provider": "openai",
    "model": "account-model",
    "api_key": "TEST-KEY-NEVER-LIVE",
    "web_search": True,
}


def configure(client, **overrides):
    response = client.post("/api/v1/ai/settings", json={**CONFIG, **overrides})
    assert response.status_code == 200, response.json()
    return response.json()


def prediction(stock_id):
    with SessionLocal() as db:
        snapshot = Snapshot(
            stock_id=stock_id,
            cutoff="2026-09-30",
            price_basis="raw",
            checksum="0" * 64,
            path="test-only",
            bar_count=100,
        )
        job = Job(kind="prediction", status="succeeded", payload={})
        db.add_all([snapshot, job])
        db.flush()
        item = Prediction(
            stock_id=stock_id,
            snapshot_id=snapshot.id,
            job_id=job.id,
            horizon="one_month",
            config={"price_basis": "raw"},
            result={
                "models": [
                    {
                        "family": "ridge",
                        "points": [{"date": "2026-11-04", "estimate": 41.5, "lower": 37.8, "upper": 45.6}],
                    }
                ]
            },
        )
        db.add(item)
        db.commit()
        return item.id, copy.deepcopy(item.result)


def conversation(client, stock_id):
    return client.post("/api/v1/advice/conversations", json={"stock_id": stock_id}).json()["id"]


def send(client, cid, **overrides):
    return client.post(
        f"/api/v1/advice/conversations/{cid}/messages",
        json={"text": "我通常持有三个月，不做短线交易。", "client_key": "one", **overrides},
    )


class TestOnlyProvider:
    __test__ = False

    def __init__(self):
        self.contexts = []
        self.extracted = []

    def extract_preferences(self, text):
        self.extracted.append(text)
        return [
            {"field": "holding_period", "value": "通常三个月", "quote": "我通常持有三个月"},
            {"field": "risk_tolerance", "value": "可承受50%损失", "quote": "并未说过这句话"},
        ]

    def advise(self, context, messages):
        self.contexts.append((context, messages))
        return {
            "text": "TEST ONLY: research answer",
            "sources": [{"url": "https://example.com/test"}],
            "web_searched": True,
        }


def test_settings_redact_keys_and_reject_key_forwarding(client):
    public = configure(client)
    assert public["key_present"] and public["chat_available"] and public["web_search"]
    assert "TEST-KEY" not in json.dumps(public)
    assert "api_key" not in client.get("/api/v1/ai/settings").json()
    assert stat.S_IMODE(ai_settings.SETTINGS_FILE.stat().st_mode) == 0o600
    for url in [
        "file:///tmp/key",
        "http://external.example/v1",
        "https://u:p@example.com/v1",
        "https://example.com/v1?key=x",
    ]:
        response = client.post(
            "/api/v1/ai/settings", json={**CONFIG, "provider": "compatible", "base_url": url}
        )
        assert response.status_code == 422
    changed = client.post(
        "/api/v1/ai/settings",
        json={**CONFIG, "api_key": None, "provider": "compatible", "base_url": "https://another.example/v1"},
    )
    assert changed.status_code == 422
    compatible = configure(client, provider="compatible", base_url="https://service.example/v1")
    assert not compatible["web_search"]


def test_chat_requires_configuration_and_same_stock_prediction(client):
    first, second = (
        stock(client),
        client.post("/api/v1/stocks", json={"exchange": "SSE", "code": "600000", "name": "另一只"}).json()[
            "id"
        ],
    )
    cid = conversation(client, first)
    assert send(client, cid).json()["code"] == "ai_not_configured"
    configure(client)
    pid, _ = prediction(second)
    assert send(client, cid, prediction_id=pid).json()["code"] == "prediction_context_invalid"
    assert send(client, cid, text="   ").status_code == 422
    assert client.get(f"/api/v1/advice/conversations/{cid}").json()["messages"] == []


def test_persistent_chat_learns_user_quotes_reuses_memory_and_freezes_prediction(client):
    configure(client)
    sid = stock(client)
    pid, frozen = prediction(sid)
    cid = conversation(client, sid)
    body = send(client, cid, prediction_id=pid).json()
    assert send(client, cid, prediction_id=pid).json() == body
    assert send(client, cid, client_key="two").json()["code"] == "conversation_busy"
    assert send(client, cid, prediction_id=pid, text="不同提问").json()["code"] == "idempotency_conflict"
    assert client.post("/api/v1/ai/settings", json=CONFIG).json()["code"] == "ai_busy"
    provider = TestOnlyProvider()
    advice_job(body["job_id"], provider)
    with SessionLocal() as db:
        db.get(Job, body["job_id"]).status = "succeeded"
        db.commit()
    # Re-execution cannot duplicate an assistant response or charge another provider call.
    advice_job(body["job_id"], provider)
    detail = client.get(f"/api/v1/advice/conversations/{cid}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    prefs = {p["field"]: p for p in detail["preferences"]}
    assert prefs["holding_period"]["value"] == "通常三个月" and not prefs["holding_period"]["confirmed"]
    assert not prefs["risk_tolerance"]["value"]
    assert provider.contexts[0][0]["prediction"]["data_cutoff"] == "2026-09-30"
    assert provider.extracted == ["我通常持有三个月，不做短线交易。"]
    assert client.get(f"/api/v1/predictions/{pid}").json()["result"] == frozen
    # A separate future conversation uses the same persistent profile.
    cid2 = conversation(client, sid)
    another = send(client, cid2, client_key="later", learn_preferences=False).json()
    advice_job(another["job_id"], provider)
    assert any(
        p["field"] == "holding_period" and p["value"] == "通常三个月"
        for p in provider.contexts[-1][0]["preferences"]
    )
    assert len(provider.extracted) == 1


def test_manual_and_forgotten_preferences_cannot_be_overwritten(client):
    configure(client)
    sid = stock(client)
    cid = conversation(client, sid)
    client.post("/api/v1/advice/preferences", json={"field": "holding_period", "value": "通常一个月"})
    body = send(client, cid).json()
    advice_job(body["job_id"], TestOnlyProvider())
    with SessionLocal() as db:
        preference = db.get(TradingPreference, "holding_period")
        assert preference.value == "通常一个月" and preference.locked and preference.confirmed
        assistant = db.scalar(select(AdviceMessage).where(AdviceMessage.role == "assistant"))
        assert (
            learn(db, assistant, [{"field": "holding_period", "value": "任意", "quote": "research answer"}])
            == []
        )
    client.post("/api/v1/advice/preferences/holding_period/forget", json={})
    with SessionLocal() as db:
        message = db.get(AdviceMessage, body["message_id"])
        assert (
            learn(db, message, [{"field": "holding_period", "value": "三个月", "quote": "我通常持有三个月"}])
            == []
        )
        assert db.get(TradingPreference, "holding_period").value == ""


def test_delete_conversation_removes_linked_memory_and_keeps_manual_preferences(client):
    configure(client)
    sid = stock(client)
    cid = conversation(client, sid)
    client.post("/api/v1/advice/preferences", json={"field": "trading_style", "value": "稳健"})
    body = send(client, cid).json()
    assert client.post(f"/api/v1/advice/conversations/{cid}/forget", json={}).status_code == 409
    advice_job(body["job_id"], TestOnlyProvider())
    with SessionLocal() as db:
        db.get(Job, body["job_id"]).status = "succeeded"
        db.commit()
    assert client.post(f"/api/v1/advice/conversations/{cid}/forget", json={}).status_code == 200
    assert client.get(f"/api/v1/advice/conversations/{cid}").status_code == 404
    with SessionLocal() as db:
        assert db.get(TradingPreference, "holding_period") is None
        assert db.get(TradingPreference, "trading_style").value == "稳健"
        assert not list(db.scalars(select(AdviceMessage)))


def test_failure_retry_and_cancel_redact_provider_errors(client, monkeypatch):
    configure(client)
    cid = conversation(client, stock(client))
    body = send(client, cid, learn_preferences=False).json()

    def fail(*_):
        raise ValueError("TEST-SECRET https://host.example?api_key=TEST-SECRET")

    monkeypatch.setattr(AIClient, "advise", fail)
    execute_job(body["job_id"])
    failed = client.get("/api/v1/jobs/" + body["job_id"]).json()
    assert failed["status"] == "failed" and "TEST-SECRET" not in failed["error"]
    retry = client.post(f"/api/v1/advice/messages/{body['message_id']}/retry", json={}).json()
    assert retry["message_id"] == body["message_id"] and retry["job_id"] != body["job_id"]
    client.post("/api/v1/jobs/" + retry["job_id"] + "/cancel", json={})
    provider = TestOnlyProvider()
    advice_job(retry["job_id"], provider)
    assert not provider.contexts
    assert len(client.get(f"/api/v1/advice/conversations/{cid}").json()["messages"]) == 1


def responses_data(text="报告指出波动。", annotations=None):
    return {
        "status": "completed",
        "model": "test-model",
        "output": [
            {"type": "web_search_call", "status": "completed"},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text, "annotations": annotations or []}],
            },
        ],
    }


def test_responses_request_requires_search_and_keeps_citations():
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert payload["store"] is False
        return httpx.Response(
            200,
            json=responses_data(
                annotations=[
                    {
                        "type": "url_citation",
                        "url": "https://exchange.example/report?id=1",
                        "title": "测试公告",
                        "end_index": 7,
                    }
                ]
            ),
        )

    provider = AIClient({**CONFIG, "base_url": "https://api.openai.com/v1"}, httpx.MockTransport(handler))
    result = provider.advise({}, [{"role": "user", "content": "test"}])
    assert calls[0]["tool_choice"] == "required" and calls[0]["tools"] == [{"type": "web_search"}]
    assert result["web_searched"] and result["sources"][0]["url"].endswith("?id=1")
    assert result["citations"][0]["end"] == 7
    with pytest.raises(ValueError):
        parse_response({"status": "incomplete", "output": []})
    bad = AIClient(
        {**CONFIG, "base_url": "https://api.openai.com/v1"},
        httpx.MockTransport(lambda _: httpx.Response(200, json=responses_data())),
    )
    with pytest.raises(ValueError, match="缺少检索或引用"):
        bad.advise({}, [])


def test_compatible_service_cannot_claim_built_in_web_search():
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "model": "local",
                "choices": [{"finish_reason": "stop", "message": {"content": "test-only offline"}}],
            },
        )

    provider = AIClient(
        {**CONFIG, "provider": "compatible", "base_url": "http://127.0.0.1:9999/v1"},
        httpx.MockTransport(handler),
    )
    result = provider.advise({}, [])
    assert not result["web_searched"] and not result["sources"]
    assert "tools" not in calls[0] and "不能核实当前行情" in calls[0]["messages"][0]["content"]


def test_preference_extraction_failure_does_not_block_answer(client):
    configure(client)
    cid = conversation(client, stock(client))
    body = send(client, cid).json()

    class FailingMemory(TestOnlyProvider):
        def extract_preferences(self, _):
            raise ValueError("invalid JSON")

    advice_job(body["job_id"], FailingMemory())
    detail = client.get(f"/api/v1/advice/conversations/{cid}").json()
    assert detail["messages"][-1]["role"] == "assistant"
    assert detail["messages"][-1]["metadata_json"]["memory_warning"]
    assert all(not p["value"] for p in detail["preferences"])


def test_manual_explanation_uses_saved_context_and_never_changes_numbers(client, monkeypatch):
    configure(client)
    sid = stock(client)
    pid, frozen = prediction(sid)
    seen = []

    def response(_client, context, messages):
        seen.append(context)
        return {
            "text": "TEST ONLY: explanation",
            "sources": [{"url": "https://example.com/test"}],
            "web_searched": True,
        }

    monkeypatch.setattr(AIClient, "advise", response)
    queued = client.post(f"/api/v1/predictions/{pid}/ai-analysis", json={}).json()
    assert client.post(f"/api/v1/predictions/{pid}/ai-analysis", json={}).json() == queued
    assert not seen
    execute_job(queued["job_id"])
    saved = client.get(f"/api/v1/predictions/{pid}").json()
    assert saved["result"] == frozen and saved["ai_status"] == "succeeded"
    assert seen[0]["prediction"]["prediction_id"] == pid
    assert saved["ai_analyses"][0]["content"]["context"]["prediction"]["data_cutoff"] == "2026-09-30"


def test_connection_test_does_not_send_private_context_or_echo_provider_exception(client, monkeypatch):
    configure(client)
    calls = []

    def complete(_client, instructions, messages):
        calls.append(messages)
        return {"text": "test-only", "model": "test-model"}

    monkeypatch.setattr(AIClient, "complete", complete)
    assert client.post("/api/v1/ai/test", json={}).json()["connected"]
    assert calls == [[{"role": "user", "content": "回复：连接成功"}]]

    def fail(*_):
        raise ValueError("TEST-SECRET")

    monkeypatch.setattr(AIClient, "complete", fail)
    response = client.post("/api/v1/ai/test", json={})
    assert response.status_code == 502 and "TEST-SECRET" not in response.text


def test_complete_backup_includes_preferences_but_not_credentials(client, tmp_path):
    from backend.app.backup import backup
    from backend.app.db import engine
    import sqlite3

    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite local backup content check")
    configure(client)
    cid = conversation(client, stock(client))
    client.post("/api/v1/advice/preferences", json={"field": "trading_style", "value": "测试用偏好"})
    destination = tmp_path / "complete"
    backup(destination)
    assert not list(destination.rglob("ai-settings.json"))
    assert "TEST-KEY-NEVER-LIVE" not in (destination / "manifest.json").read_text()
    with sqlite3.connect(destination / "database.sqlite") as db:
        assert (
            db.execute("SELECT value FROM trading_preferences WHERE field='trading_style'").fetchone()[0]
            == "测试用偏好"
        )
        assert db.execute("SELECT id FROM advice_conversations").fetchone()[0] == cid


def test_recent_conversation_context_is_bounded_and_current_question_retained(client):
    from backend.app.advice import bounded_history

    sid = stock(client)
    cid = conversation(client, sid)
    with SessionLocal() as db:
        for i in range(30):
            job = Job(kind="advice", status="succeeded", payload={})
            db.add(job)
            db.flush()
            db.add(
                AdviceMessage(
                    conversation_id=cid,
                    role="assistant",
                    text="旧回答" * 10000,
                    job_id=job.id,
                    metadata_json={},
                )
            )
            db.flush()
        current = AdviceMessage(conversation_id=cid, role="user", text="当前问题", metadata_json={})
        db.add(current)
        db.flush()
        messages = bounded_history(db, cid, current)
        assert messages[-1] == {"role": "user", "content": "当前问题"}
        assert len(messages) <= 24 and sum(len(m["content"]) for m in messages) <= 48000
        assert "旧回答因长度限制缩略" in messages[0]["content"]


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_submissions_cannot_duplicate_a_pending_chat(client, same_key):
    from concurrent.futures import ThreadPoolExecutor

    configure(client)
    cid = conversation(client, stock(client))
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda key: send(client, cid, client_key=key),
                ["parallel", "parallel" if same_key else "different"],
            )
        )
    if same_key:
        assert [r.status_code for r in responses] == [202, 202]
        assert responses[0].json() == responses[1].json()
    else:
        assert sorted(r.status_code for r in responses) == [202, 409]
    with SessionLocal() as db:
        assert len(list(db.scalars(select(AdviceMessage).where(AdviceMessage.conversation_id == cid)))) == 1
        assert len(list(db.scalars(select(Job).where(Job.kind == "advice")))) == 1
