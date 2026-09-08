from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from shop_bot.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class UserContact:
    contact_type: str
    value: str
    is_primary: bool = False

    def __post_init__(self) -> None:
        if not self.contact_type.strip() or not self.value.strip():
            raise DomainValidationError("User contact type and value cannot be blank")


@dataclass(slots=True)
class User:
    id: int | None
    name_or_nick: str
    created_at: datetime | None = None
    contacts: list[UserContact] = field(default_factory=list)
    enabled: bool = True

    def __post_init__(self) -> None:
        self.rename(self.name_or_nick)

    def activate(self) -> None:
        self.enabled = True

    def block(self) -> None:
        self.enabled = False

    def rename(self, value: str) -> None:
        normalized = value.strip()
        if not normalized:
            raise DomainValidationError("User name cannot be blank")
        self.name_or_nick = normalized

    def update_contact(self, contact: UserContact) -> None:
        if contact.is_primary:
            self.contacts = [
                UserContact(item.contact_type, item.value, False)
                if item.contact_type == contact.contact_type
                else item
                for item in self.contacts
            ]
        for index, current in enumerate(self.contacts):
            if current.contact_type == contact.contact_type and current.value == contact.value:
                self.contacts[index] = contact
                return
        self.contacts.append(contact)
