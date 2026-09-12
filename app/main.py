import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, SessionLocal, engine, get_db
from .gateway import proxy_http, proxy_websocket, selected_workspace_for_request
from .models import Slot, User, Workspace, WorkspaceStatus
from .migrations import drop_legacy_workspace_constraint, ensure_active_workspace_index
from .portal import PORTAL_CSS, PORTAL_JS
from .ui import APP_CSS, dashboard_page_html, login_page_html
from .runtime import (
    container_running,
    container_has_workspace_port,
    start_workspace,
    stop_workspace,
    touch_workspace_activity,
    workspace_last_activity,
    workspace_secret_path,
    read_workspace_stop_reason,
    write_workspace_stop_reason,
)
from .schemas import LoginRequest, RegisterRequest, TokenResponse, WorkspaceResponse, WorkspaceStartRequest
from .security import current_user, hash_password, issue_token, load_user_from_token, verify_password
from .traefik import cleanup_legacy_workspace_routes, workspace_open_url


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.jwt_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _workspace_response(ws: Workspace) -> WorkspaceResponse:
    gateway_ready = (
        ws.status == WorkspaceStatus.RUNNING
        and workspace_secret_path(ws.user_id, ws.project_slug).is_file()
    )
    url = workspace_open_url(ws.id) if gateway_ready else None
    return WorkspaceResponse(
        id=ws.id,
        project_slug=ws.project_slug,
        status=ws.status.value,
        url=url,
        slot_id=ws.slot_id,
        container_name=ws.container_name,
    )


def _seed_slots() -> None:
    with SessionLocal() as db:
        existing = {row[0] for row in db.execute(select(Slot.id)).all()}
        for i in range(1, settings.max_slots + 1):
            if i not in existing:
                db.add(Slot(id=i, in_use=False))
        db.commit()


logger = logging.getLogger(__name__)


def _release_workspace(ws: Workspace, db: Session, reason: str = "manual") -> None:
    """Destroy one runtime container and return its slot to the pool."""
    if ws.status not in (WorkspaceStatus.STOPPED, WorkspaceStatus.ERROR):
        ws.status = WorkspaceStatus.STOPPING
        db.commit()

    container_name = ws.container_name
    slot_id = ws.slot_id
    stop_workspace(container_name)

    if slot_id:
        slot = db.get(Slot, slot_id)
        if slot:
            slot.in_use = False
            slot.workspace_id = None

    ws.status = WorkspaceStatus.STOPPED
    ws.stopped_at = datetime.now(timezone.utc)
    ws.container_name = None
    ws.slot_id = None
    write_workspace_stop_reason(ws.user_id, ws.project_slug, reason)
    db.commit()




def _workspace_runtime_usable(ws: Workspace) -> bool:
    return (
        ws.status == WorkspaceStatus.RUNNING
        and ws.slot_id is not None
        and bool(ws.container_name)
        and workspace_secret_path(ws.user_id, ws.project_slug).is_file()
        and container_running(ws.container_name)
        and container_has_workspace_port(ws.container_name, ws.slot_id)
    )


def _reconcile_workspace_runtimes() -> int:
    """Release stale DB leases after crashes/upgrades or legacy runtimes."""
    reconciled = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(Workspace).where(
                Workspace.status.in_([
                    WorkspaceStatus.STARTING,
                    WorkspaceStatus.RUNNING,
                    WorkspaceStatus.STOPPING,
                ])
            )
        ).all()
        for ws in rows:
            if _workspace_runtime_usable(ws):
                continue
            logger.warning(
                "Recycling stale workspace %s (%s): status=%s container=%s slot=%s",
                ws.id, ws.project_slug, ws.status.value, ws.container_name, ws.slot_id,
            )
            _release_workspace(ws, db, reason="recycle")
            reconciled += 1
    return reconciled

