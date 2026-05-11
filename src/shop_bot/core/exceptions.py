class ShopBotError(Exception):
    """Base application error."""


class NotFoundError(ShopBotError):
    """Requested entity was not found."""


class ConflictError(ShopBotError):
    """Requested state transition conflicts with existing data."""


class ValidationError(ShopBotError):
    """Business validation error."""
