from __future__ import annotations

from fastapi import APIRouter, status

from evidentia.modules.auth.dependencies import AuthServiceDep, CurrentUser
from evidentia.modules.auth.models import User
from evidentia.modules.auth.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserRead,
    UserUpdate,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, service: AuthServiceDep) -> User:
    return await service.register(payload)


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, service: AuthServiceDep) -> TokenPair:
    return await service.authenticate(payload.email, payload.password)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, service: AuthServiceDep) -> TokenPair:
    return await service.refresh(payload.refresh_token)


@router.get("/me", response_model=UserRead)
async def read_me(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserRead)
async def update_me(payload: UserUpdate, user: CurrentUser, service: AuthServiceDep) -> User:
    return await service.update_profile(user, payload)
