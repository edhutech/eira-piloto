import unittest
from collections import Counter
from types import SimpleNamespace

from participacion.core.countability import CountabilityStatus, classify_event


def event(name, identity_type="HUMAN", event_id="event-1", participant_id=None, channel="voice", context_ambiguous=False):
    return SimpleNamespace(
        event_id=event_id,
        text=name,
        raw_text=name,
        identity_type=identity_type,
        participant_id=participant_id,
        channel=channel,
        context_ambiguous=context_ambiguous,
    )


class CountabilityTests(unittest.TestCase):
    def assert_status(self, text, expected, channel="voice"):
        decision = classify_event(event(text, event_id=f"id-{text}", channel=channel))
        self.assertIsNotNone(decision)
        self.assertEqual(decision.status, expected)
        self.assertTrue(decision.reason_code)
        self.assertTrue(decision.rule_id)
        self.assertEqual(decision.event_id, f"id-{text}")

    def test_no_count_greetings(self):
        for text in ("Hola", "Buenas tardes"):
            with self.subTest(text=text):
                self.assert_status(text, CountabilityStatus.NO_COUNT)

    def test_no_count_thanks_and_acknowledgements(self):
        for text in ("Gracias", "Ok", "Sí", "No"):
            with self.subTest(text=text):
                self.assert_status(text, CountabilityStatus.NO_COUNT)

    def test_no_count_reaction_and_technical_only(self):
        for text in ("👍", "¿Me escuchan?", "Se cortó", "no se escucha"):
            with self.subTest(text=text):
                self.assert_status(text, CountabilityStatus.NO_COUNT)

    def test_no_count_automatic_message(self):
        self.assert_status("Alice se unió a la reunión", CountabilityStatus.NO_COUNT, channel="chat")

    def test_count_short_real_question(self):
        self.assert_status("¿Por qué?", CountabilityStatus.COUNT)
        self.assert_status("¿Cuándo se entrega?", CountabilityStatus.COUNT, channel="chat")

    def test_count_explanation_opinion_proposal_and_example(self):
        for text in (
            "Sí, porque eso cambia el costo",
            "No, yo usaría la segunda opción",
            "Propongo comenzar por el segundo caso",
            "Por ejemplo, podemos medirlo con una encuesta",
            "La explicación es que cambia la demanda",
        ):
            with self.subTest(text=text):
                self.assert_status(text, CountabilityStatus.COUNT)

    def test_count_greeting_with_content(self):
        self.assert_status("Hola, propongo revisar el presupuesto", CountabilityStatus.COUNT)

    def test_count_voice_and_chat_substantive_messages(self):
        self.assert_status("Mi opinión es que debemos esperar", CountabilityStatus.COUNT, channel="voice")
        self.assert_status("Tengo un ejemplo concreto para compartir", CountabilityStatus.COUNT, channel="chat")

    def test_depends_is_count_by_conservative_default(self):
        self.assert_status("Depende", CountabilityStatus.COUNT)

    def test_short_meaningful_intervention_is_count(self):
        self.assert_status("Exacto", CountabilityStatus.COUNT)

    def test_ambiguous_requires_explicit_missing_context(self):
        decision = classify_event(event("No puedo resolverlo", event_id="ambiguous", context_ambiguous=True))
        self.assertEqual(decision.status, CountabilityStatus.AMBIGUOUS)

    def test_system_is_not_processed(self):
        self.assertIsNone(classify_event(event("Someone's Presentation", identity_type="SYSTEM")))

    def test_participant_id_does_not_affect_classification(self):
        first = classify_event(event("¿Por qué?", event_id="one", participant_id="p1"))
        second = classify_event(event("¿Por qué?", event_id="two", participant_id="p2"))
        self.assertEqual(first.status, second.status)
        self.assertEqual(first.reason_code, second.reason_code)

    def test_same_event_id_is_deterministic(self):
        first = classify_event(event("Gracias", event_id="same"))
        second = classify_event(event("Gracias", event_id="same"))
        self.assertEqual(first, second)

    def test_fixture_case_counts(self):
        cases = [
            ("Hola", CountabilityStatus.NO_COUNT),
            ("Gracias", CountabilityStatus.NO_COUNT),
            ("👍", CountabilityStatus.NO_COUNT),
            ("¿Me escuchan?", CountabilityStatus.NO_COUNT),
            ("¿Por qué?", CountabilityStatus.COUNT),
            ("Sí, porque eso cambia el costo", CountabilityStatus.COUNT),
            ("No, yo usaría la segunda opción", CountabilityStatus.COUNT),
            ("Propongo revisar el plan", CountabilityStatus.COUNT),
            ("Depende", CountabilityStatus.COUNT),
        ]
        counts = Counter(classify_event(event(text, event_id=str(index))).status for index, (text, _) in enumerate(cases))
        self.assertEqual(counts, Counter({CountabilityStatus.COUNT: 5, CountabilityStatus.NO_COUNT: 4}))

    def test_no_count_requires_an_explicit_rule(self):
        decision = classify_event(event("Mensaje humano con contenido", event_id="count"))
        self.assertEqual(decision.status, CountabilityStatus.COUNT)
        self.assertEqual(decision.reason_code, "SUBSTANTIVE_CONTENT")

    def test_rule_id_and_ruleset_version_are_stable(self):
        first = classify_event(event("Gracias", event_id="same"))
        second = classify_event(event("Gracias", event_id="same"))
        self.assertEqual(first.rule_id, "thanks_only.v1")
        self.assertEqual(first.ruleset_version, 1)
        self.assertEqual(first, second)

    def test_no_relevance_or_quality_fields(self):
        decision = classify_event(event("Una intervención"))
        self.assertFalse(hasattr(decision, "relevance"))
        self.assertFalse(hasattr(decision, "quality"))


if __name__ == "__main__":
    unittest.main()
