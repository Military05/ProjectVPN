class ShopBotError(Exception):
    """Base application error."""


class NotFoundError(ShopBotError):
    """Requested entity was not found."""


class ConflictError(ShopBotError):
    """Requested state transition conflicts with existing data."""


class ValidationError(ShopBotError):
    """Business validation error."""


class WebhookAuthenticationError(ShopBotError):
    """Incoming payment webhook authenticity could not be established."""


class WebhookPayloadError(ShopBotError):
    """Incoming payment webhook payload is malformed or unusable."""