def _dedupe_active_workspaces() -> int:
    """Collapse any legacy duplicate active leases before creating the partial index."""
    active = (WorkspaceStatus.STARTING, WorkspaceStatus.RUNNING, WorkspaceStatus.STOPPING)
    released = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(Workspace)
            .where(Workspace.status.in_(active))
            .order_by(Workspace.user_id, Workspace.project_slug, Workspace.created_at.desc())
        ).all()
        keep: set[tuple[str, str]] = set()
        for ws in rows:
            key = (ws.user_id, ws.project_slug)
            if key not in keep:
                keep.add(key)
                continue
            logger.warning(
                "Recycling duplicate active workspace %s (%s) for user %s",
                ws.id, ws.project_slug, ws.user_id,
            )
            _release_workspace(ws, db, reason="recycle")
            released += 1
    return released


def _stop_user_workspaces(user_id: str, db: Session) -> int:
    rows = db.scalars(
        select(Workspace).where(
            Workspace.user_id == user_id,
            Workspace.status.in_([WorkspaceStatus.STARTING, WorkspaceStatus.RUNNING, WorkspaceStatus.STOPPING]),
        )
    ).all()
    for ws in rows:
        _release_workspace(ws, db, reason="logout")
    return len(rows)


def _reap_idle_workspaces() -> int:
    if settings.workspace_idle_timeout_minutes <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.workspace_idle_timeout_minutes)
    reaped = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(Workspace).where(Workspace.status == WorkspaceStatus.RUNNING)
        ).all()
        for ws in rows:
            last = workspace_last_activity(ws.user_id, ws.project_slug) or ws.created_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last <= cutoff:
                logger.info(
                    "Reclaiming idle workspace %s (%s), last activity %s",
                    ws.id,
                    ws.project_slug,
                    last.isoformat(),
                )
                _release_workspace(ws, db, reason="idle")
                reaped += 1
    return reaped


async def _idle_reaper_loop() -> None:
    interval = max(5, settings.workspace_reaper_interval_seconds)
    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.to_thread(_reconcile_workspace_runtimes)
            await asyncio.to_thread(_reap_idle_workspaces)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Idle workspace reaper failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # v6.4.3 fixes the old (user_id, project_slug, status) uniqueness rule.
    # Drop it before create/reconciliation. ALTER TABLE IF EXISTS makes this
    # safe for a completely fresh PostgreSQL installation as well.
    drop_legacy_workspace_constraint()
    Base.metadata.create_all(engine)
    _seed_slots()
    settings.expanded_data_root.mkdir(parents=True, exist_ok=True)
    settings.expanded_traefik_dynamic_dir.mkdir(parents=True, exist_ok=True)
    # v6 routes all workspaces through the gateway, so v5's per-workspace
    # Traefik files must not remain active after an upgrade.
    cleanup_legacy_workspace_routes()
    _reconcile_workspace_runtimes()
    _dedupe_active_workspaces()
    ensure_active_workspace_index()
    reaper_task = None
    if settings.workspace_idle_timeout_minutes > 0:
        reaper_task = asyncio.create_task(_idle_reaper_loop(), name="workspace-idle-reaper")
    try:
        yield
    finally:
        if reaper_task:
            reaper_task.cancel()
            await asyncio.gather(reaper_task, return_exceptions=True)


app = FastAPI(
    title="OpenCode Multiuser Gateway",
    version="0.6.5",
    lifespan=lifespan,
    docs_url="/_control/docs",
    openapi_url="/_control/openapi.json",
    redoc_url=None,
)


@app.get("/healthz")
def healthz():
    return {"ok": True, "mode": "single-host-gateway"}


@app.post("/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    if not settings.allow_registration:
        raise HTTPException(status_code=403, detail="Registration disabled")
    user = User(username=req.username, password_hash=hash_password(req.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username already exists") from exc
    db.refresh(user)
    token = issue_token(user)
    _set_session_cookie(response, token)
    return TokenResponse(access_token=token)


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == req.username, User.is_active.is_(True)))
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = issue_token(user)
    _set_session_cookie(response, token)
    return TokenResponse(access_token=token)


@app.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request, db: Session = Depends(get_db)):
    user = load_user_from_token(_request_token(request), db)
    if user and settings.stop_workspaces_on_logout:
        _stop_user_workspaces(user.id, db)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.workspace_cookie_name, path="/")
    return response


