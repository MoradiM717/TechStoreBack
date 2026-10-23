from pydantic import BaseModel, ConfigDict, EmailStr


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    full_name: str | None = None
    role: str
    is_active: bool


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    role: str = "editor"
