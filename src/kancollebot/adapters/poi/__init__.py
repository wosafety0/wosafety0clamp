"""POI integration contracts; no network connection is opened here."""

from typing import Protocol


class PoiReadOnlyAdapter(Protocol):
    """Interface for a verified adapter that only reads observed state."""

    def get_status(self) -> dict[str, object]:
        """Return a sanitized status snapshot without credentials or raw cookies."""
        ...