@app.get("/_ui/app.css", response_class=PlainTextResponse)
def app_ui_css():
    return PlainTextResponse(APP_CSS, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse(login_page_html())


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    reason = request.query_params.get("reason")
    project = escape(request.query_params.get("workspace", ""))
    notice = ""
    if reason == "idle":
        name = f" <strong>{project}</strong>" if project else ""
        notice = (
            f'<div class="ui-alert" style="margin-bottom:1rem">'
            f'Workspace{name} was stopped after {settings.workspace_idle_timeout_minutes} minutes of inactivity. '
            'Its runtime slot has been freed; start it again whenever you are ready.</div>'
        )
    elif reason == "backend":
        name = f" <strong>{project}</strong>" if project else ""
        notice = (
            f'<div class="ui-alert" style="margin-bottom:1rem">'
            f'Workspace{name} runtime was no longer reachable. Its stale slot was released safely. '
            'Start the project again to recreate the runtime.</div>'
        )

    slots = db.scalars(select(Slot).order_by(Slot.id)).all()
    total_slots = len(slots)
    free_slots = sum(1 for slot in slots if not slot.in_use)
    response = HTMLResponse(
        dashboard_page_html(
            user.username,
            notice,
            total_slots,
            free_slots,
            settings.workspace_idle_timeout_minutes,
        )
    )
    if reason == "idle":
        response.delete_cookie(settings.workspace_cookie_name, path="/")
    return response


@app.get("/workspaces", response_model=list[WorkspaceResponse])
def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Workspace)
        .where(Workspace.user_id == user.id)
        .order_by(Workspace.created_at.desc())
    ).all()
    # Older releases could leave multiple terminal rows for the same project.
    # Keep that history in PostgreSQL but show only the newest project row in
    # the user-facing API/dashboard.
    newest_by_project: dict[str, Workspace] = {}
    for ws in rows:
        newest_by_project.setdefault(ws.project_slug, ws)
    return [_workspace_response(x) for x in newest_by_project.values()]


