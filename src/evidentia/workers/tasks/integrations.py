from __future__ import annotations

import uuid

import httpx

from evidentia.core.crypto import SecretBox
from evidentia.integrations.crm import CrmClient
from evidentia.modules.admin.store import SettingsStore
from evidentia.modules.auth.models import User
from evidentia.workers.celery_app import celery_app
from evidentia.workers.names import SYNC_CRM_CONTACT
from evidentia.workers.runtime import WorkerContext, run_job


async def _sync(ctx: WorkerContext, user_id: uuid.UUID) -> None:
    async with ctx.db.session() as session:
        store = SettingsStore(
            session, ctx.settings, SecretBox(ctx.settings.security.encryption_keys)
        )
        url = await store.get("crm.webhook_url")
        user = await session.get(User, user_id)
        if not url or user is None:
            return
        async with httpx.AsyncClient(timeout=10.0) as http:
            await CrmClient(http, url, await store.get("crm.api_key")).upsert_contact(
                {
                    "external_id": str(user.id),
                    "email": user.email,
                    "full_name": user.full_name,
                    "country": user.country_code,
                    "language": user.preferred_language,
                    "active": user.is_active,
                }
            )


@celery_app.task(
    name=SYNC_CRM_CONTACT,
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
    acks_late=True,
)
def sync_crm_contact(user_id: str) -> None:
    run_job(lambda ctx: _sync(ctx, uuid.UUID(user_id)), user_id=user_id)
