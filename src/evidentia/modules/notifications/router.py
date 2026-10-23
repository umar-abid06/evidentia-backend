from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from evidentia.core.errors import UnauthorizedError
from evidentia.core.security import decode_token
from evidentia.modules.auth.models import UserRole
from evidentia.modules.notifications.hub import NotificationHub

router = APIRouter(tags=["notifications"])

STAFF_ROLES = {UserRole.ADMIN.value, UserRole.REVIEWER.value}


@router.websocket("/ws/admin/notifications")
async def admin_notifications(websocket: WebSocket, token: str = Query()) -> None:
    """Live processing events for staff. Browsers cannot set headers on WebSocket
    handshakes, so the short-lived access token travels as a query parameter."""
    settings = websocket.app.state.settings
    try:
        claims = decode_token(token, "access", settings.auth)
    except UnauthorizedError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if claims.role not in STAFF_ROLES:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    hub: NotificationHub = websocket.app.state.notifications
    await websocket.accept()
    hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive; clients send pings
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(websocket)
