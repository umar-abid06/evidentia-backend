from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evidentia.core.config import AuthSettings
from evidentia.core.errors import ConflictError, NotFoundError, UnauthorizedError
from evidentia.core.security import create_token, decode_token, hash_password, verify_password
from evidentia.modules.auth.models import User, UserRole
from evidentia.modules.auth.schemas import RegisterRequest, TokenPair, UserUpdate


class AuthService:
    def __init__(self, session: AsyncSession, config: AuthSettings) -> None:
        self.session = session
        self.config = config

    async def register(self, data: RegisterRequest, *, role: UserRole = UserRole.CUSTOMER) -> User:
        email = data.email.lower()
        if await self._find_by_email(email) is not None:
            raise ConflictError("An account with this email already exists.", code="email_taken")
        user = User(
            email=email,
            password_hash=hash_password(data.password),
            full_name=data.full_name,
            role=role,
            country_code=data.country_code,
            preferred_language=data.preferred_language,
        )
        self.session.add(user)
        await self.session.commit()
        return user

    async def authenticate(self, email: str, password: str) -> TokenPair:
        user = await self._find_by_email(email.lower())
        if not verify_password(password, user.password_hash if user else None) or user is None:
            raise UnauthorizedError("Invalid email or password.", code="invalid_credentials")
        if not user.is_active:
            raise UnauthorizedError("Account is disabled.", code="account_disabled")
        user.last_login_at = datetime.now(UTC)
        await self.session.commit()
        return self.issue_tokens(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        claims = decode_token(refresh_token, "refresh", self.config)
        user = await self.session.get(User, claims.sub)
        if user is None or not user.is_active:
            raise UnauthorizedError("Account is no longer active.", code="account_disabled")
        return self.issue_tokens(user)

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def update_profile(self, user: User, data: UserUpdate) -> User:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        await self.session.commit()
        return user

    def issue_tokens(self, user: User) -> TokenPair:
        return TokenPair(
            access_token=create_token(user.id, user.role.value, "access", self.config),
            refresh_token=create_token(user.id, user.role.value, "refresh", self.config),
            expires_in=self.config.access_ttl_minutes * 60,
        )

    async def _find_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()
