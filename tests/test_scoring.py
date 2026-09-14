import unittest
from decimal import Decimal
from types import SimpleNamespace

from src.sync.countability import CountabilityDecision, CountabilityStatus
from src.sync.scoring import ParticipantSessionScore, score_events


def event(event_id, participant_id, channel, status=CountabilityStatus.COUNT, session_number=1, identity_type="HUMAN", role="participant"):
    return SimpleNamespace(
        event_id=event_id,
        participant_id=participant_id,
        channel=channel,
        session_number=session_number,
        identity_type=identity_type,
        role=role,
        decision_status=status,
    )


def decision(event_id, status):
    return CountabilityDecision(event_id, status, "TEST", "test.v1", 1)


class ScoringTests(unittest.TestCase):
    def score(self, events):
        return score_events(events, [decision(item.event_id, item.decision_status) for item in events])

    def one(self, events, participant_id="p1", session_number=1):
        results = self.score(events)
        return next(result for result in results if result.participant_id == participant_id and result.session_number == session_number)

    def test_one_voice_count_is_one(self):
        result = self.one([event("e1", "p1", "voice")])
        self.assertEqual(result.score, Decimal("1.0"))
        self.assertEqual(result.voice_total, 1)
        self.assertEqual(result.voice_valid, 1)

    def test_one_chat_count_is_half(self):
        result = self.one([event("e1", "p1", "chat")])
        self.assertEqual(result.score, Decimal("0.5"))
        self.assertEqual(result.chat_total, 1)
        self.assertEqual(result.chat_valid, 1)

    def test_voice_and_chat_score_is_one_point_five(self):
        result = self.one([event("v", "p1", "voice"), event("c", "p1", "chat")])
        self.assertEqual(result.score, Decimal("1.5"))

    def test_no_count_is_in_total_but_does_not_add(self):
        result = self.one([event("e1", "p1", "voice", CountabilityStatus.NO_COUNT)])
        self.assertEqual((result.voice_total, result.voice_valid, result.score), (1, 0, Decimal("0")))

    def test_ambiguous_is_pending_and_does_not_add(self):
        result = self.one([event("e1", "p1", "chat", CountabilityStatus.AMBIGUOUS)])
        self.assertEqual(result.ambiguous_total, 1)
        self.assertEqual(result.score, Decimal("0"))
        self.assertFalse(result.scoring_complete)

    def test_multiple_events_and_participants(self):
        events = [
            event("a1", "p1", "voice"), event("a2", "p1", "chat", CountabilityStatus.NO_COUNT),
            event("b1", "p2", "chat"), event("b2", "p2", "voice", CountabilityStatus.AMBIGUOUS),
        ]
        results = self.score(events)
        self.assertEqual(results, [
            ParticipantSessionScore(1, "p1", 1, 1, 1, 0, 0, Decimal("1.0"), True),
            ParticipantSessionScore(1, "p2", 1, 0, 1, 1, 1, Decimal("0.5"), False),
        ])

    def test_participant_only_voice_and_only_chat(self):
        results = self.score([event("v", "voice-only", "voice"), event("c", "chat-only", "chat")])
        self.assertEqual(results[0].participant_id, "chat-only")
        self.assertEqual(results[0].score, Decimal("0.5"))
        self.assertEqual(results[1].participant_id, "voice-only")
        self.assertEqual(results[1].score, Decimal("1.0"))

    def test_facilitator_is_scored_normally(self):
        result = self.one([event("e1", "fac", "voice", role="facilitator")], "fac")
        self.assertEqual(result.score, Decimal("1.0"))

    def test_system_is_excluded(self):
        self.assertEqual(self.score([event("system", "p1", "voice", identity_type="SYSTEM")]), [])

    def test_result_is_independent_of_event_order(self):
        events = [event("a", "p1", "chat"), event("b", "p1", "voice"), event("c", "p2", "chat")]
        self.assertEqual(self.score(events), self.score(list(reversed(events))))

    def test_rerun_is_exactly_equal(self):
        events = [event("a", "p1", "voice"), event("b", "p1", "chat", CountabilityStatus.NO_COUNT)]
        self.assertEqual(self.score(events), self.score(events))

    def test_scores_are_half_point_multiples(self):
        results = self.score([event(str(i), "p1", "chat" if i % 2 else "voice") for i in range(6)])
        self.assertEqual(results[0].score % Decimal("0.5"), Decimal("0"))

    def test_missing_decision_is_rejected(self):
        with self.assertRaises(ValueError):
            score_events([event("e1", "p1", "voice")], [])

    def test_duplicate_event_id_is_rejected(self):
        events = [event("same", "p1", "voice"), event("same", "p1", "chat")]
        with self.assertRaises(ValueError):
            self.score(events)

    def test_different_sessions_remain_separate(self):
        results = self.score([event("e1", "p1", "voice", session_number=1), event("e2", "p1", "voice", session_number=2)])
        self.assertEqual([(result.session_number, result.score) for result in results], [(1, Decimal("1.0")), (2, Decimal("1.0"))])

    def test_contract_has_no_relevance_or_quality(self):
        result = self.one([event("e1", "p1", "voice")])
        self.assertFalse(hasattr(result, "relevance"))
        self.assertFalse(hasattr(result, "quality"))


if __name__ == "__main__":
    unittest.main()
