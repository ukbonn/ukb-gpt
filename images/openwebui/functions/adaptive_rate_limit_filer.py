"""
title: Adaptive Rate Limit & Priority Filter (Dynamic Holiday Support)
author: sebnowak (extended by Gemini)
version: 4.7.0
license: MIT
description: A production-hardened, thread-safe filter with dynamic holiday support and optional priority injection / optional blocked-request VictoriaMetrics push.
"""

import os
import json
import time
import logging
import threading
import builtins
import urllib.request
from typing import Optional, List, Tuple, Union, Dict, Any
from pydantic import BaseModel, Field
from collections import defaultdict
from datetime import datetime

# --- Import the holidays library ---
import holidays

from fastapi import status
from fastapi.exceptions import HTTPException

log = logging.getLogger(__name__)


# --- Global Shared State ---
class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.user_requests = defaultdict(lambda: defaultdict(list))
        self.log_lock = threading.Lock()
        self.blocked_requests_log = defaultdict(lambda: defaultdict(int))
        self.rate_limit_lock = threading.Lock()
        self.current_rate_limits = {"day": 8, "night": 32}
        self.config = {}
        self.thread_lock = threading.Lock()
        self.threads: Dict[str, threading.Thread] = {}
        self.thread_owners: Dict[str, int] = {}


_SHARED_STATE_KEY = "_owui_adaptive_rate_limit_shared_state_v1"
if not hasattr(builtins, _SHARED_STATE_KEY):
    setattr(builtins, _SHARED_STATE_KEY, SharedState())
_shared_state = getattr(builtins, _SHARED_STATE_KEY)


