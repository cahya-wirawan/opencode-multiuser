from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str | None = None


class WorkspaceStartRequest(BaseModel):
    project_slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")


class WorkspaceResponse(BaseModel):
    id: str
    project_slug: str
    status: str
    url: str | None = None
    slot_id: int | None = None
    container_name: str | None = None


class AdminRoleUpdate(BaseModel):
    role: str = Field(pattern=r"^(admin|developer)$")


class AdminStatusUpdate(BaseModel):
    is_active: bool


class AdminPasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=256)
