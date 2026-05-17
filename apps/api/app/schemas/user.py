from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime
from enum import Enum

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"

class UserBase(BaseModel):
    email: EmailStr
    name: str
    username: Optional[str] = None
    phone: Optional[str] = None

class UserCreate(UserBase):
    password: str = Field(..., min_length=8)

class UserLogin(BaseModel):
    email: EmailStr
    password: str
    mfa_code: Optional[str] = Field(default=None, min_length=6, max_length=6)

class UserResponse(UserBase):
    id: Optional[str] = None
    openId: Optional[str] = None
    role: UserRole = UserRole.USER
    mfa_enabled: bool = False
    mfa_verified: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_signed_in: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class MFASetup(BaseModel):
    secret: str
    qr_code: str

class MFAVerify(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class MFAStatusResponse(BaseModel):
    mfa_enabled: bool
    mfa_verified: bool


class MFADisable(BaseModel):
    password: str = Field(..., min_length=8)


class MFALoginChallenge(BaseModel):
    mfa_required: bool = True
    message: str

class PasswordReset(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


class PasswordResetResponse(BaseModel):
    status: str
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=8)
    new_password: str = Field(..., min_length=8)


class UserProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    username: Optional[str] = Field(default=None, min_length=3, max_length=32)

class UserUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    
    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
