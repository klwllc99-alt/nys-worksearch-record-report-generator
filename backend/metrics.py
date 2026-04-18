from __future__ import annotations

import json
import os
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from time import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import Request
else:
    Request = Any

try:
    from google.cloud import firestore as gcp_firestore
except Exception:
    gcp_firestore = None


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
    storage_mode = "rolling-window"

    def __init__(
        self,
        max_events: int = 2000,
        retention_days: int = 30,
        persist_path: str | Path | None = None,
    ) -> None:
        self.max_events = max(10, int(max_events))
        self.retention_days = max(1, int(retention_days))
        self.persist_path = Path(persist_path) if persist_path else None
        self.started_at = time()
        self._events: deque[RequestEvent] = deque(maxlen=self.max_events)
        self._daily_rollups: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        self._load_persisted()

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

    def _normalize_bucket(self, bucket: dict[str, Any]) -> dict[str, Any]:
        return {
            "count": int(bucket.get("count", 0)),
            "total_duration_ms": round(float(bucket.get("total_duration_ms", 0.0)), 2),
            "avg_duration_ms": round(float(bucket.get("avg_duration_ms", 0.0)), 2),
            "max_duration_ms": round(float(bucket.get("max_duration_ms", 0.0)), 2),
            "by_method": {str(k): int(v) for k, v in dict(bucket.get("by_method", {})).items()},
            "by_status": {str(k): int(v) for k, v in dict(bucket.get("by_status", {})).items()},
            "geo_breakdown": {str(k): int(v) for k, v in dict(bucket.get("geo_breakdown", {})).items()},
        }

    def _load_persisted(self) -> None:
        if not self.persist_path or not self.persist_path.exists():
            return

        try:
            payload = json.loads(self.persist_path.read_text(encoding="utf-8"))
            self.started_at = float(payload.get("started_at", self.started_at))
            saved_rollups = payload.get("daily_rollups", {})
            if not isinstance(saved_rollups, dict):
                return

            self._daily_rollups = {
                str(day_key): self._normalize_bucket(bucket)
                for day_key, bucket in saved_rollups.items()
                if isinstance(bucket, dict)
            }
            self._prune_old_rollups_locked()
        except Exception:
            self._daily_rollups = {}

    def _persist_locked(self) -> None:
        if not self.persist_path:
            return

        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "started_at": self.started_at,
            "retention_days": self.retention_days,
            "daily_rollups": self._daily_rollups,
        }
        temp_path = self.persist_path.with_suffix(f"{self.persist_path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.persist_path)

    def _prune_old_rollups_locked(self) -> None:
        cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=self.retention_days - 1)
        for day_key in list(self._daily_rollups.keys()):
            try:
                bucket_date = datetime.fromisoformat(day_key).date()
            except ValueError:
                self._daily_rollups.pop(day_key, None)
                continue
            if bucket_date < cutoff_date:
                self._daily_rollups.pop(day_key, None)

    def _prune_old_events_locked(self) -> None:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        retained_events = [
            event
            for event in self._events
            if datetime.fromisoformat(event.timestamp) >= cutoff_dt
        ]
        self._events = deque(retained_events, maxlen=self.max_events)

    def _update_rollup_locked(self, event: RequestEvent) -> None:
        day_key = event.timestamp[:10]
        bucket = self._daily_rollups.setdefault(
            day_key,
            {
                "count": 0,
                "total_duration_ms": 0.0,
                "avg_duration_ms": 0.0,
                "max_duration_ms": 0.0,
                "by_method": {},
                "by_status": {},
                "geo_breakdown": {},
            },
        )

        bucket["count"] += 1
        bucket["total_duration_ms"] = round(bucket["total_duration_ms"] + event.duration_ms, 2)
        bucket["avg_duration_ms"] = round(bucket["total_duration_ms"] / bucket["count"], 2)
        bucket["max_duration_ms"] = round(max(bucket["max_duration_ms"], event.duration_ms), 2)
        bucket["by_method"][event.method] = int(bucket["by_method"].get(event.method, 0)) + 1

        status_key = str(event.status_code)
        bucket["by_status"][status_key] = int(bucket["by_status"].get(status_key, 0)) + 1

        geo_key = f"{event.country} / {event.region}"
        bucket["geo_breakdown"][geo_key] = int(bucket["geo_breakdown"].get(geo_key, 0)) + 1

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
            self._update_rollup_locked(event)
            self._prune_old_events_locked()
            self._prune_old_rollups_locked()
            self._persist_locked()

    def recent_requests(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)[:limit]
        return [asdict(event) for event in events]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
            daily_rollups = {key: self._normalize_bucket(value) for key, value in self._daily_rollups.items()}

        durations = [event.duration_ms for event in events]
        retained_request_count = sum(int(bucket.get("count", 0)) for bucket in daily_rollups.values())
        total_requests = retained_request_count or len(events)
        unique_ips = len({event.ip_address for event in events})
        avg_duration_ms = round(sum(durations) / len(durations), 2) if durations else 0.0
        p95_duration_ms = self._percentile(durations, 0.95)
        max_duration_ms = round(max(durations), 2) if durations else 0.0
        uptime_seconds = int(time() - self.started_at)
        requests_per_minute = round((total_requests / max(uptime_seconds, 1)) * 60, 2)

        method_counts = Counter()
        status_counts = Counter()
        geo_counts = Counter()
        for bucket in daily_rollups.values():
            method_counts.update(bucket.get("by_method", {}))
            status_counts.update(bucket.get("by_status", {}))
            geo_counts.update(bucket.get("geo_breakdown", {}))

        ip_counts = Counter(event.ip_address for event in events)
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

        daily_totals = []
        for day_key in sorted(daily_rollups.keys(), reverse=True):
            bucket = daily_rollups[day_key]
            daily_totals.append(
                {
                    "date": day_key,
                    "count": int(bucket.get("count", 0)),
                    "avg_duration_ms": round(float(bucket.get("avg_duration_ms", 0.0)), 2),
                    "max_duration_ms": round(float(bucket.get("max_duration_ms", 0.0)), 2),
                }
            )

        return {
            "storage_mode": self.storage_mode,
            "retention_days": self.retention_days,
            "retained_days": len(daily_totals),
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
            "daily_totals": daily_totals[:14],
            "recent_requests": self.recent_requests(limit=50),
        }

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = round((len(ordered) - 1) * percentile)
        return round(ordered[index], 2)


