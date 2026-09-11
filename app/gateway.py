import asyncio
import base64
from typing import Iterable

import httpx
import websockets
from fastapi import HTTPException, Request, WebSocket, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .config import settings
from .models import User, Workspace, WorkspaceStatus
from .runtime import read_workspace_secret, workspace_host_port
from .security import load_user_from_token

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _basic_auth(password: str) -> str:
    raw = f"opencode:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _clean_cookie_header(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    blocked = {settings.session_cookie_name, settings.workspace_cookie_name}
    kept: list[str] = []
    for part in cookie_header.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name = item.split("=", 1)[0].strip()
        if name not in blocked:
            kept.append(item)
    return "; ".join(kept) if kept else None


def _forward_headers(headers: Iterable[tuple[str, str]], password: str) -> dict[str, str]:
    result: dict[str, str] = {}
    cookie_header: str | None = None
    for key, value in headers:
        lower = key.lower()
        if lower in _HOP_BY_HOP or lower in {"host", "authorization", "content-length"}:
            continue
        if lower == "x-opencode-workspace":
            # Selection is handled and authorized by the gateway; never forward
            # a client-controlled routing header to OpenCode.
            continue
        if lower == "cookie":
            cookie_header = value
            continue
        result[key] = value
    cleaned_cookie = _clean_cookie_header(cookie_header)
    if cleaned_cookie:
        result["cookie"] = cleaned_cookie
    result["authorization"] = _basic_auth(password)
    return result


def _find_selected_workspace(
    *,
    user: User,
    db: Session,
    selector: str | None,
) -> Workspace:
    stmt = select(Workspace).where(
        Workspace.user_id == user.id,
        Workspace.status == WorkspaceStatus.RUNNING,
    )
    if selector:
        stmt = stmt.where(
            (Workspace.id == selector) | (Workspace.container_name == selector)
        )
        ws = db.scalar(stmt)
        if not ws:
            raise HTTPException(status_code=404, detail="Selected running workspace not found")
        return ws

    rows = db.scalars(stmt.order_by(Workspace.created_at.desc())).all()
    if not rows:
        raise HTTPException(status_code=409, detail="No running workspace. Start one from /dashboard.")
    if len(rows) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple running workspaces. Open one from /dashboard or send X-OpenCode-Workspace.",
        )
    return rows[0]


def selected_workspace_for_request(request: Request, user: User, db: Session) -> Workspace:
    selector = request.headers.get("x-opencode-workspace") or request.cookies.get(settings.workspace_cookie_name)
    return _find_selected_workspace(user=user, db=db, selector=selector)


def selected_workspace_for_websocket(websocket: WebSocket, user: User, db: Session) -> Workspace:
    selector = websocket.headers.get("x-opencode-workspace") or websocket.cookies.get(settings.workspace_cookie_name)
    return _find_selected_workspace(user=user, db=db, selector=selector)


def _backend_http_url(ws: Workspace, request: Request) -> str:
    if ws.slot_id is None:
        raise HTTPException(status_code=503, detail="Workspace has no assigned slot")
    port = workspace_host_port(ws.slot_id)
    path = request.url.path
    query = request.url.query
    return f"http://127.0.0.1:{port}{path}" + (f"?{query}" if query else "")


def _backend_ws_url(ws: Workspace, websocket: WebSocket) -> str:
    if ws.slot_id is None:
        raise RuntimeError("Workspace has no assigned slot")
    port = workspace_host_port(ws.slot_id)
    path = websocket.url.path
    query = websocket.url.query
    return f"ws://127.0.0.1:{port}{path}" + (f"?{query}" if query else "")


async def proxy_http(request: Request, user: User, db: Session):
    ws = selected_workspace_for_request(request, user, db)
    password = read_workspace_secret(user.id, ws.project_slug)
    target = _backend_http_url(ws, request)
    headers = _forward_headers(request.headers.items(), password)
    body = await request.body()

    client = httpx.AsyncClient(follow_redirects=False, timeout=None, trust_env=False)
    try:
        upstream = await client.send(
            client.build_request(request.method, target, headers=headers, content=body),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"OpenCode backend unavailable: {exc}") from exc

    async def close_upstream() -> None:
        await upstream.aclose()
        await client.aclose()

    response = StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        background=BackgroundTask(close_upstream),
    )
    # Preserve duplicate response headers such as Set-Cookie. Rewrite an
    # absolute loopback redirect, if OpenCode emits one, back to the public URL.
    raw_headers: list[tuple[bytes, bytes]] = []
    backend_origin = f"http://127.0.0.1:{workspace_host_port(ws.slot_id)}"
    for key, value in upstream.headers.raw:
        lower = key.decode("latin-1").lower()
        if lower in _HOP_BY_HOP or lower == "content-length":
            continue
        if lower == "location":
            text = value.decode("latin-1")
            if text.startswith(backend_origin):
                value = (settings.control_plane_url.rstrip("/") + text[len(backend_origin):]).encode("latin-1")
        raw_headers.append((key, value))
    response.raw_headers = raw_headers
    return response


async def proxy_websocket(websocket: WebSocket, db: Session) -> None:
    token = websocket.cookies.get(settings.session_cookie_name)
    auth_header = websocket.headers.get("authorization")
    if not token and auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1]
    user = load_user_from_token(token, db)
    if not user:
        await websocket.close(code=4401)
        return

    try:
        ws = selected_workspace_for_websocket(websocket, user, db)
        password = read_workspace_secret(user.id, ws.project_slug)
        target = _backend_ws_url(ws, websocket)
        upstream_headers = {"Authorization": _basic_auth(password)}

        async with websockets.connect(
            target,
            additional_headers=upstream_headers,
            open_timeout=15,
            max_size=None,
            proxy=None,
        ) as upstream:
            await websocket.accept()

            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    kind = message.get("type")
                    if kind == "websocket.disconnect":
                        break
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            done, pending = await asyncio.wait(
                {
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                },
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
