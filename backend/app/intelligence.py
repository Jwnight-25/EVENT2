"""Public headlines and report metadata, with source links and retrieval evidence."""

import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, func

from .config import DATA_DIR
from .db import SessionLocal, now, uid
from .entities import Stock, IntelligenceItem, IntelligenceSync, Job
from .errors import DomainError, require
from .market_updates import market_time, SHANGHAI

NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
REPORT_URL = "https://reportapi.eastmoney.com/report/list"


def clean(value, limit=500):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", str(value or "")))).strip()[:limit]


def safe_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        if any(ord(c) < 32 for c in value):
            return None
        return value
    except ValueError:
        return None


def publication(value):
    try:
        stamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=SHANGHAI)
        return stamp.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def decode_jsonp(value):
    value = value.strip()
    if not value.startswith("{"):
        match = re.fullmatch(r"[A-Za-z_$][\w.$]*\((.*)\);?", value, re.S)
        if not match:
            raise DomainError("新闻接口响应格式异常", "news_source_failed", 502)
        value = match.group(1)
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("Expected object")
        return parsed
    except ValueError:
        raise DomainError("新闻接口响应不是有效JSON对象", "news_source_failed", 502)


def fetch_source(kind, stock, directory, industry_code=None):
    stamp = market_time()
    if kind == "news":
        query = {
            "uid": "",
            "keyword": stock.code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 30,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        url = NEWS_URL
        params = {
            "cb": "jQuery351" + str(int(stamp.timestamp() * 1000)),
            "param": json.dumps(query, ensure_ascii=False),
            "_": str(int(stamp.timestamp() * 1000)),
        }
    else:
        if kind == "industry_report" and not industry_code:
            return [], {"note": "暂无可核验行业编码，未猜测行业报告", "industry_code": None, "skipped": True}
        url = REPORT_URL
        params = {
            "industryCode": industry_code if kind == "industry_report" else "*",
            "pageSize": "30",
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": (stamp.date() - timedelta(days=180)).isoformat(),
            "endTime": stamp.date().isoformat(),
            "pageNo": "1",
            "p": "1",
            "pageNum": "1",
            "pageNumber": "1",
            "qType": "1" if kind == "industry_report" else "0",
            "code": "" if kind == "industry_report" else stock.code,
            "orgCode": "",
            "rcode": "",
            "fields": "",
        }
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
        response = client.get(
            url,
            params=params,
            headers={
                "Accept": "application/json, text/javascript, */*",
                "Referer": "https://so.eastmoney.com/news/s?keyword=" + stock.code,
                "User-Agent": "Mozilla/5.0",
            },
        )
    blob = response.content
    (directory / "response.txt").write_bytes(blob)
    evidence = {
        "provider": "Eastmoney",
        "url": str(response.url),
        "retrieved_at": now(),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "http_status": response.status_code,
        "query": stock.code,
        "kind": kind,
    }
    (directory / "provenance.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    response.raise_for_status()
    value = decode_jsonp(response.text) if kind == "news" else response.json()
    if kind == "news":
        values = value["result"]["cmsArticleWebOld"]
        rows = []
        for item in values[:30]:
            code = str(item.get("code", ""))
            url = item.get("url") or (
                f"https://finance.eastmoney.com/a/{code}.html" if re.fullmatch(r"\d+", code) else ""
            )
            rows.append(
                {
                    "title": item.get("title"),
                    "summary": item.get("content"),
                    "url": url,
                    "publisher": item.get("mediaName"),
                    "published_at": item.get("date"),
                    "metadata": {"association": "关键词关联", "query": stock.code},
                }
            )
        return rows, evidence
    rows = []
    values = value["data"]
    for item in values[:30]:
        info = str(item.get("infoCode", ""))
        if not re.fullmatch(r"[A-Za-z0-9]+", info):
            continue
        if kind == "stock_report" and str(item.get("stockCode")) != stock.code:
            continue
        item_industry = str(item.get("industryCode") or item.get("indvInduCode") or "")
        if kind == "industry_report" and item_industry != industry_code:
            continue
        rows.append(
            {
                "title": item.get("title"),
                "summary": "",
                "publisher": item.get("orgSName") or item.get("orgName"),
                "url": f"https://pdf.dfcfw.com/pdf/H3_{info}_1.pdf",
                "published_at": item.get("publishDate"),
                "metadata": {
                    "industry": item.get("indvInduName") or item.get("industryName"),
                    "industry_code": item_industry,
                    "association": "行业关联" if kind == "industry_report" else "个股研报",
                    "rating": item.get("emRatingName"),
                    "published_precision": "date",
                },
            }
        )
    return rows, evidence


def enqueue_intelligence(db, stock_id, key=None):
    from .advice import serial_write

    serial_write(db)
    require(db.scalar(select(Stock).where(Stock.id == stock_id).with_for_update()), "股票不存在")
    if key:
        prior = db.scalar(select(Job).where(Job.idempotency_key == key))
        if prior:
            return prior
    for job in db.scalars(
        select(Job).where(Job.kind == "intelligence", Job.status.in_(["queued", "running"]))
    ):
        if job.payload.get("stock_id") == stock_id:
            return job
    job = Job(
        kind="intelligence", payload={"stock_id": stock_id}, idempotency_key=key or "intel-manual:" + uid()
    )
    db.add(job)
    db.commit()
    return job


def intelligence_job(job_id):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        stock = db.get(Stock, job.payload["stock_id"])
        industry_code = None
        successful = 0
        sync_ids = []
        for kind in ("news", "stock_report", "industry_report"):
            if db.get(Job, job_id).cancel_requested:
                break
            sync = IntelligenceSync(stock_id=stock.id, kind=kind)
            db.add(sync)
            db.commit()
            sync_ids.append(sync.id)
            directory = DATA_DIR / "audits" / "intelligence" / sync.id
            directory.mkdir(parents=True)
            try:
                rows, evidence = fetch_source(kind, stock, directory, industry_code)
                added = skipped = 0
                if kind == "stock_report":
                    codes = [str(r["metadata"].get("industry_code") or "") for r in rows]
                    industry_code = next((code for code in codes if re.fullmatch(r"\d+", code)), None)
                for row in rows:
                    title, url = clean(row.get("title")), safe_url(row.get("url"))
                    published = publication(row.get("published_at"))
                    if (
                        not title
                        or not url
                        or (
                            published
                            and datetime.fromisoformat(published)
                            > datetime.now(timezone.utc) + timedelta(minutes=5)
                        )
                    ):
                        skipped += 1
                        continue
                    fingerprint = hashlib.sha256(f"{kind}|{url}".encode()).hexdigest()
                    if db.scalar(
                        select(IntelligenceItem.id).where(
                            IntelligenceItem.stock_id == stock.id, IntelligenceItem.fingerprint == fingerprint
                        )
                    ):
                        continue
                    db.add(
                        IntelligenceItem(
                            stock_id=stock.id,
                            fingerprint=fingerprint,
                            kind=kind,
                            title=title,
                            url=url,
                            publisher=clean(row.get("publisher"), 150) or "东方财富（聚合）",
                            summary=clean(row.get("summary"), 120),
                            published_at=published,
                            metadata_json={
                                **row.get("metadata", {}),
                                "sync_id": sync.id,
                                "source": "Eastmoney",
                            },
                        )
                    )
                    added += 1
                sync.status = "skipped" if evidence.get("skipped") else "succeeded"
                sync.result = {"added": added, "skipped": skipped, "evidence": evidence, "finished_at": now()}
                successful += int(not evidence.get("skipped"))
                db.commit()
            except Exception as exc:
                db.rollback()
                sync = db.get(IntelligenceSync, sync.id)
                sync.status, sync.error = "failed", "公开来源暂不可用，已保留历史情报；稍后再次刷新"
                sync.result = {"error_type": type(exc).__name__, "finished_at": now()}
                db.commit()
            (directory / "result.json").write_text(
                json.dumps(
                    {"status": sync.status, "error": sync.error, **sync.result}, ensure_ascii=False, indent=2
                )
            )
        job = db.get(Job, job_id)
        job.result = {"sync_ids": sync_ids, "successful_sources": successful}
        db.commit()
        if not successful:
            raise DomainError("本次公开情报来源均未完成，历史记录保持可查", "intelligence_unavailable", 502)


def intelligence_status(db, stock_id, kind=None, before=None, limit=30):
    require(db.get(Stock, stock_id), "股票不存在")
    query = select(IntelligenceItem).where(IntelligenceItem.stock_id == stock_id)
    if kind:
        query = query.where(IntelligenceItem.kind == kind)
    if before:
        previous = require(db.get(IntelligenceItem, before), "分页记录不存在")
        require(previous.stock_id == stock_id or None, "分页股票不符")
        # Cursor is a stable (publication/retrieval time, id) ordering.
        stamp = previous.published_at or previous.retrieved_at
        sort = func.coalesce(IntelligenceItem.published_at, IntelligenceItem.retrieved_at)
        from sqlalchemy import or_, and_

        query = query.where(or_(sort < stamp, and_(sort == stamp, IntelligenceItem.id < previous.id)))
    items = list(
        db.scalars(
            query.order_by(
                func.coalesce(IntelligenceItem.published_at, IntelligenceItem.retrieved_at).desc(),
                IntelligenceItem.id.desc(),
            ).limit(limit + 1)
        )
    )
    has_more = len(items) > limit
    items = items[:limit]
    syncs = list(
        db.scalars(
            select(IntelligenceSync)
            .where(IntelligenceSync.stock_id == stock_id)
            .order_by(IntelligenceSync.created_at.desc())
            .limit(6)
        )
    )
    return {
        "items": [
            {
                "id": r.id,
                "kind": r.kind,
                "title": r.title,
                "url": r.url,
                "publisher": r.publisher,
                "summary": r.summary,
                "published_at": r.published_at,
                "retrieved_at": r.retrieved_at,
                "metadata": r.metadata_json,
            }
            for r in items
        ],
        "next_cursor": items[-1].id if has_more and items else None,
        "syncs": [
            {
                "kind": s.kind,
                "status": s.status,
                "error": s.error,
                "created_at": s.created_at,
                "result": s.result,
            }
            for s in syncs
        ],
        "schedule": "每日07:00—22:00，每2小时更新；显示来源与发布时间，未知发布时间单独标注",
    }
