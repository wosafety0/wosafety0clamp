from __future__ import annotations

import threading
import time
import unittest

from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
    SchedulerInvariantError,
    UiOperationArbiter,
)


class ContinuousSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = ContinuousExpeditionScheduler()
        self.mission2 = MissionTuple(1, 1, 1793530637469, 0, (1, 6, 8, 25))
        self.mission3 = MissionTuple(1, 3, 1793531044934, 0, (4, 52, 32, 63))

    def dispatch(self, fleet_id, mission):
        self.scheduler.mark_ready(fleet_id)
        self.scheduler.begin_dispatch(fleet_id)
        self.scheduler.confirm_dispatch(fleet_id, mission)

    def returned(self, mission):
        return MissionTuple(2, mission.mission_id, mission.final_time_ms,
                            mission.reserved, mission.ship_ids)

    def home_port(self, fleet_id, mission):
        self.scheduler.confirm_home_port_after_dispatch(
            fleet_id, ui_state="HOME_PORT", observed_mission=mission
        )

    def test_default_scope_is_fleet_two_and_three_only(self):
        self.assertEqual([s.fleet_id for s in self.scheduler.states()], [2, 3])
        with self.assertRaisesRegex(SchedulerInvariantError, "read-only"):
            self.scheduler.fleet(1)
        with self.assertRaisesRegex(SchedulerInvariantError, "deferred"):
            self.scheduler.fleet(4)

    def test_fleets_have_independent_lifecycles(self):
        self.dispatch(2, self.mission2)
        self.home_port(2, self.mission2)
        self.scheduler.mark_ready(3)
        self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.EXPEDITION_ACTIVE)
        self.assertTrue(self.scheduler.may_wait_for_return(2))
        self.assertEqual(self.scheduler.fleet(3).phase, FleetPhase.READY)

    def test_cannot_wait_before_home_port_confirmation(self):
        self.dispatch(2, self.mission2)
        self.assertFalse(self.scheduler.may_wait_for_return(2))
        with self.assertRaisesRegex(SchedulerInvariantError, "before Home Port"):
            self.scheduler.confirm_returned(2, self.returned(self.mission2))

    def test_dispatch_must_return_to_home_port(self):
        self.dispatch(2, self.mission2)
        with self.assertRaisesRegex(SchedulerInvariantError, "HOME_PORT"):
            self.scheduler.confirm_home_port_after_dispatch(
                2, ui_state="EXPEDITION_LIST", observed_mission=self.mission2
            )

    def test_home_port_requires_identical_active_mission_tuple(self):
        self.dispatch(2, self.mission2)
        changed = MissionTuple(1, 1, self.mission2.final_time_ms + 1,
                               0, self.mission2.ship_ids)
        with self.assertRaisesRegex(SchedulerInvariantError, "tuple changed"):
            self.scheduler.confirm_home_port_after_dispatch(
                2, ui_state="HOME_PORT", observed_mission=changed
            )

    def test_return_requires_same_mission_identity_and_status_two(self):
        self.dispatch(2, self.mission2)
        self.home_port(2, self.mission2)
        wrong_identity = MissionTuple(2, 1, self.mission2.final_time_ms,
                                      0, (1, 6, 8))
        with self.assertRaisesRegex(SchedulerInvariantError, "identity"):
            self.scheduler.confirm_returned(2, wrong_identity)
        with self.assertRaisesRegex(SchedulerInvariantError, "status 2"):
            self.scheduler.confirm_returned(2, self.mission2)

    def test_return_settlement_and_resupply_transitions(self):
        self.dispatch(2, self.mission2)
        self.home_port(2, self.mission2)
        self.scheduler.confirm_returned(2, self.returned(self.mission2))
        self.scheduler.confirm_return_result(2, ui_state="RETURN_RESULT")
        self.scheduler.confirm_bonus_items(2, ui_state="BONUS_ITEMS")
        self.scheduler.confirm_reward_claimed(2, claimed=True)
        self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.RESUPPLY_REQUIRED)
        self.scheduler.confirm_resupply(2, fully_supplied=True)
        self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.READY)

    def test_bonus_page_cannot_be_skipped(self):
        self.dispatch(2, self.mission2)
        self.home_port(2, self.mission2)
        self.scheduler.confirm_returned(2, self.returned(self.mission2))
        self.scheduler.confirm_return_result(2, ui_state="RETURN_RESULT")
        with self.assertRaisesRegex(SchedulerInvariantError, "BONUS_ITEMS"):
            self.scheduler.confirm_reward_claimed(2, claimed=True)

    def test_reward_claim_requires_positive_confirmation(self):
        self.dispatch(2, self.mission2)
        self.home_port(2, self.mission2)
        self.scheduler.confirm_returned(2, self.returned(self.mission2))
        self.scheduler.confirm_return_result(2, ui_state="RETURN_RESULT")
        self.scheduler.confirm_bonus_items(2, ui_state="BONUS_ITEMS")
        with self.assertRaises(SchedulerInvariantError):
            self.scheduler.confirm_reward_claimed(2, claimed=False)

    def test_blocked_fleet_cannot_dispatch(self):
        self.scheduler.block(2, "state unknown")
        with self.assertRaisesRegex(SchedulerInvariantError, "READY"):
            self.scheduler.begin_dispatch(2)

    def test_ui_arbiter_serializes_shared_window_operations(self):
        arbiter = UiOperationArbiter()
        active = 0
        peak = 0
        guard = threading.Lock()

        def worker(label):
            nonlocal active, peak
            with arbiter.operation(label):
                with guard:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.02)
                with guard:
                    active -= 1

        threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(peak, 1)

    def test_blocking_an_active_fleet_clears_home_port_wait_permission(self):
        self.dispatch(2, self.mission2)
        self.home_port(2, self.mission2)
        blocked = self.scheduler.block(2, "post-dispatch evidence conflict")
        self.assertEqual(blocked.phase, FleetPhase.BLOCKED)
        self.assertFalse(blocked.home_port_verified)
        self.assertFalse(self.scheduler.may_wait_for_return(2))

    def test_ui_arbiter_requires_a_label(self):
        arbiter = UiOperationArbiter()
        with self.assertRaises(ValueError):
            with arbiter.operation(" "):
                pass


if __name__ == "__main__":
    unittest.main()
