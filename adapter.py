from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

try:
    import aiohttp
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by Hermes at runtime
    aiohttp = None  # type: ignore[assignment]
    web = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_image_from_url,
    resolve_channel_prompt,
)

logger = logging.getLogger(__name__)


PLATFORM_NAME = "napcat"
MAX_MESSAGE_LENGTH = 4500
DEFAULT_REVERSE_HOST = "127.0.0.1"
DEFAULT_REVERSE_PORT = 6099
DEFAULT_REVERSE_PATH = "/onebot/v11/ws"
DEFAULT_ACTION_TIMEOUT = 30.0
DEFAULT_RECONNECT_BASE = 2.0
DEFAULT_RECONNECT_MAX = 30.0

_CQ_RE = re.compile(r"\[CQ:(?P<type>[A-Za-z0-9_-]+)(?P<attrs>(?:,[^\]]*)?)\]")


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _csv_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    raw = str(value).strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _coerce_onebot_id(value: Any) -> int | str:
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def _normalize_reverse_path(path: Any) -> str:
    normalized = _string(path, DEFAULT_REVERSE_PATH)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _normalize_chat_id(value: Any, default_kind: str = "group") -> str:
    raw = _string(value)
    if not raw:
        return raw
    lower = raw.lower()
    for prefix in ("group:", "private:", "dm:", "user:", "g:", "u:"):
        if lower.startswith(prefix):
            kind, _, ident = raw.partition(":")
            normalized_kind = {
                "dm": "private",
                "user": "private",
                "u": "private",
                "g": "group",
            }.get(kind.lower(), kind.lower())
            return f"{normalized_kind}:{ident.strip()}"
    kind = "private" if str(default_kind).lower() in {"private", "dm", "user"} else "group"
    return f"{kind}:{raw}"


def _parse_chat_id(chat_id: Any, default_kind: str = "group") -> tuple[str, str]:
    normalized = _normalize_chat_id(chat_id, default_kind=default_kind)
    kind, sep, ident = normalized.partition(":")
    if not sep or not ident:
        return default_kind, normalized
    if kind in {"dm", "user", "u"}:
        return "private", ident
    if kind in {"g"}:
        return "group", ident
    if kind not in {"private", "group"}:
        return default_kind, ident
    return kind, ident


def _unescape_cq(value: str) -> str:
    return (
        value.replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#44;", ",")
        .replace("&amp;", "&")
    )


