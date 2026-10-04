"""JSON API for Namma Uru Cab. Run with: python -m api.app"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
from wsgiref.simple_server import make_server


API_PREFIX = "/api/v1"
MAX_BODY_BYTES = 64 * 1024
PASSWORD_ITERATIONS = 310_000
ACTIVE_BOOKING_STATUSES = ("requested", "awaiting_customer_confirmation", "confirmed", "assigned")
VEHICLE_CLASSES = {"hatchback", "sedan", "suv", "crysta"}
TRIP_TYPES = {"oneway", "round", "hourly", "airport"}
STATUSES = {"pending", "approved", "rejected", "suspended"}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+$")
PHONE_RE = re.compile(r"^[+0-9() -]{7,20}$")
ID_RE = re.compile(r"^[0-9a-f-]{36}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return str(uuid.uuid4())


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${derived.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


DUMMY_PASSWORD_HASH = password_hash("not-a-user-password", bytes.fromhex("00112233445566778899aabbccddeeff"))


def normalize_city(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().casefold())
    if "bengaluru" in normalized or "bangalore" in normalized:
        return "bengaluru"
    if "mysuru" in normalized or "mysore" in normalized:
        return "mysuru"
    return normalized


def parse_datetime(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "invalid_request", f"{field} must be an ISO-8601 datetime with a timezone.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ApiError(400, "invalid_request", f"{field} must be an ISO-8601 datetime with a timezone.") from exc


def required_text(body: dict[str, Any], key: str, maximum: int = 200) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ApiError(400, "invalid_request", f"{key} is required and must be at most {maximum} characters.")
    try:
        value.strip().encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ApiError(400, "invalid_request", f"{key} must contain valid Unicode text.") from exc
    return value.strip()


def optional_text(body: dict[str, Any], key: str, maximum: int = 500) -> str | None:
    value = body.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise ApiError(400, "invalid_request", f"{key} must be at most {maximum} characters.")
    try:
        value.strip().encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ApiError(400, "invalid_request", f"{key} must contain valid Unicode text.") from exc
    return value.strip()


def require_phone(value: str) -> str:
    if not PHONE_RE.fullmatch(value):
        raise ApiError(400, "invalid_request", "phone must contain 7 to 20 digits or phone punctuation.")
    return value


def require_email(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) > 254 or not EMAIL_RE.fullmatch(normalized):
        raise ApiError(400, "invalid_request", "email must be a valid email address.")
    return normalized


def require_password(body: dict[str, Any]) -> str:
    value = body.get("password")
    if not isinstance(value, str) or not 12 <= len(value) <= 256:
        raise ApiError(400, "invalid_request", "password must be between 12 and 256 characters.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ApiError(400, "invalid_request", "password must contain valid Unicode text.") from exc
    return value


def require_integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ApiError(400, "invalid_request", f"{field} must be an integer from {minimum} to {maximum}.")
    return value


def public_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


class CabApi:
    def __init__(
        self,
        database_path: str | os.PathLike[str],
        admin_email: str | None = None,
        admin_password: str | None = None,
        allowed_origins: set[str] | None = None,
        whatsapp_verify_token: str | None = None,
        whatsapp_app_secret: str | None = None,
        whatsapp_access_token: str | None = None,
        whatsapp_phone_number_id: str | None = None,
        whatsapp_api_version: str | None = None,
    ):
        self.database_path = str(database_path)
        self.whatsapp_verify_token = whatsapp_verify_token or ""
        self.whatsapp_app_secret = whatsapp_app_secret or ""
        self.whatsapp_access_token = whatsapp_access_token or ""
        self.whatsapp_phone_number_id = whatsapp_phone_number_id or ""
        self.whatsapp_api_version = whatsapp_api_version or "v23.0"
        self.allowed_origins = allowed_origins or {
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "https://jnravr-del.github.io",
        }
        self._initialize()
        if bool(admin_email) != bool(admin_password):
            raise RuntimeError("Set both NAMMAURU_ADMIN_EMAIL and NAMMAURU_ADMIN_PASSWORD to bootstrap an admin.")
        if admin_email and admin_password:
            self._bootstrap_admin(admin_email, admin_password)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _initialize(self) -> None:
        path = Path(self.database_path)
        if self.database_path != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    phone TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('customer', 'vendor', 'driver', 'admin')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vendors (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    business_name TEXT NOT NULL,
                    partner_type TEXT NOT NULL CHECK (partner_type IN ('single', 'fleet')),
                    base_city TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drivers (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    base_city TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vehicles (
                    id TEXT PRIMARY KEY,
                    vendor_id TEXT NOT NULL REFERENCES vendors(user_id),
                    vehicle_class TEXT NOT NULL CHECK (vehicle_class IN ('hatchback', 'sedan', 'suv', 'crysta')),
                    make_model TEXT NOT NULL,
                    registration_number TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    seats INTEGER NOT NULL CHECK (seats BETWEEN 1 AND 16),
                    rate_per_km INTEGER NOT NULL CHECK (rate_per_km BETWEEN 1 AND 10000),
                    driver_allowance INTEGER NOT NULL CHECK (driver_allowance BETWEEN 0 AND 100000),
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bookings (
                    id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL REFERENCES users(id),
                    vehicle_id TEXT NOT NULL REFERENCES vehicles(id),
                    driver_id TEXT REFERENCES drivers(user_id),
                    trip_type TEXT NOT NULL CHECK (trip_type IN ('oneway', 'round', 'hourly', 'airport')),
                    pickup_city TEXT NOT NULL,
                    drop_city TEXT NOT NULL,
                    pickup_at TEXT NOT NULL,
                    dropoff_at TEXT NOT NULL,
                    passengers INTEGER NOT NULL CHECK (passengers BETWEEN 1 AND 16),
                    notes TEXT,
                    quoted_fare INTEGER CHECK (quoted_fare IS NULL OR quoted_fare BETWEEN 1 AND 10000000),
                    status TEXT NOT NULL CHECK (status IN ('requested', 'awaiting_customer_confirmation', 'confirmed', 'assigned', 'cancelled', 'rejected', 'completed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS bookings_vehicle_slot ON bookings(vehicle_id, pickup_at, dropoff_at, status);
                CREATE INDEX IF NOT EXISTS bookings_driver_slot ON bookings(driver_id, pickup_at, dropoff_at, status);
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    booking_id TEXT REFERENCES bookings(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    read_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS notifications_user_created ON notifications(user_id, created_at);
                CREATE TABLE IF NOT EXISTS contact_messages (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS whatsapp_conversations (
                    wa_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS whatsapp_inbound_messages (
                    message_id TEXT PRIMARY KEY,
                    wa_id TEXT NOT NULL,
                    reply TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _bootstrap_admin(self, email: str, password: str) -> None:
        email = require_email(email)
        if not 16 <= len(password) <= 256:
            raise RuntimeError("The bootstrap admin password must be between 16 and 256 characters.")
        with self._transaction() as db:
            existing = db.execute("SELECT id, role FROM users WHERE email = ?", (email,)).fetchone()
            if existing:
                if existing["role"] != "admin":
                    raise RuntimeError("The configured admin email belongs to a non-admin user.")
                return
            db.execute(
                "INSERT INTO users (id, name, email, phone, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, 'admin', ?)",
                (new_id(), "Administrator", email, "", password_hash(password), now_iso()),
            )

    def _json(self, environ: dict[str, Any]) -> dict[str, Any]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", "Content-Length must be a valid integer.") from exc
        if length < 0:
            raise ApiError(400, "invalid_request", "Content-Length must not be negative.")
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "payload_too_large", "Request body must be at most 64 KB.")
        if length == 0:
            return {}
        try:
            raw = environ["wsgi.input"].read(length)
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(400, "invalid_json", "Request body must contain valid JSON.") from exc
        if not isinstance(value, dict):
            raise ApiError(400, "invalid_json", "Request body must be a JSON object.")
        return value

    def _authenticate(self, environ: dict[str, Any]) -> sqlite3.Row:
        header = environ.get("HTTP_AUTHORIZATION", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token or " " in token:
            raise ApiError(401, "unauthorized", "A valid bearer token is required.")
        hashed = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connection() as db:
            row = db.execute(
                """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""",
                (hashed, now_iso()),
            ).fetchone()
        if row is None:
            raise ApiError(401, "unauthorized", "Session is invalid or expired.")
        return row

    @staticmethod
    def _role(user: sqlite3.Row, *roles: str) -> None:
        if user["role"] not in roles:
            raise ApiError(403, "forbidden", "You do not have permission to perform this action.")

    @staticmethod
    def _notify(db: sqlite3.Connection, user_id: str, kind: str, message: str, booking_id: str | None = None) -> None:
        db.execute(
            "INSERT INTO notifications (id, user_id, booking_id, kind, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), user_id, booking_id, kind, message, now_iso()),
        )

    def _notify_admins(self, db: sqlite3.Connection, kind: str, message: str, booking_id: str | None = None) -> None:
        for row in db.execute("SELECT id FROM users WHERE role = 'admin'"):
            self._notify(db, row["id"], kind, message, booking_id)

    @staticmethod
    def _vehicle(db: sqlite3.Connection, vehicle_id: str) -> sqlite3.Row:
        if not ID_RE.fullmatch(vehicle_id):
            raise ApiError(404, "not_found", "Vehicle not found.")
        row = db.execute(
            """SELECT v.*, p.business_name, p.base_city, p.status AS vendor_status
               FROM vehicles v JOIN vendors p ON p.user_id = v.vendor_id WHERE v.id = ?""",
            (vehicle_id,),
        ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "Vehicle not found.")
        return row

    @staticmethod
    def _public_vehicle(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "vehicle_class": row["vehicle_class"],
            "make_model": row["make_model"],
            "seats": row["seats"],
            "rate_per_km": row["rate_per_km"],
            "driver_allowance": row["driver_allowance"],
            "vendor": {"business_name": row["business_name"], "base_city": row["base_city"]},
        }

    @staticmethod
    def _booking(db: sqlite3.Connection, booking_id: str) -> sqlite3.Row:
        if not ID_RE.fullmatch(booking_id):
            raise ApiError(404, "not_found", "Booking not found.")
        row = db.execute(
            """SELECT b.*, u.name AS customer_name, u.email AS customer_email, u.phone AS customer_phone,
                      v.vendor_id, v.vehicle_class, v.make_model, v.registration_number,
                      p.business_name
               FROM bookings b JOIN users u ON u.id = b.customer_id
               JOIN vehicles v ON v.id = b.vehicle_id JOIN vendors p ON p.user_id = v.vendor_id
               WHERE b.id = ?""",
            (booking_id,),
        ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "Booking not found.")
        return row

    @staticmethod
    def _public_booking(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "vehicle_id": row["vehicle_id"],
            "vehicle": {
                "vehicle_class": row["vehicle_class"],
                "make_model": row["make_model"],
                "registration_number": row["registration_number"],
                "vendor": row["business_name"],
            },
            "driver_id": row["driver_id"],
            "trip_type": row["trip_type"],
            "pickup_city": row["pickup_city"],
            "drop_city": row["drop_city"],
            "pickup_at": row["pickup_at"],
            "dropoff_at": row["dropoff_at"],
            "passengers": row["passengers"],
            "notes": row["notes"],
            "quoted_fare": row["quoted_fare"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _booking_for_user(cls, row: sqlite3.Row, user: sqlite3.Row) -> dict[str, Any]:
        result = cls._public_booking(row)
        if user["role"] == "admin":
            result["customer"] = {
                "name": row["customer_name"],
                "email": row["customer_email"],
                "phone": row["customer_phone"],
            }
        elif row["vendor_id"] == user["id"] or row["driver_id"] == user["id"]:
            result["customer"] = {"name": row["customer_name"], "phone": row["customer_phone"]}
        return result

    @staticmethod
    def _is_available(db: sqlite3.Connection, vehicle_id: str, start: str, end: str, exclude_id: str | None = None) -> bool:
        sql = """SELECT 1 FROM bookings WHERE vehicle_id = ? AND status IN (?, ?, ?, ?)
                 AND pickup_at < ? AND dropoff_at > ?"""
        args: list[Any] = [vehicle_id, *ACTIVE_BOOKING_STATUSES, end, start]
        if exclude_id:
            sql += " AND id <> ?"
            args.append(exclude_id)
        return db.execute(sql + " LIMIT 1", args).fetchone() is None

    def _signup(self, body: dict[str, Any], role: str) -> dict[str, Any]:
        name = required_text(body, "name", 120)
        email = require_email(required_text(body, "email", 254))
        phone = require_phone(required_text(body, "phone", 20))
        password = require_password(body)
        encoded_password = password_hash(password)
        user_id, created = new_id(), now_iso()
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise ApiError(409, "email_exists", "An account with this email already exists.")
            db.execute(
                "INSERT INTO users (id, name, email, phone, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, name, email, phone, encoded_password, role, created),
            )
            if role == "vendor":
                business_name = required_text(body, "business_name", 120)
                partner_type = body.get("partner_type", "single")
                if not isinstance(partner_type, str) or partner_type not in {"single", "fleet"}:
                    raise ApiError(400, "invalid_request", "partner_type must be single or fleet.")
                base_city = required_text(body, "base_city", 120)
                db.execute(
                    "INSERT INTO vendors (user_id, business_name, partner_type, base_city, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, business_name, partner_type, base_city, created),
                )
                self._notify_admins(db, "vendor_signup", f"Vendor signup pending review: {business_name}.")
            elif role == "driver":
                base_city = required_text(body, "base_city", 120)
                db.execute(
                    "INSERT INTO drivers (user_id, base_city, created_at) VALUES (?, ?, ?)",
                    (user_id, base_city, created),
                )
                self._notify_admins(db, "driver_signup", f"Driver signup pending review: {name}.")
            user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            token = self._create_session(db, user_id)
        return {"user": public_user(user), "access_token": token, "token_type": "Bearer", "expires_in": 43200}

    @staticmethod
    def _create_session(db: sqlite3.Connection, user_id: str) -> str:
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)", (token_hash, user_id, expires))
        return token

    @staticmethod
    def _limit(query: dict[str, list[str]]) -> int:
        raw = query.get("limit", ["50"])[0]
        try:
            limit = int(raw)
        except ValueError as exc:
            raise ApiError(400, "invalid_request", "limit must be an integer from 1 to 100.") from exc
        if not 1 <= limit <= 100:
            raise ApiError(400, "invalid_request", "limit must be an integer from 1 to 100.")
        return limit

    @staticmethod
    def _whatsapp_digits(value: str) -> str:
        return re.sub(r"\D", "", value)

    def _whatsapp_customer(self, db: sqlite3.Connection, wa_id: str) -> sqlite3.Row | None:
        customers = db.execute("SELECT id, phone FROM users WHERE role = 'customer'").fetchall()
        matches = [row for row in customers if self._whatsapp_digits(row["phone"]) == wa_id]
        if len(matches) > 1:
            raise ApiError(
                409,
                "whatsapp_account_ambiguous",
                "More than one customer account uses this WhatsApp number. Contact dispatch before booking.",
            )
        return matches[0] if matches else None

    def _whatsapp_vehicle_options(
        self, db: sqlite3.Connection, data: dict[str, Any]
    ) -> list[sqlite3.Row]:
        pickup_city = normalize_city(data["pickup_city"])
        rows = db.execute(
            """SELECT v.id, v.vehicle_class, v.make_model, v.seats, p.business_name
               FROM vehicles v JOIN vendors p ON p.user_id = v.vendor_id
               WHERE v.status = 'approved' AND p.status = 'approved' AND v.seats >= ?
                 AND instr(replace(replace(lower(p.base_city), 'bangalore', 'bengaluru'),
                                   'mysore', 'mysuru'), ?) > 0
                 AND NOT EXISTS (
                   SELECT 1 FROM bookings b WHERE b.vehicle_id = v.id
                     AND b.status IN (?, ?, ?, ?) AND b.pickup_at < ? AND b.dropoff_at > ?
                 )
               ORDER BY v.rate_per_km, v.id LIMIT 5""",
            (
                data["passengers"],
                pickup_city,
                *ACTIVE_BOOKING_STATUSES,
                data["dropoff_at"],
                data["pickup_at"],
            ),
        ).fetchall()
        return list(rows)

    @staticmethod
    def _whatsapp_booking_status(
        db: sqlite3.Connection, customer_id: str, booking_id: str | None
    ) -> sqlite3.Row:
        if booking_id:
            if not ID_RE.fullmatch(booking_id):
                raise ApiError(400, "invalid_request", "Use the booking reference shown in your confirmation.")
            row = db.execute(
                "SELECT id FROM bookings WHERE id = ? AND customer_id = ?", (booking_id, customer_id)
            ).fetchone()
        else:
            row = db.execute(
                """SELECT id FROM bookings WHERE customer_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (customer_id,),
            ).fetchone()
        if row is None:
            raise ApiError(404, "not_found", "No booking was found for this WhatsApp number.")
        return CabApi._booking(db, row["id"])

    def _whatsapp_status_text(self, db: sqlite3.Connection, row: sqlite3.Row) -> str:
        lines = [
            f"Booking {row['id']}: {row['status'].replace('_', ' ')}.",
            f"{row['pickup_city']} to {row['drop_city']} · pickup {row['pickup_at']}.",
            f"Vehicle: {row['make_model']} ({row['vehicle_class']}) from {row['business_name']}.",
        ]
        if row["quoted_fare"] is not None:
            lines.append(f"Admin-quoted fare: ₹{row['quoted_fare']}.")
        if row["status"] == "awaiting_customer_confirmation":
            lines.append(f"Reply CONFIRM {row['id']} to accept this booking quote.")
        if row["driver_id"] and row["status"] == "assigned":
            driver = db.execute(
                """SELECT u.name, u.phone FROM users u
                   WHERE u.id = ? AND u.role = 'driver'""",
                (row["driver_id"],),
            ).fetchone()
            if driver:
                lines.append(f"Driver: {driver['name']} · {driver['phone']}.")
            lines.append(f"Vehicle registration: {row['registration_number']}.")
        if row["status"] == "requested":
            lines.append("The dispatch team has not quoted a fare yet.")
        return "\n".join(lines)

    def _whatsapp_handle_message(
        self, db: sqlite3.Connection, wa_id: str, text: str
    ) -> str:
        normalized = text.strip()
        command = normalized.casefold()
        if command in {"help", "start", "restart", "book"}:
            db.execute(
                """INSERT INTO whatsapp_conversations (wa_id, state, data_json, updated_at)
                   VALUES (?, 'pickup', '{}', ?)
                   ON CONFLICT(wa_id) DO UPDATE SET state = 'pickup', data_json = '{}', updated_at = excluded.updated_at""",
                (wa_id, now_iso()),
            )
            return "Let's request a one-way cab. What is your pickup city?"

        match = re.fullmatch(r"(?:status|confirm)\s+([0-9a-fA-F-]{36})", normalized, re.IGNORECASE)
        if command == "status" or command.startswith("status ") or command == "confirm" or command.startswith("confirm "):
            if (command.startswith("status ") or command.startswith("confirm ")) and not match:
                return "That booking reference is not valid. Use STATUS or CONFIRM followed by the booking reference."
            customer = self._whatsapp_customer(db, wa_id)
            if customer is None:
                return "No customer account matches this WhatsApp number. Register on the Namma Uru Cab website with this same number, then message START."
            booking_id = match.group(1).lower() if match else None
            if command == "status" or command.startswith("status "):
                row = self._whatsapp_booking_status(db, customer["id"], booking_id)
                return self._whatsapp_status_text(db, row)
            if booking_id:
                row = self._whatsapp_booking_status(db, customer["id"], booking_id)
            else:
                pending_quote = db.execute(
                    """SELECT id FROM bookings WHERE customer_id = ?
                       AND status = 'awaiting_customer_confirmation'
                       ORDER BY updated_at DESC LIMIT 1""",
                    (customer["id"],),
                ).fetchone()
                if pending_quote is None:
                    return "There is no fare quote awaiting confirmation. Reply STATUS to check your latest booking."
                row = self._booking(db, pending_quote["id"])
            if row["status"] != "awaiting_customer_confirmation" or row["quoted_fare"] is None:
                return "There is no fare quote awaiting confirmation for that booking. Reply STATUS to check its current state."
            timestamp = now_iso()
            db.execute(
                "UPDATE bookings SET status = 'confirmed', updated_at = ? WHERE id = ?",
                (timestamp, row["id"]),
            )
            self._notify(db, row["vendor_id"], "booking_confirmed", f"Customer confirmed booking {row['id']} via WhatsApp.", row["id"])
            self._notify_admins(db, "booking_confirmed", f"Customer confirmed booking {row['id']} via WhatsApp.", row["id"])
            return (
                f"Booking {row['id']} is confirmed at the admin-quoted fare of ₹{row['quoted_fare']}."
            )

        conversation = db.execute(
            "SELECT state, data_json FROM whatsapp_conversations WHERE wa_id = ?", (wa_id,)
        ).fetchone()
        if conversation is None:
            db.execute(
                """INSERT INTO whatsapp_conversations (wa_id, state, data_json, updated_at)
                   VALUES (?, 'pickup', '{}', ?)""",
                (wa_id, now_iso()),
            )
            return "Welcome to Namma Uru Cab. I can request a one-way ride using approved available cabs. What is your pickup city?"

        state = conversation["state"]
        data = json.loads(conversation["data_json"])
        if state == "pickup":
            data["pickup_city"] = required_text({"pickup_city": normalized}, "pickup_city", 120)
            next_state = "destination"
            reply = "What is your destination city?"
        elif state == "destination":
            destination = required_text({"destination": normalized}, "destination", 120)
            if normalize_city(destination) == normalize_city(data["pickup_city"]):
                return "Pickup and destination must be different. Please send your destination city."
            data["drop_city"] = destination
            next_state = "pickup_time"
            reply = "What is your requested pickup date and time? Send an ISO-8601 time with timezone, for example 2026-12-25T09:30+05:30."
        elif state == "pickup_time":
            try:
                pickup_at = parse_datetime(normalized, "pickup_at")
            except ApiError:
                return "I couldn't read that time. Send an ISO-8601 date and time with timezone, for example 2026-12-25T09:30+05:30."
            start = datetime.strptime(pickup_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            dropoff_at = (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            if start <= datetime.now(timezone.utc) or start > datetime.now(timezone.utc) + timedelta(days=7):
                return "Pickup must be in the future and within the next 7 days. Please send another ISO-8601 pickup time."
            data["pickup_at"] = pickup_at
            data["dropoff_at"] = dropoff_at
            next_state = "passengers"
            reply = "How many passengers are travelling? WhatsApp booking currently supports 1–16 passengers and one-way trips."
        elif state == "passengers":
            try:
                passengers = int(normalized)
            except ValueError:
                return "Please reply with the number of passengers (1–16)."
            if not 1 <= passengers <= 16:
                return "Please reply with a number of passengers from 1 to 16."
            data["passengers"] = passengers
            customer = self._whatsapp_customer(db, wa_id)
            if customer is None:
                next_state = "awaiting_account"
                reply = "Your ride details are saved for this chat, but a booking requires a customer account. Register on the Namma Uru Cab website using this same WhatsApp phone number, then send any message here."
            else:
                options = self._whatsapp_vehicle_options(db, data)
                if not options:
                    next_state = "pickup"
                    data = {}
                    reply = "There are no approved cabs available for that city, time, and passenger count. Send another pickup city to try again."
                else:
                    data["options"] = [row["id"] for row in options]
                    next_state = "vehicle"
                    choices = [
                        f"{index}. {row['make_model']} ({row['vehicle_class']}, {row['seats']} seats) — {row['business_name']}"
                        for index, row in enumerate(options, 1)
                    ]
                    reply = "Available cabs (fare is quoted by dispatch after your request):\n" + "\n".join(choices) + "\nReply with the option number."
        elif state == "awaiting_account":
            customer = self._whatsapp_customer(db, wa_id)
            if customer is None:
                return "I still can't find a customer account using this WhatsApp number. Register with the same number, then message again."
            options = self._whatsapp_vehicle_options(db, data)
            if not options:
                next_state = "pickup"
                data = {}
                reply = "There are no approved cabs available for those details. Send another pickup city to try again."
            else:
                data["options"] = [row["id"] for row in options]
                next_state = "vehicle"
                choices = [
                    f"{index}. {row['make_model']} ({row['vehicle_class']}, {row['seats']} seats) — {row['business_name']}"
                    for index, row in enumerate(options, 1)
                ]
                reply = "Available cabs (fare is quoted by dispatch after your request):\n" + "\n".join(choices) + "\nReply with the option number."
        elif state == "vehicle":
            try:
                choice = int(normalized)
            except ValueError:
                return "Reply with the number of one of the available cab options, or START to begin again."
            options = data.get("options", [])
            if not 1 <= choice <= len(options):
                return "That option is not in the available list. Reply with a listed option number, or START to begin again."
            customer = self._whatsapp_customer(db, wa_id)
            if customer is None:
                return "Your customer account could not be matched to this WhatsApp number. Register with this same number and send START."
            vehicle = self._vehicle(db, options[choice - 1])
            if (
                vehicle["status"] != "approved"
                or vehicle["vendor_status"] != "approved"
                or vehicle["seats"] < data["passengers"]
                or not self._is_available(db, vehicle["id"], data["pickup_at"], data["dropoff_at"])
            ):
                return "That cab is no longer available. Send START to search again."
            booking_id, created = new_id(), now_iso()
            db.execute(
                """INSERT INTO bookings
                   (id, customer_id, vehicle_id, trip_type, pickup_city, drop_city, pickup_at, dropoff_at,
                    passengers, notes, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'oneway', ?, ?, ?, ?, ?, 'Requested via WhatsApp', 'requested', ?, ?)""",
                (
                    booking_id, customer["id"], vehicle["id"], data["pickup_city"], data["drop_city"],
                    data["pickup_at"], data["dropoff_at"], data["passengers"], created, created,
                ),
            )
            self._notify_admins(db, "booking_requested", f"New WhatsApp cab booking request {booking_id}.", booking_id)
            self._notify(db, vehicle["vendor_id"], "booking_requested", f"New WhatsApp booking request {booking_id}.", booking_id)
            data["booking_id"] = booking_id
            next_state = "booked"
            reply = (
                f"Booking request {booking_id} has been sent to dispatch. "
                "The fare is not set yet; dispatch must provide a quote. Reply STATUS to check for a quote."
            )
        elif state == "booked":
            return "Your booking request is with dispatch. Reply STATUS for its latest state or START to request another ride."
        else:
            next_state, data = "pickup", {}
            reply = "Let's start a one-way ride request. What is your pickup city?"

        db.execute(
            """INSERT INTO whatsapp_conversations (wa_id, state, data_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(wa_id) DO UPDATE
                 SET state = excluded.state, data_json = excluded.data_json, updated_at = excluded.updated_at""",
            (wa_id, next_state, json.dumps(data, separators=(",", ":")), now_iso()),
        )
        return reply

    def _send_whatsapp(self, wa_id: str, text: str) -> None:
        if not all((self.whatsapp_access_token, self.whatsapp_phone_number_id)):
            raise ApiError(503, "whatsapp_not_configured", "WhatsApp outbound messaging is not configured.")
        endpoint = (
            f"https://graph.facebook.com/{self.whatsapp_api_version}/"
            f"{self.whatsapp_phone_number_id}/messages"
        )
        payload = json.dumps(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": wa_id,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.whatsapp_access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                if response.status < 200 or response.status >= 300:
                    raise ApiError(502, "whatsapp_delivery_failed", "WhatsApp did not accept the outbound message.")
        except HTTPError as exc:
            print(f"WhatsApp Graph API returned HTTP {exc.code}.", file=sys.stderr)
            raise ApiError(502, "whatsapp_delivery_failed", "WhatsApp did not accept the outbound message.") from exc
        except (URLError, TimeoutError) as exc:
            print(f"WhatsApp Graph API request failed: {exc}", file=sys.stderr)
            raise ApiError(502, "whatsapp_delivery_failed", "WhatsApp message delivery failed.") from exc

    def _whatsapp_webhook_post(self, raw: bytes) -> None:
        if not self.whatsapp_app_secret:
            raise ApiError(503, "whatsapp_not_configured", "WhatsApp webhook signature verification is not configured.")
        if not self.whatsapp_access_token or not self.whatsapp_phone_number_id:
            raise ApiError(503, "whatsapp_not_configured", "WhatsApp outbound messaging is not configured.")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid_json", "Webhook body must be a JSON object.")
        if payload.get("object") != "whatsapp_business_account":
            raise ApiError(400, "invalid_webhook", "Webhook object must be a WhatsApp business account.")
        entries = payload.get("entry", [])
        if not isinstance(entries, list):
            raise ApiError(400, "invalid_webhook", "Webhook entry must be a list.")
        for entry in entries:
            changes = entry.get("changes", []) if isinstance(entry, dict) else []
            if not isinstance(changes, list):
                continue
            for change in changes:
                value = change.get("value", {}) if isinstance(change, dict) else {}
                messages = value.get("messages", []) if isinstance(value, dict) else []
                if not isinstance(messages, list):
                    continue
                metadata = value.get("metadata", {})
                if messages and (
                    not isinstance(metadata, dict)
                    or metadata.get("phone_number_id") != self.whatsapp_phone_number_id
                ):
                    raise ApiError(403, "wrong_whatsapp_number", "Webhook event belongs to a different WhatsApp phone number.")
                for message in messages:
                    if not isinstance(message, dict) or not isinstance(message.get("id"), str) or not message["id"]:
                        continue
                    wa_id = message.get("from")
                    if not isinstance(wa_id, str) or not re.fullmatch(r"\d{7,20}", wa_id):
                        continue
                    if message.get("type") == "text":
                        text_content = message.get("text", {})
                        message_text = text_content.get("body", "") if isinstance(text_content, dict) else ""
                    elif message.get("type") == "button":
                        button = message.get("button", {})
                        message_text = button.get("text", "") if isinstance(button, dict) else ""
                    elif message.get("type") == "interactive":
                        interactive = message.get("interactive", {})
                        if not isinstance(interactive, dict):
                            interactive = {}
                        button_reply = interactive.get("button_reply", {})
                        list_reply = interactive.get("list_reply", {})
                        message_text = (
                            (button_reply.get("title") if isinstance(button_reply, dict) else None)
                            or (list_reply.get("title") if isinstance(list_reply, dict) else None)
                            or ""
                        )
                    else:
                        message_text = ""
                    if not isinstance(message_text, str):
                        message_text = ""
                    with self._transaction() as db:
                        previous = db.execute(
                            "SELECT wa_id, reply FROM whatsapp_inbound_messages WHERE message_id = ?",
                            (message["id"],),
                        ).fetchone()
                        if previous is not None:
                            if previous["wa_id"] != wa_id:
                                continue
                            reply = previous["reply"]
                        else:
                            try:
                                reply = self._whatsapp_handle_message(db, wa_id, message_text)
                            except ApiError as exc:
                                reply = exc.message
                            db.execute(
                                """INSERT INTO whatsapp_inbound_messages
                                   (message_id, wa_id, reply, created_at) VALUES (?, ?, ?, ?)""",
                                (message["id"], wa_id, reply, now_iso()),
                            )
                    self._send_whatsapp(wa_id, reply)

    def _dispatch(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: dict[str, Any],
        environ: dict[str, Any],
    ) -> tuple[int, Any]:
        if path == f"{API_PREFIX}/health" and method == "GET":
            return 200, {"status": "ok"}
        if path == f"{API_PREFIX}/auth/signup" and method == "POST":
            return 201, self._signup(body, "customer")
        if path == f"{API_PREFIX}/auth/vendor-signup" and method == "POST":
            return 201, self._signup(body, "vendor")
        if path == f"{API_PREFIX}/auth/driver-signup" and method == "POST":
            return 201, self._signup(body, "driver")
        if path == f"{API_PREFIX}/auth/signin" and method == "POST":
            email = require_email(required_text(body, "email", 254))
            password = body.get("password")
            if not isinstance(password, str) or len(password) > 256:
                raise ApiError(400, "invalid_request", "password is required.")
            try:
                password.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ApiError(400, "invalid_request", "password must contain valid Unicode text.") from exc
            with self._connection() as db:
                user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
                encoded_hash = user["password_hash"] if user is not None else DUMMY_PASSWORD_HASH
                valid_password = password_matches(password, encoded_hash)
                if user is None or not valid_password:
                    raise ApiError(401, "invalid_credentials", "Email or password is incorrect.")
            with self._transaction() as db:
                token = self._create_session(db, user["id"])
            return 200, {"user": public_user(user), "access_token": token, "token_type": "Bearer", "expires_in": 43200}

        if path.startswith(f"{API_PREFIX}/"):
            is_public = (
                (path == f"{API_PREFIX}/cabs/search" and method == "GET")
                or (re.fullmatch(re.escape(API_PREFIX) + r"/cabs/([0-9a-f-]{36})", path) is not None and method == "GET")
                or (path == f"{API_PREFIX}/contact" and method == "POST")
            )
            user = None if is_public else self._authenticate(environ)
            if path == f"{API_PREFIX}/auth/signout" and method == "POST":
                token = environ["HTTP_AUTHORIZATION"].partition(" ")[2]
                with self._transaction() as db:
                    db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
                return 200, {"signed_out": True}
            if path == f"{API_PREFIX}/me" and method == "GET":
                return 200, public_user(user)
            if path == f"{API_PREFIX}/me" and method == "PATCH":
                changes: dict[str, str] = {}
                if "name" in body:
                    changes["name"] = required_text(body, "name", 120)
                if "phone" in body:
                    changes["phone"] = require_phone(required_text(body, "phone", 20))
                if not changes:
                    raise ApiError(400, "invalid_request", "Provide name or phone to update.")
                with self._transaction() as db:
                    db.execute(
                        "UPDATE users SET " + ", ".join(f"{key} = ?" for key in changes) + " WHERE id = ?",
                        (*changes.values(), user["id"]),
                    )
                    row = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
                return 200, public_user(row)
            if path == f"{API_PREFIX}/notifications" and method == "GET":
                self._role(user, "customer", "vendor", "driver", "admin")
                limit = self._limit(query)
                with self._connection() as db:
                    rows = db.execute(
                        "SELECT id, booking_id, kind, message, read_at, created_at FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                        (user["id"], limit),
                    ).fetchall()
                return 200, [dict(row) for row in rows]
            match = re.fullmatch(re.escape(API_PREFIX) + r"/notifications/([0-9a-f-]{36})/read", path)
            if match and method == "POST":
                with self._transaction() as db:
                    result = db.execute(
                        "UPDATE notifications SET read_at = ? WHERE id = ? AND user_id = ?",
                        (now_iso(), match.group(1), user["id"]),
                    )
                    if result.rowcount == 0:
                        raise ApiError(404, "not_found", "Notification not found.")
                return 200, {"read": True}

            if path == f"{API_PREFIX}/vendors/me" and method == "GET":
                self._role(user, "vendor")
                with self._connection() as db:
                    row = db.execute("SELECT * FROM vendors WHERE user_id = ?", (user["id"],)).fetchone()
                return 200, dict(row)
            if path == f"{API_PREFIX}/vendors/me" and method == "PATCH":
                self._role(user, "vendor")
                changes = {key: required_text(body, key, 120) for key in ("business_name", "base_city") if key in body}
                if "partner_type" in body:
                    if not isinstance(body["partner_type"], str) or body["partner_type"] not in {"single", "fleet"}:
                        raise ApiError(400, "invalid_request", "partner_type must be single or fleet.")
                    changes["partner_type"] = body["partner_type"]
                if not changes:
                    raise ApiError(400, "invalid_request", "Provide business_name, base_city, or partner_type.")
                with self._transaction() as db:
                    db.execute(
                        "UPDATE vendors SET " + ", ".join(f"{key} = ?" for key in changes) + ", status = 'pending' WHERE user_id = ?",
                        (*changes.values(), user["id"]),
                    )
                    row = db.execute("SELECT * FROM vendors WHERE user_id = ?", (user["id"],)).fetchone()
                    self._notify_admins(db, "vendor_updated", f"Vendor account updated and requires review: {row['business_name']}.")
                return 200, dict(row)
            if path == f"{API_PREFIX}/vendors/me/vehicles" and method == "GET":
                self._role(user, "vendor")
                with self._connection() as db:
                    rows = db.execute(
                        "SELECT id, vehicle_class, make_model, registration_number, seats, rate_per_km, driver_allowance, status, created_at FROM vehicles WHERE vendor_id = ? ORDER BY created_at DESC",
                        (user["id"],),
                    ).fetchall()
                return 200, [dict(row) for row in rows]
            if path == f"{API_PREFIX}/vendors/me/vehicles" and method == "POST":
                self._role(user, "vendor")
                vehicle_class = body.get("vehicle_class")
                if not isinstance(vehicle_class, str) or vehicle_class not in VEHICLE_CLASSES:
                    raise ApiError(400, "invalid_request", "vehicle_class must be hatchback, sedan, suv, or crysta.")
                vehicle_id, created = new_id(), now_iso()
                plate = required_text(body, "registration_number", 20).upper()
                if not re.fullmatch(r"[A-Z0-9 -]{5,20}", plate):
                    raise ApiError(400, "invalid_request", "registration_number contains invalid characters.")
                with self._transaction() as db:
                    db.execute(
                        """INSERT INTO vehicles
                           (id, vendor_id, vehicle_class, make_model, registration_number, seats, rate_per_km, driver_allowance, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            vehicle_id, user["id"], vehicle_class, required_text(body, "make_model", 100), plate,
                            require_integer(body.get("seats"), "seats", 1, 16),
                            require_integer(body.get("rate_per_km"), "rate_per_km", 1, 10000),
                            require_integer(body.get("driver_allowance"), "driver_allowance", 0, 100000), created,
                        ),
                    )
                    row = db.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
                return 201, dict(row)
            match = re.fullmatch(re.escape(API_PREFIX) + r"/vendors/me/vehicles/([0-9a-f-]{36})", path)
            if match and method == "PATCH":
                self._role(user, "vendor")
                allowed = {"vehicle_class", "make_model", "registration_number", "seats", "rate_per_km", "driver_allowance"}
                changes: dict[str, Any] = {}
                for key in allowed.intersection(body):
                    value = body[key]
                    if key == "vehicle_class":
                        if not isinstance(value, str) or value not in VEHICLE_CLASSES:
                            raise ApiError(400, "invalid_request", "vehicle_class is invalid.")
                    elif key in {"seats", "rate_per_km", "driver_allowance"}:
                        bounds = {"seats": (1, 16), "rate_per_km": (1, 10000), "driver_allowance": (0, 100000)}[key]
                        value = require_integer(value, key, *bounds)
                    elif key == "registration_number":
                        value = required_text({key: value}, key, 20).upper()
                        if not re.fullmatch(r"[A-Z0-9 -]{5,20}", value):
                            raise ApiError(400, "invalid_request", "registration_number contains invalid characters.")
                    else:
                        value = required_text({key: value}, key, 100)
                    changes[key] = value
                if not changes:
                    raise ApiError(400, "invalid_request", "Provide one or more vehicle fields to update.")
                with self._transaction() as db:
                    result = db.execute(
                        "UPDATE vehicles SET " + ", ".join(f"{key} = ?" for key in changes) + ", status = 'pending' WHERE id = ? AND vendor_id = ?",
                        (*changes.values(), match.group(1), user["id"]),
                    )
                    if not result.rowcount:
                        raise ApiError(404, "not_found", "Vehicle not found.")
                    row = db.execute("SELECT * FROM vehicles WHERE id = ?", (match.group(1),)).fetchone()
                    self._notify_admins(db, "vehicle_updated", f"Vehicle updated and requires review: {row['registration_number']}.")
                return 200, dict(row)

            if path == f"{API_PREFIX}/drivers/me" and method == "GET":
                self._role(user, "driver")
                with self._connection() as db:
                    row = db.execute("SELECT * FROM drivers WHERE user_id = ?", (user["id"],)).fetchone()
                return 200, dict(row)
            if path == f"{API_PREFIX}/drivers/me" and method == "PATCH":
                self._role(user, "driver")
                base_city = required_text(body, "base_city", 120)
                with self._transaction() as db:
                    db.execute("UPDATE drivers SET base_city = ? WHERE user_id = ?", (base_city, user["id"]))
                    row = db.execute("SELECT * FROM drivers WHERE user_id = ?", (user["id"],)).fetchone()
                return 200, dict(row)
            if path == f"{API_PREFIX}/drivers/me/bookings" and method == "GET":
                self._role(user, "driver")
                limit = self._limit(query)
                with self._connection() as db:
                    ids = db.execute(
                        "SELECT id FROM bookings WHERE driver_id = ? ORDER BY created_at DESC LIMIT ?",
                        (user["id"], limit),
                    ).fetchall()
                    result = [self._booking_for_user(self._booking(db, row["id"]), user) for row in ids]
                return 200, result

            if path == f"{API_PREFIX}/cabs/search" and method == "GET":
                pickup_city = query.get("pickup_city", [""])[0].strip()
                if not pickup_city or len(pickup_city) > 120:
                    raise ApiError(400, "invalid_request", "pickup_city is required and must be at most 120 characters.")
                pickup_city = normalize_city(pickup_city)
                start = parse_datetime(query.get("pickup_at", [None])[0], "pickup_at")
                try:
                    duration = int(query.get("duration_minutes", ["120"])[0])
                except ValueError as exc:
                    raise ApiError(400, "invalid_request", "duration_minutes must be an integer from 30 to 10080.") from exc
                if not 30 <= duration <= 10080:
                    raise ApiError(400, "invalid_request", "duration_minutes must be from 30 to 10080.")
                try:
                    end = (
                        datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                        + timedelta(minutes=duration)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                except OverflowError as exc:
                    raise ApiError(400, "invalid_request", "The requested pickup time is out of range.") from exc
                vehicle_class = query.get("vehicle_class", [None])[0]
                if vehicle_class and vehicle_class not in VEHICLE_CLASSES:
                    raise ApiError(400, "invalid_request", "vehicle_class is invalid.")
                limit = self._limit(query)
                sql = """SELECT v.*, p.business_name, p.base_city, p.status AS vendor_status
                         FROM vehicles v JOIN vendors p ON p.user_id = v.vendor_id
                         WHERE v.status = 'approved' AND p.status = 'approved'
                           AND instr(replace(replace(lower(p.base_city), 'bangalore', 'bengaluru'), 'mysore', 'mysuru'), ?) > 0
                           AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.vehicle_id = v.id
                             AND b.status IN (?, ?, ?, ?) AND b.pickup_at < ? AND b.dropoff_at > ?)"""
                args: list[Any] = [pickup_city, *ACTIVE_BOOKING_STATUSES, end, start]
                if vehicle_class:
                    sql += " AND v.vehicle_class = ?"
                    args.append(vehicle_class)
                args.append(limit)
                with self._connection() as db:
                    rows = db.execute(sql + " ORDER BY v.rate_per_km, v.id LIMIT ?", args).fetchall()
                return 200, [self._public_vehicle(row) for row in rows]
            match = re.fullmatch(re.escape(API_PREFIX) + r"/cabs/([0-9a-f-]{36})", path)
            if match and method == "GET":
                with self._connection() as db:
                    row = self._vehicle(db, match.group(1))
                if row["status"] != "approved" or row["vendor_status"] != "approved":
                    raise ApiError(404, "not_found", "Vehicle not found.")
                return 200, self._public_vehicle(row)

            if path == f"{API_PREFIX}/bookings" and method == "POST":
                self._role(user, "customer")
                vehicle_id = required_text(body, "vehicle_id", 36)
                trip_type = body.get("trip_type")
                if not isinstance(trip_type, str) or trip_type not in TRIP_TYPES:
                    raise ApiError(400, "invalid_request", "trip_type must be oneway, round, hourly, or airport.")
                pickup_city = required_text(body, "pickup_city", 120)
                drop_city = required_text(body, "drop_city", 120)
                if trip_type != "hourly" and normalize_city(pickup_city) == normalize_city(drop_city):
                    raise ApiError(400, "invalid_request", "pickup_city and drop_city must be different.")
                start = parse_datetime(body.get("pickup_at"), "pickup_at")
                end = parse_datetime(body.get("dropoff_at"), "dropoff_at")
                duration = (
                    datetime.strptime(end, "%Y-%m-%dT%H:%M:%SZ")
                    - datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
                ).total_seconds() / 60
                if (
                    duration < 30
                    or duration > 10080
                    or datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)
                ):
                    raise ApiError(400, "invalid_request", "dropoff_at must be 30 minutes to 7 days after a future pickup_at.")
                passengers = require_integer(body.get("passengers"), "passengers", 1, 16)
                notes = optional_text(body, "notes", 500)
                booking_id, created = new_id(), now_iso()
                with self._transaction() as db:
                    vehicle = self._vehicle(db, vehicle_id)
                    if vehicle["status"] != "approved" or vehicle["vendor_status"] != "approved":
                        raise ApiError(409, "cab_unavailable", "This cab is not currently available to book.")
                    if passengers > vehicle["seats"]:
                        raise ApiError(400, "invalid_request", "passengers exceeds the selected cab's seat capacity.")
                    if not self._is_available(db, vehicle_id, start, end):
                        raise ApiError(409, "cab_unavailable", "This cab is already booked for that time.")
                    db.execute(
                        """INSERT INTO bookings
                           (id, customer_id, vehicle_id, trip_type, pickup_city, drop_city, pickup_at, dropoff_at,
                            passengers, notes, status, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'requested', ?, ?)""",
                        (booking_id, user["id"], vehicle_id, trip_type, pickup_city, drop_city, start, end, passengers, notes, created, created),
                    )
                    self._notify_admins(db, "booking_requested", f"New cab booking request {booking_id}.", booking_id)
                    self._notify(db, vehicle["vendor_id"], "booking_requested", f"New booking request {booking_id}.", booking_id)
                    row = self._booking(db, booking_id)
                return 201, self._public_booking(row)

            if path == f"{API_PREFIX}/bookings" and method == "GET":
                self._role(user, "customer", "admin")
                limit = self._limit(query)
                with self._connection() as db:
                    if user["role"] == "admin":
                        rows = db.execute("SELECT id FROM bookings ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
                    else:
                        rows = db.execute("SELECT id FROM bookings WHERE customer_id = ? ORDER BY created_at DESC LIMIT ?", (user["id"], limit)).fetchall()
                    result = [self._booking_for_user(self._booking(db, row["id"]), user) for row in rows]
                return 200, result
            match = re.fullmatch(re.escape(API_PREFIX) + r"/bookings/([0-9a-f-]{36})", path)
            if match and method == "GET":
                with self._connection() as db:
                    row = self._booking(db, match.group(1))
                if user["role"] not in {"admin"} and row["customer_id"] != user["id"] and row["vendor_id"] != user["id"] and row["driver_id"] != user["id"]:
                    raise ApiError(404, "not_found", "Booking not found.")
                return 200, self._booking_for_user(row, user)
            match = re.fullmatch(re.escape(API_PREFIX) + r"/bookings/([0-9a-f-]{36})/confirm", path)
            if match and method == "POST":
                self._role(user, "customer")
                with self._transaction() as db:
                    row = self._booking(db, match.group(1))
                    if row["customer_id"] != user["id"]:
                        raise ApiError(404, "not_found", "Booking not found.")
                    if row["status"] != "awaiting_customer_confirmation" or row["quoted_fare"] is None:
                        raise ApiError(409, "invalid_booking_state", "Only a quoted booking awaiting your confirmation can be confirmed.")
                    timestamp = now_iso()
                    db.execute("UPDATE bookings SET status = 'confirmed', updated_at = ? WHERE id = ?", (timestamp, row["id"]))
                    self._notify(db, row["vendor_id"], "booking_confirmed", f"Customer confirmed booking {row['id']}.", row["id"])
                    self._notify_admins(db, "booking_confirmed", f"Customer confirmed booking {row['id']}.", row["id"])
                    row = self._booking(db, row["id"])
                return 200, self._booking_for_user(row, user)
            match = re.fullmatch(re.escape(API_PREFIX) + r"/bookings/([0-9a-f-]{36})/cancel", path)
            if match and method == "POST":
                with self._transaction() as db:
                    row = self._booking(db, match.group(1))
                    if user["role"] != "admin" and (user["role"] != "customer" or row["customer_id"] != user["id"]):
                        raise ApiError(404, "not_found", "Booking not found.")
                    if row["status"] in {"cancelled", "rejected", "completed"}:
                        raise ApiError(409, "invalid_booking_state", "This booking can no longer be cancelled.")
                    db.execute("UPDATE bookings SET status = 'cancelled', updated_at = ? WHERE id = ?", (now_iso(), row["id"]))
                    self._notify(db, row["customer_id"], "booking_cancelled", f"Booking {row['id']} was cancelled.", row["id"])
                    self._notify(db, row["vendor_id"], "booking_cancelled", f"Booking {row['id']} was cancelled.", row["id"])
                    if row["driver_id"]:
                        self._notify(db, row["driver_id"], "booking_cancelled", f"Booking {row['id']} was cancelled.", row["id"])
                    self._notify_admins(db, "booking_cancelled", f"Booking {row['id']} was cancelled.", row["id"])
                    row = self._booking(db, row["id"])
                return 200, self._booking_for_user(row, user)

            if path == f"{API_PREFIX}/vendors/me/bookings" and method == "GET":
                self._role(user, "vendor")
                limit = self._limit(query)
                with self._connection() as db:
                    ids = db.execute(
                        """SELECT b.id FROM bookings b JOIN vehicles v ON v.id = b.vehicle_id
                           WHERE v.vendor_id = ? ORDER BY b.created_at DESC LIMIT ?""",
                        (user["id"], limit),
                    ).fetchall()
                    result = [self._booking_for_user(self._booking(db, row["id"]), user) for row in ids]
                return 200, result

            if path == f"{API_PREFIX}/admin/bookings" and method == "GET":
                self._role(user, "admin")
                limit = self._limit(query)
                status_filter = query.get("status", [None])[0]
                if status_filter and status_filter not in {
                    "requested", "awaiting_customer_confirmation", "confirmed", "assigned", "cancelled", "rejected", "completed"
                }:
                    raise ApiError(400, "invalid_request", "status is not a valid booking status.")
                sql = "SELECT id FROM bookings"
                args: list[Any] = []
                if status_filter:
                    sql += " WHERE status = ?"
                    args.append(status_filter)
                sql += " ORDER BY created_at DESC LIMIT ?"
                args.append(limit)
                with self._connection() as db:
                    ids = db.execute(sql, args).fetchall()
                    result = [self._booking_for_user(self._booking(db, row["id"]), user) for row in ids]
                return 200, result
            match = re.fullmatch(re.escape(API_PREFIX) + r"/admin/bookings/([0-9a-f-]{36})/quote", path)
            if match and method == "POST":
                self._role(user, "admin")
                fare = require_integer(body.get("quoted_fare"), "quoted_fare", 1, 10000000)
                with self._transaction() as db:
                    row = self._booking(db, match.group(1))
                    if row["status"] != "requested":
                        raise ApiError(409, "invalid_booking_state", "Only a new booking request can be quoted.")
                    if not self._is_available(db, row["vehicle_id"], row["pickup_at"], row["dropoff_at"], row["id"]):
                        raise ApiError(409, "cab_unavailable", "This cab is no longer available for the requested time.")
                    db.execute(
                        "UPDATE bookings SET quoted_fare = ?, status = 'awaiting_customer_confirmation', updated_at = ? WHERE id = ?",
                        (fare, now_iso(), row["id"]),
                    )
                    self._notify(db, row["customer_id"], "booking_quoted", f"Booking {row['id']} is ready for your confirmation.", row["id"])
                    self._notify(db, row["vendor_id"], "booking_quoted", f"Admin quoted booking {row['id']}.", row["id"])
                    row = self._booking(db, row["id"])
                return 200, self._booking_for_user(row, user)
            match = re.fullmatch(re.escape(API_PREFIX) + r"/admin/bookings/([0-9a-f-]{36})/assign", path)
            if match and method == "POST":
                self._role(user, "admin")
                driver_id = required_text(body, "driver_id", 36)
                if not ID_RE.fullmatch(driver_id):
                    raise ApiError(400, "invalid_request", "driver_id must be a valid id.")
                with self._transaction() as db:
                    row = self._booking(db, match.group(1))
                    if row["status"] != "confirmed":
                        raise ApiError(409, "invalid_booking_state", "Only a customer-confirmed booking can be assigned.")
                    driver = db.execute(
                        "SELECT d.status, d.base_city, u.name FROM drivers d JOIN users u ON u.id = d.user_id WHERE d.user_id = ?",
                        (driver_id,),
                    ).fetchone()
                    if driver is None or driver["status"] != "approved":
                        raise ApiError(409, "driver_unavailable", "Driver is not approved or does not exist.")
                    conflict = db.execute(
                        """SELECT 1 FROM bookings WHERE driver_id = ? AND status IN (?, ?, ?, ?)
                           AND pickup_at < ? AND dropoff_at > ? LIMIT 1""",
                        (driver_id, *ACTIVE_BOOKING_STATUSES, row["dropoff_at"], row["pickup_at"]),
                    ).fetchone()
                    if conflict:
                        raise ApiError(409, "driver_unavailable", "Driver is already assigned during that time.")
                    db.execute(
                        "UPDATE bookings SET driver_id = ?, status = 'assigned', updated_at = ? WHERE id = ?",
                        (driver_id, now_iso(), row["id"]),
                    )
                    self._notify(db, driver_id, "booking_assigned", f"You have been assigned booking {row['id']}.", row["id"])
                    self._notify(db, row["customer_id"], "driver_assigned", f"A driver has been assigned to booking {row['id']}.", row["id"])
                    row = self._booking(db, row["id"])
                return 200, self._booking_for_user(row, user)
            match = re.fullmatch(re.escape(API_PREFIX) + r"/admin/bookings/([0-9a-f-]{36})/(reject|complete)", path)
            if match and method == "POST":
                self._role(user, "admin")
                action = match.group(2)
                with self._transaction() as db:
                    row = self._booking(db, match.group(1))
                    target = "rejected" if action == "reject" else "completed"
                    if action == "reject" and row["status"] != "requested":
                        raise ApiError(409, "invalid_booking_state", "Only a new booking request can be rejected.")
                    if action == "complete" and row["status"] != "assigned":
                        raise ApiError(409, "invalid_booking_state", "Only an assigned booking can be completed.")
                    db.execute("UPDATE bookings SET status = ?, updated_at = ? WHERE id = ?", (target, now_iso(), row["id"]))
                    self._notify(db, row["customer_id"], f"booking_{target}", f"Booking {row['id']} was {target}.", row["id"])
                    self._notify(db, row["vendor_id"], f"booking_{target}", f"Booking {row['id']} was {target}.", row["id"])
                    row = self._booking(db, row["id"])
                return 200, self._booking_for_user(row, user)

            if path == f"{API_PREFIX}/admin/vendors" and method == "GET":
                self._role(user, "admin")
                with self._connection() as db:
                    rows = db.execute(
                        """SELECT v.user_id, v.business_name, v.partner_type, v.base_city, v.status, v.created_at,
                                  u.name, u.email, u.phone FROM vendors v JOIN users u ON u.id = v.user_id
                           ORDER BY v.created_at DESC LIMIT ?""",
                        (self._limit(query),),
                    ).fetchall()
                return 200, [dict(row) for row in rows]
            if path == f"{API_PREFIX}/admin/drivers" and method == "GET":
                self._role(user, "admin")
                with self._connection() as db:
                    rows = db.execute(
                        """SELECT d.user_id, d.base_city, d.status, d.created_at, u.name, u.email, u.phone
                           FROM drivers d JOIN users u ON u.id = d.user_id ORDER BY d.created_at DESC LIMIT ?""",
                        (self._limit(query),),
                    ).fetchall()
                return 200, [dict(row) for row in rows]
            if path == f"{API_PREFIX}/admin/vehicles" and method == "GET":
                self._role(user, "admin")
                with self._connection() as db:
                    rows = db.execute(
                        """SELECT v.id, v.vendor_id, v.vehicle_class, v.make_model, v.registration_number,
                                  v.seats, v.rate_per_km, v.driver_allowance, v.status, v.created_at,
                                  p.business_name, p.base_city
                           FROM vehicles v JOIN vendors p ON p.user_id = v.vendor_id
                           ORDER BY v.created_at DESC LIMIT ?""",
                        (self._limit(query),),
                    ).fetchall()
                return 200, [dict(row) for row in rows]
            match = re.fullmatch(re.escape(API_PREFIX) + r"/admin/(vendors|drivers|vehicles)/([0-9a-f-]{36})/status", path)
            if match and method == "PATCH":
                self._role(user, "admin")
                resource, resource_id = match.groups()
                status = body.get("status")
                if not isinstance(status, str) or status not in STATUSES:
                    raise ApiError(400, "invalid_request", "status must be pending, approved, rejected, or suspended.")
                table, id_column = {"vendors": ("vendors", "user_id"), "drivers": ("drivers", "user_id"), "vehicles": ("vehicles", "id")}[resource]
                with self._transaction() as db:
                    result = db.execute(f"UPDATE {table} SET status = ? WHERE {id_column} = ?", (status, resource_id))
                    if not result.rowcount:
                        raise ApiError(404, "not_found", f"{resource[:-1].capitalize()} not found.")
                    row = db.execute(f"SELECT * FROM {table} WHERE {id_column} = ?", (resource_id,)).fetchone()
                return 200, dict(row)

            if path == f"{API_PREFIX}/contact" and method == "POST":
                name = required_text(body, "name", 120)
                email = require_email(required_text(body, "email", 254))
                phone = optional_text(body, "phone", 20)
                if phone:
                    require_phone(phone)
                message = required_text(body, "message", 4000)
                message_id = new_id()
                with self._transaction() as db:
                    db.execute(
                        "INSERT INTO contact_messages (id, name, email, phone, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (message_id, name, email, phone, message, now_iso()),
                    )
                    self._notify_admins(db, "contact_message", f"New contact message {message_id}.")
                return 201, {"id": message_id, "status": "received"}
            if path == f"{API_PREFIX}/admin/contact" and method == "GET":
                self._role(user, "admin")
                with self._connection() as db:
                    rows = db.execute(
                        "SELECT id, name, email, phone, message, created_at FROM contact_messages ORDER BY created_at DESC LIMIT ?",
                        (self._limit(query),),
                    ).fetchall()
                return 200, [dict(row) for row in rows]

        if path.startswith(f"{API_PREFIX}/"):
            raise ApiError(405, "method_not_allowed", "The HTTP method is not supported for this endpoint.")
        raise ApiError(404, "not_found", "Endpoint not found.")

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        origin = environ.get("HTTP_ORIGIN")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Date", formatdate(usegmt=True)),
        ]
        if origin:
            if origin not in self.allowed_origins:
                error = ApiError(403, "origin_not_allowed", "This origin is not allowed.")
                return self._respond(start_response, error.status, {"error": {"code": error.code, "message": error.message}}, headers)
            headers.extend([("Access-Control-Allow-Origin", origin), ("Vary", "Origin")])
        if method == "OPTIONS":
            headers.extend([
                ("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS"),
                ("Access-Control-Allow-Headers", "Authorization, Content-Type"),
                ("Access-Control-Max-Age", "600"),
            ])
            return self._respond(start_response, 204, None, headers)
        path = environ.get("PATH_INFO", "")
        if path == f"{API_PREFIX}/webhooks/whatsapp":
            if method == "GET":
                query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
                mode = query.get("hub.mode", [""])[0]
                token = query.get("hub.verify_token", [""])[0]
                challenge = query.get("hub.challenge", [""])[0]
                if not self.whatsapp_verify_token:
                    error = ApiError(503, "whatsapp_not_configured", "WhatsApp webhook verification is not configured.")
                    return self._respond(start_response, error.status, {"error": {"code": error.code, "message": error.message}}, headers)
                if mode != "subscribe" or not hmac.compare_digest(token, self.whatsapp_verify_token):
                    error = ApiError(403, "webhook_verification_failed", "WhatsApp webhook verification failed.")
                    return self._respond(start_response, error.status, {"error": {"code": error.code, "message": error.message}}, headers)
                headers[0] = ("Content-Type", "text/plain; charset=utf-8")
                body = challenge.encode("utf-8")
                headers.append(("Content-Length", str(len(body))))
                start_response("200 OK", headers)
                return [body]
            if method != "POST":
                error = ApiError(405, "method_not_allowed", "The HTTP method is not supported for this endpoint.")
                return self._respond(start_response, error.status, {"error": {"code": error.code, "message": error.message}}, headers)
            try:
                if not self.whatsapp_app_secret:
                    raise ApiError(503, "whatsapp_not_configured", "WhatsApp webhook signature verification is not configured.")
                if not environ.get("CONTENT_TYPE", "").startswith("application/json"):
                    raise ApiError(415, "unsupported_media_type", "Content-Type must be application/json.")
                try:
                    length = int(environ.get("CONTENT_LENGTH") or 0)
                except ValueError as exc:
                    raise ApiError(400, "invalid_request", "Content-Length must be a valid integer.") from exc
                if length < 0:
                    raise ApiError(400, "invalid_request", "Content-Length must not be negative.")
                if length > MAX_BODY_BYTES:
                    raise ApiError(413, "payload_too_large", "Webhook body must be at most 64 KB.")
                raw = environ["wsgi.input"].read(length)
                signature = environ.get("HTTP_X_HUB_SIGNATURE_256", "")
                expected = "sha256=" + hmac.new(
                    self.whatsapp_app_secret.encode("utf-8"), raw, hashlib.sha256
                ).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ApiError(401, "invalid_webhook_signature", "WhatsApp webhook signature is invalid.")
                self._whatsapp_webhook_post(raw)
                return self._respond(start_response, 200, {"data": {"received": True}}, headers)
            except ApiError as exc:
                return self._respond(start_response, exc.status, {"error": {"code": exc.code, "message": exc.message}}, headers)
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                return self._respond(
                    start_response,
                    400,
                    {"error": {"code": "invalid_json", "message": "Webhook body must contain valid JSON."}},
                    headers,
                )
        try:
            if method not in {"GET", "POST", "PATCH"}:
                raise ApiError(405, "method_not_allowed", "HTTP method is not supported.")
            content_type = environ.get("CONTENT_TYPE", "")
            if method in {"POST", "PATCH"} and environ.get("CONTENT_LENGTH", "0") != "0" and not content_type.startswith("application/json"):
                raise ApiError(415, "unsupported_media_type", "Content-Type must be application/json.")
            path = environ.get("PATH_INFO", "")
            query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
            body = self._json(environ) if method in {"POST", "PATCH"} else {}
            status, data = self._dispatch(method, path, query, body, environ)
            return self._respond(start_response, status, {"data": data}, headers)
        except ApiError as exc:
            return self._respond(start_response, exc.status, {"error": {"code": exc.code, "message": exc.message}}, headers)
        except sqlite3.IntegrityError as exc:
            message = "A record with that value already exists." if "UNIQUE" in str(exc).upper() else "The request conflicts with existing data."
            return self._respond(start_response, 409, {"error": {"code": "conflict", "message": message}}, headers)
        except sqlite3.Error as exc:
            print(f"Database operation failed: {exc}", file=sys.stderr)
            return self._respond(start_response, 500, {"error": {"code": "internal_error", "message": "The request could not be completed."}}, headers)

    @staticmethod
    def _respond(start_response: Any, status: int, payload: Any, headers: list[tuple[str, str]]) -> list[bytes]:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        phrase = {
            200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 401: "Unauthorized",
            403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
            413: "Payload Too Large", 415: "Unsupported Media Type", 500: "Internal Server Error",
        }.get(status, "Error")
        headers.append(("Content-Length", str(len(body))))
        start_response(f"{status} {phrase}", headers)
        return [body]


def create_app(
    database_path: str | os.PathLike[str] | None = None,
    admin_email: str | None = None,
    admin_password: str | None = None,
    allowed_origins: set[str] | None = None,
    whatsapp_verify_token: str | None = None,
    whatsapp_app_secret: str | None = None,
    whatsapp_access_token: str | None = None,
    whatsapp_phone_number_id: str | None = None,
    whatsapp_api_version: str | None = None,
) -> CabApi:
    path = database_path or os.environ.get(
        "NAMMAURU_DB", str(Path(__file__).resolve().parent / "nammaurucab.sqlite3")
    )
    origins = allowed_origins
    if origins is None and os.environ.get("NAMMAURU_CORS_ORIGINS"):
        origins = {origin.strip() for origin in os.environ["NAMMAURU_CORS_ORIGINS"].split(",") if origin.strip()}
    return CabApi(
        path,
        admin_email if admin_email is not None else os.environ.get("NAMMAURU_ADMIN_EMAIL"),
        admin_password if admin_password is not None else os.environ.get("NAMMAURU_ADMIN_PASSWORD"),
        origins,
        whatsapp_verify_token if whatsapp_verify_token is not None else os.environ.get("NAMMAURU_WHATSAPP_VERIFY_TOKEN"),
        whatsapp_app_secret if whatsapp_app_secret is not None else os.environ.get("NAMMAURU_WHATSAPP_APP_SECRET"),
        whatsapp_access_token if whatsapp_access_token is not None else os.environ.get("NAMMAURU_WHATSAPP_ACCESS_TOKEN"),
        whatsapp_phone_number_id if whatsapp_phone_number_id is not None else os.environ.get("NAMMAURU_WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_api_version if whatsapp_api_version is not None else os.environ.get("NAMMAURU_WHATSAPP_API_VERSION"),
    )


def main() -> None:
    host = os.environ.get("NAMMAURU_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("NAMMAURU_PORT", "8080")))
    app = create_app()
    with make_server(host, port, app) as server:
        print(f"Namma Uru Cab API listening on http://{host}:{port}{API_PREFIX}")
        server.serve_forever()


if __name__ == "__main__":
    main()
