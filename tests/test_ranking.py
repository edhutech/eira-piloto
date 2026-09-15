import unittest
from decimal import Decimal

from participacion.core.participants import Participant
from participacion.core.scoring import ParticipantSessionScore
from participacion.core.ranking import ProgramRanking, build_program_ranking, top_n


def participant(participant_id, name=None, role="participant"):
    return Participant(participant_id, name or participant_id, role=role)


def result(session, participant_id, voice_total=0, voice_valid=0, chat_total=0,
           chat_valid=0, score="0", complete=True):
    return ParticipantSessionScore(
        session, participant_id, voice_total, voice_valid, chat_total,
        chat_valid, 0, Decimal(score), complete,
    )


class RankingTests(unittest.TestCase):
    def test_one_session_accumulates_and_ranks(self):
        ranking = build_program_ranking(
            [participant("p1", "Ana"), participant("p2", "Beto")],
            [result(1, "p1", voice_total=2, voice_valid=2, score="2"),
             result(1, "p2", chat_total=2, chat_valid=2, score="1")],
        )
        self.assertEqual([(entry.participant_id, entry.score_total, entry.rank) for entry in ranking.entries],
                         [("p1", Decimal("2"), 1), ("p2", Decimal("1"), 2)])

    def test_multiple_sessions_and_activity_accumulate_by_id(self):
        ranking = build_program_ranking(
            [participant("p1", "Nombre Actual")],
            [result(1, "p1", voice_total=2, voice_valid=1, score="1"),
             result(2, "p1", chat_total=2, chat_valid=2, score="1")],
        )
        entry = ranking.entries[0]
        self.assertEqual(entry.participant, "Nombre Actual")
        self.assertEqual(entry.sessions_with_activity, 2)
        self.assertEqual(entry.voice_valid_total, 1)
        self.assertEqual(entry.chat_valid_total, 2)
        self.assertEqual(entry.score_total, Decimal("2"))

    def test_roles_control_eligibility_but_not_scores(self):
        ranking = build_program_ranking(
            [participant("p", "Participante", "participant"),
             participant("f", "Facilitador", "facilitator"),
             participant("o", "Otro", "other")],
            [result(1, "p", voice_valid=1, score="1"),
             result(1, "f", voice_valid=10, score="10"),
             result(1, "o", voice_valid=5, score="5")],
        )
        self.assertEqual([entry.participant_id for entry in ranking.entries], ["p"])
        self.assertEqual(ranking.excluded_scores["f"], Decimal("10"))
        self.assertEqual(ranking.excluded_scores["o"], Decimal("5"))

    def test_zero_score_with_persisted_result_is_included(self):
        ranking = build_program_ranking([participant("p")], [result(1, "p")])
        self.assertEqual(len(ranking.entries), 1)
        self.assertEqual(ranking.entries[0].rank, 1)
        self.assertEqual(ranking.entries[0].score_total, Decimal("0"))

    def test_competition_ranking_and_stable_name_order(self):
        people = [participant(str(i), name) for i, name in enumerate(["Zeta", "Beta", "Alpha", "Delta"])]
        scores = ["10", "8", "8", "5"]
        ranking = build_program_ranking(people, [result(1, str(i), voice_valid=1, score=s) for i, s in enumerate(scores)])
        self.assertEqual([(e.participant, e.rank) for e in ranking.entries],
                         [("Zeta", 1), ("Alpha", 2), ("Beta", 2), ("Delta", 4)])

    def test_top_n_includes_all_entries_tied_at_boundary(self):
        people = [participant(str(i), f"P{i}") for i in range(6)]
        ranking = build_program_ranking(people, [result(1, "0", score="10"), result(1, "1", score="9"),
                                                  result(1, "2", score="8"), result(1, "3", score="7"),
                                                  result(1, "4", score="6"), result(1, "5", score="6")])
        self.assertEqual([e.participant_id for e in top_n(ranking, 5)], ["0", "1", "2", "3", "4", "5"])

    def test_voice_only_chat_only_and_half_point_are_exact(self):
        ranking = build_program_ranking(
            [participant("v", "Voice"), participant("c", "Chat")],
            [result(1, "v", voice_total=1, voice_valid=1, score="1"),
             result(1, "c", chat_total=1, chat_valid=1, score="0.5")],
        )
        self.assertEqual(ranking.entries[1].score_total, Decimal("0.5"))
        self.assertEqual(ranking.entries[0].voice_valid_total, 1)
        self.assertEqual(ranking.entries[1].chat_valid_total, 1)

    def test_input_order_and_rerun_are_deterministic(self):
        people = [participant("b", "Beta"), participant("a", "Alpha")]
        values = [result(2, "b", score="1"), result(1, "a", score="1")]
        first = build_program_ranking(people, values)
        second = build_program_ranking(list(reversed(people)), list(reversed(values)))
        self.assertEqual(first, second)

    def test_missing_participant_is_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "inexistente"):
            build_program_ranking([participant("p")], [result(1, "missing")])

    def test_duplicate_session_participant_result_is_error(self):
        with self.assertRaisesRegex(ValueError, "duplicado"):
            build_program_ranking([participant("p")], [result(1, "p"), result(1, "p")])

    def test_incomplete_scoring_marks_eligible_ranking_incomplete(self):
        ranking = build_program_ranking([participant("p")], [result(1, "p", score="0.5", complete=False)])
        self.assertFalse(ranking.entries[0].ranking_complete)
        self.assertFalse(ranking.ranking_complete)

    def test_incomplete_facilitator_does_not_make_eligible_ranking_incomplete(self):
        ranking = build_program_ranking(
            [participant("p"), participant("f", role="facilitator")],
            [result(1, "p", score="1"), result(1, "f", score="10", complete=False)],
        )
        self.assertTrue(ranking.ranking_complete)

    def test_no_relevance_or_quality_fields(self):
        self.assertFalse(hasattr(ProgramRanking, "relevance"))
        self.assertFalse(hasattr(ProgramRanking, "quality"))
        self.assertFalse(hasattr(ranking_entry := build_program_ranking([participant("p")], [result(1, "p")]).entries[0], "relevance"))
        self.assertFalse(hasattr(ranking_entry, "quality"))

    def test_invalid_top_n_is_rejected(self):
        ranking = build_program_ranking([participant("p")], [result(1, "p")])
        with self.assertRaises(ValueError):
            top_n(ranking, 0)


if __name__ == "__main__":
    unittest.main()