def _parse_cq_attrs(raw_attrs: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    raw = raw_attrs.lstrip(",")
    if not raw:
        return attrs
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        attrs[key.strip()] = _unescape_cq(value.strip())
    return attrs


def _auth_headers(token: str) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _url_with_access_token(url: str, token: str) -> str:
    if not token:
        return url
    parsed = urlsplit(url)
    query = parsed.query
    extra = urlencode({"access_token": token})
    query = f"{query}&{extra}" if query else extra
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _response_ok(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status", "")).lower()
    retcode = payload.get("retcode")
    if status == "ok":
        return True
    return retcode in {0, "0", None} and "data" in payload and status not in {"failed", "async"}


def _response_error(payload: dict[str, Any]) -> str:
    for key in ("wording", "message", "msg", "error"):
        value = payload.get(key)
        if value:
            return str(value)
    retcode = payload.get("retcode")
    if retcode is not None:
        return f"OneBot action failed with retcode {retcode}"
    return "OneBot action failed"


async def _call_http_action(
    http_url: str,
    token: str,
    action: str,
    params: dict[str, Any],
    *,
    timeout: float = DEFAULT_ACTION_TIMEOUT,
) -> dict[str, Any]:
    if not AIOHTTP_AVAILABLE:
        raise RuntimeError("aiohttp is not installed")
    url = f"{http_url.rstrip('/')}/{action.lstrip('/')}"
    headers = {"Content-Type": "application/json", **_auth_headers(token)}
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as session:
        async with session.post(url, json=params) as response:
            text = await response.text()
            try:
                payload = json.loads(text) if text else {}
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"NapCat HTTP returned non-JSON response: HTTP {response.status}") from exc
            if response.status >= 400:
                raise RuntimeError(f"NapCat HTTP {response.status}: {_response_error(payload)}")
            if not _response_ok(payload):
                raise RuntimeError(_response_error(payload))
            return payload


def _extract_message_id(payload: dict[str, Any]) -> str | None:
    data = payload.get("data")
    if isinstance(data, dict):
        message_id = data.get("message_id")
        if message_id is not None:
            return str(message_id)
    message_id = payload.get("message_id")
    return str(message_id) if message_id is not None else None


def _config_value(extra: dict[str, Any], env_name: str, *keys: str, default: Any = "") -> Any:
    env_value = os.getenv(env_name)
    if env_value is not None and env_value != "":
        return env_value
    for key in keys:
        value = extra.get(key)
        if value is not None and value != "":
            return value
    return default


def _configured_values(config: PlatformConfig | None = None) -> dict[str, Any]:
    extra = getattr(config, "extra", {}) or {}
    mode = _string(_config_value(extra, "NAPCAT_MODE", "mode", default="forward")).lower()
    ws_url = _string(_config_value(extra, "NAPCAT_WS_URL", "ws_url", "websocket_url", "url"))
    http_url = _string(_config_value(extra, "NAPCAT_HTTP_URL", "http_url", "api_url"))
    reverse_port = _int(
        _config_value(extra, "NAPCAT_REVERSE_PORT", "reverse_port", default=DEFAULT_REVERSE_PORT),
        DEFAULT_REVERSE_PORT,
    )
    reverse_host = _string(
        _config_value(extra, "NAPCAT_REVERSE_HOST", "reverse_host", default=DEFAULT_REVERSE_HOST),
        DEFAULT_REVERSE_HOST,
    )
    reverse_path = _normalize_reverse_path(
        _config_value(extra, "NAPCAT_REVERSE_PATH", "reverse_path", default=DEFAULT_REVERSE_PATH)
    )
    token = _string(_config_value(extra, "NAPCAT_ACCESS_TOKEN", "access_token", "token"))
    self_id = _string(_config_value(extra, "NAPCAT_SELF_ID", "self_id", "bot_qq"))
    return {
        "mode": mode,
        "ws_url": ws_url,
        "http_url": http_url,
        "reverse_host": reverse_host,
        "reverse_port": reverse_port,
        "reverse_path": reverse_path,
        "access_token": token,
        "self_id": self_id,
    }


class NapCatAdapter(BasePlatformAdapter):
    """Hermes platform adapter for QQ through NapCat OneBot v11."""

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH
    SUPPORTS_MESSAGE_EDITING = False

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform(PLATFORM_NAME))
        values = _configured_values(config)
        extra = config.extra or {}

        self.mode = values["mode"]
        self.ws_url = values["ws_url"]
        self.http_url = values["http_url"]
        self.access_token = values["access_token"]
        self.self_id = values["self_id"]
        self.reverse_host = values["reverse_host"]
        self.reverse_port = values["reverse_port"]
        self.reverse_path = values["reverse_path"]

        self.default_target_type = _string(
            _config_value(extra, "NAPCAT_DEFAULT_TARGET_TYPE", "default_target_type", default="group"),
            "group",
        ).lower()
        self.require_mention = _truthy(
            _config_value(extra, "NAPCAT_REQUIRE_MENTION", "require_mention", default=True),
            True,
        )
        self.download_media = _truthy(
            _config_value(extra, "NAPCAT_DOWNLOAD_MEDIA", "download_media", default=True),
            True,
        )
        self.include_sender_prefix = _truthy(
            _config_value(extra, "NAPCAT_INCLUDE_SENDER_PREFIX", "include_sender_prefix", default=False),
            False,
        )
        self.reply_with_quote = _truthy(
            _config_value(extra, "NAPCAT_REPLY_WITH_QUOTE", "reply_with_quote", default=True),
            True,
        )

        self.free_response_groups = _csv_set(
            _config_value(extra, "NAPCAT_FREE_RESPONSE_GROUPS", "free_response_channels", "free_response_groups")
        )
        self.allowed_groups = _csv_set(
            _config_value(extra, "NAPCAT_ALLOWED_GROUPS", "allowed_groups", "group_allowlist")
        )
        self.ignored_groups = _csv_set(
            _config_value(extra, "NAPCAT_IGNORED_GROUPS", "ignored_groups", "group_blocklist")
        )
        self.mention_patterns = self._compile_mention_patterns(
            _config_value(extra, "NAPCAT_MENTION_PATTERNS", "mention_patterns")
        )

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse | web.WebSocketResponse] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._server_runner: Optional[web.AppRunner] = None
        self._server_site: Optional[web.TCPSite] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._seen_messages: dict[str, float] = {}
        self._stopping = False

    @property
    def name(self) -> str:
        return "NapCat"

    @property
    def is_connected(self) -> bool:
        ws = self._ws
        return bool(self._running and ws is not None and not ws.closed)

    async def connect(self) -> bool:
        if not AIOHTTP_AVAILABLE:
            self._set_fatal_error("missing_dependency", "aiohttp is not installed", retryable=False)
            return False

        self._stopping = False
        if self.mode == "reverse":
            await self._start_reverse_server()
            self._running = True
            self._write_runtime_status_safe(
                "listening",
                platform_state="listening",
                error_code=None,
                error_message=None,
            )
            logger.info(
                "NapCat reverse WebSocket listening on %s:%s%s",
                self.reverse_host,
                self.reverse_port,
                self.reverse_path,
            )
            return True

        if not self.ws_url:
            self._set_fatal_error(
                "missing_ws_url",
                "NAPCAT_WS_URL is required for forward WebSocket mode",
                retryable=False,
            )
            return False

        self._listen_task = asyncio.create_task(self._forward_loop(), name="napcat-forward-ws")
        return True

    async def disconnect(self) -> None:
        self._stopping = True
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await asyncio.wait_for(self._listen_task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._listen_task = None

        ws = self._ws
        self._ws = None
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass

        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

        if self._server_runner is not None:
            try:
                await self._server_runner.cleanup()
            except Exception:
                pass
            self._server_runner = None
            self._server_site = None

        self._fail_pending("NapCat adapter disconnected")
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        chunks = self.truncate_message(
            self.format_message(content or ""),
            max_length=self.MAX_MESSAGE_LENGTH,
            len_fn=self.message_len_fn,
        )
        message_ids: list[str] = []
        last_result: Optional[SendResult] = None
        for index, chunk in enumerate(chunks):
            result = await self._send_segments(
                chat_id,
                self._text_segments(chunk, reply_to=reply_to if index == 0 else None),
            )
            last_result = result
            if not result.success:
                return result
            if result.message_id:
                message_ids.append(result.message_id)
        if last_result is None:
            return SendResult(success=True)
        return SendResult(
            success=True,
            message_id=message_ids[-1] if message_ids else last_result.message_id,
            raw_response=last_result.raw_response,
            continuation_message_ids=tuple(message_ids[:-1]),
        )

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        return None

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        segments = []
        if reply_to and self.reply_with_quote:
            segments.append({"type": "reply", "data": {"id": str(reply_to)}})
        if caption:
            segments.append({"type": "text", "data": {"text": caption}})
        segments.append({"type": "image", "data": {"file": image_url}})
        return await self._send_segments(chat_id, segments)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        path = Path(image_path).expanduser().resolve()
        return await self.send_image(
            chat_id,
            f"file://{quote(str(path))}",
            caption=caption,
            reply_to=reply_to,
            metadata=metadata,
        )

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        kind, ident = _parse_chat_id(chat_id, default_kind=self.default_target_type)
        if kind == "group":
            return {"name": f"QQ group {ident}", "type": "group", "chat_id": f"group:{ident}"}
        return {"name": f"QQ user {ident}", "type": "dm", "chat_id": f"private:{ident}"}

    async def _start_reverse_server(self) -> None:
        app = web.Application()
        app.router.add_get(self.reverse_path, self._handle_reverse_ws)
        self._server_runner = web.AppRunner(app)
        await self._server_runner.setup()
        self._server_site = web.TCPSite(
            self._server_runner,
            self.reverse_host,
            self.reverse_port,
        )
        await self._server_site.start()

    async def _handle_reverse_ws(self, request: web.Request) -> web.StreamResponse:
        if self.access_token and not self._request_authorized(request):
            return web.Response(status=401, text="unauthorized")

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        old_ws = self._ws
        if old_ws is not None and not old_ws.closed and old_ws is not ws:
            try:
                await old_ws.close(code=1000, message=b"replaced by a newer NapCat connection")
            except Exception:
                pass

        self._ws = ws
        self._mark_connected()
        logger.info("NapCat reverse WebSocket connected from %s", request.remote)

        try:
            await self._consume_ws(ws)
        finally:
            if self._ws is ws:
                self._ws = None
            self._fail_pending("NapCat reverse WebSocket disconnected")
            if not self._stopping:
                self._write_runtime_status_safe(
                    "disconnected",
                    platform_state="disconnected",
                    error_code=None,
                    error_message=None,
                )
        return ws

    def _request_authorized(self, request: web.Request) -> bool:
        auth = request.headers.get("Authorization", "").strip()
        expected = f"Bearer {self.access_token}"
        if auth == expected:
            return True
        return request.query.get("access_token") == self.access_token

    async def _forward_loop(self) -> None:
        delay = DEFAULT_RECONNECT_BASE
        headers = _auth_headers(self.access_token)
        url = self.ws_url
        if _truthy(os.getenv("NAPCAT_ACCESS_TOKEN_QUERY"), False):
            url = _url_with_access_token(url, self.access_token)

        while not self._stopping:
            try:
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=30)
                async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                    self._session = session
                    async with session.ws_connect(url, heartbeat=30, autoping=True) as ws:
                        self._ws = ws
                        self._mark_connected()
                        delay = DEFAULT_RECONNECT_BASE
                        logger.info("Connected to NapCat WebSocket: %s", self._safe_ws_url())
                        await self._consume_ws(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._stopping:
                    logger.warning("NapCat WebSocket disconnected: %s", exc)
                    self._write_runtime_status_safe(
                        "disconnected",
                        platform_state="disconnected",
                        error_code=None,
                        error_message=str(exc),
                    )
            finally:
                self._ws = None
                self._session = None
                self._fail_pending("NapCat WebSocket disconnected")

            if self._stopping:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 1.7, DEFAULT_RECONNECT_MAX)

    def _safe_ws_url(self) -> str:
        parsed = urlsplit(self.ws_url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    async def _consume_ws(self, ws: Any) -> None:
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                await self._handle_ws_text(message.data)
            elif message.type == aiohttp.WSMsgType.BINARY:
                continue
            elif message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                break

    async def _handle_ws_text(self, text: str) -> None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Ignoring non-JSON NapCat WebSocket frame")
            return

        if isinstance(payload, dict) and "echo" in payload:
            self._resolve_pending(payload)
            return
        if isinstance(payload, dict):
            await self._handle_onebot_event(payload)

    def _resolve_pending(self, payload: dict[str, Any]) -> None:
        echo = str(payload.get("echo"))
        future = self._pending.pop(echo, None)
        if future is not None and not future.done():
            future.set_result(payload)

    def _fail_pending(self, reason: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(RuntimeError(reason))
        self._pending.clear()

    async def _call_action(
        self,
        action: str,
        params: dict[str, Any],
        *,
        timeout: float = DEFAULT_ACTION_TIMEOUT,
    ) -> dict[str, Any]:
        ws = self._ws
        if ws is not None and not ws.closed:
            echo = f"hermes-napcat-{uuid.uuid4().hex}"
            payload = {"action": action, "params": params, "echo": echo}
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._pending[echo] = future
            try:
                await ws.send_str(_json_dumps(payload))
                response = await asyncio.wait_for(future, timeout=timeout)
            finally:
                self._pending.pop(echo, None)
            if not isinstance(response, dict):
                raise RuntimeError("NapCat returned a malformed action response")
            if not _response_ok(response):
                raise RuntimeError(_response_error(response))
            return response

        if self.http_url:
            return await _call_http_action(self.http_url, self.access_token, action, params, timeout=timeout)
        raise RuntimeError("NapCat WebSocket is not connected and NAPCAT_HTTP_URL is not configured")

    async def _send_segments(self, chat_id: str, segments: list[dict[str, Any]]) -> SendResult:
        kind, ident = _parse_chat_id(chat_id, default_kind=self.default_target_type)
        if not ident:
            return SendResult(success=False, error="empty NapCat chat_id")
        if kind == "private":
            action = "send_private_msg"
            params = {"user_id": _coerce_onebot_id(ident), "message": segments}
        elif kind == "group":
            action = "send_group_msg"
            params = {"group_id": _coerce_onebot_id(ident), "message": segments}
        else:
            return SendResult(success=False, error=f"unsupported NapCat chat type: {kind}")
        try:
            payload = await self._call_action(action, params)
            return SendResult(
                success=True,
                message_id=_extract_message_id(payload),
                raw_response=payload,
            )
        except Exception as exc:
            return SendResult(success=False, error=str(exc), retryable=self._is_retryable_send_error(exc))

    def _text_segments(self, content: str, reply_to: Optional[str] = None) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        if reply_to and self.reply_with_quote:
            segments.append({"type": "reply", "data": {"id": str(reply_to)}})
        segments.append({"type": "text", "data": {"text": content}})
        return segments

    def _is_retryable_send_error(self, exc: BaseException) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in ("timeout", "temporarily", "disconnected", "connection", "network"))

    async def _handle_onebot_event(self, payload: dict[str, Any]) -> None:
        post_type = payload.get("post_type")
        if post_type == "meta_event" and payload.get("self_id") and not self.self_id:
            self.self_id = str(payload.get("self_id"))
            return
        if post_type not in {"message", "message_sent"}:
            return
        if post_type == "message_sent" and not _truthy(
            _config_value(self.config.extra or {}, "NAPCAT_HANDLE_SELF_SENT", "handle_self_sent", default=False),
            False,
        ):
            return

        message_type = str(payload.get("message_type") or "").lower()
        if message_type not in {"private", "group"}:
            return

        if payload.get("self_id") and not self.self_id:
            self.self_id = str(payload.get("self_id"))

        user_id = str(payload.get("user_id") or "")
        if self.self_id and user_id == self.self_id:
            return

        message_id = str(payload.get("message_id") or "")
        if self._already_seen(payload, message_id):
            return

        parsed = await self._message_to_text_and_media(payload)
        text = parsed["text"].strip()
        media_urls = parsed["media_urls"]
        media_types = parsed["media_types"]
        if not text and not media_urls:
            return

        sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
        user_name = (
            _string(sender.get("card"))
            or _string(sender.get("nickname"))
            or _string(sender.get("user_id"))
            or user_id
        )

        if message_type == "group":
            group_id = str(payload.get("group_id") or "")
            if not group_id or not self._group_is_allowed(group_id):
                return
            if not self._should_accept_group_message(group_id, text, parsed["mentioned_self"]):
                return
            chat_id = f"group:{group_id}"
            chat_type = "group"
            chat_name = f"QQ group {group_id}"
            if self.include_sender_prefix and user_name:
                text = f"{user_name}: {text}"
        else:
            chat_id = f"private:{user_id}"
            chat_type = "dm"
            chat_name = user_name

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_name,
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
            message_id=message_id or None,
        )
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=payload,
            message_id=message_id or None,
            media_urls=media_urls,
            media_types=media_types,
            reply_to_message_id=parsed.get("reply_to_message_id"),
            channel_prompt=resolve_channel_prompt(self.config.extra or {}, chat_id),
            timestamp=__import__("datetime").datetime.now(),
        )
        await self.handle_message(event)

    def _already_seen(self, payload: dict[str, Any], message_id: str) -> bool:
        now = time.time()
        if len(self._seen_messages) > 2048:
            cutoff = now - 600
            self._seen_messages = {
                key: seen_at for key, seen_at in self._seen_messages.items() if seen_at >= cutoff
            }
        key = message_id or f"{payload.get('time')}:{payload.get('message_type')}:{payload.get('user_id')}:{payload.get('raw_message')}"
        if key in self._seen_messages:
            return True
        self._seen_messages[key] = now
        return False

    async def _message_to_text_and_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = payload.get("message")
        if isinstance(message, list):
            return await self._segments_to_text_and_media(message)
        raw = payload.get("raw_message")
        text = str(message if message is not None else raw or "")
        return await self._cq_string_to_text_and_media(text)

    async def _segments_to_text_and_media(self, segments: list[Any]) -> dict[str, Any]:
        parts: list[str] = []
        media_urls: list[str] = []
        media_types: list[str] = []
        mentioned_self = False
        reply_to_message_id: str | None = None

        for segment in segments:
            if not isinstance(segment, dict):
                continue
            seg_type = str(segment.get("type") or "").lower()
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}

            if seg_type == "text":
                parts.append(str(data.get("text") or ""))
            elif seg_type == "at":
                qq = str(data.get("qq") or "")
                if self.self_id and qq == self.self_id:
                    mentioned_self = True
                elif qq == "all":
                    mentioned_self = True
                    parts.append("@all")
                elif qq:
                    parts.append(f"@{qq}")
            elif seg_type == "reply":
                reply_id = data.get("id")
                if reply_id is not None:
                    reply_to_message_id = str(reply_id)
            elif seg_type == "image":
                image_path = await self._cache_incoming_image(data)
                if image_path:
                    media_urls.append(image_path)
                    media_types.append("image")
            elif seg_type in {"record", "voice"}:
                parts.append("[voice message]")
            elif seg_type == "video":
                parts.append("[video message]")
            elif seg_type == "face":
                face_id = data.get("id")
                parts.append(f"[face:{face_id}]" if face_id is not None else "[face]")
            elif seg_type:
                parts.append(f"[{seg_type}]")

        return {
            "text": "".join(parts).strip(),
            "media_urls": media_urls,
            "media_types": media_types,
            "mentioned_self": mentioned_self,
            "reply_to_message_id": reply_to_message_id,
        }

    async def _cq_string_to_text_and_media(self, raw: str) -> dict[str, Any]:
        parts: list[str] = []
        media_urls: list[str] = []
        media_types: list[str] = []
        mentioned_self = False
        reply_to_message_id: str | None = None
        pos = 0

        for match in _CQ_RE.finditer(raw):
            if match.start() > pos:
                parts.append(_unescape_cq(raw[pos:match.start()]))
            pos = match.end()

            seg_type = match.group("type").lower()
            data = _parse_cq_attrs(match.group("attrs") or "")
            if seg_type == "at":
                qq = str(data.get("qq") or "")
                if self.self_id and qq == self.self_id:
                    mentioned_self = True
                elif qq == "all":
                    mentioned_self = True
                    parts.append("@all")
                elif qq:
                    parts.append(f"@{qq}")
            elif seg_type == "reply":
                reply_id = data.get("id")
                if reply_id is not None:
                    reply_to_message_id = str(reply_id)
            elif seg_type == "image":
                image_path = await self._cache_incoming_image(data)
                if image_path:
                    media_urls.append(image_path)
                    media_types.append("image")
            elif seg_type in {"record", "voice"}:
                parts.append("[voice message]")
            elif seg_type == "video":
                parts.append("[video message]")
            elif seg_type == "face":
                face_id = data.get("id")
                parts.append(f"[face:{face_id}]" if face_id is not None else "[face]")
            else:
                parts.append(f"[{seg_type}]")

        if pos < len(raw):
            parts.append(_unescape_cq(raw[pos:]))

        return {
            "text": "".join(parts).strip(),
            "media_urls": media_urls,
            "media_types": media_types,
            "mentioned_self": mentioned_self,
            "reply_to_message_id": reply_to_message_id,
        }

    async def _cache_incoming_image(self, data: dict[str, Any]) -> str | None:
        if not self.download_media:
            return None
        url = _string(data.get("url") or data.get("file"))
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return None
        ext = ".jpg"
        file_name = _string(data.get("file"))
        if "." in file_name:
            candidate = "." + file_name.rsplit(".", 1)[-1].lower()
            if re.fullmatch(r"\.[a-z0-9]{1,8}", candidate):
                ext = candidate
        try:
            return await cache_image_from_url(url, ext=ext)
        except Exception as exc:
            logger.debug("Failed to cache NapCat image: %s", exc)
            return None

    def _group_is_allowed(self, group_id: str) -> bool:
        candidates = {group_id, f"group:{group_id}"}
        if "*" in self.ignored_groups or candidates & self.ignored_groups:
            return False
        if self.allowed_groups and "*" not in self.allowed_groups and not (candidates & self.allowed_groups):
            return False
        return True

    def _should_accept_group_message(self, group_id: str, text: str, mentioned_self: bool) -> bool:
        candidates = {group_id, f"group:{group_id}"}
        if "*" in self.free_response_groups or candidates & self.free_response_groups:
            return True
        if not self.require_mention:
            return True
        if mentioned_self:
            return True
        return any(pattern.search(text) for pattern in self.mention_patterns)

    def _compile_mention_patterns(self, value: Any) -> list[re.Pattern[str]]:
        patterns: list[re.Pattern[str]] = []
        raw_patterns = _csv_set(value)
        bot_names = _csv_set(
            _config_value(self.config.extra or {}, "NAPCAT_BOT_NAMES", "bot_names")
        )
        for name in bot_names:
            raw_patterns.add(rf"(?i)(^|\s)@?{re.escape(name)}([,:\s]|$)")
        for raw in raw_patterns:
            try:
                patterns.append(re.compile(raw))
            except re.error:
                logger.warning("Ignoring invalid NapCat mention pattern: %s", raw)
        return patterns


