from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import settings
from .db import Base, SessionLocal, engine, get_db
from .models import Slot, User, Workspace, WorkspaceStatus
from .runtime import start_workspace, stop_workspace
from .schemas import LoginRequest, RegisterRequest, TokenResponse, WorkspaceResponse, WorkspaceStartRequest
from .security import current_user, hash_password, issue_token, verify_password
from .traefik import remove_route, write_route


def _workspace_response(ws: Workspace) -> WorkspaceResponse:
    url = None
    if ws.status == WorkspaceStatus.RUNNING:
        from .traefik import workspace_host
        url = f"{settings.workspace_scheme}://{workspace_host(ws.id)}"
    return WorkspaceResponse(id=ws.id, project_slug=ws.project_slug, status=ws.status.value, url=url, slot_id=ws.slot_id)


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
    yield


app = FastAPI(title="OpenCode Multiuser Control Plane", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
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
    return TokenResponse(access_token=issue_token(user))


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == req.username, User.is_active.is_(True)))
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenResponse(access_token=issue_token(user))


@app.get("/workspaces", response_model=list[WorkspaceResponse])
def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Workspace).where(Workspace.user_id == user.id).order_by(Workspace.created_at.desc())).all()
    return [_workspace_response(x) for x in rows]


@app.post("/workspaces/start", response_model=WorkspaceResponse)
def start(req: WorkspaceStartRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # Return an already running workspace for the same user/project.
    existing = db.scalar(select(Workspace).where(
        Workspace.user_id == user.id,
        Workspace.project_slug == req.project_slug,
        Workspace.status.in_([WorkspaceStatus.STARTING, WorkspaceStatus.RUNNING]),
    ))
    if existing:
        return _workspace_response(existing)

    # PostgreSQL uses FOR UPDATE SKIP LOCKED. SQLite ignores these hints, useful for local development only.
    slot_stmt = select(Slot).where(Slot.in_use.is_(False)).order_by(Slot.id).limit(1)
    if not settings.database_url.startswith("sqlite"):
        slot_stmt = slot_stmt.with_for_update(skip_locked=True)
    slot = db.scalar(slot_stmt)
    if not slot:
        raise HTTPException(status_code=503, detail="No workspace capacity available")

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
        write_route(ws.id, runtime.container_name)
        db.commit()
        db.refresh(ws)
        # The Basic auth password stays only inside the runtime environment in this MVP.
        # Production UI should proxy OpenCode and inject the credential server-side.
        return _workspace_response(ws)
    except Exception as exc:
        stop_workspace(ws.container_name)
        remove_route(ws.id)
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
    remove_route(ws.id)
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
