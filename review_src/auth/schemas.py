from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)
    turnstile_token: str | None = None

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("兩次密碼輸入不一致")
        return self

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        email = normalize_email(value)
        if not EMAIL_RE.match(email):
            raise ValueError("Email 格式不正確")
        return email


class VerifyEmailRequest(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=12)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        email = normalize_email(value)
        if not EMAIL_RE.match(email):
            raise ValueError("Email 格式不正確")
        return email


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)
    turnstile_token: str | None = None

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        email = normalize_email(value)
        if not EMAIL_RE.match(email):
            raise ValueError("Email 格式不正確")
        return email


class ResendVerificationRequest(BaseModel):
    email: str
    turnstile_token: str | None = None

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        email = normalize_email(value)
        if not EMAIL_RE.match(email):
            raise ValueError("Email 格式不正確")
        return email


class PasswordResetRequest(BaseModel):
    email: str
    turnstile_token: str | None = None

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        email = normalize_email(value)
        if not EMAIL_RE.match(email):
            raise ValueError("Email 格式不正確")
        return email


class PasswordResetConfirmRequest(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=12)
    new_password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("兩次新密碼輸入不一致")
        return self

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        email = normalize_email(value)
        if not EMAIL_RE.match(email):
            raise ValueError("Email 格式不正確")
        return email


class WatchlistAddRequest(BaseModel):
    query: str = Field(min_length=1, max_length=40)


class PasswordChangeRequest(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("兩次新密碼輸入不一致")
        return self


class WatchlistReorderRequest(BaseModel):
    codes: list[str]


class ApiResponse(BaseModel):
    ok: bool
    message: str | None = None
    data: dict[str, Any] | None = None