def check_requirements() -> bool:
    return AIOHTTP_AVAILABLE


def validate_config(config: PlatformConfig) -> bool:
    values = _configured_values(config)
    if values["mode"] == "reverse":
        return bool(values["reverse_host"] and values["reverse_port"] and values["reverse_path"])
    return bool(values["ws_url"])


def is_connected(config: PlatformConfig) -> bool:
    return validate_config(config)


def _env_enablement() -> dict[str, Any] | None:
    values = _configured_values(None)
    mode = values["mode"]
    if mode != "reverse" and not values["ws_url"]:
        return None
    seed: dict[str, Any] = dict(values)
    home = _string(os.getenv("NAPCAT_HOME_CHANNEL"))
    if home:
        default_kind = _string(os.getenv("NAPCAT_HOME_CHANNEL_TYPE"), "group")
        seed["home_channel"] = {
            "chat_id": _normalize_chat_id(home, default_kind=default_kind),
            "name": _string(os.getenv("NAPCAT_HOME_CHANNEL_NAME"), "NapCat Home"),
        }
    return seed


def _apply_yaml_config(yaml_cfg: dict, napcat_cfg: dict) -> dict[str, Any] | None:
    del yaml_cfg
    if not isinstance(napcat_cfg, dict):
        return None

    key_to_env = {
        "mode": "NAPCAT_MODE",
        "ws_url": "NAPCAT_WS_URL",
        "websocket_url": "NAPCAT_WS_URL",
        "http_url": "NAPCAT_HTTP_URL",
        "api_url": "NAPCAT_HTTP_URL",
        "access_token": "NAPCAT_ACCESS_TOKEN",
        "token": "NAPCAT_ACCESS_TOKEN",
        "reverse_host": "NAPCAT_REVERSE_HOST",
        "reverse_port": "NAPCAT_REVERSE_PORT",
        "reverse_path": "NAPCAT_REVERSE_PATH",
        "self_id": "NAPCAT_SELF_ID",
        "bot_qq": "NAPCAT_SELF_ID",
        "require_mention": "NAPCAT_REQUIRE_MENTION",
        "download_media": "NAPCAT_DOWNLOAD_MEDIA",
        "free_response_groups": "NAPCAT_FREE_RESPONSE_GROUPS",
        "allowed_groups": "NAPCAT_ALLOWED_GROUPS",
        "ignored_groups": "NAPCAT_IGNORED_GROUPS",
        "mention_patterns": "NAPCAT_MENTION_PATTERNS",
        "bot_names": "NAPCAT_BOT_NAMES",
        "default_target_type": "NAPCAT_DEFAULT_TARGET_TYPE",
        "allow_from": "NAPCAT_ALLOWED_USERS",
        "allowed_users": "NAPCAT_ALLOWED_USERS",
        "allow_all_users": "NAPCAT_ALLOW_ALL_USERS",
        "allow_all": "NAPCAT_ALLOW_ALL_USERS",
    }
    seeded: dict[str, Any] = {}
    for key, env_name in key_to_env.items():
        if key not in napcat_cfg:
            continue
        value = napcat_cfg[key]
        if isinstance(value, (list, tuple, set)):
            env_value = ",".join(str(item) for item in value)
        else:
            env_value = str(value).lower() if isinstance(value, bool) else str(value)
        if not os.getenv(env_name):
            os.environ[env_name] = env_value
        seeded[key] = value

    home = napcat_cfg.get("home_channel")
    if home:
        if isinstance(home, dict):
            raw_chat_id = home.get("chat_id") or home.get("id")
            name = home.get("name") or "NapCat Home"
            default_kind = home.get("type") or napcat_cfg.get("home_channel_type") or "group"
        else:
            raw_chat_id = home
            name = napcat_cfg.get("home_channel_name") or "NapCat Home"
            default_kind = napcat_cfg.get("home_channel_type") or "group"
        if raw_chat_id:
            seeded["home_channel"] = {
                "chat_id": _normalize_chat_id(raw_chat_id, default_kind=str(default_kind)),
                "name": str(name),
            }
    return seeded or None


