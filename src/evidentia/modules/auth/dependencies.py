from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from evidentia.api.deps import SessionDep, SettingsDep
from evidentia.core.errors import ForbiddenError, UnauthorizedError
from evidentia.core.security import decode_token
from evidentia.modules.auth.models import User, UserRole
from evidentia.modules.auth.service import AuthService

_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    return AuthService(session, settings.auth)


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise UnauthorizedError()
    claims = decode_token(credentials.credentials, "access", settings.auth)
    user = await session.get(User, claims.sub)
    if user is None or not user.is_active:
        raise UnauthorizedError("Account is no longer active.", code="account_disabled")
    return user


def require_roles(*roles: UserRole) -> Callable[[User], Awaitable[User]]:
    async def _check(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise ForbiddenError("Your role does not allow this action.")
        return user

    return _check


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CurrentUser = Annotated[User, Depends(get_current_user)]
StaffUser = Annotated[User, Depends(require_roles(UserRole.REVIEWER, UserRole.ADMIN))]
AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
