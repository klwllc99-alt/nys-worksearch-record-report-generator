from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import Lock
from time import time
from typing import Any

from fastapi import Request


@dataclass(slots=True)
class RequestEvent:
    timestamp: str
    method: str
    path: str
    status_code: int
    duration_ms: float
    ip_address: str
    country: str
    region: str
    user_agent: str


class MetricsStore:
    def __init__(self, max_events: int = 2000) -> None:
        self.max_events = max_events
        self.started_at = time()
        self._events: deque[RequestEvent] = deque(maxlen=max_events)
        self._lock = Lock()

    def _extract_ip(self, request: Request) -> str:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        for header_name in ("cf-connecting-ip", "x-real-ip", "x-client-ip"):
            candidate = request.headers.get(header_name)
            if candidate:
                return candidate.strip()

        return request.client.host if request.client else "unknown"

    def _extract_location(self, request: Request) -> tuple[str, str]:
        country = (
            request.headers.get("cf-ipcountry")
            or request.headers.get("x-appengine-country")
            or request.headers.get("x-vercel-ip-country")
            or "Unknown"
        )
        region = (
            request.headers.get("x-appengine-region")
            or request.headers.get("x-vercel-ip-country-region")
            or request.headers.get("cf-region")
            or "Unknown"
        )
        return country, region

    def record(self, request: Request, status_code: int, duration_ms: float) -> None:
        country, region = self._extract_location(request)
        event = RequestEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            method=request.method,
            path=request.url.path,
            status_code=int(status_code),
            duration_ms=round(duration_ms, 2),
            ip_address=self._extract_ip(request),
            country=country,
            region=region,
            user_agent=(request.headers.get("user-agent") or "Unknown")[:200],
        )
        with self._lock:
            self._events.appendleft(event)

    def recent_requests(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)[:limit]
        return [asdict(event) for event in events]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)

        durations = [event.duration_ms for event in events]
        total_requests = len(events)
        unique_ips = len({event.ip_address for event in events})
        avg_duration_ms = round(sum(durations) / total_requests, 2) if durations else 0.0
        p95_duration_ms = self._percentile(durations, 0.95)
        max_duration_ms = round(max(durations), 2) if durations else 0.0
        uptime_seconds = int(time() - self.started_at)
        requests_per_minute = round((total_requests / max(uptime_seconds, 1)) * 60, 2)

        method_counts = Counter(event.method for event in events)
        status_counts = Counter(str(event.status_code) for event in events)
        ip_counts = Counter(event.ip_address for event in events)
        geo_counts = Counter(f"{event.country} / {event.region}" for event in events)

        path_samples: dict[str, list[float]] = defaultdict(list)
        for event in events:
            path_samples[event.path].append(event.duration_ms)

        latest_by_ip = {}
        for event in events:
            latest_by_ip.setdefault(event.ip_address, event)

        top_paths = []
        for path, samples in sorted(path_samples.items(), key=lambda item: (-len(item[1]), item[0])):
            top_paths.append(
                {
                    "path": path,
                    "count": len(samples),
                    "avg_duration_ms": round(sum(samples) / len(samples), 2),
                    "p95_duration_ms": self._percentile(samples, 0.95),
                    "max_duration_ms": round(max(samples), 2),
                }
            )

        top_ips = []
        for ip_address, count in ip_counts.most_common(20):
            latest = latest_by_ip[ip_address]
            top_ips.append(
                {
                    "ip_address": ip_address,
                    "count": count,
                    "country": latest.country,
                    "region": latest.region,
                    "last_path": latest.path,
                }
            )

        geo_breakdown = []
        for location, count in geo_counts.most_common(20):
            country, region = location.split(" / ", 1)
            geo_breakdown.append({"country": country, "region": region, "count": count})

        return {
            "uptime_seconds": uptime_seconds,
            "total_requests": total_requests,
            "unique_ips": unique_ips,
            "requests_per_minute": requests_per_minute,
            "avg_duration_ms": avg_duration_ms,
            "p95_duration_ms": p95_duration_ms,
            "max_duration_ms": max_duration_ms,
            "by_method": [{"label": key, "count": value} for key, value in sorted(method_counts.items())],
            "by_status": [{"label": key, "count": value} for key, value in sorted(status_counts.items())],
            "top_paths": top_paths,
            "top_ips": top_ips,
            "geo_breakdown": geo_breakdown,
            "recent_requests": self.recent_requests(limit=50),
        }

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = round((len(ordered) - 1) * percentile)
        return round(ordered[index], 2)
