from decimal import Decimal
import unittest

from src.sync.follow_up import (FollowUpLevel, FollowUpReasonCode,
                                build_program_follow_up)
from src.sync.models import SessionRecord
from src.sync.participants import Participant
from src.sync.scoring import ParticipantSessionScore


def score(session, pid, voice=0, chat=0, total=None):
    return ParticipantSessionScore(session, pid, voice, voice, chat, chat, 0,
                                   Decimal(str(total if total is not None else voice + chat / 2)), True)


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        self.sessions = tuple(SessionRecord(i, f"Sesión {i}", f"f{i}") for i in range(1, 5))
        self.statuses = {i: "PROCESSED" for i in range(1, 5)}
        self.tracking = {i: True for i in range(1, 5)}

    def build(self, participant, active_sessions, scores):
        statuses = {i: ("PROCESSED" if i in active_sessions else "INCOMPLETE") for i in range(1, 5)}
        return build_program_follow_up([participant], self.sessions, statuses, self.tracking, scores)

    def test_missing_result_is_circle_and_zero_recent_is_critical(self):
        result = self.build(Participant("p1", "Ana"), {1, 2, 3, 4}, [])
        entry = result.entries[0]
        self.assertEqual(entry.recent_window, "○ ○ ○ ○")
        self.assertEqual(entry.follow_up_level, FollowUpLevel.CRITICAL)
        self.assertEqual(entry.reason_code, FollowUpReasonCode.NEVER_PARTICIPATED)

    def test_exact_thresholds_and_insufficient_history(self):
        result = self.build(Participant("p1", "Ana"), {1, 2, 3, 4}, [score(1, "p1", voice=1), score(2, "p1", voice=1)])
        self.assertEqual(result.entries[0].follow_up_level, FollowUpLevel.NORMAL)
        five = tuple(SessionRecord(i, f"Sesión {i}", f"f{i}") for i in range(1, 6))
        exact = build_program_follow_up([Participant("p3", "Cata")], five, {i: "PROCESSED" for i in range(1, 6)}, {i: True for i in range(1, 6)}, [score(1, "p3", voice=1)])
        self.assertEqual(exact.entries[0].historical_frequency, Decimal("0.2"))
        self.assertEqual(exact.entries[0].follow_up_level, FollowUpLevel.CRITICAL)
        short = build_program_follow_up([Participant("p2", "Bea")], self.sessions[:3], {1: "PROCESSED", 2: "PROCESSED", 3: "PROCESSED"}, {1: True, 2: True, 3: True}, [])
        self.assertIsNone(short.entries[0].follow_up_level)
        self.assertEqual(short.entries[0].reason_code, FollowUpReasonCode.INSUFFICIENT_HISTORY)

    def test_nontracking_and_nonprocessed_do_not_count(self):
        statuses = {1: "PROCESSED", 2: "PROCESSED", 3: "INCOMPLETE", 4: "PROCESSED"}
        tracking = {1: True, 2: False, 3: True, 4: True}
        result = build_program_follow_up([Participant("p1", "Ana")], self.sessions, statuses, tracking, [score(1, "p1", voice=1)])
        self.assertEqual(result.entries[0].eligible_sessions, 2)

    def test_inactive_and_invalid_are_visible_without_classification(self):
        inactive = Participant("p1", "Ana", enrollment_status="inactive", start_session=1, end_session=2)
        invalid = Participant("p2", "Bea", enrollment_status="active", end_session=2)
        result = build_program_follow_up([inactive, invalid], self.sessions, self.statuses, self.tracking, [])
        self.assertEqual(result.entries[0].reason_code, FollowUpReasonCode.INVALID_ENROLLMENT_CONFIG)
        self.assertEqual(result.entries[1].reason_code, FollowUpReasonCode.INACTIVE)

    def test_since_replays_current_level(self):
        result = self.build(Participant("p1", "Ana"), {1, 2, 3, 4}, [score(4, "p1", voice=1)])
        self.assertEqual(result.entries[0].follow_up_level, FollowUpLevel.OBSERVE)
        self.assertEqual(result.entries[0].follow_up_since_session, 4)
