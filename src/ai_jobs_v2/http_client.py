"""Polite HTTP client with bounded retries, per-host rate limiting, conditional GET caching, POST support, and secret redaction."""
from __future__ import annotations
import json
import logging
import pathlib
import random
import re
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable
from urllib.parse import urlparse

import requests

logger = logging.getLogger("ai_jobs_v2.http")

SECRET_REDACT_PATTERN = re.compile(
    r"(token|key|secret|authorization|password|bearer)\s*([=:])\s*([^\s&\"']+)",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    if not text:
        return ""
    return SECRET_REDACT_PATTERN.sub(r"\1\2[REDACTED]", text)


@dataclass
class HTTPResponseData:
    status_code: int
    text: str
    headers: Dict[str, str]
    url: str
    not_modified: bool = False

    def json(self) -> Any:
        return json.loads(self.text)


class PoliteHTTPClient:
    USER_AGENT = "AIJobsHubCollector/2.0 (+https://github.com/azarya78/ai-jobs-aggregator; public-careers-sync)"
    MAX_CACHE_BODY_CHARS = 600_000
    MAX_CACHE_ENTRIES = 80

    def __init__(
        self,
        cache_path: Optional[pathlib.Path] = None,
        max_retries: int = 2,
        backoff_base_sec: float = 1.0,
        transport_override: Optional[Callable[..., HTTPResponseData]] = None,
        post_transport_override: Optional[Callable[[str, Dict[str, str], Optional[Dict[str, Any]], int], HTTPResponseData]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.cache_path = cache_path
        self.max_retries = max_retries
        self.backoff_base_sec = backoff_base_sec
        self.transport_override = transport_override
        self.post_transport_override = post_transport_override
        self.sleep_fn = sleep_fn
        self._last_request_by_host: Dict[str, float] = {}
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json, application/rss+xml, text/html, application/xhtml+xml;q=0.9",
                "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
            }
        )
        if self.cache_path and self.cache_path.exists():
            try:
                loaded = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._cache = loaded
            except Exception as exc:
                logger.warning("Ignoring unreadable HTTP cache: %s", redact_secrets(str(exc)))
                self._cache = {}

    def save_cache(self) -> None:
        if not self.cache_path:
            return
        # Prune empty/oversized entries and bound total cached keys to prevent repository bloat
        valid_items = [
            (k, v)
            for k, v in self._cache.items()
            if isinstance(v, dict)
            and isinstance(v.get("body"), str)
            and 0 < len(v["body"]) <= self.MAX_CACHE_BODY_CHARS
        ]
        if len(valid_items) > self.MAX_CACHE_ENTRIES:
            valid_items = valid_items[-self.MAX_CACHE_ENTRIES :]
        self._cache = dict(valid_items)

        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)

    def _throttle(self, url: str, rate_limit_rps: float) -> None:
        if rate_limit_rps <= 0:
            return
        host = (urlparse(url).hostname or "").lower()
        min_interval = 1.0 / rate_limit_rps
        now = time.monotonic()
        last = self._last_request_by_host.get(host)
        if last is not None:
            elapsed = now - last
            if elapsed < min_interval:
                self.sleep_fn(min_interval - elapsed)
        self._last_request_by_host[host] = time.monotonic()

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        timeout_sec: int = 20,
        rate_limit_rps: float = 1.5,
        use_conditional_cache: bool = True,
    ) -> HTTPResponseData:
        cache_key = url if not params else f"{url}?{'&'.join(f'{k}={v}' for k, v in sorted(params.items()))}"
        req_headers: Dict[str, str] = {}
        cached_entry = self._cache.get(cache_key) if use_conditional_cache else None
        has_usable_cached_body = bool(
            isinstance(cached_entry, dict)
            and isinstance(cached_entry.get("body"), str)
            and cached_entry["body"].strip()
        )
        if cached_entry and has_usable_cached_body:
            if cached_entry.get("etag"):
                req_headers["If-None-Match"] = cached_entry["etag"]
            if cached_entry.get("last_modified"):
                req_headers["If-Modified-Since"] = cached_entry["last_modified"]
        elif cached_entry and not has_usable_cached_body:
            self._cache.pop(cache_key, None)
            cached_entry = None

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._throttle(url, rate_limit_rps)
            try:
                if self.transport_override is not None:
                    resp = self.transport_override(url, req_headers, params, timeout_sec)
                else:
                    raw_resp = self._session.get(
                        url,
                        params=params,
                        headers=req_headers,
                        timeout=timeout_sec,
                        allow_redirects=True,
                    )
                    resp = HTTPResponseData(
                        status_code=raw_resp.status_code,
                        text=raw_resp.text,
                        headers={k.lower(): v for k, v in raw_resp.headers.items()},
                        url=raw_resp.url,
                    )

                if resp.status_code == 304:
                    if cached_entry and isinstance(cached_entry.get("body"), str) and cached_entry["body"].strip():
                        # Refresh LRU order
                        self._cache[cache_key] = self._cache.pop(cache_key)
                        return HTTPResponseData(
                            status_code=200,
                            text=cached_entry["body"],
                            headers=resp.headers,
                            url=resp.url,
                            not_modified=True,
                        )
                    # Safeguard: 304 received without a valid cached body -> strip conditional headers and re-fetch
                    req_headers.pop("If-None-Match", None)
                    req_headers.pop("If-Modified-Since", None)
                    self._cache.pop(cache_key, None)
                    cached_entry = None
                    if attempt < self.max_retries:
                        continue
                    raise RuntimeError(f"HTTP 304 received without usable cached body for {redact_secrets(url)}")

                if resp.status_code in (401, 403, 404, 451):
                    raise RuntimeError(f"HTTP {resp.status_code} access denied or not found for {redact_secrets(url)}")

                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(f"Transient HTTP {resp.status_code} from {redact_secrets(url)}")

                if 200 <= resp.status_code < 300:
                    etag = resp.headers.get("etag")
                    last_mod = resp.headers.get("last-modified")
                    if (
                        use_conditional_cache
                        and (etag or last_mod)
                        and resp.text.strip()
                        and len(resp.text) <= self.MAX_CACHE_BODY_CHARS
                    ):
                        self._cache.pop(cache_key, None)
                        self._cache[cache_key] = {
                            "etag": etag,
                            "last_modified": last_mod,
                            "body": resp.text,
                        }
                    return resp

                raise RuntimeError(f"Unexpected HTTP {resp.status_code} from {redact_secrets(url)}")

            except Exception as exc:
                last_exc = exc
                msg = redact_secrets(str(exc))
                if any(code in msg for code in ("HTTP 401", "HTTP 403", "HTTP 404", "HTTP 451")):
                    break
                if attempt < self.max_retries:
                    delay = self.backoff_base_sec * (2 ** attempt) + random.uniform(0.05, 0.25)
                    logger.warning("Retry %d/%d for %s after error: %s", attempt + 1, self.max_retries, redact_secrets(url), msg)
                    self.sleep_fn(delay)

        raise RuntimeError(redact_secrets(str(last_exc) if last_exc else f"Request failed for {url}"))

    def post(
        self,
        url: str,
        json_body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout_sec: int = 20,
        rate_limit_rps: float = 1.5,
    ) -> HTTPResponseData:
        req_headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            req_headers.update(headers)

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._throttle(url, rate_limit_rps)
            try:
                if self.post_transport_override is not None:
                    resp = self.post_transport_override(url, req_headers, json_body, timeout_sec)
                elif self.transport_override is not None:
                    resp = self.transport_override(url, req_headers, json_body, timeout_sec)
                else:
                    raw_resp = self._session.post(
                        url,
                        json=json_body if json_body is not None else {},
                        headers=req_headers,
                        timeout=timeout_sec,
                        allow_redirects=True,
                    )
                    resp = HTTPResponseData(
                        status_code=raw_resp.status_code,
                        text=raw_resp.text,
                        headers={k.lower(): v for k, v in raw_resp.headers.items()},
                        url=raw_resp.url,
                    )

                if resp.status_code in (401, 403, 404, 451):
                    raise RuntimeError(f"HTTP {resp.status_code} access denied or not found for {redact_secrets(url)}")

                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(f"Transient HTTP {resp.status_code} from {redact_secrets(url)}")

                if 200 <= resp.status_code < 300:
                    return resp

                raise RuntimeError(f"Unexpected HTTP {resp.status_code} from {redact_secrets(url)}")

            except Exception as exc:
                last_exc = exc
                msg = redact_secrets(str(exc))
                if any(code in msg for code in ("HTTP 401", "HTTP 403", "HTTP 404", "HTTP 451", "Unexpected HTTP 4")):
                    break
                if attempt < self.max_retries:
                    delay = self.backoff_base_sec * (2 ** attempt) + random.uniform(0.05, 0.25)
                    logger.warning("Retry %d/%d POST %s after error: %s", attempt + 1, self.max_retries, redact_secrets(url), msg)
                    self.sleep_fn(delay)

        raise RuntimeError(redact_secrets(str(last_exc) if last_exc else f"POST request failed for {url}"))
