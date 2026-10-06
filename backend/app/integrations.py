"""Provider contracts; no credentials or synthetic news are embedded."""

import os
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse

import httpx
from .db import SessionLocal
from .entities import Prediction, AIAnalysis, Stock


class DataProvider(Protocol):
    def historical_bars(
        self, exchange: str, code: str, start: str, end: str, interval: str
    ) -> list[dict]: ...
    def trading_calendar(self, start: str, end: str) -> list[dict]: ...


class AnalysisProvider(Protocol):
    def analyze(self, request: dict) -> dict: ...


class HttpAnalysisProvider:
    """User-configured research service. Must return text and attributable sources."""

    def analyze(self, request):
        url = os.getenv("AI_ANALYSIS_URL", "")
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
        ):
            raise ValueError("分析接口必须使用HTTPS或本机HTTP")
        headers = {"Authorization": "Bearer " + os.environ["AI_API_KEY"]} if os.getenv("AI_API_KEY") else {}
        with httpx.Client(timeout=45, follow_redirects=False) as client:
            response = client.post(url, json=request, headers=headers)
            response.raise_for_status()
            data = response.json()
        sources = data.get("sources", [])
        if (
            not isinstance(data.get("text"), str)
            or not data["text"].strip()
            or not isinstance(sources, list)
            or not sources
        ):
            raise ValueError("联网分析必须返回正文及可追溯来源")
        normalized = []
        for item in sources[:30]:
            if not isinstance(item, dict) or urlparse(str(item.get("url", ""))).scheme not in (
                "https",
                "http",
            ):
                raise ValueError("来源链接无效")
            normalized.append(
                {
                    "url": item["url"],
                    "title": str(item.get("title", item["url"]))[:500],
                    "published_at": item.get("published_at"),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return {
            "text": data["text"][:30000],
            "sources": normalized,
            "model": data.get("model"),
            "provider": "configured_http",
            "schema_version": "analysis-v1",
        }


def analyze_prediction(identifier, provider=None):
    with SessionLocal() as db:
        prediction = db.get(Prediction, identifier)
        stock = db.get(Stock, prediction.stock_id)
        request = {
            "schema_version": "analysis-v1",
            "stock": {"exchange": stock.exchange, "code": stock.code, "name": stock.name},
            "forecast": prediction.result,
            "instructions": "联网查阅公告、财报及事件，返回text与sources；区分事实和推断，不修改模型数值，不给出自动交易指令。",
        }
    if not provider and not os.getenv("AI_ANALYSIS_URL"):
        status, content = "not_configured", {"message": "尚未配置联网分析接口，模型结果已保留"}
    else:
        try:
            content = (provider or HttpAnalysisProvider()).analyze(request)
            status = "succeeded"
        except Exception:
            # Do not persist response bodies, URLs or headers that may contain secrets.
            status, content = "failed", {"message": "联网分析失败或返回内容缺少来源；模型结果已保留"}
    with SessionLocal() as db:
        db.add(AIAnalysis(prediction_id=identifier, status=status, content=content))
        db.get(Prediction, identifier).ai_status = status
        db.commit()