# --- Helper Function (Unchanged) ---
def parse_prometheus_metrics(text: str, metric_names: List[str]) -> float:
    total = 0.0
    try:
        for line in text.split("\n"):
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2 and any(
                metric_name in parts[0] for metric_name in metric_names
            ):
                total += float(parts[1])
    except (ValueError, IndexError) as e:
        log.warning(f"Could not parse metric line: '{line}'. Error: {e}")
    return total


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0, description="Execution priority. 0 runs first."
        )

        day_rate_limit: Union[str, int] = Field(
            default='{"10": 60, "15": 20, "20": 5, "25": 2, "30": 1, "35": 0}',
            description="JSON string for adaptive rate limits OR a fixed integer limit.",
        )
        night_rate_limit: Union[str, int] = Field(
            default='{"32": 64, "40": 56, "48": 48, "56": 40, "64": 32, "80": 24, "96": 16, "112": 12, "128": 8, "134": 4, "148": 0}',
            description="JSON string for adaptive rate limits OR a fixed integer limit.",
        )

        night_start_hour: int = Field(
            default=22, description="The hour when the night rate limit starts (0-23)."
        )
        night_end_hour: int = Field(
            default=6, description="The hour when the night rate limit ends (0-23)."
        )

        apply_night_limit_fullday: str = Field(
            default="Saturday, Sunday, holiday_DE_NRW",
            description="Comma-separated list of days/rules to apply the night limit for the full day. "
            "Valid weekdays: Monday-Sunday. "
            "Valid holiday format: holiday_{COUNTRY_CODE}_{SUBDIVISION} (e.g., holiday_DE_BW, holiday_US_CA, holiday_GB). Subdivision is optional.",
        )

        fallback_day_rate_limit: int = Field(
            default=8, description="Fallback day rate limit."
        )
        fallback_night_rate_limit: int = Field(
            default=32, description="Fallback night rate limit."
        )
        update_adaptive_rate_limits_interval_seconds: int = Field(
            default=1, description="Interval for polling backend metrics.", ge=1
        )

        global_limit: bool = Field(
            default=True,
            description="Apply rate limits globally across all models for a user. Recommended to keep True.",
        )
        enabled_for_admins: bool = Field(
            default=True, description="If true, rate limiting is applied to admins."
        )
        priority_whitelist: str = Field(
            default="browser", description="High priority (0) whitelist."
        )
        rate_limit_whitelist: str = Field(
            default="browser", description="Rate limit exemption whitelist."
        )

        log_report_interval_seconds: int = Field(
            default=60, description="Interval for reporting blocked requests.", ge=10
        )
        log_report_victoriametrics_url: Optional[str] = Field(
            default="http://proxy:8081",
            description="VictoriaMetrics URL (e.g. http://proxy:8081).",
        )
        enable_victoriametrics_block_logging: bool = Field(
            default=False,
            description="If True, blocked-request counters are pushed to VictoriaMetrics.",
        )
        inject_priority: bool = Field(
            default=False,
            description="If True, injects a 'priority' field into the request body.",
        )

        allow_anonymous_requests: bool = Field(
            default=False,
            description="If False, all requests from unidentified (anonymous) users will be blocked with a 401 error.",
        )
        enable_debug_logging: bool = Field(
            default=False,
            description="If True, the filter will output verbose INFO logs for every step of its decision process.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.state = _shared_state
        self.metrics_url = self._get_metrics_url_from_env()
        self.instance_id = time.time_ns()
        # Cache for holiday objects to avoid re-instantiation
        self.holiday_objects = {}
        self._update_shared_config()
        log.info("Adaptive Rate Limit Filter initialized (Singleton Mode).")
        self._ensure_thread_running("OWUI_Filter_Cleanup", self._cleanup_inactive_users)
        self._ensure_thread_running(
            "OWUI_Filter_Reporter", self._report_blocked_requests
        )
        self._ensure_thread_running(
            "OWUI_Filter_Metrics", self._update_adaptive_rate_limits
        )

    def _update_shared_config(self):
        with self.state.lock:
            self.state.config = {"valves": self.valves, "metrics_url": self.metrics_url}

    def _ensure_thread_running(self, name: str, target):
        with self.state.thread_lock:
            existing = self.state.threads.get(name)
            existing_owner = self.state.thread_owners.get(name)
            if (
                existing is not None
                and existing.is_alive()
                and existing_owner == self.instance_id
            ):
                return
            if existing is not None and existing.is_alive():
                log.info(
                    f"Replacing stale singleton thread: {name} (owner={existing_owner} -> {self.instance_id})"
                )
            log.info(f"Starting singleton background thread: {name}")
            t = threading.Thread(
                target=target,
                args=(name, self.instance_id),
                name=f"{name}:{self.instance_id}",
                daemon=True,
            )
            self.state.threads[name] = t
            self.state.thread_owners[name] = self.instance_id
            t.start()

    def _thread_is_current(self, name: str, owner_id: int) -> bool:
        with self.state.thread_lock:
            thread = self.state.threads.get(name)
            return (
                thread is not None
                and thread.is_alive()
                and self.state.thread_owners.get(name) == owner_id
            )

    def _get_metrics_url_from_env(self) -> Optional[str]:
        base_urls = os.getenv("OPENAI_API_BASE_URLS")
        if not base_urls:
            return None
        first_url = base_urls.split(";")[0].strip()
        base_url = (
            first_url[:-3]
            if first_url.endswith("/v1")
            else (first_url[:-1] if first_url.endswith("/") else first_url)
        )
        return f"{base_url}/metrics"

    def _resolve_limit_mode(self) -> Tuple[bool, str, str]:
        """
        Determines if we are in 'night' mode (which includes weekends/holidays).
        Returns: (is_night_mode: bool, current_day_name: str, detailed_reason: str)
        """
        now = datetime.now()
        day_name = now.strftime("%A")  # e.g., "Saturday"
        full_day_options = {
            opt.strip().lower()
            for opt in self.valves.apply_night_limit_fullday.split(",")
            if opt.strip()
        }

        # 1. Check for day-of-the-week match (Name)
        is_fullday_by_name = day_name.lower() in full_day_options

        # 2. Check for holiday match
        is_fullday_by_holiday = False
        holiday_match_name = None

        # Only check holidays if not already matched by name (optimization)
        if not is_fullday_by_name:
            for option in full_day_options:
                if option.startswith("holiday_"):
                    if option not in self.holiday_objects:
                        try:
                            parts = option.split("_")
                            if len(parts) < 2:
                                continue
                            country_code = parts[1].upper()
                            subdivision = (
                                parts[2].upper()
                                if len(parts) > 2 and parts[2]
                                else None
                            )
                            country_class = getattr(holidays, country_code)
                            self.holiday_objects[option] = (
                                country_class(subdiv=subdivision)
                                if subdivision
                                else country_class()
                            )
                        except Exception:
                            # Cache failure as None to ignore in future
                            self.holiday_objects[option] = None

                    holiday_checker = self.holiday_objects.get(option)
                    if holiday_checker and (now in holiday_checker):
                        is_fullday_by_holiday = True
                        holiday_match_name = holiday_checker.get(now)  # Name of holiday
                        break

        # 3. Check for night-hours match
        start, end, hour = (
            self.valves.night_start_hour,
            self.valves.night_end_hour,
            now.hour,
        )
        is_night_hours = (
            (hour >= start or hour < end) if start > end else (start <= hour < end)
        )

        use_night_limit = is_fullday_by_name or is_fullday_by_holiday or is_night_hours

        reason_parts = []
        if is_fullday_by_name:
            reason_parts.append(f"FullDayByName({day_name})")
        if is_fullday_by_holiday:
            reason_parts.append(f"Holiday({holiday_match_name})")
        if is_night_hours:
            reason_parts.append(f"NightHours({hour}:00)")

        reason_str = ", ".join(reason_parts) if reason_parts else "Standard Day Hours"

        return use_night_limit, day_name, reason_str

    def _update_adaptive_rate_limits(self, thread_name: str, owner_id: int):
        while self._thread_is_current(thread_name, owner_id):
            if not self.state.config:
                time.sleep(5)
                continue
            valves, url = self.state.config["valves"], self.state.config["metrics_url"]
            time.sleep(valves.update_adaptive_rate_limits_interval_seconds)
            if not url:
                continue
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.status != 200:
                        raise ConnectionError(f"Status {response.status}")
                    metrics_text = response.read().decode("utf-8")

                running = parse_prometheus_metrics(
                    metrics_text,
                    ["sglang:num_running_reqs", "vllm:num_requests_running"],
                )
                queued = parse_prometheus_metrics(
                    metrics_text, ["sglang:num_queue_reqs", "vllm:num_requests_waiting"]
                )
                total_load = running + queued

                # Determine current context for cleaner logging
                is_night_mode, day_name, _ = self._resolve_limit_mode()

                if valves.enable_debug_logging:
                    mode_str = "NIGHT/HOLIDAY" if is_night_mode else "DAY"
                    log.info(
                        f"DEBUG: Metrics Poll -> Day: {day_name} | Active Mode: {mode_str} | Load: {total_load} (R={running}, Q={queued})"
                    )

                # Only log verbose calculations for the Active mode
                new_day = self._calculate_limit(
                    total_load,
                    valves.day_rate_limit,
                    valves.fallback_day_rate_limit,
                    verbose=(not is_night_mode and valves.enable_debug_logging),
                )
                new_night = self._calculate_limit(
                    total_load,
                    valves.night_rate_limit,
                    valves.fallback_night_rate_limit,
                    verbose=(is_night_mode and valves.enable_debug_logging),
                )

                with self.state.rate_limit_lock:
                    current = self.state.current_rate_limits
                    if current["day"] != new_day or current["night"] != new_night:
                        log.info(
                            f"Backend load: {total_load:.0f}. Limits updated -> Day: {new_day}, Night: {new_night}"
                        )
                        current["day"], current["night"] = new_day, new_night
            except Exception as e:
                log.warning(f"Metrics poll failed: {e}. Using fallback.")
                with self.state.rate_limit_lock:
                    (
                        self.state.current_rate_limits["day"],
                        self.state.current_rate_limits["night"],
                    ) = (
                        valves.fallback_day_rate_limit,
                        valves.fallback_night_rate_limit,
                    )
        log.info(f"Stopping stale singleton background thread: {thread_name}")

    def _calculate_limit(
        self, load: float, config: Union[str, int], fallback: int, verbose: bool = False
    ) -> int:
        if verbose:
            log.info(
                f"DEBUG: Calculating limit for load '{load}' using config: {config}"
            )
        if isinstance(config, int):
            return config
        try:
            limit_map = {int(k): v for k, v in json.loads(config).items()}
            sorted_thresholds = sorted(limit_map.keys())
            if not sorted_thresholds:
                return fallback
            for threshold in sorted_thresholds:
                if load <= threshold:
                    if verbose:
                        log.info(
                            f"DEBUG:   -> Match found: load {load} <= threshold {threshold}. Limit: {limit_map[threshold]}"
                        )
                    return limit_map[threshold]
            if verbose:
                log.info(
                    f"DEBUG:   -> No match, using most restrictive limit for highest threshold. {limit_map[sorted_thresholds[-1]]}"
                )
            return limit_map[sorted_thresholds[-1]]
        except Exception:
            return fallback

    def _push_metrics(self, log_data, url):
        if not url:
            return
        lines, ts = [], int(time.time() * 1e9)
        for user, priorities in log_data.items():
            for prio, count in priorities.items():
                if count == 0:
                    continue
                source = "frontend" if prio == 0 else "api"
                lines.append(
                    f"llm_rate_limit_blocks,user={user},source={source} blocks_count={count}i {ts}"
                )
        if not lines:
            return
        try:
            req = urllib.request.Request(
                f"{url.rstrip('/')}/write",
                data="\n".join(lines).encode(),
                headers={"Content-Type": "text/plain"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if not (200 <= resp.status < 300):
                    log.warning(f"VM push failed: {resp.status}")
        except Exception as e:
            log.error(f"VM push error: {e}")

    def _report_blocked_requests(self, thread_name: str, owner_id: int):
        while self._thread_is_current(thread_name, owner_id):
            if not self.state.config:
                time.sleep(5)
                continue
            valves = self.state.config["valves"]
            time.sleep(valves.log_report_interval_seconds)
            with self.state.log_lock:
                if not self.state.blocked_requests_log:
                    continue
                if valves.enable_victoriametrics_block_logging:
                    self._push_metrics(
                        self.state.blocked_requests_log,
                        valves.log_report_victoriametrics_url,
                    )
                total, report_parts = 0, []
                for user, prios in self.state.blocked_requests_log.items():
                    u_total = sum(prios.values())
                    total += u_total
                    report_parts.append(f"{user}({u_total})")
                log.warning(
                    f"Blocks in last {valves.log_report_interval_seconds}s ({total}): {', '.join(report_parts)}"
                )
                self.state.blocked_requests_log.clear()
        log.info(f"Stopping stale singleton background thread: {thread_name}")

    def _cleanup_inactive_users(self, thread_name: str, owner_id: int):
        while self._thread_is_current(thread_name, owner_id):
            time.sleep(60)
            now = time.time()
            with self.state.lock:
                if not self.state.user_requests:
                    continue
                inactive = [
                    u
                    for u, models in self.state.user_requests.items()
                    if all(not ts or (now - max(ts)) > 300 for ts in models.values())
                ]
                if inactive:
                    for u in inactive:
                        del self.state.user_requests[u]
                    log.info(f"Cleaned up {len(inactive)} inactive users.")
        log.info(f"Stopping stale singleton background thread: {thread_name}")

    def _is_browser(self, headers: List[Tuple[bytes, bytes]]) -> bool:
        try:
            ua = (
                {k.decode().lower(): v.decode() for k, v in headers}
                .get("user-agent", "")
                .lower()
            )
            return any(
                b in ua
                for b in [
                    "firefox",
                    "chrome",
                    "safari",
                    "edg",
                    "opera",
                    "msie",
                    "trident",
                ]
            ) and not any(
                t in ua for t in ["curl", "python-requests", "postman", "bot", "httpie"]
            )
        except:
            return False

    def _check_whitelist(self, ident, role, headers, w_list, list_name) -> bool:
        items = {i.strip().lower() for i in w_list.split(",") if i.strip()}
        is_browser = self._is_browser(headers)
        if self.valves.enable_debug_logging:
            log.info(
                f"DEBUG: Evaluating '{list_name}' for '{ident}' (role={role}, is_browser={is_browser}) with items={sorted(items)}"
            )
        if "admins" in items and role == "admin":
            if self.valves.enable_debug_logging:
                log.info(
                    f"DEBUG: '{ident}' whitelisted by '{list_name}' due to admin role."
                )
            return True
        if ident.lower() in items:
            if self.valves.enable_debug_logging:
                log.info(
                    f"DEBUG: '{ident}' whitelisted by '{list_name}' by direct match."
                )
            return True
        if "browser" in items and is_browser:
            if self.valves.enable_debug_logging:
                log.info(
                    f"DEBUG: '{ident}' whitelisted by '{list_name}' due to browser user-agent."
                )
            return True
        if self.valves.enable_debug_logging:
            log.info(f"DEBUG: '{ident}' is NOT whitelisted by '{list_name}'.")
        return False

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __model__: Optional[dict] = None,
        __request__: Optional[dict] = None,
    ) -> dict:
        self._update_shared_config()
        uid = __user__["id"] if __user__ else "anonymous"
        ident = __user__.get("email", uid) if __user__ else "anonymous"
        role = __user__["role"] if __user__ else None
        headers = __request__["headers"] if __request__ else []
        model_id = (
            __model__.get("id", "unknown_model") if __model__ else "unknown_model"
        )

        if self.valves.enable_debug_logging:
            log.info(
                f"DEBUG: ---------- Inlet Triggered for user: '{ident}' ----------"
            )
            log.info(
                "DEBUG: Inlet context -> "
                f"instance_id={self.instance_id}, role={role}, model_id={model_id}, "
                f"global_limit={self.valves.global_limit}, enabled_for_admins={self.valves.enabled_for_admins}, "
                f"priority_whitelist='{self.valves.priority_whitelist}', rate_limit_whitelist='{self.valves.rate_limit_whitelist}'"
            )

        if ident == "anonymous" and not self.valves.allow_anonymous_requests:
            log.warning(f"Blocking anonymous request.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Anonymous requests are not permitted.",
            )

        is_high_prio = self._check_whitelist(
            ident, role, headers, self.valves.priority_whitelist, "priority_whitelist"
        )
        request_priority = 0 if is_high_prio else 1
        if self.valves.enable_debug_logging:
            log.info(
                f"DEBUG: Priority decision for '{ident}' -> request_priority={request_priority}"
            )
        if self.valves.inject_priority:
            body["priority"] = request_priority

        rate_limit_whitelisted = self._check_whitelist(
            ident,
            role,
            headers,
            self.valves.rate_limit_whitelist,
            "rate_limit_whitelist",
        )
        if rate_limit_whitelisted:
            if self.valves.enable_debug_logging:
                log.info(
                    f"DEBUG: Bypassing rate limit for '{ident}' due to rate_limit_whitelist."
                )
            return body

        if role == "admin" and not self.valves.enabled_for_admins:
            if self.valves.enable_debug_logging:
                log.info(
                    f"DEBUG: Bypassing rate limit for '{ident}' because admins are exempt."
                )
            return body

        model_key = "__global__" if self.valves.global_limit else (model_id)
        current_ts = time.time()

        with self.state.lock:
            timestamps = self.state.user_requests[ident][model_key]
            valid_timestamps = [t for t in timestamps if current_ts - t < 60]
            self.state.user_requests[ident][model_key] = valid_timestamps

            # --- DYNAMIC TIME CHECKING LOGIC (Consolidated) ---
            use_night_limit, day_name, reason_str = self._resolve_limit_mode()
            limit_key = "night" if use_night_limit else "day"

            if self.valves.enable_debug_logging:
                log.info(
                    f"DEBUG: Time Check -> Current Day: '{day_name}'. Selected Mode: '{limit_key.upper()}'. Reason: [{reason_str}]"
                )
            # --- END DYNAMIC LOGIC ---

            with self.state.rate_limit_lock:
                day_limit = self.state.current_rate_limits["day"]
                night_limit = self.state.current_rate_limits["night"]
                limit = self.state.current_rate_limits[limit_key]

            if self.valves.enable_debug_logging:
                log.info(
                    f"DEBUG: Limits snapshot -> day={day_limit}, night={night_limit}, selected={limit_key}:{limit}"
                )
                log.info(
                    f"DEBUG: Final Check for '{ident}': Requests in last min={len(valid_timestamps)}, Limit={limit}"
                )

            if len(valid_timestamps) >= limit:
                if self.valves.enable_debug_logging:
                    log.info(f"DEBUG: BLOCKING request for '{ident}'.")
                with self.state.log_lock:
                    self.state.blocked_requests_log[ident][request_priority] += 1
                wait = (
                    int(60 - (current_ts - valid_timestamps[0]))
                    if valid_timestamps
                    else 60
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Limit {limit}/min exceeded. Wait {wait}s.",
                )

            if self.valves.enable_debug_logging:
                log.info(f"DEBUG: ALLOWING request for '{ident}'.")
            self.state.user_requests[ident][model_key].append(current_ts)

        return body