async def _standalone_send(
    pconfig: PlatformConfig,
    chat_id: str,
    message: str,
    *,
    thread_id=None,
    media_files=None,
    force_document: bool = False,
) -> dict[str, Any]:
    del thread_id, media_files, force_document
    values = _configured_values(pconfig)
    http_url = values["http_url"]
    if not http_url:
        return {"error": "NAPCAT_HTTP_URL is required for standalone NapCat delivery"}
    kind, ident = _parse_chat_id(chat_id, default_kind="group")
    segments = [{"type": "text", "data": {"text": message}}]
    if kind == "private":
        action = "send_private_msg"
        params = {"user_id": _coerce_onebot_id(ident), "message": segments}
    else:
        action = "send_group_msg"
        params = {"group_id": _coerce_onebot_id(ident), "message": segments}
    try:
        payload = await _call_http_action(http_url, values["access_token"], action, params)
        return {"success": True, "message_id": _extract_message_id(payload)}
    except Exception as exc:
        return {"error": str(exc)}


def _build_adapter(config: PlatformConfig) -> NapCatAdapter:
    return NapCatAdapter(config)


def register(ctx) -> None:
    ctx.register_platform(
        name=PLATFORM_NAME,
        label="NapCat QQ",
        adapter_factory=_build_adapter,
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=[],
        install_hint="aiohttp is bundled with Hermes in normal installs",
        env_enablement_fn=_env_enablement,
        apply_yaml_config_fn=_apply_yaml_config,
        cron_deliver_env_var="NAPCAT_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="NAPCAT_ALLOWED_USERS",
        allow_all_env="NAPCAT_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        platform_hint=(
            "You are chatting through QQ via NapCat/OneBot. "
            "Use plain text by default. QQ supports basic text and images; "
            "avoid platform-specific Markdown assumptions."
        ),
        emoji="QQ",
        allow_update_command=True,
    )
