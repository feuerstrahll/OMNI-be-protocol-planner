from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
import structlog
from diskcache import Cache


@dataclass
class AppConfig:
    ncbi_api_key: Optional[str]
    ncbi_email: Optional[str]
    ncbi_tool: str
    cache_dir: str
    log_level: str


def load_config() -> AppConfig:
    return AppConfig(
        ncbi_api_key=os.getenv("NCBI_API_KEY"),
        ncbi_email=os.getenv("NCBI_EMAIL"),
        ncbi_tool=os.getenv("NCBI_TOOL", "be-mvp"),
        cache_dir=os.getenv("CACHE_DIR", "backend/.cache"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def configure_logging() -> structlog.BoundLogger:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    )
    return structlog.get_logger()


def get_cache(cache_dir: str) -> Cache:
    os.makedirs(cache_dir, exist_ok=True)
    return Cache(cache_dir)


def request_json_with_cache(
    cache: Cache,
    url: str,
    params: Dict[str, Any],
    ttl_seconds: int = 3600,
    timeout: int = 20,
) -> Dict[str, Any]:
    key = json.dumps({"url": url, "params": params}, sort_keys=True)
    if key in cache:
        return cache[key]
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    cache.set(key, data, expire=ttl_seconds)
    return data


def request_text_with_cache(
    cache: Cache,
    url: str,
    params: Dict[str, Any],
    ttl_seconds: int = 3600,
    timeout: int = 20,
) -> str:
    key = json.dumps({"url": url, "params": params}, sort_keys=True)
    if key in cache:
        return cache[key]
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    text = resp.text
    cache.set(key, text, expire=ttl_seconds)
    return text


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def safe_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except Exception:
        return None


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
