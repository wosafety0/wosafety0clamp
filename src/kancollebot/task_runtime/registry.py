"""DESIGN REFERENCE — general task registration (NOT wired into production).

A registration is a *capability declaration*, not a runnable task. Declaring
combat here does not implement combat, does not grant UI access and does not
change any existing expedition behaviour. See docs/DESIGN_multi_task_runtime.md.

The registry deliberately does not duplicate policy rules: fleet policy comes
from ``task_scope.fleet_policy_for`` and the enabled gate delegates to
``task_scope.require_task_enabled``. Its own job is to enforce the pairings a
plain dataclass cannot: a policy must belong to the task it is registered for,
and an enabled task must not depend on expedition-bound capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kancollebot.runtime_config import RUNTIME_CONFIG, RuntimeConfig
from kancollebot.task_scope import (
    SHARED_CAPABILITIES,
    FleetPolicy,
    TaskKind,
    TaskScopeError,
    coerce_task_kind,
    fleet_policy_for,
    require_task_enabled,
    require_task_neutral_capability,
)


class TaskRegistryError(RuntimeError):
    """A registration violates the multi-task isolation contract."""


class RegistrationStatus(str, Enum):
    DECLARED = "declared"
    CALIBRATED = "calibrated"
    ENABLED = "enabled"


_KNOWN_CAPABILITIES = frozenset(item.name for item in SHARED_CAPABILITIES)


@dataclass(frozen=True, slots=True)
class TaskRegistration:
    """Everything a composition root must know before letting a task act."""

    task_kind: TaskKind
    status: RegistrationStatus
    fleet_policy: FleetPolicy
    screen_ids: tuple[str, ...] = ()
    visual_gates_verified: bool = False
    required_capabilities: tuple[str, ...] = ()
    authorization_required: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.task_kind, TaskKind):
            raise TaskRegistryError("task_kind must be a TaskKind")
        if not isinstance(self.status, RegistrationStatus):
            raise TaskRegistryError("status must be a RegistrationStatus")
        # `dataclasses.replace` can forge a mismatched policy on a frozen
        # dataclass, so the pairing is verified here rather than assumed.
        if self.fleet_policy.task_kind is not self.task_kind:
            raise TaskRegistryError(
                f"fleet policy belongs to {self.fleet_policy.task_kind.value!r}, "
                f"not {self.task_kind.value!r}; a task must not borrow another "
                "task's fleet policy"
            )
        if not isinstance(self.authorization_required, bool) or not self.authorization_required:
            raise TaskRegistryError(
                "authorization_required must stay True; explicit operator "
                "authorization cannot be disabled by a registration"
            )
        unknown = tuple(
            name for name in self.required_capabilities
            if name not in _KNOWN_CAPABILITIES
        )
        if unknown:
            raise TaskRegistryError(
                f"unknown required capabilities {list(unknown)}; declared names are "
                f"{sorted(_KNOWN_CAPABILITIES)}"
            )
        if self.visual_gates_verified and not self.screen_ids:
            raise TaskRegistryError(
                "visual_gates_verified requires at least one calibrated screen id"
            )
        if self.status is RegistrationStatus.CALIBRATED:
            if not self.visual_gates_verified or not self.screen_ids:
                raise TaskRegistryError(
                    "CALIBRATED requires verified visual gates and screen ids"
                )
        if self.status is RegistrationStatus.ENABLED:
            if not self.fleet_policy.implemented or not self.fleet_policy.ui_click_capable:
                raise TaskRegistryError(
                    f"task {self.task_kind.value!r} is not implemented; it cannot be ENABLED"
                )
            if not self.visual_gates_verified or not self.screen_ids:
                raise TaskRegistryError(
                    "ENABLED requires calibrated screens and verified visual gates"
                )
            for name in self.required_capabilities:
                try:
                    require_task_neutral_capability(name)
                except TaskScopeError as exc:
                    raise TaskRegistryError(
                        f"task {self.task_kind.value!r} cannot be ENABLED while it "
                        f"depends on a non-task-neutral capability: {exc}"
                    ) from exc


class TaskRegistry:
    """In-memory, offline registry. No Poi, no clicks, no persistent store."""

    def __init__(self, *, config: RuntimeConfig = RUNTIME_CONFIG) -> None:
        self._config = config
        self._registrations: dict[TaskKind, TaskRegistration] = {}

    def register(self, registration: TaskRegistration) -> TaskRegistration:
        if not isinstance(registration, TaskRegistration):
            raise TaskRegistryError("register() expects a TaskRegistration")
        # The policy must be EXACTLY the one task_scope derives for this kind.
        # Checking only `policy.task_kind` is insufficient: `dataclasses.replace`
        # can forge "expedition policy content with a combat label", which would
        # smuggle Fleet 1's reservation (and the whole expedition ceiling) into
        # another task. There is only one source of truth for fleet policy.
        expected_policy = fleet_policy_for(registration.task_kind, config=self._config)
        if registration.fleet_policy != expected_policy:
            raise TaskRegistryError(
                f"fleet policy for {registration.task_kind.value!r} is not the "
                "task_scope-derived policy; a task must not borrow or forge "
                "another task's fleet policy"
            )
        if registration.status is RegistrationStatus.ENABLED:
            # Second gate: the registry does not own the implemented/calibrated
            # rules, it defers to task_scope so there is only one source of truth.
            try:
                require_task_enabled(registration.task_kind, config=self._config)
            except TaskScopeError as exc:
                raise TaskRegistryError(
                    f"task_scope refused to enable {registration.task_kind.value!r}: {exc}"
                ) from exc
        existing = self._registrations.get(registration.task_kind)
        if existing is not None:
            if existing == registration:
                return existing
            raise TaskRegistryError(
                f"task {registration.task_kind.value!r} is already registered with "
                "different content; refusing to overwrite silently"
            )
        self._registrations[registration.task_kind] = registration
        return registration

    def get(self, task_kind: TaskKind | str) -> TaskRegistration | None:
        return self._registrations.get(coerce_task_kind(task_kind))

    def require_enabled(self, task_kind: TaskKind | str) -> TaskRegistration:
        kind = coerce_task_kind(task_kind)
        registration = self._registrations.get(kind)
        if registration is None:
            raise TaskRegistryError(
                f"task {kind.value!r} is not registered; nothing was clicked"
            )
        if registration.status is not RegistrationStatus.ENABLED:
            raise TaskRegistryError(
                f"task {kind.value!r} is {registration.status.value}, not enabled; "
                "nothing was clicked"
            )
        try:
            require_task_enabled(kind, config=self._config)
        except TaskScopeError as exc:
            raise TaskRegistryError(
                f"task_scope refused {kind.value!r}: {exc}"
            ) from exc
        return registration

    def registrations(self) -> tuple[TaskRegistration, ...]:
        return tuple(self._registrations.values())

    def describe(self) -> tuple[str, ...]:
        return tuple(
            f"{item.task_kind.value:<12} {item.status.value:<11} "
            f"screens={list(item.screen_ids)} "
            f"capabilities={list(item.required_capabilities)}"
            for item in self._registrations.values()
        )

    def __len__(self) -> int:
        return len(self._registrations)

    def __contains__(self, task_kind: object) -> bool:
        try:
            return coerce_task_kind(task_kind) in self._registrations  # type: ignore[arg-type]
        except TaskScopeError:
            return False
