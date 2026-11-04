"""Task-scoped automation policy for KancolleBot.

This project is NOT expedition-only. Expedition automation is the first
implemented task; combat, sparkle (morale grinding), fleet composition and
quest automation are declared future tasks. The important consequence is that
anything decided *for expeditions* must not silently become a global law about
the game or about a fleet.

The clearest example is Fleet 1. ``RuntimeConfig.non_dispatchable_fleet_ids``
reserves Fleet 1 because it is the flagship **sortie/combat** fleet and the
game does not offer it on the expedition tab strip. That is an EXPEDITION
policy. Combat automation will need Fleet 1, so reusing the expedition policy
there would reject it before a single click. Fleet policy is therefore indexed
by ``TaskKind`` here, not read from a single global set.

Only ``TaskKind.EXPEDITION`` currently has a reviewed, visible-UI execution
path. Every other declared task fails closed: its fleet policy is deliberately
empty and uncalibrated rather than guessed, and ``require_task_enabled`` must
be called before any UI action. Declaring a task here is NOT implementing it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kancollebot.runtime_config import RUNTIME_CONFIG, RuntimeConfig


class TaskScopeError(RuntimeError):
    """A task was asked to act outside its declared, calibrated scope."""


class TaskKind(str, Enum):
    """Automation tasks this project covers, now or in the future.

    EXPEDITION is the only implemented one. The other four are the declared
    roadmap items (combat, sparkle/morale, fleet composition, quests); they
    exist here so their policies, screens and evidence can be added without
    borrowing expedition assumptions.
    """

    EXPEDITION = "expedition"
    COMBAT = "combat"
    SPARKLE = "sparkle"
    COMPOSITION = "composition"
    QUESTS = "quests"


IMPLEMENTED_TASK_KINDS: frozenset[TaskKind] = frozenset({TaskKind.EXPEDITION})


# Infrastructure that is genuinely shared by every automation task. These
# modules must stay task-neutral: adding combat/quest logic inside them would
# couple every future task to expedition semantics. The list is documentation
# that is also queryable at runtime (used by `kancollebot tasks`).
SHARED_CAPABILITIES: tuple[tuple[str, str], ...] = (
    (
        "kancollebot.adapters.poi.ui_operation_lock",
        "Cross-process mutex for the single shared Poi UI; every task must use it",
    ),
    (
        "kancollebot.adapters.poi.cdp_fleet_state",
        "Read-only fleet/ship snapshot from Poi Redux (task-neutral data)",
    ),
    (
        "kancollebot.adapters.poi.full_supply",
        "Supply preflight and verification; resupply is needed before sortie too",
    ),
    (
        "kancollebot.expeditions.mission_eligibility",
        "Generic fleet composition snapshot and requirement shapes",
    ),
    (
        "kancollebot.expeditions.continuous_dispatch_controller",
        "Append-only event sink shared by every controller in a run",
    ),
    (
        "kancollebot.adapters.poi.visual_target",
        "Visual dependency gate and screen coordinate primitives",
    ),
)


@dataclass(frozen=True, slots=True)
class FleetPolicy:
    """Fleet permissions and calibrated evidence for ONE task kind."""

    task_kind: TaskKind
    dispatchable_fleet_ids: tuple[int, ...]
    reserved_fleet_ids: tuple[int, ...]
    resupply_verified_fleet_ids: tuple[int, ...]
    implemented: bool
    ui_click_capable: bool
    note: str


_UNCALIBRATED_NOTE = (
    "declared roadmap task, not implemented; its fleet policy, screens, "
    "coordinates and visual gates must be calibrated on a real client before "
    "any UI action. Expedition fleet policy must not be reused."
)


def _expedition_policy(config: RuntimeConfig) -> FleetPolicy:
    return FleetPolicy(
        task_kind=TaskKind.EXPEDITION,
        # Derived, never copied: RuntimeConfig stays the single source of truth
        # for the expedition policy ceiling.
        dispatchable_fleet_ids=config.dispatchable_fleet_ids,
        reserved_fleet_ids=config.non_dispatchable_fleet_ids,
        resupply_verified_fleet_ids=config.resupply_verified_fleet_ids,
        implemented=TaskKind.EXPEDITION in IMPLEMENTED_TASK_KINDS,
        ui_click_capable=True,
        note=(
            "Only task with a reviewed visible-UI path. Fleet 1 is reserved "
            "FOR EXPEDITIONS ONLY: it is the sortie/combat fleet, so combat "
            "automation must declare its own policy instead of reusing this one."
        ),
    )


def coerce_task_kind(task_kind: TaskKind | str) -> TaskKind:
    """Accept an enum or its string value; reject anything else fail-closed."""
    if isinstance(task_kind, TaskKind):
        return task_kind
    if isinstance(task_kind, str):
        try:
            return TaskKind(task_kind)
        except ValueError as exc:
            raise TaskScopeError(
                f"unknown task kind {task_kind!r}; declared kinds are "
                f"{[kind.value for kind in TaskKind]}"
            ) from exc
    raise TaskScopeError(f"task kind must be a TaskKind or its string value, got {task_kind!r}")


def fleet_policy_for(
    task_kind: TaskKind | str, *, config: RuntimeConfig = RUNTIME_CONFIG
) -> FleetPolicy:
    """Return the fleet policy for a task, without borrowing another task's.

    Unimplemented tasks get an EMPTY, uncalibrated policy. Returning the
    expedition policy for them would be the exact bug this module exists to
    prevent: a combat run would inherit "Fleet 1 is forbidden".
    """
    kind = coerce_task_kind(task_kind)
    if kind == TaskKind.EXPEDITION:
        if config.dispatch_policy_task_kind != TaskKind.EXPEDITION.value:
            raise TaskScopeError(
                "RuntimeConfig dispatch policy is scoped to "
                f"{config.dispatch_policy_task_kind!r}, not 'expedition'; "
                "refusing to apply it as the expedition policy"
            )
        return _expedition_policy(config)
    return FleetPolicy(
        task_kind=kind,
        dispatchable_fleet_ids=(),
        reserved_fleet_ids=(),
        resupply_verified_fleet_ids=(),
        implemented=False,
        ui_click_capable=False,
        note=_UNCALIBRATED_NOTE,
    )


def require_task_enabled(
    task_kind: TaskKind | str, *, config: RuntimeConfig = RUNTIME_CONFIG
) -> FleetPolicy:
    """Fail closed before any UI action for a task that is not implemented.

    Call this in a composition root. It is deliberately stricter than checking
    ``implemented`` alone: a task may be wired but still lack calibrated visual
    evidence, and clicking anyway would be an unreviewed game action.
    """
    policy = fleet_policy_for(task_kind, config=config)
    if not policy.implemented or not policy.ui_click_capable:
        raise TaskScopeError(
            f"task {policy.task_kind.value!r} is declared but not implemented; "
            "no fleet policy, screen definitions or visual gates are calibrated "
            "for it. Refusing to act and refusing to reuse expedition policy; "
            "nothing was clicked."
        )
    return policy


def task_event_fields(task_kind: TaskKind | str) -> dict[str, object]:
    """Structured attribution fields for event logs / evidence chains.

    Every task's events must carry these so a mixed log can be split by task
    instead of being guessed from free-form event name prefixes.
    """
    policy = fleet_policy_for(task_kind)
    return {
        "task_kind": policy.task_kind.value,
        "task_implemented": policy.implemented,
        "task_ui_click_capable": policy.ui_click_capable,
    }


def declared_task_kinds() -> tuple[TaskKind, ...]:
    return tuple(TaskKind)


def implemented_task_kinds() -> tuple[TaskKind, ...]:
    return tuple(kind for kind in TaskKind if kind in IMPLEMENTED_TASK_KINDS)


def describe_shared_capabilities() -> tuple[str, ...]:
    return tuple(f"{module}: {reason}" for module, reason in SHARED_CAPABILITIES)
