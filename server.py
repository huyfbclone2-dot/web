import argparse
import os
import hashlib
import json
import secrets
import shutil
import subprocess
import threading
import urllib.request
import xml.etree.ElementTree as ET
from http.cookies import SimpleCookie
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image

from decode_activitylist import decode_activity_bytes
from swf_extract_images import decode_activity_asset, decompress_swf_body, extract_images


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
CACHE_DIR = ROOT / "cache"
CACHE_META_VERSION = 9
MAX_PREVIEW_IMAGES = 3
CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
QR_IMAGE = ROOT / "qr.jpg"
AUTH_CONFIG = ROOT / "auth.json"
SESSION_COOKIE_NAME = "activity_dev_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7
FLASH_PROXY_ALLOWED_HOST_SUFFIXES = ("truykich.vn",)
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "giahuy2712"


def resolve_ffdec_exe() -> Path:
    env_path = os.environ.get("FFDEC_BIN", "").strip()
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.exists():
            return candidate

    candidates = [
        ROOT / "tools" / "ffdec_full" / "ffdec-cli.exe",
        ROOT / "tools" / "ffdec_full" / "ffdec.sh",
        ROOT / "tools" / "ffdec_full" / "ffdec",
        ROOT.parent / "tools" / "ffdec_full" / "ffdec-cli.exe",
        ROOT.parent / "tools" / "ffdec_full" / "ffdec.sh",
        ROOT.parent / "tools" / "ffdec_full" / "ffdec",
        Path("/opt/ffdec/ffdec.sh"),
        Path("/opt/ffdec/ffdec"),
        Path("/opt/ffdec/ffdec-cli.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


FFDEC_EXE = resolve_ffdec_exe()


def safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    cleaned = cleaned.strip("._-")
    return cleaned or "item"


def select_large_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not images:
        return []

    max_area = max(image["area"] for image in images)
    min_area = max(120000, int(max_area * 0.28))
    kept = [
        image
        for image in images
        if image["area"] >= min_area and image["width"] >= 420 and image["height"] >= 220
    ]

    if not kept:
        kept = [max(images, key=lambda image: (image["area"], image["width"], image["height"], image["sizeBytes"]))]

    kept.sort(key=lambda image: (image["area"], image["width"], image["height"], image["sizeBytes"]), reverse=True)
    return kept


def image_info(image_path: Path) -> dict[str, Any]:
    with Image.open(image_path) as image:
        width, height = image.size
    stat = image_path.stat()
    return {
        "name": image_path.name,
        "width": width,
        "height": height,
        "area": width * height,
        "sizeBytes": stat.st_size,
    }


def apply_qr_overlay(image_path: Path):
    if not QR_IMAGE.exists() or not image_path.exists():
        return

    with Image.open(image_path).convert("RGBA") as base_image, Image.open(QR_IMAGE).convert("RGBA") as qr_image:
        margin = max(10, min(base_image.width, base_image.height) // 40)
        target_width = max(90, min(180, base_image.width // 5))
        max_width = max(1, base_image.width - margin * 2)
        max_height = max(1, base_image.height - margin * 2)
        target_width = min(target_width, max_width)
        target_height = max(1, int(target_width * qr_image.height / qr_image.width))

        if target_height > max_height:
            target_height = max_height
            target_width = max(1, int(target_height * qr_image.width / qr_image.height))

        qr_resized = qr_image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        shift_left = max(8, int(qr_resized.width * 0.14))
        x = max(margin, base_image.width - qr_resized.width - margin - shift_left)
        y = margin
        base_image.paste(qr_resized, (x, y), qr_resized)
        base_image.save(image_path, format="PNG")


def copy_image(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def load_auth_config() -> dict[str, Any]:
    default = {
        "version": 2,
        "accounts": [
            {
                "username": DEFAULT_ADMIN_USERNAME,
                "password": DEFAULT_ADMIN_PASSWORD,
                "role": "admin",
            }
        ],
    }
    if not AUTH_CONFIG.exists():
        AUTH_CONFIG.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
        return default

    raw = json.loads(AUTH_CONFIG.read_text(encoding="utf-8"))
    accounts: list[dict[str, str]] = []

    if isinstance(raw, dict) and isinstance(raw.get("accounts"), list):
        for item in raw["accounts"]:
            if not isinstance(item, dict):
                continue
            username = str(item.get("username", "")).strip()
            password = str(item.get("password", "")).strip()
            role = "admin" if str(item.get("role", "user")).strip().lower() == "admin" else "user"
            if not username or not password:
                continue
            if any(existing["username"].lower() == username.lower() for existing in accounts):
                continue
            accounts.append(
                {
                    "username": username,
                    "password": password,
                    "role": role,
                }
            )
    elif isinstance(raw, dict):
        username = str(raw.get("username", DEFAULT_ADMIN_USERNAME)).strip() or DEFAULT_ADMIN_USERNAME
        password = str(raw.get("password", DEFAULT_ADMIN_PASSWORD)).strip() or DEFAULT_ADMIN_PASSWORD
        accounts.append(
            {
                "username": username,
                "password": password,
                "role": "admin",
            }
        )

    if not accounts:
        accounts = list(default["accounts"])
    if not any(account["role"] == "admin" for account in accounts):
        accounts[0]["role"] = "admin"

    normalized = {
        "version": 2,
        "accounts": accounts,
    }
    if raw != normalized:
        AUTH_CONFIG.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def normalize_remote_swf_url(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        raise ValueError("Missing SWF url")

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("SWF url must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("SWF url is missing a host")

    hostname = (parsed.hostname or "").lower()
    if not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in FLASH_PROXY_ALLOWED_HOST_SUFFIXES):
        raise ValueError("Only Truy Kich CDN hosts are allowed")
    if ".swf" not in parsed.path.lower():
        raise ValueError("URL must point to a .swf file")

    return value


def prepare_browser_swf(raw_data: bytes) -> bytes:
    candidates = [raw_data]
    decoded = decode_activity_asset(raw_data)
    if decoded != raw_data:
        candidates.append(decoded)

    last_error: Exception | None = None
    for candidate in candidates:
        signature = candidate[:3]
        try:
            if signature == b"FWS":
                return candidate
            if signature in (b"CWS", b"ZWS"):
                decompress_swf_body(candidate)
                return candidate
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise ValueError(f"Could not prepare SWF for browser: {last_error}")
    return raw_data


def load_xml_root(path: Path) -> ET.Element:
    text = path.read_text(encoding="utf-8", errors="replace")
    return load_xml_root_from_text(text)


def load_xml_root_from_text(text: str) -> ET.Element:
    root_start = text.find("<root>")
    if root_start > 0:
        text = text[root_start:]
    return ET.fromstring(text)


def collect_time_ranges(child: ET.Element) -> tuple[str, str, list[dict[str, str]]]:
    ranges: list[tuple[str, str]] = []
    for node in child.iter():
        start_time = node.attrib.get("startTime")
        end_time = node.attrib.get("endTime")
        if start_time or end_time:
            ranges.append((start_time or "", end_time or ""))

    deduped = sorted(set(ranges))
    if not deduped:
        return "", "", []

    starts = [start for start, _ in deduped if start]
    ends = [end for _, end in deduped if end]
    start_min = min(starts) if starts else ""
    end_max = max(ends) if ends else ""
    pairs = [{"start": start, "end": end} for start, end in deduped]
    return start_min, end_max, pairs


def build_config_map(config_path: Path) -> dict[str, dict[str, str]]:
    root = load_xml_root(config_path)
    activityres = root.find("activityres")
    if activityres is None:
        raise ValueError("config.xml does not contain <activityres>.")

    config_map: dict[str, dict[str, str]] = {}
    for node in activityres:
        if node.tag.startswith("act"):
            config_map[node.tag[3:]] = dict(node.attrib)
    return config_map


def build_catalog(
    activitylist_path: Path,
    config_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    root = load_xml_root(activitylist_path)
    config_map = build_config_map(config_path)
    catalog: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}

    for child in root.findall("child"):
        activity_id = (child.findtext("id") or "").strip()
        url = (child.findtext("url") or "").strip()
        btn_label = (child.findtext("btnIndex") or "").strip()
        title = (child.findtext("title") or "").strip()
        description = (child.findtext("description") or "").strip()
        start_time, end_time, ranges = collect_time_ranges(child)
        config_entry = config_map.get(url, {})

        item: dict[str, Any] = {
            "id": activity_id,
            "url": url,
            "btnLabel": btn_label,
            "title": title,
            "description": description,
            "activity": (child.findtext("activity") or "").strip(),
            "sortIndex": (child.findtext("sortIndex") or "").strip(),
            "newIcon": (child.findtext("newIcon") or "").strip(),
            "isTabShow": (child.findtext("isTabShow") or "").strip(),
            "poolId": child.find("pool").attrib.get("id", "") if child.find("pool") is not None else "",
            "timeStart": start_time,
            "timeEnd": end_time,
            "timeRanges": ranges,
            "assetRelativeUrl": config_entry.get("url", ""),
            "assetType": config_entry.get("type", ""),
            "assetSize": config_entry.get("size", ""),
            "assetMd5": config_entry.get("md5", ""),
        }
        catalog.append(item)
        if activity_id:
            by_id[activity_id] = item

    catalog.sort(key=lambda row: int(row["id"]) if row["id"].isdigit() else -1, reverse=True)
    return catalog, by_id


@dataclass(frozen=True)
class ServerConfig:
    server_id: str
    label: str
    base_url: str


class ActivityBrowser:
    def __init__(self, activitylist_path: Path, config_path: Path, autoload_local: bool = False):
        self.servers = [
            ServerConfig("s2", "HCM S2", "http://hcm.cdn.truykich.vn/s2/"),
        ]
        self.server_map = {server.server_id: server for server in self.servers}
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._auth_lock = threading.Lock()
        self.sessions: dict[str, str] = {}
        self.auth_config = load_auth_config()
        self.catalog: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        self.current_info: dict[str, Any] = {
            "source": "none",
            "resConfigName": "",
            "configUrl": "",
            "activityListUrl": "",
            "serverId": "s2",
        }
        if autoload_local and activitylist_path.exists() and config_path.exists():
            catalog, by_id = build_catalog(activitylist_path, config_path)
            self._set_catalog(
                catalog,
                by_id,
                {
                    "source": "local",
                    "resConfigName": config_path.name,
                    "configUrl": str(config_path.resolve()),
                    "activityListUrl": str(activitylist_path.resolve()),
                    "serverId": "local",
                },
            )

    def reset(self):
        self._set_catalog(
            [],
            {},
            {
                "source": "none",
                "resConfigName": "",
                "configUrl": "",
                "activityListUrl": "",
                "serverId": "s2",
            },
        )

    def _find_account_index(self, username: str) -> int:
        target = username.strip().lower()
        for index, account in enumerate(self.auth_config.get("accounts", [])):
            if account["username"].lower() == target:
                return index
        return -1

    def _get_account(self, username: str) -> dict[str, str] | None:
        index = self._find_account_index(username)
        if index < 0:
            return None
        return dict(self.auth_config["accounts"][index])

    def _account_public(self, account: dict[str, str], current_username: str = "") -> dict[str, Any]:
        return {
            "username": account["username"],
            "role": account.get("role", "user"),
            "isCurrent": bool(current_username and account["username"].lower() == current_username.lower()),
        }

    def _save_auth_config(self):
        AUTH_CONFIG.write_text(json.dumps(self.auth_config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _revoke_user_sessions(self, username: str):
        target = username.strip().lower()
        with self._session_lock:
            stale_tokens = [token for token, session_user in self.sessions.items() if session_user.lower() == target]
            for token in stale_tokens:
                self.sessions.pop(token, None)

    def _require_admin(self, token: str) -> dict[str, Any]:
        status = self.auth_status(token)
        if not status.get("authenticated"):
            raise PermissionError("Bạn phải đăng nhập admin trước đã.")
        if not status.get("canManageAccounts"):
            raise PermissionError("Tài khoản này không có quyền quản lý user.")
        return status

    def auth_status(self, token: str = "") -> dict[str, Any]:
        username = ""
        authenticated = False
        role = "guest"
        if token:
            with self._session_lock:
                username = self.sessions.get(token, "")
            account = self._get_account(username)
            authenticated = bool(account)
            if account:
                username = account["username"]
                role = account.get("role", "user")
            elif username:
                self.logout(token)
        return {
            "authenticated": authenticated,
            "username": username if authenticated else "",
            "role": role,
            "canManageAccounts": role == "admin",
        }

    def login(self, username: str, password: str) -> tuple[bool, str, dict[str, Any]]:
        account = self._get_account(username)
        if account and password == account["password"]:
            token = secrets.token_urlsafe(32)
            with self._session_lock:
                self.sessions[token] = account["username"]
            return True, token, self.auth_status(token)
        return False, "", {"authenticated": False, "username": "", "role": "guest", "canManageAccounts": False}

    def list_accounts(self, token: str) -> dict[str, Any]:
        status = self._require_admin(token)
        current_username = status["username"]
        with self._auth_lock:
            accounts = [self._account_public(account, current_username) for account in self.auth_config.get("accounts", [])]
        accounts.sort(key=lambda account: (0 if account["role"] == "admin" else 1, account["username"].lower()))
        return {"accounts": accounts}

    def save_account(self, token: str, username: str, password: str, role: str) -> dict[str, Any]:
        requester = self._require_admin(token)
        normalized_username = username.strip()
        normalized_password = password.strip()
        normalized_role = "admin" if role.strip().lower() == "admin" else "user"

        if not normalized_username:
            raise ValueError("Thiếu tên tài khoản.")
        if len(normalized_username) < 3:
            raise ValueError("Tài khoản phải có ít nhất 3 ký tự.")

        with self._auth_lock:
            index = self._find_account_index(normalized_username)
            created = index < 0
            if created and not normalized_password:
                raise ValueError("Tài khoản mới phải có mật khẩu.")

            accounts = [dict(account) for account in self.auth_config.get("accounts", [])]
            if created:
                accounts.append(
                    {
                        "username": normalized_username,
                        "password": normalized_password,
                        "role": normalized_role,
                    }
                )
            else:
                current = dict(accounts[index])
                current["role"] = normalized_role
                if normalized_password:
                    current["password"] = normalized_password
                accounts[index] = current

            if not any(account["role"] == "admin" for account in accounts):
                raise ValueError("Phải luôn còn ít nhất 1 admin.")

            self.auth_config = {
                "version": 2,
                "accounts": accounts,
            }
            self._save_auth_config()

        if not created and normalized_password and normalized_username.lower() != requester["username"].lower():
            self._revoke_user_sessions(normalized_username)
        account = self._get_account(normalized_username) or {
            "username": normalized_username,
            "role": normalized_role,
        }
        return {
            "ok": True,
            "created": created,
            "account": self._account_public(account),
        }

    def delete_account(self, token: str, username: str) -> dict[str, Any]:
        status = self._require_admin(token)
        normalized_username = username.strip()
        if not normalized_username:
            raise ValueError("Thiếu tên tài khoản cần xóa.")
        if normalized_username.lower() == status["username"].lower():
            raise ValueError("Không thể tự xóa tài khoản đang đăng nhập.")

        with self._auth_lock:
            index = self._find_account_index(normalized_username)
            if index < 0:
                raise KeyError(f"Unknown account: {normalized_username}")

            accounts = [dict(account) for account in self.auth_config.get("accounts", [])]
            target = accounts[index]
            if target.get("role") == "admin":
                admin_count = sum(1 for account in accounts if account.get("role") == "admin")
                if admin_count <= 1:
                    raise ValueError("Không thể xóa admin cuối cùng.")

            accounts.pop(index)
            self.auth_config = {
                "version": 2,
                "accounts": accounts,
            }
            self._save_auth_config()

        self._revoke_user_sessions(normalized_username)
        return {"ok": True, "deleted": normalized_username}

    def logout(self, token: str):
        if not token:
            return
        with self._session_lock:
            self.sessions.pop(token, None)

    def _set_catalog(self, catalog: list[dict[str, Any]], by_id: dict[str, dict[str, Any]], info: dict[str, Any]):
        with self._state_lock:
            self.catalog = catalog
            self.by_id = by_id
            self.current_info = info

    def slug_for_url(self, url: str) -> str:
        digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        return f"{safe_name(url)}_{digest}"

    def get_activity(self, activity_id: str) -> dict[str, Any]:
        with self._state_lock:
            item = self.by_id.get(activity_id)
        if item is None:
            raise KeyError(f"Unknown activity id: {activity_id}")
        return item

    def get_activity_with_recovery(self, activity_id: str, server_id: str, res_config_name: str = "") -> dict[str, Any]:
        try:
            return self.get_activity(activity_id)
        except KeyError:
            if not res_config_name:
                raise
            self.load_remote_res_config(server_id, res_config_name)
            return self.get_activity(activity_id)

    def list_activities(self) -> dict[str, Any]:
        with self._state_lock:
            catalog = list(self.catalog)
            current = dict(self.current_info)
        rows = [self._attach_cache_summary(item) for item in catalog]
        return {
            "total": len(rows),
            "current": current,
            "servers": [
                {
                    "id": server.server_id,
                    "label": server.label,
                    "baseUrl": server.base_url,
                }
                for server in self.servers
            ],
            "activities": rows,
        }

    def _cache_paths(self, activity: dict[str, Any], server_id: str) -> dict[str, Path]:
        slug = self.slug_for_url(activity.get("assetRelativeUrl") or activity["url"] or activity["id"])
        return {
            "asset_dir": CACHE_DIR / "assets" / server_id,
            "image_dir": CACHE_DIR / "public_images" / server_id / slug,
            "private_image_dir": CACHE_DIR / "private_images" / server_id / slug,
            "decoded_dir": CACHE_DIR / "decoded" / server_id,
            "decoded_path": CACHE_DIR / "decoded" / server_id / f"{slug}.swf",
            "render_dir": CACHE_DIR / "public_renders" / server_id / slug,
            "private_render_dir": CACHE_DIR / "private_renders" / server_id / slug,
            "meta_dir": CACHE_DIR / "meta" / server_id,
            "asset_path": CACHE_DIR / "assets" / server_id / f"{slug}.swf.enc",
            "meta_path": CACHE_DIR / "meta" / server_id / f"{slug}.json",
        }

    def _meta_to_public(self, meta: dict[str, Any], authenticated: bool = False) -> dict[str, Any]:
        images = [
            {
                "path": image["privatePath"] if authenticated else image["publicPath"],
                "url": (
                    f"/cache-private/{image['privatePath']}"
                    if authenticated
                    else f"/cache/{image['publicPath']}"
                ),
                "source": image.get("source", "bitmap"),
                "width": image["width"],
                "height": image["height"],
                "area": image["area"],
                "sizeBytes": image["sizeBytes"],
                "name": image["name"],
            }
            for image in meta.get("images", [])
        ]
        preview_path = meta.get("previewPrivatePath" if authenticated else "previewPublicPath", "")
        return {
            "decodeMode": meta.get("decodeMode", ""),
            "previewSource": meta.get("previewSource", ""),
            "imageCount": meta.get("imageCount", 0),
            "previewPath": preview_path,
            "previewUrl": (
                f"/cache-private/{preview_path}"
                if authenticated and preview_path
                else (f"/cache/{preview_path}" if preview_path else "")
            ),
            "images": images,
        }

    def _read_meta(self, meta_path: Path) -> dict[str, Any] | None:
        if not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("version") != CACHE_META_VERSION:
            return None
        return meta

    def _attach_cache_summary(self, activity: dict[str, Any]) -> dict[str, Any]:
        item = dict(activity)
        cached: dict[str, Any] = {}
        for server in self.servers:
            meta = self._read_meta(self._cache_paths(activity, server.server_id)["meta_path"])
            if meta:
                cached[server.server_id] = {
                    "imageCount": meta.get("imageCount", 0),
                    "previewUrl": f"/cache/{meta['previewPublicPath']}" if meta.get("previewPublicPath") else "",
                }
        item["cached"] = cached
        return item

    def load_remote_res_config(self, server_id: str, res_config_name: str) -> dict[str, Any]:
        server = self.server_map.get(server_id)
        if server is None:
            raise KeyError(f"Unknown server: {server_id}")

        file_name = Path(res_config_name).name.strip()
        if not file_name:
            raise ValueError("Missing res_config filename")
        if not file_name.lower().endswith(".xml"):
            raise ValueError("res_config filename must end with .xml")

        session_dir = CACHE_DIR / "sessions" / server_id / safe_name(file_name)
        session_dir.mkdir(parents=True, exist_ok=True)
        config_path = session_dir / file_name
        activity_raw_path = session_dir / "activityList.bin"
        activity_xml_path = session_dir / "activitylist.xml"

        config_url = server.base_url.rstrip("/") + "/config/" + file_name
        with urllib.request.urlopen(config_url) as response:
            config_text = response.read().decode("utf-8", "replace")
        config_path.write_text(config_text, encoding="utf-8")

        root = load_xml_root_from_text(config_text)
        activity_node = root.find(".//activityListXML")
        if activity_node is None or "url" not in activity_node.attrib:
            raise ValueError("res_config does not contain activityListXML")

        activity_list_url = activity_node.attrib["url"]
        full_activity_url = server.base_url.rstrip("/") + "/" + activity_list_url.lstrip("/")
        with urllib.request.urlopen(full_activity_url) as response:
            activity_bytes = response.read()
        activity_raw_path.write_bytes(activity_bytes)
        activity_xml_path.write_bytes(decode_activity_bytes(activity_bytes))

        catalog, by_id = build_catalog(activity_xml_path, config_path)
        self._set_catalog(
            catalog,
            by_id,
            {
                "source": "remote",
                "resConfigName": file_name,
                "configUrl": config_url,
                "activityListUrl": full_activity_url,
                "serverId": server_id,
            },
        )
        return self.list_activities()

    def _render_frame(self, decoded_path: Path, render_dir: Path) -> Path | None:
        if not FFDEC_EXE.exists():
            return None

        if render_dir.exists():
            shutil.rmtree(render_dir)
        render_dir.mkdir(parents=True, exist_ok=True)

        command = [
            str(FFDEC_EXE),
            "-cli",
            "-format",
            "frame:png",
            "-select",
            "1",
            "-export",
            "frame",
            str(render_dir),
            str(decoded_path),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError(f"FFDec frame export failed: {completed.stdout.strip()}")

        direct = render_dir / "1.png"
        if direct.exists():
            return direct

        png_files = sorted(render_dir.rglob("*.png"))
        return png_files[0] if png_files else None

    def ensure_images(self, activity_id: str, server_id: str) -> dict[str, Any]:
        activity = self.get_activity(activity_id)
        server = self.server_map.get(server_id)
        if server is None:
            raise KeyError(f"Unknown server: {server_id}")
        if not activity.get("assetRelativeUrl"):
            raise ValueError(f"Activity {activity_id} does not have an asset in config.xml")

        paths = self._cache_paths(activity, server_id)
        meta = self._read_meta(paths["meta_path"])
        if meta:
            return meta

        with self._lock:
            meta = self._read_meta(paths["meta_path"])
            if meta:
                return meta

            paths["asset_dir"].mkdir(parents=True, exist_ok=True)
            paths["decoded_dir"].mkdir(parents=True, exist_ok=True)
            paths["meta_dir"].mkdir(parents=True, exist_ok=True)
            paths["image_dir"].mkdir(parents=True, exist_ok=True)
            paths["private_image_dir"].mkdir(parents=True, exist_ok=True)

            if not paths["asset_path"].exists():
                asset_url = server.base_url.rstrip("/") + "/" + activity["assetRelativeUrl"]
                with urllib.request.urlopen(asset_url) as response:
                    paths["asset_path"].write_bytes(response.read())

            paths["decoded_path"].write_bytes(decode_activity_asset(paths["asset_path"].read_bytes()))

            render_entry = None
            try:
                render_path = self._render_frame(paths["decoded_path"], paths["private_render_dir"])
            except Exception:
                render_path = None

            if render_path is not None and render_path.exists():
                if paths["render_dir"].exists():
                    shutil.rmtree(paths["render_dir"])
                paths["render_dir"].mkdir(parents=True, exist_ok=True)
                public_render_path = paths["render_dir"] / render_path.name
                copy_image(render_path, public_render_path)
                apply_qr_overlay(public_render_path)
                private_render_rel = render_path.resolve().relative_to(CACHE_DIR.resolve()).as_posix()
                public_render_rel = public_render_path.resolve().relative_to(CACHE_DIR.resolve()).as_posix()
                render_entry = {
                    "privatePath": private_render_rel,
                    "publicPath": public_render_rel,
                    "source": "frame",
                    **image_info(render_path),
                }

            if paths["image_dir"].exists():
                shutil.rmtree(paths["image_dir"])
            if paths["private_image_dir"].exists():
                shutil.rmtree(paths["private_image_dir"])
            paths["image_dir"].mkdir(parents=True, exist_ok=True)
            paths["private_image_dir"].mkdir(parents=True, exist_ok=True)

            decode_mode, saved = extract_images(paths["asset_path"], paths["private_image_dir"])
            images = []
            for image_path in saved:
                images.append(
                    {
                        "privatePath": image_path.resolve().relative_to(CACHE_DIR.resolve()).as_posix(),
                        "source": "bitmap",
                        **image_info(image_path),
                    }
                )

            kept = select_large_images(images)
            kept_paths = {image["privatePath"] for image in kept}
            for image in images:
                private_target = CACHE_DIR / image["privatePath"]
                if image["privatePath"] not in kept_paths:
                    private_target.unlink(missing_ok=True)
                    continue
                public_target = paths["image_dir"] / private_target.name
                copy_image(private_target, public_target)
                apply_qr_overlay(public_target)
                image["publicPath"] = public_target.resolve().relative_to(CACHE_DIR.resolve()).as_posix()

            kept = [image for image in kept if image.get("publicPath")]
            combined = (([render_entry] if render_entry else []) + kept)[:MAX_PREVIEW_IMAGES]
            preview_public_path = render_entry["publicPath"] if render_entry else (kept[0]["publicPath"] if kept else "")
            preview_private_path = render_entry["privatePath"] if render_entry else (kept[0]["privatePath"] if kept else "")
            meta = {
                "version": CACHE_META_VERSION,
                "decodeMode": decode_mode,
                "previewSource": render_entry["source"] if render_entry else (kept[0]["source"] if kept else ""),
                "imageCount": len(combined),
                "previewPublicPath": preview_public_path,
                "previewPrivatePath": preview_private_path,
                "images": combined,
            }
            paths["meta_path"].write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return meta


class ActivityHandler(BaseHTTPRequestHandler):
    browser: ActivityBrowser

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/auth/login":
                payload = self._read_json_body()
                username = str(payload.get("username", ""))
                password = str(payload.get("password", ""))
                ok, token, auth_payload = self.browser.login(username, password)
                if not ok:
                    self._json({"error": "Sai tài khoản hoặc mật khẩu"}, status=HTTPStatus.UNAUTHORIZED)
                    return
                self._json(
                    {
                        "ok": True,
                        **auth_payload,
                    },
                    cookie_value=token,
                )
                return
            if path == "/api/auth/logout":
                self.browser.logout(self._session_token())
                self._json(
                    {
                        "ok": True,
                        "authenticated": False,
                        "username": "",
                        "role": "guest",
                        "canManageAccounts": False,
                    },
                    clear_cookie=True,
                )
                return
            if path == "/api/auth/accounts/save":
                payload = self._read_json_body()
                self._json(
                    self.browser.save_account(
                        self._session_token(),
                        str(payload.get("username", "")),
                        str(payload.get("password", "")),
                        str(payload.get("role", "user")),
                    )
                )
                return
            if path == "/api/auth/accounts/delete":
                payload = self._read_json_body()
                self._json(
                    self.browser.delete_account(
                        self._session_token(),
                        str(payload.get("username", "")),
                    )
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except CLIENT_DISCONNECT_ERRORS:
            return
        except PermissionError as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
        except Exception as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/auth/status":
                self._json(self.browser.auth_status(self._session_token()))
                return
            if path == "/api/auth/accounts":
                self._json(self.browser.list_accounts(self._session_token()))
                return
            if path == "/api/reset":
                self.browser.reset()
                self._json(self.browser.list_activities())
                return
            if path == "/api/catalog":
                self._json(self.browser.list_activities())
                return
            if path == "/api/load-res-config":
                server_id = query.get("server", ["s2"])[0]
                res_config_name = query.get("name", [""])[0]
                if not res_config_name:
                    self._json({"error": "Missing res_config name"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._json(self.browser.load_remote_res_config(server_id, res_config_name))
                return
            if path == "/api/detail":
                activity_id = query.get("id", [""])[0]
                server_id = query.get("server", ["s2"])[0]
                res_config_name = query.get("resConfigName", [""])[0]
                if not activity_id:
                    self._json({"error": "Missing id"}, status=HTTPStatus.BAD_REQUEST)
                    return
                activity = self.browser.get_activity_with_recovery(activity_id, server_id, res_config_name)
                images_meta = self.browser.ensure_images(activity_id, server_id)
                images = self.browser._meta_to_public(
                    images_meta,
                    authenticated=self.browser.auth_status(self._session_token())["authenticated"],
                )
                payload = dict(activity)
                payload["images"] = images
                self._json(payload)
                return
            if path == "/api/proxy-swf":
                remote_url = normalize_remote_swf_url(query.get("url", [""])[0])
                request = urllib.request.Request(
                    remote_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 FlashViewer/1.0",
                        "Referer": "http://127.0.0.1/",
                    },
                )
                with urllib.request.urlopen(request) as response:
                    body = prepare_browser_swf(response.read())
                self._send_bytes(body, "application/x-shockwave-flash")
                return
            if path.startswith("/cache-private/"):
                if not self.browser.auth_status(self._session_token())["authenticated"]:
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                relative = path.removeprefix("/cache-private/")
                target = (CACHE_DIR / relative).resolve()
                if not str(target).startswith(str(CACHE_DIR.resolve())) or not target.exists():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_file(target)
                return
            if path.startswith("/cache/"):
                relative = path.removeprefix("/cache/")
                target = (CACHE_DIR / relative).resolve()
                if not str(target).startswith(str(CACHE_DIR.resolve())) or not target.exists():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_file(target)
                return

            target = WEB_DIR / ("index.html" if path in ("", "/") else path.lstrip("/"))
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_file(target)
        except CLIENT_DISCONNECT_ERRORS:
            return
        except PermissionError as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
        except KeyError as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args):
        return

    def _session_token(self) -> str:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return ""
        cookie = SimpleCookie()
        cookie.load(raw)
        morsel = cookie.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else ""

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
        cookie_value: str = "",
        clear_cookie: bool = False,
    ):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if cookie_value:
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE_NAME}={cookie_value}; Path=/; Max-Age={SESSION_MAX_AGE}; HttpOnly; SameSite=Lax",
                )
            elif clear_cookie:
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
                )
            self.end_headers()
            self.wfile.write(body)
        except CLIENT_DISCONNECT_ERRORS:
            return

    def _send_file(self, target: Path):
        suffix_map = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".swf": "application/x-shockwave-flash",
        }
        body = target.read_bytes()
        content_type = suffix_map.get(target.suffix.lower(), "application/octet-stream")
        self._send_bytes(body, content_type)

    def _send_bytes(self, body: bytes, content_type: str):
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except CLIENT_DISCONNECT_ERRORS:
            return


def main():
    parser = argparse.ArgumentParser(description="Serve a local activity browser for activitylist.xml")
    parser.add_argument("--activitylist", type=Path, default=ROOT / "activitylist.xml")
    parser.add_argument("--config", type=Path, default=ROOT / "config.xml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--autoload-local",
        action="store_true",
        help="Load local activitylist.xml/config.xml immediately on startup",
    )
    args = parser.parse_args()

    browser = ActivityBrowser(args.activitylist, args.config, autoload_local=args.autoload_local)
    ActivityHandler.browser = browser
    server = ThreadingHTTPServer((args.host, args.port), ActivityHandler)
    print(f"[ok] Activity browser listening at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
