from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class AdminAuthStore:
    def __init__(self, storage_path: Path, default_email: str, default_password: str) -> None:
        self.storage_path = storage_path
        self.default_email = default_email.strip().lower()
        self.default_password = default_password
        self._lock = Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_seeded()

    def _ensure_seeded(self) -> None:
        if self.storage_path.exists():
            try:
                payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
                if payload.get("users"):
                    return
            except (json.JSONDecodeError, OSError, AttributeError):
                pass

        default_user = {
            "email": self.default_email,
            "password_hash": self._hash_password(self.default_password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_users([default_user])

    def _load_users(self) -> list[dict[str, Any]]:
        if not self.storage_path.exists():
            return []
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        return list(payload.get("users", []))

    def _save_users(self, users: list[dict[str, Any]]) -> None:
        payload = {"users": users}
        self.storage_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _hash_password(password: str, salt: str | None = None) -> str:
        resolved_salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            resolved_salt.encode("utf-8"),
            120000,
        ).hex()
        return f"{resolved_salt}${digest}"

    @staticmethod
    def _verify_password(password: str, stored_hash: str) -> bool:
        if "$" not in stored_hash:
            return False
        salt, existing_digest = stored_hash.split("$", 1)
        comparison = AdminAuthStore._hash_password(password, salt=salt).split("$", 1)[1]
        return hmac.compare_digest(existing_digest, comparison)

    def authenticate(self, email: str, password: str) -> str | None:
        normalized_email = email.strip().lower()
        with self._lock:
            for user in self._load_users():
                if user.get("email", "").lower() == normalized_email and self._verify_password(password, user.get("password_hash", "")):
                    token = secrets.token_urlsafe(32)
                    self._sessions[token] = {
                        "email": normalized_email,
                        "expires_at": datetime.now(timezone.utc) + timedelta(hours=12),
                    }
                    return token
        return None

    def verify_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if session["expires_at"] < datetime.now(timezone.utc):
                self._sessions.pop(token, None)
                return None
            return {"email": session["email"]}

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            users = self._load_users()
        return [
            {
                "email": user.get("email", ""),
                "created_at": user.get("created_at", ""),
            }
            for user in users
        ]

    def create_user(self, email: str, password: str) -> dict[str, Any]:
        normalized_email = email.strip().lower()
        if not normalized_email or "@" not in normalized_email:
            raise ValueError("A valid email is required.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters long.")

        with self._lock:
            users = self._load_users()
            if any(user.get("email", "").lower() == normalized_email for user in users):
                raise ValueError("That admin user already exists.")

            new_user = {
                "email": normalized_email,
                "password_hash": self._hash_password(password),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            users.append(new_user)
            self._save_users(users)
        return {"email": normalized_email, "created_at": new_user["created_at"]}

    def change_password(self, email: str, new_password: str) -> None:
        normalized_email = email.strip().lower()
        if len(new_password) < 6:
            raise ValueError("Password must be at least 6 characters long.")

        with self._lock:
            users = self._load_users()
            updated = False
            for user in users:
                if user.get("email", "").lower() == normalized_email:
                    user["password_hash"] = self._hash_password(new_password)
                    updated = True
                    break

            if not updated:
                raise ValueError("Admin user not found.")

            self._save_users(users)