class FirestoreMetricsStore(MetricsStore):
    storage_mode = "firestore"

    def __init__(
        self,
        max_events: int = 2000,
        retention_days: int = 30,
        firestore_collection: str = "nys_ws5_metrics_daily",
        firestore_project: str | None = None,
        firestore_client: Any | None = None,
    ) -> None:
        self.firestore_collection = firestore_collection
        self.firestore_project = firestore_project
        if firestore_client is not None:
            self._firestore_client = firestore_client
        elif gcp_firestore is not None:
            self._firestore_client = gcp_firestore.Client(project=firestore_project) if firestore_project else gcp_firestore.Client()
        else:
            raise RuntimeError("google-cloud-firestore is not installed.")

        self._collection = self._firestore_client.collection(self.firestore_collection)
        super().__init__(max_events=max_events, retention_days=retention_days, persist_path=None)

    def _load_persisted(self) -> None:
        try:
            loaded: dict[str, dict[str, Any]] = {}
            for doc in self._collection.stream():
                loaded[str(doc.id)] = self._normalize_bucket(doc.to_dict() or {})
            self._daily_rollups = loaded
            self._prune_old_rollups_locked()
        except Exception:
            self._daily_rollups = {}

    def _persist_locked(self) -> None:
        return

    def _persist_day_locked(self, day_key: str) -> None:
        bucket = self._normalize_bucket(self._daily_rollups.get(day_key, {}))
        payload = dict(bucket)
        payload["date"] = day_key
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._collection.document(day_key).set(payload, merge=True)

    def _prune_old_rollups_locked(self) -> None:
        known_keys = set(self._daily_rollups.keys())
        super()._prune_old_rollups_locked()
        removed_keys = known_keys - set(self._daily_rollups.keys())
        for day_key in removed_keys:
            try:
                self._collection.document(day_key).delete()
            except Exception:
                pass

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
            self._update_rollup_locked(event)
            self._prune_old_events_locked()
            self._prune_old_rollups_locked()
            self._persist_day_locked(event.timestamp[:10])


def build_metrics_store(
    max_events: int = 2000,
    retention_days: int = 30,
    persist_path: str | Path | None = None,
    storage_backend: str | None = None,
    firestore_project: str | None = None,
    firestore_collection: str | None = None,
    firestore_client: Any | None = None,
) -> MetricsStore:
    backend = (storage_backend or os.getenv("METRICS_STORAGE_BACKEND", "local")).strip().lower()

    if backend == "firestore":
        try:
            return FirestoreMetricsStore(
                max_events=max_events,
                retention_days=retention_days,
                firestore_collection=(
                    firestore_collection
                    or os.getenv("FIRESTORE_METRICS_COLLECTION", "nys_ws5_metrics_daily")
                ).strip(),
                firestore_project=(
                    firestore_project
                    or os.getenv("GOOGLE_CLOUD_PROJECT")
                    or os.getenv("GCP_PROJECT")
                    or None
                ),
                firestore_client=firestore_client,
            )
        except Exception:
            pass

    return MetricsStore(max_events=max_events, retention_days=retention_days, persist_path=persist_path)
