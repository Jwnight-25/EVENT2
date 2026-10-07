"""Local-only credentials. Never return the key or include it in research backups."""

import json
import os
import tempfile
from urllib.parse import urlparse

from .config import DATA_DIR
from .errors import DomainError

SETTINGS_FILE = DATA_DIR / "ai-settings.json"


def validate_url(url):
    parsed = urlparse(url)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))
        )
    ):
        raise ValueError("AI地址必须是HTTPS或本机HTTP，不能含账号、密码或查询参数")
    return url.rstrip("/")


def settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    provider = os.getenv("AI_PROVIDER", "")
    if provider in ("openai", "compatible"):
        return {
            "provider": provider,
            "base_url": "https://api.openai.com/v1" if provider == "openai" else os.getenv("AI_BASE_URL", ""),
            "model": os.getenv("AI_MODEL", ""),
            "api_key": os.getenv("AI_API_KEY", ""),
            "web_search": provider == "openai" and os.getenv("AI_WEB_SEARCH", "true").lower() == "true",
        }
    return {"provider": "legacy" if os.getenv("AI_ANALYSIS_URL") else "none"}


def public_settings():
    config = settings()
    provider = config.get("provider", "none")
    base_url = ""
    try:
        if provider in ("openai", "compatible"):
            base_url = validate_url(config.get("base_url", ""))
        valid = bool(base_url and config.get("model"))
        configured = valid and bool(config.get("api_key"))
    except ValueError:
        configured = False
    return {
        "provider": provider,
        "base_url": base_url,
        "model": config.get("model", ""),
        "key_present": bool(config.get("api_key")),
        "configured": bool(configured),
        "web_search": bool(configured and provider == "openai" and config.get("web_search")),
        "explanation_available": bool(configured or provider == "legacy"),
        "chat_available": bool(configured),
        "connection_verified": False,
    }


def save_settings(body):
    old = settings()
    config = body.model_dump()
    config["base_url"] = "https://api.openai.com/v1" if config["provider"] == "openai" else config["base_url"]
    try:
        config["base_url"] = validate_url(config["base_url"])
    except ValueError as exc:
        raise DomainError(str(exc)) from None
    if not config["model"].strip():
        raise DomainError("请填写服务支持的模型名称")
    if config["provider"] != "openai":
        config["web_search"] = False
    # A saved secret must never follow an endpoint change.
    if not config.get("api_key"):
        same_target = old.get("provider") == config["provider"] and old.get("base_url") == config["base_url"]
        config["api_key"] = old.get("api_key", "") if same_target else ""
    if not config["api_key"]:
        raise DomainError("首次接入或更换服务地址时，请在本机填写API密钥")
    if any(c in config["api_key"] for c in "\r\n"):
        raise DomainError("密钥格式无效")
    descriptor, temporary = tempfile.mkstemp(dir=DATA_DIR, prefix=".ai-settings-")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(config, stream, ensure_ascii=False)
        os.replace(temporary, SETTINGS_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return public_settings()
