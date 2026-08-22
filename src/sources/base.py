"""Source interface."""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from models import Item


@runtime_checkable
class ItemSource(Protocol):
    name: str

    def fetch(self) -> Iterable[Item]:
        """Return every item currently visible to this source.

        Sources are not responsible for deduplication -- the caller filters
        against the SeenStore -- so returning the same items on every poll is
        both expected and correct.
        """
        ...
