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


class CapabilityBoundary(str, Enum):
    """How far a module/symbol really is from being usable by every task.

    The old inventory called six whole *modules* "task-neutral". That was too
    coarse: a module can hold one genuinely shared primitive and, next to it,
    expedition-only coordinates or dispatch logic. Boundary is therefore
    recorded per SYMBOL, and only ``TASK_NEUTRAL`` symbols may be used by a
    second task today.
    """

    TASK_NEUTRAL = "task_neutral"
    NEEDS_DECOUPLING = "needs_decoupling"
    TASK_SPECIFIC = "task_specific"


@dataclass(frozen=True, slots=True)
class SharedCapability:
    """One symbol-level capability and the boundary it sits on."""

    name: str
    module: str
    symbols: tuple[str, ...]
    boundary: CapabilityBoundary
    rationale: str
    decoupling_note: str = ""


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


# Capability boundary, recorded PER SYMBOL rather than per module.
#
# TASK_NEUTRAL        : usable by any task today, contains no expedition rule.
# NEEDS_DECOUPLING    : the CAPABILITY should be shared, but the current
#                       implementation embeds an expedition assumption. A second
#                       task must not call it until that assumption is removed
#                       and re-verified. Listing it here is a work item, NOT a
#                       permission.
# TASK_SPECIFIC       : expedition-only. Listed explicitly so nobody "promotes"
#                       it to shared by accident; it is NOT a shared capability.
#
# `require_task_neutral_capability` below turns this documentation into a gate.
SHARED_CAPABILITIES: tuple[SharedCapability, ...] = (
    SharedCapability(
        name="poi_ui_mutex",
        module="kancollebot.adapters.poi.ui_operation_lock",
        symbols=("poi_ui_operation", "PoiUiOperationLockError"),
        boundary=CapabilityBoundary.TASK_NEUTRAL,
        rationale=(
            "Cross-process mutex serialising the single shared Poi UI; no "
            "expedition rule inside it. It has NO task ownership or priority, "
            "which is what the UiArbitration design adds ON TOP of the mutex."
        ),
    ),
    SharedCapability(
        name="visual_dependency_gate",
        module="kancollebot.adapters.poi.visual_target",
        symbols=("require_visual_dependencies", "VisualTargetError"),
        boundary=CapabilityBoundary.TASK_NEUTRAL,
        rationale=(
            "Startup dependency check (OpenCV/NumPy) with an actionable "
            "fail-closed error; independent of any task's screens."
        ),
    ),
    SharedCapability(
        name="append_only_event_sink",
        module="kancollebot.expeditions.continuous_dispatch_controller",
        symbols=("SqliteDispatchEventLog",),
        boundary=CapabilityBoundary.TASK_NEUTRAL,
        rationale=(
            "Append-only event sink with opt-in structured task attribution; "
            "its payload is byte-for-byte unchanged when no task_kind is given."
        ),
        decoupling_note=(
            "Lives in an expedition-named module and in dispatch_events; a "
            "task-neutral home/table name is a later cleanup, not a blocker "
            "for using the sink as-is."
        ),
    ),
    SharedCapability(
        name="fleet_snapshot",
        module="kancollebot.adapters.poi.cdp_fleet_state",
        symbols=("read_poi_fleet_states", "PoiFleetMissionState"),
        boundary=CapabilityBoundary.NEEDS_DECOUPLING,
        rationale=(
            "Read-only Poi Redux snapshot. The CAPABILITY is shared (fleet ids, "
            "ship membership), but the current shape is expedition-shaped."
        ),
        decoupling_note=(
            "PoiFleetMissionState.mission is the api_mission 4-tuple and the "
            "read expression is documented as 'Fleet 2/3 supply'; it carries "
            "no morale (api_cond) or HP, which combat/sparkle need. Split the "
            "generic fleet/ship facts from the expedition mission tuple before "
            "a second task consumes it."
        ),
    ),
    SharedCapability(
        name="resupply_preflight",
        module="kancollebot.adapters.poi.full_supply",
        symbols=("build_full_supply_plan", "select_full_supply_targets", "validate_full_supply_progress"),
        boundary=CapabilityBoundary.NEEDS_DECOUPLING,
        rationale=(
            "Resupply is needed before sortie as well as after an expedition, "
            "so the CAPABILITY is cross-task."
        ),
        decoupling_note=(
            "The preflight hard-codes api_mission != [0,0,0,0] as 'fleet is "
            "out on an expedition', defaults supported_fleet_ids to (1,2,3), "
            "and its comments assume expedition states. Generalise the "
            "'fleet is busy' predicate and the fleet set before reuse."
        ),
    ),
    SharedCapability(
        name="supply_tab_coordinates",
        module="kancollebot.adapters.poi.visual_target",
        symbols=("SUPPLY_FLEET_TAB_REGIONS", "locate_supply_fleet_tab", "locate_square_checkbox"),
        boundary=CapabilityBoundary.NEEDS_DECOUPLING,
        rationale=(
            "Screen primitives are reusable in principle, but these are "
            "calibrated for ONE expedition supply screen."
        ),
        decoupling_note=(
            "SUPPLY_FLEET_TAB_REGIONS has keys 1/2/3 only (no Fleet 4); the "
            "checkbox fingerprints belong to the expedition supply screen. "
            "Move them into a per-screen registry instead of treating them as "
            "global constants."
        ),
    ),
    SharedCapability(
        name="expedition_composition_checks",
        module="kancollebot.expeditions.mission_eligibility",
        symbols=("MissionCompositionRequirement", "evaluate_mission_composition", "KNOWN_COMPOSITION"),
        boundary=CapabilityBoundary.TASK_SPECIFIC,
        rationale=(
            "Module docstring says 'Pure, offline expedition composition "
            "checks'; MissionCompositionRequirement requires a mission_id. "
            "This is expedition-specific, NOT a shared capability."
        ),
        decoupling_note=(
            "The generic SHAPE (fleet snapshot + requirement) is reusable for "
            "automated composition, but must be redefined per task; do not "
            "import this module from a combat/quest path."
        ),
    ),
    SharedCapability(
        name="expedition_dispatch_transaction",
        module="kancollebot.expeditions.continuous_dispatch_controller",
        symbols=("ContinuousDispatchController", "DurableDispatchCoordinator"),
        boundary=CapabilityBoundary.TASK_SPECIFIC,
        rationale=(
            "Owns the expedition dispatch transaction boundary and imports "
            "ContinuousExpeditionScheduler, mission_eligibility and planner. "
            "Not shared."
        ),
        decoupling_note=(
            "The durable intent/recovery PATTERN is worth generalising, but "
            "the current implementation is expedition-bound."
        ),
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


def shared_capabilities(
    *, boundary: CapabilityBoundary | str | None = None
) -> tuple[SharedCapability, ...]:
    """Query the capability boundary, optionally filtered by classification."""
    if boundary is None:
        return SHARED_CAPABILITIES
    if isinstance(boundary, str):
        try:
            boundary = CapabilityBoundary(boundary)
        except ValueError as exc:
            raise TaskScopeError(f"unknown capability boundary {boundary!r}") from exc
    if not isinstance(boundary, CapabilityBoundary):
        raise TaskScopeError("boundary must be a CapabilityBoundary or its string value")
    return tuple(item for item in SHARED_CAPABILITIES if item.boundary is boundary)


def require_task_neutral_capability(name: str) -> SharedCapability:
    """Fail closed if a second task tries to use a still-expedition-bound symbol.

    This is the executable form of the boundary. A task registry should call it
    for every capability it declares as a prerequisite: that way "we will
    decouple it later" cannot silently become "we are using it now".
    """
    for item in SHARED_CAPABILITIES:
        if item.name == name:
            if item.boundary is CapabilityBoundary.TASK_NEUTRAL:
                return item
            raise TaskScopeError(
                f"capability {name!r} is {item.boundary.value}, not task-neutral; "
                f"a second task must not use it yet. {item.decoupling_note} "
                "nothing was clicked."
            )
    raise TaskScopeError(
        f"unknown capability {name!r}; declared capabilities are "
        f"{[item.name for item in SHARED_CAPABILITIES]}"
    )


def describe_shared_capabilities() -> tuple[str, ...]:
    """Backward-compatible one-line view used by ``kancollebot tasks``.

    The ``module: ...`` shape is preserved so existing readers keep working;
    the boundary tag is added inside the description text.
    """
    return tuple(
        f"{item.module}: [{item.boundary.value}] {item.rationale}"
        for item in SHARED_CAPABILITIES
    )
