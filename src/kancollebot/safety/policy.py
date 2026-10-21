"""Explicit safety gates for any future gameplay action adapter."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    allow_live_actions: bool = False
    require_explicit_confirmation: bool = True

    def authorize_live_action(self, *, confirmed: bool = False) -> None:
        """Raise unless live actions are enabled and required confirmation exists."""
        if not self.allow_live_actions:
            raise PermissionError("Live gameplay actions are disabled by policy")
        if self.require_explicit_confirmation and not confirmed:
            raise PermissionError("Explicit confirmation is required for live actions")
