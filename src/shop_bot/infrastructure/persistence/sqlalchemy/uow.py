from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from shop_bot.infrastructure.persistence.repositories import (
    AdminRepository,
    AuditRepository,
    NodeRepository,
    PaymentRepository,
    ServerRepository,
    SubscriptionRepository,
    UserRepository,
    VpnRepository,
)


class SqlAlchemyUnitOfWork:
    """Transaction boundary and repository factory for one application use case."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self.connection: AsyncConnection | None = None
        self._transaction: AsyncTransaction | None = None
        self.users: UserRepository
        self.subscriptions: SubscriptionRepository
        self.payments: PaymentRepository
        self.servers: ServerRepository
        self.vpn: VpnRepository
        self.admin: AdminRepository
        self.nodes: NodeRepository
        self.audit: AuditRepository

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
        self.audit = AuditRepository(self.connection)
        return self

    async def commit(self) -> None:
        if self._transaction is not None and self._transaction.is_active:
            await self._transaction.commit()

    async def rollback(self) -> None:
        if self._transaction is not None and self._transaction.is_active:
            await self._transaction.rollback()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc is not None:
                await self.rollback()
            else:
                await self.commit()
        finally:
            if self.connection is not None:
                await self.connection.close()
