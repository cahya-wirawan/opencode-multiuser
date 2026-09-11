from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html import escape

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, SessionLocal, engine, get_db
from .gateway import proxy_http, proxy_websocket, selected_workspace_for_request
from .models import Slot, User, Workspace, WorkspaceStatus
from .runtime import start_workspace, stop_workspace, workspace_secret_path
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    _seed_slots()
    settings.expanded_data_root.mkdir(parents=True, exist_ok=True)
    settings.expanded_traefik_dynamic_dir.mkdir(parents=True, exist_ok=True)
    # v6 routes all workspaces through the gateway, so v5's per-workspace
    # Traefik files must not remain active after an upgrade.
    cleanup_legacy_workspace_routes()
    yield


app = FastAPI(
    title="OpenCode Multiuser Gateway",
    version="0.3.0",
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
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.workspace_cookie_name, path="/")
    return response


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse(
        """<!doctype html>
<html><head><meta charset="utf-8"><title>OpenCode Login</title>
<style>body{font-family:system-ui;max-width:520px;margin:10vh auto;padding:24px}input,button{font:inherit;padding:10px;margin:6px 0;width:100%;box-sizing:border-box}.error{color:#b00}</style></head>
<body><h1>OpenCode</h1><p>Sign in to the multi-user gateway.</p>
<form id="login"><input id="username" placeholder="Username" autocomplete="username" required>
<input id="password" type="password" placeholder="Password" autocomplete="current-password" required>
<button>Sign in</button></form><p id="error" class="error"></p>
<script>
document.getElementById('login').addEventListener('submit', async (e) => {
  e.preventDefault();
  const r = await fetch('/auth/login', {method:'POST', headers:{'content-type':'application/json'},
    body: JSON.stringify({username:document.getElementById('username').value,password:document.getElementById('password').value})});
  if (r.ok) location.href='/dashboard'; else document.getElementById('error').textContent=(await r.json()).detail || 'Login failed';
});
</script></body></html>"""
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(user: User = Depends(current_user)):
    username = escape(user.username)
    return HTMLResponse(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><title>OpenCode Workspaces</title>
<style>body{{font-family:system-ui;max-width:900px;margin:40px auto;padding:20px}}button,input{{font:inherit;padding:8px}}.ws{{padding:12px;border:1px solid #ddd;margin:8px 0;border-radius:8px}}a{{margin-right:10px}}</style></head>
<body><div style="float:right"><a href="/logout">Logout</a></div><h1>OpenCode Workspaces</h1><p>Signed in as <b>{username}</b></p>
<form id="new"><input id="project" required pattern="[A-Za-z0-9_.-]+" placeholder="project-slug"><button>Start workspace</button></form>
<div id="list">Loading…</div>
<script>
async function api(url, opts={{}}) {{ const r=await fetch(url, opts); if(!r.ok) throw new Error((await r.json()).detail || r.statusText); return r.json(); }}
async function refresh() {{ const rows=await api('/workspaces'); const el=document.getElementById('list'); el.innerHTML='';
 rows.forEach(w => {{ const d=document.createElement('div'); d.className='ws'; d.innerHTML=`<b>${{w.project_slug}}</b> — ${{w.status}} — slot ${{w.slot_id ?? '-'}} `;
 if(w.status==='running' && w.url) {{ const a=document.createElement('a'); a.href=w.url; a.textContent='Open'; d.appendChild(a); const b=document.createElement('button'); b.textContent='Stop'; b.onclick=async()=>{{await api('/workspaces/'+w.id+'/stop',{{method:'POST'}});refresh();}}; d.appendChild(b); }} else if(w.status==='running' && !w.url) {{ const u=document.createElement('button'); u.textContent='Upgrade workspace'; u.onclick=async()=>{{const x=await api('/workspaces/start',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{project_slug:w.project_slug}})}});location.href=x.url;}}; d.appendChild(u); }} el.appendChild(d); }}); }}
document.getElementById('new').onsubmit=async(e)=>{{e.preventDefault(); try{{ const w=await api('/workspaces/start',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{project_slug:project.value}})}}); location.href=w.url; }}catch(err){{alert(err.message)}} }};
refresh().catch(e=>document.getElementById('list').textContent=e.message);
</script></body></html>"""
    )


@app.get("/workspaces", response_model=list[WorkspaceResponse])
def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Workspace).where(Workspace.user_id == user.id).order_by(Workspace.created_at.desc())).all()
    return [_workspace_response(x) for x in rows]


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
        if existing.status == WorkspaceStatus.RUNNING and workspace_secret_path(user.id, req.project_slug).is_file():
            return _workspace_response(existing)
        stop_workspace(existing.container_name)
        if existing.slot_id:
            old_slot = db.get(Slot, existing.slot_id)
            if old_slot:
                old_slot.in_use = False
                old_slot.workspace_id = None
        existing.status = WorkspaceStatus.STOPPED
        existing.container_name = None
        existing.slot_id = None
        db.commit()

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
    if ws.status in (WorkspaceStatus.STOPPED, WorkspaceStatus.ERROR):
        return _workspace_response(ws)

    ws.status = WorkspaceStatus.STOPPING
    db.commit()
    stop_workspace(ws.container_name)
    if ws.slot_id:
        slot = db.get(Slot, ws.slot_id)
        if slot:
            slot.in_use = False
            slot.workspace_id = None
    ws.status = WorkspaceStatus.STOPPED
    ws.stopped_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(ws)
    return _workspace_response(ws)


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


@app.get("/")
async def gateway_root(request: Request, db: Session = Depends(get_db)):
    user = load_user_from_token(_request_token(request), db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    try:
        selected_workspace_for_request(request, user, db)
    except HTTPException:
        return RedirectResponse("/dashboard", status_code=303)
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
    return await proxy_http(request, user, db)


@app.websocket("/{path:path}")
async def gateway_websocket(path: str, websocket: WebSocket):
    with SessionLocal() as db:
        await proxy_websocket(websocket, db)
