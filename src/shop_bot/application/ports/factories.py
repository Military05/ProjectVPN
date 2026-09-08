from __future__ import annotations

from collections.abc import Callable

from shop_bot.domain.repositories.interfaces import UnitOfWork

UnitOfWorkFactory = Callable[[], UnitOfWork]
