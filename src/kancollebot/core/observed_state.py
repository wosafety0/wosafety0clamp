"""Freshness checks for observed state; this module performs no I/O."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ObservedState(Generic[T]):
    """A payload tagged with the time and source of its observation."""

    payload: T
    observed_at: datetime
    source: str
    source_freshness_verified: bool = False

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not self.source.strip():
            raise ValueError("source must not be empty")
        if not isinstance(self.source_freshness_verified, bool):
            raise ValueError("source_freshness_verified must be a bool")

    def require_source_freshness_verified(self) -> None:
        """Refuse safety-sensitive use without independent source freshness evidence.

        A recent local observation timestamp alone is not evidence that the
        underlying source data was recently updated.
        """
        if not self.source_freshness_verified:
            raise ValueError("Source data freshness has not been verified")

    def is_fresh(
        self,
        *,
        now: datetime,
        max_age: timedelta,
    ) -> bool:
        """Return whether this observation is within the permitted age.

        Future-dated observations are rejected rather than treated as fresh.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if max_age < timedelta(0):
            raise ValueError("max_age must not be negative")
        age = now - self.observed_at
        return timedelta(0) <= age <= max_age

    def require_fresh(
        self,
        *,
        now: datetime,
        max_age: timedelta,
    ) -> None:
        """Raise when the state is stale or future-dated."""
        if not self.is_fresh(now=now, max_age=max_age):
            raise ValueError("Observed state is stale or has an invalid timestamp")
