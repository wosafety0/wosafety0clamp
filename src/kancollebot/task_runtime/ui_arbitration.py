"""DESIGN REFERENCE — UI admission arbitration (NOT wired into production).

``adapters/poi/ui_operation_lock.poi_ui_operation`` is a single cross-process
mutex with a free-form label: it has no task ownership, no priority and no
notion of a visual gate. This module is an ADMISSION layer that decides whether
a task may *request* entry; it does NOT replace the mutex. Any real click must
still hold ``poi_ui_operation``.

Hard safety boundary: a high-priority request may ask a lower-priority holder to
yield (PREEMPT_REQUESTED), but ``may_interrupt_in_flight_click`` is always
False. Interrupting half a click sequence could trigger an unreviewed game
action, so handover happens only after the current operation finishes.
See docs/DESIGN_multi_task_runtime.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Protocol, runtime_checkable

from kancollebot.task_scope import (
    TaskKind,
    TaskScopeError,
    coerce_task_kind,
    require_task_enabled,
)

from .registry import TaskRegistry, TaskRegistryError


class UiPriority(IntEnum):
    """Lower value = higher priority."""

    SAFETY = 0
    OPERATOR = 1
    AUTOMATION = 2


class UiDecision(str, Enum):
    GRANTED = "granted"
    REJECTED_TASK_DISABLED = "rejected_task_disabled"
    REJECTED_NO_VISUAL_GATE = "rejected_no_visual_gate"
    REJECTED_BUSY = "rejected_busy"
    PREEMPT_REQUESTED = "preempt_requested"


@dataclass(frozen=True, slots=True)
class UiRequest:
    task_kind: TaskKind
    operation: str
    priority: UiPriority
    run_id: str
    visual_gate_verified: bool
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not isinstance(self.task_kind, TaskKind):
            raise ValueError("task_kind must be a TaskKind")
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError("operation must be a non-empty string")
        if not isinstance(self.priority, UiPriority):
            raise ValueError("priority must be a UiPriority")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(self.visual_gate_verified, bool):
            raise ValueError("visual_gate_verified must be a bool")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")


@dataclass(frozen=True, slots=True)
class UiArbitrationResult:
    decision: UiDecision
    request: UiRequest
    holder_run_id: str | None = None
    may_interrupt_in_flight_click: bool = False
    reason: str = ""


@runtime_checkable
class UiArbiter(Protocol):
    """The contract a production arbiter must satisfy."""

    def arbitrate(
        self, request: UiRequest, holder: UiRequest | None = None
    ) -> UiArbitrationResult: ...


def ui_operation_label(request: UiRequest) -> str:
    """Task-attributed label for the existing cross-process mutex."""
    return f"{request.task_kind.value}:{request.operation}:{request.run_id}"


class InMemoryUiArbiter:
    """Offline reference decision maker. No queue, no preemption, no clicks."""

    def __init__(self, *, registry: TaskRegistry | None = None) -> None:
        self._registry = registry

    def arbitrate(
        self, request: UiRequest, holder: UiRequest | None = None
    ) -> UiArbitrationResult:
        if not isinstance(request, UiRequest):
            raise TypeError("request must be a UiRequest")
        if holder is not None and not isinstance(holder, UiRequest):
            raise TypeError("holder must be a UiRequest or None")

        kind = coerce_task_kind(request.task_kind)
        try:
            if self._registry is not None:
                self._registry.require_enabled(kind)
            else:
                require_task_enabled(kind)
        except (TaskScopeError, TaskRegistryError) as exc:
            return UiArbitrationResult(
                decision=UiDecision.REJECTED_TASK_DISABLED,
                request=request,
                holder_run_id=holder.run_id if holder else None,
                reason=f"task {kind.value!r} is not enabled: {exc}",
            )

        if not request.visual_gate_verified:
            return UiArbitrationResult(
                decision=UiDecision.REJECTED_NO_VISUAL_GATE,
                request=request,
                holder_run_id=holder.run_id if holder else None,
                reason=(
                    "no calibrated visual gate for this operation; refusing an "
                    "unreviewed click"
                ),
            )

        if holder is None:
            return UiArbitrationResult(
                decision=UiDecision.GRANTED,
                request=request,
                reason="no current UI holder",
            )

        higher_priority = request.priority < holder.priority
        if higher_priority and request.priority is UiPriority.SAFETY:
            return UiArbitrationResult(
                decision=UiDecision.PREEMPT_REQUESTED,
                request=request,
                holder_run_id=holder.run_id,
                may_interrupt_in_flight_click=False,
                reason=(
                    f"SAFETY request from {request.run_id!r} asks {holder.run_id!r} "
                    "to yield; handover happens only AFTER the current operation "
                    "finishes. No in-flight click is interrupted."
                ),
            )

        return UiArbitrationResult(
            decision=UiDecision.REJECTED_BUSY,
            request=request,
            holder_run_id=holder.run_id,
            reason=(
                f"UI is held by {holder.task_kind.value!r}/{holder.run_id!r} "
                f"(priority {holder.priority.name}); request priority "
                f"{request.priority.name} does not outrank it and no queue exists"
            ),
        )
