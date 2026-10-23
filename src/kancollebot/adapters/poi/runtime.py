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
        diagnostic, observation = reader.diagnose()
        report.update({
            "status": "PASS_READ_ONLY_HEALTH_GATED",
            "reason": "page_health_and_source_event_freshness_confirmed",
            "http_reads_performed": True,
            "forwarder_event_accepted": True,
            "page_probe_performed": True,
            "page_document_ready": observation.document_ready_state == "complete",
            "source_event_age_seconds": max(
                0.0, (datetime.now(timezone.utc) - observation.latest_valid_state_at).total_seconds()
            ) if observation.latest_valid_state_at is not None else None,
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
