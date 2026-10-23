"""Operational commands: ``evidentia <command> [options]``."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from evidentia.core.config import get_settings
from evidentia.db.registry import load_models
from evidentia.db.session import Database
from evidentia.modules.auth.models import UserRole
from evidentia.modules.auth.schemas import RegisterRequest
from evidentia.modules.auth.service import AuthService


async def _create_admin(email: str, password: str, full_name: str | None) -> None:
    settings = get_settings()
    load_models()
    db = Database(settings.database, null_pool=True)
    try:
        async with db.session() as session:
            payload = RegisterRequest(email=email, password=password, full_name=full_name)
            user = await AuthService(session, settings.auth).register(payload, role=UserRole.ADMIN)
            print(f"created admin {user.email} ({user.id})")
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evidentia")
    commands = parser.add_subparsers(dest="command", required=True)

    admin = commands.add_parser("create-admin", help="Create an administrator account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--full-name", default=None)

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        password = getpass.getpass("Password (12+ chars): ")
        asyncio.run(_create_admin(args.email, password, args.full_name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
