from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from shop_bot.infrastructure.db.repositories.admin import AdminRepository
from shop_bot.infrastructure.db.repositories.nodes import NodeRepository
from shop_bot.infrastructure.db.repositories.payments import PaymentRepository
from shop_bot.infrastructure.db.repositories.servers import ServerRepository
from shop_bot.infrastructure.db.repositories.subscriptions import SubscriptionRepository
from shop_bot.infrastructure.db.repositories.users import UserRepository
from shop_bot.infrastructure.db.repositories.vpn import VpnRepository


class SqlAlchemyUnitOfWork:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self.connection: AsyncConnection | None = None
        self._transaction = None
        self.users: UserRepository | None = None
        self.subscriptions: SubscriptionRepository | None = None
        self.payments: PaymentRepository | None = None
        self.servers: ServerRepository | None = None
        self.vpn: VpnRepository | None = None
        self.admin: AdminRepository | None = None
        self.nodes: NodeRepository | None = None

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self.connection = await self._engine.connect()
        self._transaction = await self.connection.begin()
        self.users = UserRepository(self.connection)
        self.subscriptions = SubscriptionRepository(self.connection)
        self.payments = PaymentRepository(self.connection)
        self.servers = ServerRepository(self.connection)
        self.vpn = VpnRepository(self.connection)
        self.admin = AdminRepository(self.connection)
        self.nodes = NodeRepository(self.connection)
        return self

    async def commit(self) -> None:
        await self._transaction.commit()

    async def rollback(self) -> None:
        if self._transaction.is_active:
            await self._transaction.rollback()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc:
            await self.rollback()
        elif self._transaction.is_active:
            await self.commit()
        await self.connection.close()