@app.post("/workspaces/start", response_model=WorkspaceResponse)
def start(req: WorkspaceStartRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = db.scalar(
        select(Workspace)
        .where(Workspace.user_id == user.id, Workspace.project_slug == req.project_slug)
        .order_by(Workspace.created_at.desc())
    )

    if existing and existing.status in (WorkspaceStatus.STARTING, WorkspaceStatus.RUNNING):
        # A v5 runtime has no gateway secret and no loopback slot port. Recycle it
        # transparently the first time it is opened after a v6 upgrade.
        if (
            existing.status == WorkspaceStatus.RUNNING
            and workspace_secret_path(user.id, req.project_slug).is_file()
            and container_running(existing.container_name)
        ):
            touch_workspace_activity(user.id, req.project_slug)
            return _workspace_response(existing)
        _release_workspace(existing, db, reason="recycle")

    slot_stmt = select(Slot).where(Slot.in_use.is_(False)).order_by(Slot.id).limit(1)
    if not settings.database_url.startswith("sqlite"):
        slot_stmt = slot_stmt.with_for_update(skip_locked=True)
    slot = db.scalar(slot_stmt)
    if not slot:
        raise HTTPException(status_code=503, detail="No workspace capacity available")

    if existing:
        ws = existing
        ws.slot_id = slot.id
        ws.status = WorkspaceStatus.STARTING
        ws.container_name = None
        ws.stopped_at = None
        ws.created_at = datetime.now(timezone.utc)
    else:
        ws = Workspace(user_id=user.id, project_slug=req.project_slug, slot_id=slot.id, status=WorkspaceStatus.STARTING)
        db.add(ws)
        db.flush()

    slot.in_use = True
    slot.workspace_id = ws.id
    db.commit()
    db.refresh(ws)

    try:
        runtime = start_workspace(user.id, req.project_slug, ws.id, slot.id)
        ws.container_name = runtime.container_name
        ws.status = WorkspaceStatus.RUNNING
        db.commit()
        db.refresh(ws)
        return _workspace_response(ws)
    except Exception as exc:
        stop_workspace(ws.container_name)
        slot.in_use = False
        slot.workspace_id = None
        ws.status = WorkspaceStatus.ERROR
        db.commit()
        raise HTTPException(status_code=500, detail=f"Workspace start failed: {exc}") from exc


@app.post("/workspaces/{workspace_id}/stop", response_model=WorkspaceResponse)
def stop(workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = db.scalar(select(Workspace).where(Workspace.id == workspace_id, Workspace.user_id == user.id))
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.status == WorkspaceStatus.STOPPED:
        return _workspace_response(ws)

    _release_workspace(ws, db, reason="manual")
    db.refresh(ws)
    return _workspace_response(ws)


@app.get("/_portal/widget.css", response_class=PlainTextResponse)
def portal_widget_css():
    return PlainTextResponse(PORTAL_CSS, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.get("/_portal/widget.js", response_class=PlainTextResponse)
def portal_widget_js():
    return PlainTextResponse(PORTAL_JS, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/_portal/dashboard")
def portal_dashboard(user: User = Depends(current_user)):
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/_portal/overview")
def portal_overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    slots = db.scalars(select(Slot).order_by(Slot.id)).all()
    running = db.scalars(
        select(Workspace).where(
            Workspace.user_id == user.id,
            Workspace.status == WorkspaceStatus.RUNNING,
        )
    ).all()
    return {
        "platform_status": "online",
        "total_slots": len(slots),
        "free_slots": sum(1 for slot in slots if not slot.in_use),
        "running_workspaces": len(running),
        "idle_timeout_minutes": settings.workspace_idle_timeout_minutes,
    }


@app.get("/_portal/status")
def portal_status(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    selector = request.cookies.get(settings.workspace_cookie_name)
    if not selector:
        return {"workspace_status": "none"}
    ws = db.scalar(select(Workspace).where(Workspace.id == selector, Workspace.user_id == user.id))
    if not ws:
        return {"workspace_status": "none"}
    reason = read_workspace_stop_reason(user.id, ws.project_slug) if ws.status == WorkspaceStatus.STOPPED else None
    return {
        "workspace_id": ws.id,
        "project_slug": ws.project_slug,
        "workspace_status": ws.status.value,
        "slot_id": ws.slot_id,
        "stop_reason": reason,
    }


@app.post("/_portal/stop")
def portal_stop(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    selector = request.cookies.get(settings.workspace_cookie_name)
    if selector:
        ws = db.scalar(select(Workspace).where(Workspace.id == selector, Workspace.user_id == user.id))
        if ws and ws.status != WorkspaceStatus.STOPPED:
            _release_workspace(ws, db, reason="manual")
    response = RedirectResponse("/dashboard", status_code=303)
    response.delete_cookie(settings.workspace_cookie_name, path="/")
    return response


@app.post("/_portal/logout")
def portal_logout(request: Request, db: Session = Depends(get_db)):
    user = load_user_from_token(_request_token(request), db)
    if user and settings.stop_workspaces_on_logout:
        _stop_user_workspaces(user.id, db)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.workspace_cookie_name, path="/")
    return response


@app.get("/open/{workspace_id}")
def open_workspace(
    workspace_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ws = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.user_id == user.id,
            Workspace.status == WorkspaceStatus.RUNNING,
        )
    )
    if not ws:
        raise HTTPException(status_code=404, detail="Running workspace not found")
    if not workspace_secret_path(user.id, ws.project_slug).is_file():
        raise HTTPException(
            status_code=409,
            detail="Legacy workspace must be recycled through /workspaces/start before opening through the v6 gateway.",
        )
    touch_workspace_activity(user.id, ws.project_slug)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        settings.workspace_cookie_name,
        ws.id,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


def _request_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1]
    return request.cookies.get(settings.session_cookie_name)


def _browser_navigation(request: Request) -> bool:
    if request.method != "GET":
        return False
    if request.headers.get("sec-fetch-mode", "").lower() == "navigate":
        return True
    return "text/html" in request.headers.get("accept", "").lower()


def _idle_workspace_for_request(request: Request, user: User, db: Session) -> Workspace | None:
    selector = request.headers.get("x-opencode-workspace") or request.cookies.get(settings.workspace_cookie_name)
    if not selector:
        return None
    # A stopped runtime clears container_name, so the stable workspace id is the
    # reliable selector after reclamation. API callers should use the id if they
    # need timeout diagnostics after the runtime has stopped.
    ws = db.scalar(
        select(Workspace).where(
            Workspace.id == selector,
            Workspace.user_id == user.id,
            Workspace.status == WorkspaceStatus.STOPPED,
        )
    )
    if ws and read_workspace_stop_reason(user.id, ws.project_slug) == "idle":
        return ws
    return None


def _idle_timeout_response(request: Request, user: User, db: Session):
    ws = _idle_workspace_for_request(request, user, db)
    if not ws:
        return None
    if _browser_navigation(request):
        from urllib.parse import quote
        response = RedirectResponse(
            f"/dashboard?reason=idle&workspace={quote(ws.project_slug)}",
            status_code=303,
        )
        response.delete_cookie(settings.workspace_cookie_name, path="/")
        return response
    raise HTTPException(
        status_code=409,
        detail={
            "code": "workspace_idle_timeout",
            "message": "Workspace stopped due to inactivity. Return to /dashboard and start it again.",
            "workspace_id": ws.id,
            "project_slug": ws.project_slug,
        },
    )


@app.get("/")
async def gateway_root(request: Request, db: Session = Depends(get_db)):
    user = load_user_from_token(_request_token(request), db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    try:
        ws = selected_workspace_for_request(request, user, db)
    except HTTPException:
        idle = _idle_timeout_response(request, user, db)
        if idle is not None:
            return idle
        response = RedirectResponse("/dashboard", status_code=303)
        response.delete_cookie(settings.workspace_cookie_name, path="/")
        return response
    if not _workspace_runtime_usable(ws):
        project = ws.project_slug
        _release_workspace(ws, db, reason="recycle")
        from urllib.parse import quote
        response = RedirectResponse(
            f"/dashboard?reason=backend&workspace={quote(project)}",
            status_code=303,
        )
        response.delete_cookie(settings.workspace_cookie_name, path="/")
        return response
    return await proxy_http(request, user, db)


# Keep this catch-all last. The control-plane endpoints above remain local;
# everything else is transparently forwarded to the selected OpenCode runtime.
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def gateway_http(
    path: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        ws = selected_workspace_for_request(request, user, db)
        if not _workspace_runtime_usable(ws):
            project = ws.project_slug
            _release_workspace(ws, db, reason="recycle")
            if _browser_navigation(request):
                from urllib.parse import quote
                response = RedirectResponse(
                    f"/dashboard?reason=backend&workspace={quote(project)}",
                    status_code=303,
                )
                response.delete_cookie(settings.workspace_cookie_name, path="/")
                return response
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "workspace_backend_unavailable",
                    "message": "Workspace runtime is no longer reachable. Start it again from /dashboard.",
                    "workspace_id": ws.id,
                    "project_slug": project,
                },
            )
        return await proxy_http(request, user, db)
    except HTTPException as exc:
        if exc.status_code in (404, 409):
            idle = _idle_timeout_response(request, user, db)
            if idle is not None:
                return idle
        raise


@app.websocket("/{path:path}")
async def gateway_websocket(path: str, websocket: WebSocket):
    with SessionLocal() as db:
        await proxy_websocket(websocket, db)
