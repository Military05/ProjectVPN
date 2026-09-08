class DomainError(Exception):
    """Base error raised when a domain invariant is violated."""


class InvalidStateTransition(DomainError):
    """The requested state transition is not allowed for the current entity state."""


class DomainValidationError(DomainError):
    """A value violates a domain invariant."""


class PaymentInvariantViolation(DomainError):
    """A verified payment event does not satisfy settlement invariants."""
