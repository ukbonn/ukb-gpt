"""
title: Adaptive Rate Limit Filter Pipeline
author: sebnowak (migrated by Codex)
version: 1.0.0
license: MIT
description: Static or adaptive rate limiting for Open WebUI Pipelines with optional priority injection and blocked-request metrics reporting.
"""

import asyncio
import json
import logging
import os
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import holidays
from fastapi import HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

RUNNING_METRIC_NAMES = ["sglang:num_running_reqs", "vllm:num_requests_running"]
QUEUED_METRIC_NAMES = ["sglang:num_queue_reqs", "vllm:num_requests_waiting"]


def parse_prometheus_metrics(text: str, metric_names: List[str]) -> float:
    total = 0.0
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        metric_name = parts[0]
        if not any(name in metric_name for name in metric_names):
            continue
        try:
            total += float(parts[1])
        except ValueError:
            log.warning("Could not parse metric line %r", line)
    return total


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_limit_config(name: str, default: Union[str, int]) -> Union[str, int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip()
    if value.isdigit():
        return int(value)
    return value


def _normalize_metric_base(base: str) -> str:
    cleaned = base.strip()
    if not cleaned:
        return cleaned
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        url = cleaned
    else:
        url = f"http://{cleaned}"
    if url.endswith("/v1"):
        url = url[:-3]
    elif url.endswith("/"):
        url = url[:-1]
    return url


def resolve_metrics_url_from_env() -> Optional[str]:
    direct = os.getenv("ADAPTIVE_RATE_LIMIT_METRICS_URL", "").strip()
    if direct:
        return direct

    backend_nodes = os.getenv("LLM_BACKEND_NODES", "").strip()
    if backend_nodes:
        first_node = backend_nodes.split(",")[0].strip()
        if first_node:
            return f"{_normalize_metric_base(first_node)}/metrics"

    endpoint = os.getenv("LLM_ENDPOINT", "").strip() or os.getenv(
        "PRIMARY_OPENAI_ENDPOINT", ""
    ).strip()
    if endpoint:
        return f"{_normalize_metric_base(endpoint)}/metrics"

    return None


class Pipeline:
    class Valves(BaseModel):
        pipelines: List[str] = Field(default_factory=lambda: ["*"])
        priority: int = 0
        mode: Literal["adaptive", "static"] = "adaptive"
        metrics_url: Optional[str] = None

        requests_per_minute: Optional[int] = None
        requests_per_hour: Optional[int] = None
        sliding_window_limit: Optional[int] = None
        sliding_window_minutes: Optional[int] = None

        day_rate_limit: Union[str, int] = Field(
            default='{"0": 16, "1": 15, "2": 14, "3": 13, "4": 12, "5": 11, "6": 10, "7": 9, "8": 8, "9": 7, "10": 6, "11": 5, "12": 4, "13": 3, "14": 2, "15": 1, "16": 0}',
            description="JSON string for adaptive day limits or a fixed integer limit.",
        )
        night_rate_limit: Union[str, int] = Field(
            default='{"32": 64, "40": 56, "48": 48, "56": 40, "64": 32, "80": 24, "96": 16, "112": 12, "128": 8, "134": 4, "148": 0}',
            description="JSON string for adaptive night limits or a fixed integer limit.",
        )
        night_start_hour: int = Field(
            default=22, description="Hour when the night rate limit starts (0-23)."
        )
        night_end_hour: int = Field(
            default=6, description="Hour when the night rate limit ends (0-23)."
        )
        apply_night_limit_fullday: str = Field(
            default="Saturday, Sunday, holiday_DE_NRW",
            description="Comma-separated day names or holiday rules using holiday_{COUNTRY}_{SUBDIVISION}.",
        )
        fallback_day_rate_limit: int = Field(default=8)
        fallback_night_rate_limit: int = Field(default=32)
        update_adaptive_rate_limits_interval_seconds: int = Field(default=1, ge=1)

        global_limit: bool = Field(default=True)
        enabled_for_admins: bool = Field(default=True)
        priority_whitelist: str = Field(
            default="browser",
            description="Comma-separated email, user-id, admins, or browser tokens that force priority=0.",
        )
        rate_limit_whitelist: str = Field(
            default="",
            description="Comma-separated email, user-id, admins, or browser tokens exempt from rate limits.",
        )

        log_report_interval_seconds: int = Field(default=60, ge=10)
        log_report_victoriametrics_url: Optional[str] = Field(
            default="http://proxy:8081"
        )
        enable_victoriametrics_block_logging: bool = Field(default=False)
        inject_priority: bool = Field(default=False)
        allow_anonymous_requests: bool = Field(default=False)
        enable_debug_logging: bool = Field(default=False)

    def __init__(self):
        self.type = "filter"
        self.name = "Adaptive Rate Limit Filter"
        self.valves = self.Valves(
            **{
                "pipelines": os.getenv("RATE_LIMIT_PIPELINES", "*").split(","),
                "priority": int(os.getenv("RATE_LIMIT_FILTER_PRIORITY", "0")),
                "mode": os.getenv("ADAPTIVE_RATE_LIMIT_MODE", "adaptive").strip().lower(),
                "metrics_url": resolve_metrics_url_from_env(),
                "requests_per_minute": _env_optional_int(
                    "RATE_LIMIT_REQUESTS_PER_MINUTE", 10
                ),
                "requests_per_hour": _env_optional_int(
                    "RATE_LIMIT_REQUESTS_PER_HOUR", 1000
                ),
                "sliding_window_limit": _env_optional_int(
                    "RATE_LIMIT_SLIDING_WINDOW_LIMIT", 100
                ),
                "sliding_window_minutes": _env_optional_int(
                    "RATE_LIMIT_SLIDING_WINDOW_MINUTES", 15
                ),
                "day_rate_limit": _env_limit_config(
                    "ADAPTIVE_RATE_LIMIT_DAY_RATE_LIMIT",
                    self.Valves.model_fields["day_rate_limit"].default,
                ),
                "night_rate_limit": _env_limit_config(
                    "ADAPTIVE_RATE_LIMIT_NIGHT_RATE_LIMIT",
                    self.Valves.model_fields["night_rate_limit"].default,
                ),
                "night_start_hour": int(
                    os.getenv("ADAPTIVE_RATE_LIMIT_NIGHT_START_HOUR", "22")
                ),
                "night_end_hour": int(
                    os.getenv("ADAPTIVE_RATE_LIMIT_NIGHT_END_HOUR", "6")
                ),
                "apply_night_limit_fullday": os.getenv(
                    "ADAPTIVE_RATE_LIMIT_FULLDAY_RULES",
                    self.Valves.model_fields["apply_night_limit_fullday"].default,
                ),
                "fallback_day_rate_limit": int(
                    os.getenv("ADAPTIVE_RATE_LIMIT_FALLBACK_DAY", "8")
                ),
                "fallback_night_rate_limit": int(
                    os.getenv("ADAPTIVE_RATE_LIMIT_FALLBACK_NIGHT", "32")
                ),
                "update_adaptive_rate_limits_interval_seconds": int(
                    os.getenv("ADAPTIVE_RATE_LIMIT_UPDATE_INTERVAL_SECONDS", "1")
                ),
                "global_limit": _env_bool("ADAPTIVE_RATE_LIMIT_GLOBAL_LIMIT", True),
                "enabled_for_admins": _env_bool(
                    "ADAPTIVE_RATE_LIMIT_ENABLED_FOR_ADMINS", True
                ),
                "priority_whitelist": os.getenv(
                    "ADAPTIVE_RATE_LIMIT_PRIORITY_WHITELIST", ""
                ),
                "rate_limit_whitelist": os.getenv(
                    "ADAPTIVE_RATE_LIMIT_WHITELIST", ""
                ),
                "log_report_interval_seconds": int(
                    os.getenv("ADAPTIVE_RATE_LIMIT_REPORT_INTERVAL_SECONDS", "60")
                ),
                "log_report_victoriametrics_url": os.getenv(
                    "ADAPTIVE_RATE_LIMIT_VICTORIAMETRICS_URL", "http://proxy:8081"
                ),
                "enable_victoriametrics_block_logging": _env_bool(
                    "ADAPTIVE_RATE_LIMIT_ENABLE_VICTORIAMETRICS_BLOCK_LOGGING",
                    False,
                ),
                "inject_priority": _env_bool(
                    "ADAPTIVE_RATE_LIMIT_INJECT_PRIORITY", False
                ),
                "allow_anonymous_requests": _env_bool(
                    "ADAPTIVE_RATE_LIMIT_ALLOW_ANONYMOUS", False
                ),
                "enable_debug_logging": _env_bool(
                    "ADAPTIVE_RATE_LIMIT_DEBUG_LOGGING", False
                ),
            }
        )

        self.user_requests: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.blocked_requests_log: Dict[str, Dict[int, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self.current_rate_limits = {
            "day": self.valves.fallback_day_rate_limit,
            "night": self.valves.fallback_night_rate_limit,
        }
        self.request_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.rate_limit_lock = threading.Lock()
        self.holiday_objects: Dict[str, Any] = {}
        self._shutdown_event: Optional[asyncio.Event] = None
        self._tasks: List[asyncio.Task[Any]] = []

    async def on_startup(self) -> None:
        if self._tasks:
            return
        log.info("Starting adaptive/static rate limit pipeline tasks.")
        self._shutdown_event = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self._cleanup_loop(), name="ukbgpt-rate-limit-cleanup"),
            asyncio.create_task(self._report_blocked_requests_loop(), name="ukbgpt-rate-limit-report"),
            asyncio.create_task(self._update_adaptive_rate_limits_loop(), name="ukbgpt-rate-limit-metrics"),
        ]

    async def on_shutdown(self) -> None:
        if self._shutdown_event is not None:
            self._shutdown_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._shutdown_event = None

    async def on_valves_updated(self) -> None:
        with self.rate_limit_lock:
            self.current_rate_limits["day"] = self.valves.fallback_day_rate_limit
            self.current_rate_limits["night"] = self.valves.fallback_night_rate_limit
        self.holiday_objects.clear()
        self._debug("Rate limit valves updated.")

    async def inlet(
        self,
        body: dict,
        user: Optional[dict] = None,
        request: Optional[dict] = None,
    ) -> dict:
        uid, ident, role, model_id = self._request_identity(user, body)

        if ident == "anonymous" and not self.valves.allow_anonymous_requests:
            self._raise_http_error(401, "Anonymous requests are not permitted.")

        request_priority, priority_reason = self._resolve_request_priority(
            ident, uid, role, request
        )
        self._debug(
            f"Priority decision ident={ident} request_priority={request_priority} "
            f"reason={priority_reason} user_agent={self._request_user_agent(request)!r}"
        )
        if self.valves.inject_priority:
            body["priority"] = request_priority

        if self._check_whitelist(
            ident, uid, role, self.valves.rate_limit_whitelist, request
        ):
            self._debug(f"Bypassing rate limit for {ident} due to whitelist.")
            return body

        if role == "admin" and not self.valves.enabled_for_admins:
            self._debug(f"Bypassing rate limit for admin {ident}.")
            return body

        model_key = "__global__" if self.valves.global_limit else model_id
        now = self._time()

        with self.request_lock:
            timestamps = self._prune_requests_locked(ident, model_key, now)
            if self.valves.mode == "static":
                self._enforce_static_limit(ident, model_key, timestamps, now, request_priority)
            else:
                self._enforce_adaptive_limit(
                    ident, model_key, timestamps, now, request_priority
                )
            self.user_requests[ident][model_key].append(now)

        return body

    def _debug(self, message: str) -> None:
        if self.valves.enable_debug_logging:
            log.info("DEBUG: %s", message)

    def _time(self) -> float:
        return time.time()

    def _now(self) -> datetime:
        return datetime.now()

    def _request_identity(
        self, user: Optional[dict], body: dict
    ) -> Tuple[str, str, Optional[str], str]:
        if not user:
            return "anonymous", "anonymous", None, body.get("model", "unknown_model")

        uid = str(user.get("id") or "anonymous")
        ident = str(user.get("email") or uid or "anonymous")
        role = user.get("role")
        model_id = str(body.get("model") or "unknown_model")
        return uid, ident, role, model_id

    def _normalized_whitelist_items(self, raw_list: str) -> set[str]:
        return {item.strip().lower() for item in raw_list.split(",") if item.strip()}

    def _request_headers(self, request: Optional[dict]) -> Dict[str, str]:
        if not request or not isinstance(request, dict):
            return {}

        raw_headers = request.get("headers", {})
        if isinstance(raw_headers, dict):
            return {
                str(key).lower(): "" if value is None else str(value)
                for key, value in raw_headers.items()
            }

        normalized: Dict[str, str] = {}
        if isinstance(raw_headers, list):
            for entry in raw_headers:
                if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                    continue
                key, value = entry
                key_text = (
                    key.decode("utf-8", errors="ignore")
                    if isinstance(key, bytes)
                    else str(key)
                )
                value_text = (
                    value.decode("utf-8", errors="ignore")
                    if isinstance(value, bytes)
                    else str(value)
                )
                normalized[key_text.lower()] = value_text
        return normalized

    def _request_user_agent(self, request: Optional[dict]) -> str:
        return self._request_headers(request).get("user-agent", "")

    def _is_browser_request(self, request: Optional[dict]) -> bool:
        user_agent = self._request_user_agent(request).lower()
        if not user_agent:
            return False
        browser_tokens = [
            "firefox",
            "chrome",
            "safari",
            "edg",
            "opera",
            "msie",
            "trident",
        ]
        api_tokens = ["curl", "python-requests", "postman", "bot", "httpie"]
        return any(token in user_agent for token in browser_tokens) and not any(
            token in user_agent for token in api_tokens
        )

    def _check_whitelist(
        self,
        ident: str,
        uid: str,
        role: Optional[str],
        whitelist: str,
        request: Optional[dict] = None,
    ) -> bool:
        items = self._normalized_whitelist_items(whitelist)
        if not items:
            return False
        if role == "admin" and "admins" in items:
            return True
        if "browser" in items and self._is_browser_request(request):
            return True
        ident_lower = ident.lower()
        uid_lower = uid.lower()
        return ident_lower in items or uid_lower in items

    def _resolve_request_priority(
        self,
        ident: str,
        uid: str,
        role: Optional[str],
        request: Optional[dict],
    ) -> Tuple[int, str]:
        if self._check_whitelist(
            ident, uid, role, self.valves.priority_whitelist, request
        ):
            return 0, "priority_whitelist"
        if self._is_browser_request(request):
            return 0, "browser_user_agent"
        return 1, "api_default"

    def _raise_http_error(self, status_code: int, detail: Any) -> None:
        raise HTTPException(status_code=int(status_code), detail=detail)

    def _prune_requests_locked(
        self, ident: str, model_key: str, now: float
    ) -> List[float]:
        timestamps = self.user_requests[ident][model_key]
        max_window = self._max_tracked_window_seconds()
        valid = [timestamp for timestamp in timestamps if now - timestamp < max_window]
        self.user_requests[ident][model_key] = valid
        return valid

    def _max_tracked_window_seconds(self) -> int:
        windows = [60]
        if self.valves.requests_per_hour is not None:
            windows.append(3600)
        if (
            self.valves.sliding_window_limit is not None
            and self.valves.sliding_window_minutes is not None
        ):
            windows.append(max(1, self.valves.sliding_window_minutes) * 60)
        return max(windows)

    def _enforce_static_limit(
        self,
        ident: str,
        model_key: str,
        timestamps: List[float],
        now: float,
        request_priority: int,
    ) -> None:
        del model_key
        if self.valves.requests_per_minute is not None:
            per_minute_limit = self.valves.requests_per_minute
            requests_last_minute = [
                timestamp for timestamp in timestamps if now - timestamp < 60
            ]
            if per_minute_limit <= 0 or len(requests_last_minute) >= per_minute_limit:
                wait_seconds = (
                    int(max(1, 60 - (now - requests_last_minute[0])))
                    if requests_last_minute
                    else 60
                )
                self._record_block(ident, request_priority)
                self._raise_http_error(
                    429,
                    f"Limit {per_minute_limit}/min exceeded. Wait {wait_seconds}s.",
                )

        if self.valves.requests_per_hour is not None:
            per_hour_limit = self.valves.requests_per_hour
            requests_last_hour = [
                timestamp for timestamp in timestamps if now - timestamp < 3600
            ]
            if per_hour_limit <= 0 or len(requests_last_hour) >= per_hour_limit:
                wait_seconds = (
                    int(max(1, 3600 - (now - requests_last_hour[0])))
                    if requests_last_hour
                    else 3600
                )
                self._record_block(ident, request_priority)
                self._raise_http_error(
                    429,
                    f"Limit {per_hour_limit}/hour exceeded. Wait {wait_seconds}s.",
                )

        if (
            self.valves.sliding_window_limit is not None
            and self.valves.sliding_window_minutes is not None
        ):
            sliding_window_limit = self.valves.sliding_window_limit
            window_seconds = max(1, self.valves.sliding_window_minutes) * 60
            requests_in_window = [
                timestamp for timestamp in timestamps if now - timestamp < window_seconds
            ]
            if sliding_window_limit <= 0 or len(requests_in_window) >= sliding_window_limit:
                wait_seconds = (
                    int(max(1, window_seconds - (now - requests_in_window[0])))
                    if requests_in_window
                    else window_seconds
                )
                self._record_block(ident, request_priority)
                self._raise_http_error(
                    429,
                    f"Limit {sliding_window_limit}/{self.valves.sliding_window_minutes}m exceeded. Wait {wait_seconds}s.",
                )

    def _enforce_adaptive_limit(
        self,
        ident: str,
        model_key: str,
        timestamps: List[float],
        now: float,
        request_priority: int,
    ) -> None:
        del model_key
        requests_last_minute = [
            timestamp for timestamp in timestamps if now - timestamp < 60
        ]
        use_night_limit, _, reason_str = self._resolve_limit_mode()
        limit_key = "night" if use_night_limit else "day"

        with self.rate_limit_lock:
            limit = self.current_rate_limits[limit_key]

        self._debug(
            f"Adaptive mode selected {limit_key} limit={limit} reason={reason_str} "
            f"requests_last_minute={len(requests_last_minute)}"
        )

        if limit <= 0 or len(requests_last_minute) >= limit:
            wait_seconds = (
                int(max(1, 60 - (now - requests_last_minute[0])))
                if requests_last_minute
                else 60
            )
            self._record_block(ident, request_priority)
            self._raise_http_error(
                429, f"Limit {limit}/min exceeded. Wait {wait_seconds}s."
            )

    def _record_block(self, ident: str, request_priority: int) -> None:
        with self.log_lock:
            self.blocked_requests_log[ident][request_priority] += 1

    def _resolve_limit_mode(self) -> Tuple[bool, str, str]:
        now = self._now()
        day_name = now.strftime("%A")
        full_day_options = {
            option.strip().lower()
            for option in self.valves.apply_night_limit_fullday.split(",")
            if option.strip()
        }

        is_fullday_by_name = day_name.lower() in full_day_options
        is_fullday_by_holiday = False
        holiday_match_name = None

        if not is_fullday_by_name:
            for option in full_day_options:
                if not option.startswith("holiday_"):
                    continue
                if option not in self.holiday_objects:
                    try:
                        parts = option.split("_")
                        country_code = parts[1].upper()
                        subdivision = (
                            parts[2].upper() if len(parts) > 2 and parts[2] else None
                        )
                        country_class = getattr(holidays, country_code)
                        self.holiday_objects[option] = (
                            country_class(subdiv=subdivision)
                            if subdivision
                            else country_class()
                        )
                    except Exception:
                        self.holiday_objects[option] = None

                holiday_checker = self.holiday_objects.get(option)
                if holiday_checker and now in holiday_checker:
                    is_fullday_by_holiday = True
                    holiday_match_name = holiday_checker.get(now)
                    break

        start = self.valves.night_start_hour
        end = self.valves.night_end_hour
        hour = now.hour
        is_night_hours = (
            (hour >= start or hour < end) if start > end else (start <= hour < end)
        )

        use_night_limit = (
            is_fullday_by_name or is_fullday_by_holiday or is_night_hours
        )
        reason_parts = []
        if is_fullday_by_name:
            reason_parts.append(f"FullDayByName({day_name})")
        if is_fullday_by_holiday:
            reason_parts.append(f"Holiday({holiday_match_name})")
        if is_night_hours:
            reason_parts.append(f"NightHours({hour}:00)")
        reason_str = ", ".join(reason_parts) if reason_parts else "Standard Day Hours"
        return use_night_limit, day_name, reason_str

    def _calculate_limit(
        self, load: float, config: Union[str, int], fallback: int
    ) -> int:
        if isinstance(config, int):
            return config
        try:
            limit_map = {int(key): int(value) for key, value in json.loads(config).items()}
        except Exception:
            return fallback

        sorted_thresholds = sorted(limit_map.keys())
        if not sorted_thresholds:
            return fallback

        for threshold in sorted_thresholds:
            if load <= threshold:
                return limit_map[threshold]
        return limit_map[sorted_thresholds[-1]]

    def _fetch_metrics_text(self, url: str) -> str:
        with urllib.request.urlopen(url, timeout=5) as response:
            status_code = getattr(response, "status", 200)
            if status_code != 200:
                raise ConnectionError(f"Status {status_code}")
            return response.read().decode("utf-8")

    async def _update_adaptive_rate_limits_loop(self) -> None:
        while not await self._wait_or_stop(
            self.valves.update_adaptive_rate_limits_interval_seconds
        ):
            metrics_url = self.valves.metrics_url or resolve_metrics_url_from_env()
            if not metrics_url:
                continue

            try:
                metrics_text = await asyncio.to_thread(
                    self._fetch_metrics_text, metrics_url
                )
                running = parse_prometheus_metrics(metrics_text, RUNNING_METRIC_NAMES)
                queued = parse_prometheus_metrics(metrics_text, QUEUED_METRIC_NAMES)
                total_load = running + queued

                new_day_limit = self._calculate_limit(
                    total_load,
                    self.valves.day_rate_limit,
                    self.valves.fallback_day_rate_limit,
                )
                new_night_limit = self._calculate_limit(
                    total_load,
                    self.valves.night_rate_limit,
                    self.valves.fallback_night_rate_limit,
                )

                with self.rate_limit_lock:
                    changed = (
                        self.current_rate_limits["day"] != new_day_limit
                        or self.current_rate_limits["night"] != new_night_limit
                    )
                    self.current_rate_limits["day"] = new_day_limit
                    self.current_rate_limits["night"] = new_night_limit

                if changed:
                    log.info(
                        "Adaptive rate limits updated from metrics load %.0f -> day=%s night=%s",
                        total_load,
                        new_day_limit,
                        new_night_limit,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Metrics poll failed: %s. Falling back to configured limits.", exc)
                with self.rate_limit_lock:
                    self.current_rate_limits["day"] = self.valves.fallback_day_rate_limit
                    self.current_rate_limits["night"] = self.valves.fallback_night_rate_limit

    def _push_metrics(self, log_data: Dict[str, Dict[int, int]], url: str) -> None:
        lines = []
        timestamp = int(self._time() * 1e9)
        for user, priorities in log_data.items():
            for priority, count in priorities.items():
                if count == 0:
                    continue
                source = "frontend" if priority == 0 else "api"
                lines.append(
                    f"llm_rate_limit_blocks,user={user},source={source} blocks_count={count}i {timestamp}"
                )
        if not lines:
            return

        request = urllib.request.Request(
            f"{url.rstrip('/')}/write",
            data="\n".join(lines).encode("utf-8"),
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            status_code = getattr(response, "status", 200)
            if not (200 <= status_code < 300):
                raise ConnectionError(f"VictoriaMetrics push failed with {status_code}")

    async def _report_blocked_requests_loop(self) -> None:
        while not await self._wait_or_stop(self.valves.log_report_interval_seconds):
            with self.log_lock:
                if not self.blocked_requests_log:
                    continue
                snapshot = {
                    user: dict(priorities)
                    for user, priorities in self.blocked_requests_log.items()
                }
                self.blocked_requests_log.clear()

            if self.valves.enable_victoriametrics_block_logging:
                try:
                    await asyncio.to_thread(
                        self._push_metrics,
                        snapshot,
                        self.valves.log_report_victoriametrics_url or "",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("VictoriaMetrics block logging failed: %s", exc)

            total_blocks = 0
            report_parts = []
            for user, priorities in snapshot.items():
                user_total = sum(priorities.values())
                total_blocks += user_total
                report_parts.append(f"{user}({user_total})")

            log.warning(
                "Rate-limit blocks in last %ss (%s): %s",
                self.valves.log_report_interval_seconds,
                total_blocks,
                ", ".join(report_parts),
            )

    async def _cleanup_loop(self) -> None:
        while not await self._wait_or_stop(60):
            inactive_threshold = max(300, self._max_tracked_window_seconds())
            now = self._time()
            with self.request_lock:
                inactive_users = [
                    user
                    for user, models in self.user_requests.items()
                    if all(
                        not timestamps or (now - max(timestamps)) > inactive_threshold
                        for timestamps in models.values()
                    )
                ]
                for user in inactive_users:
                    del self.user_requests[user]
            if inactive_users:
                log.info("Cleaned up %s inactive rate-limit users.", len(inactive_users))

    async def _wait_or_stop(self, timeout_seconds: int) -> bool:
        if self._shutdown_event is None:
            await asyncio.sleep(timeout_seconds)
            return False
        try:
            await asyncio.wait_for(
                self._shutdown_event.wait(), timeout=max(1, timeout_seconds)
            )
            return True
        except asyncio.TimeoutError:
            return False
