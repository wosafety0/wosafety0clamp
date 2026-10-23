"""Runnable, read-only Poi preflight composed behind the health-gated facade.

Start this command before configuring Poi Forwarder to POST to the printed
loopback webhook URL. It waits for a fresh successful state response, then
performs a read-only CDP page probe and gated HTTP diagnostic. It never
dispatches, collects, clicks, reloads, or issues game API requests.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from kancollebot.adapters.poi.cdp_probe import probe_poi_page
from kancollebot.adapters.poi.health_gated_http_reader import PoiHealthGatedHttpReader
from kancollebot.adapters.poi.http_read_only import PoiHttpReadOnlyAdapter
from kancollebot.adapters.poi.state_observation_tracker import PoiStateObservationTracker
from kancollebot.adapters.poi.webhook_receiver import PoiWebhookReceiver


@dataclass(frozen=True, slots=True)
class PoiPreflightConfig:
    http_url: str = "http://127.0.0.1:17777"
    cdp_endpoint: str = "http://127.0.0.1:9222"
    title_hint: str = "poi"
    url_hint: str = ""
    webhook_port: int = 9223
    event_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not isinstance(self.http_url, str) or not self.http_url:
            raise ValueError("http_url must be a non-empty string")
        if not isinstance(self.cdp_endpoint, str) or not self.cdp_endpoint:
            raise ValueError("cdp_endpoint must be a non-empty string")
        if not isinstance(self.title_hint, str) or not isinstance(self.url_hint, str):
            raise ValueError("CDP target hints must be strings")
        if not self.title_hint.strip() and not self.url_hint.strip():
            raise ValueError("at least one CDP target hint is required")
        if isinstance(self.webhook_port, bool) or not isinstance(self.webhook_port, int) or not 0 <= self.webhook_port <= 65535:
            raise ValueError("webhook_port must be an integer in the range 0..65535")
        if (
            isinstance(self.event_timeout_seconds, bool)
            or not isinstance(self.event_timeout_seconds, (int, float))
            or self.event_timeout_seconds <= 0
        ):
            raise ValueError("event_timeout_seconds must be positive")


def _config_from_environment() -> PoiPreflightConfig:
    try:
        port = int(os.environ.get("KANCOLLE_POI_WEBHOOK_PORT", "9223"))
        timeout = float(os.environ.get("KANCOLLE_POI_EVENT_TIMEOUT_SECONDS", "60"))
    except ValueError as exc:
        raise ValueError("webhook port or event timeout environment value is invalid") from exc
    return PoiPreflightConfig(
        http_url=os.environ.get("KANCOLLE_POI_HTTP_URL", "http://127.0.0.1:17777"),
        cdp_endpoint=os.environ.get("KANCOLLE_POI_CDP_ENDPOINT", "http://127.0.0.1:9222"),
        title_hint=os.environ.get("KANCOLLE_POI_TITLE_HINT", "poi"),
        url_hint=os.environ.get("KANCOLLE_POI_URL_HINT", ""),
        webhook_port=port,
        event_timeout_seconds=timeout,
    )


def run_read_only_preflight(
    config: PoiPreflightConfig,
    *,
    receiver_factory: Callable[..., Any] = PoiWebhookReceiver,
    adapter_factory: Callable[..., Any] = PoiHttpReadOnlyAdapter,
    tracker_factory: Callable[[], Any] = PoiStateObservationTracker,
    probe_factory: Callable[..., Any] = probe_poi_page,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[int, dict[str, Any]]:
    """Run one health-gated read-only preflight; dependencies are injectable for tests."""
    receiver = None
    report: dict[str, Any] = {
        "status": "FAILED",
        "actions_enabled": False,
        "gameplay_actions_enabled": False,
        "http_reads_performed": False,
        "http_diagnostic_attempted": False,
        "http_diagnostic_completed": False,
        "gates": {
            "forwarder_event_accepted": False,
            "source_event_fresh": False,
            "cdp_target_identity_validated": False,
            "cdp_document_ready": False,
            "fleets_endpoint_read": False,
            "ships_endpoint_read": False,
            "fleet_ship_references_valid": False,
            "gameplay_actions_disabled": True,
        },
        "reason": "preflight_not_completed",
    }
    try:
        adapter = adapter_factory(base_url=config.http_url)
        tracker = tracker_factory()
        receiver = receiver_factory(host="127.0.0.1", port=config.webhook_port)
        bound_port = receiver.start()
        webhook_url = f"http://127.0.0.1:{bound_port}/webhook/kancolle"
        report["webhook_url"] = webhook_url
        report["forwarder_configuration_required"] = True

        deadline = monotonic() + float(config.event_timeout_seconds)
        accepted = False
        while monotonic() < deadline:
            remaining = deadline - monotonic()
            event = receiver.get_event(timeout=min(0.25, max(0.0, remaining)))
            if event is not None and tracker.observe_event(event):
                accepted = True
                break
        if not accepted:
            report["reason"] = "fresh_successful_forwarder_event_timeout"
            report["http_reads_performed"] = False
            return 2, report

        # Preserve the completed event gate if a later CDP or HTTP check fails.
        report["gates"]["forwarder_event_accepted"] = True
        report["forwarder_event_accepted"] = True
        # Record freshness at acceptance, even when subsequent page checks fail.
        accepted_at = getattr(tracker, "latest_valid_state_at", None)
        if isinstance(accepted_at, datetime) and accepted_at.tzinfo is not None:
            accepted_age = (datetime.now(timezone.utc) - accepted_at).total_seconds()
            report["gates"]["source_event_fresh"] = 0 <= accepted_age <= 15.0

        def page_probe():
            return probe_factory(
                endpoint=config.cdp_endpoint,
                title_hint=config.title_hint,
                url_hint=config.url_hint,
            )

        reader = PoiHealthGatedHttpReader(
            adapter,
            receiver,
            tracker,
            probe_page=page_probe,
        )
        def tracked_diagnostic():
            report["http_diagnostic_attempted"] = True
            return adapter.diagnose()

        # The read callback is invoked only after the initial health gate.
        diagnostic, observation = reader._read_with_health(tracked_diagnostic)
        source_event_age = (
            (datetime.now(timezone.utc) - observation.latest_valid_state_at).total_seconds()
            if observation.latest_valid_state_at is not None
            else None
        )
        source_event_fresh = source_event_age is not None and 0 <= source_event_age <= 15.0
        endpoints = diagnostic.get("endpoints", {})
        gates = {
            "forwarder_event_accepted": True,
            "source_event_fresh": source_event_fresh,
            # probe_poi_page only returns target_found=True after live title/URL
            # identity has been revalidated against the discovered renderer.
            "cdp_target_identity_validated": (
                observation.target_found is True
                and bool(observation.title.strip())
                and bool(observation.url.strip())
            ),
            "cdp_document_ready": observation.document_ready_state == "complete",
            "fleets_endpoint_read": isinstance(endpoints, dict) and endpoints.get("fleets") == "read",
            "ships_endpoint_read": isinstance(endpoints, dict) and endpoints.get("ships") == "read",
            "fleet_ship_references_valid": diagnostic.get("fleet_ship_references_valid") is True,
            "gameplay_actions_disabled": True,
        }
        if not all(gates.values()):
            report["reason"] = "post_read_gate_report_inconsistent"
            report["gates"] = gates
            report["http_reads_performed"] = True
            report["http_diagnostic_completed"] = True
            return 2, report
        report.update({
            "status": "PASS_READ_ONLY_HEALTH_GATED",
            "reason": "all_read_only_acceptance_gates_confirmed",
            "http_reads_performed": True,
            "http_diagnostic_attempted": True,
            "http_diagnostic_completed": True,
            "forwarder_event_accepted": True,
            "page_probe_performed": True,
            "page_document_ready": observation.document_ready_state == "complete",
            "source_event_age_seconds": max(0.0, source_event_age) if source_event_age is not None else None,
            "gates": gates,
            "diagnostic": diagnostic,
            "actions_enabled": False,
            "gameplay_actions_enabled": False,
        })
        return 0, report
    except Exception as exc:
        # Keep runtime exceptions, URLs, event bodies, and local game data out of output.
        report["reason"] = "preflight_failed_" + type(exc).__name__
        report["actions_enabled"] = False
        report["gameplay_actions_enabled"] = False
        return 2, report
    finally:
        if receiver is not None:
            try:
                receiver.stop()
            except Exception:
                pass


def main() -> int:
    try:
        config = _config_from_environment()
        code, report = run_read_only_preflight(config)
    except Exception as exc:
        code = 2
        report = {
            "status": "FAILED",
            "reason": "configuration_invalid_" + type(exc).__name__,
            "actions_enabled": False,
            "gameplay_actions_enabled": False,
        }
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
