import unittest

from participacion.cli.init import (
    build_plan,
    extract_folder_id,
    session_folder_name,
    validate_participant_columns,
    validate_session_count,
)


class InitProgramTests(unittest.TestCase):
    def test_extract_folder_id_from_drive_folder_url(self):
        self.assertEqual(
            extract_folder_id("https://drive.google.com/drive/u/0/folders/abc123XYZ"),
            "abc123XYZ",
        )

    def test_extract_folder_id_from_url_with_query(self):
        self.assertEqual(
            extract_folder_id("https://drive.google.com/drive/folders/abc123XYZ?usp=sharing"),
            "abc123XYZ",
        )

    def test_extract_folder_id_rejects_non_folder_url(self):
        with self.assertRaises(ValueError):
            extract_folder_id("https://docs.google.com/spreadsheets/d/abc123XYZ/edit")

    def test_validate_session_count_requires_positive_integer(self):
        self.assertEqual(validate_session_count("3"), 3)
        for value in ("0", "3.5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_session_count(value)

    def test_session_folder_name_uses_required_numbering(self):
        self.assertEqual(session_folder_name(1), "01 - Sesión 1")
        self.assertEqual(session_folder_name(12), "12 - Sesión 12")

    def test_build_plan_reuses_existing_folders_and_sheet(self):
        plan = build_plan(
            program_name="Programa A",
            folder_id="root-id",
            folder_url="https://drive.google.com/drive/folders/root-id",
            session_count=2,
            participant_mode="auto",
            imported_participants=[],
            existing_children={"01 - Sesión 1": "session-1"},
            existing_sheet_id="sheet-1",
        )
        self.assertEqual(
            plan.session_folders,
            [("01 - Sesión 1", "session-1", False), ("02 - Sesión 2", None, True)],
        )
        self.assertEqual(plan.sheet_id, "sheet-1")
        self.assertFalse(plan.create_sheet)

    def test_existing_sheet_accepts_canonical_and_legacy_titles_without_duplicating(self):
        from participacion.adapters.google.bootstrap import _find_existing_sheet

        children = [
            {"id": "sheet-1", "name": "Participación - Programa A - Eira Piloto",
             "mimeType": "application/vnd.google-apps.spreadsheet"},
        ]
        self.assertEqual(_find_existing_sheet(children, "Participación - Programa A"), "sheet-1")
        self.assertIsNone(_find_existing_sheet([], "Participación - Programa A"))

        with self.assertRaisesRegex(ValueError, "más de un workbook"):
            _find_existing_sheet(children + [
                {"id": "sheet-2", "name": "Participación - Programa A",
                 "mimeType": "application/vnd.google-apps.spreadsheet"},
            ], "Participación - Programa A")

    def test_build_plan_marks_root_for_rename(self):
        plan = build_plan(
            program_name="Programa Nuevo",
            current_folder_name="Nombre anterior",
            folder_id="root-id",
            folder_url="https://drive.google.com/drive/folders/root-id",
            session_count=1,
            participant_mode="auto",
            imported_participants=[],
            existing_children={},
            existing_sheet_id=None,
        )
        self.assertEqual(plan.current_folder_name, "Nombre anterior")
        self.assertNotEqual(plan.current_folder_name, plan.program_name)

    def test_sheet_sessions_has_headers_only_and_control_numbers_are_integers(self):
        from participacion.cli.init import _sheet_values

        plan = build_plan(
            program_name="Programa A", folder_id="root-id", folder_url="url",
            session_count=2, participant_mode="auto", imported_participants=[],
            existing_children={}, existing_sheet_id=None,
        )
        values = _sheet_values(plan, [
            {"session_number": "1", "session_name": "01 - Sesión 1", "folder_id": "f1"},
            {"session_number": "2", "session_name": "02 - Sesión 2", "folder_id": "f2"},
        ])
        self.assertEqual(values["Sesiones"][0], [
            "session_number", "session_name", "participant_id", "participant", "email",
            "voice_total", "voice_valid", "chat_total", "chat_valid", "ambiguous_total",
            "score", "scoring_complete", "countability_ruleset_version",
        ])
        self.assertEqual(len(values["Sesiones"]), 1)
        self.assertEqual(values["Control"][1][0], 1)
        self.assertEqual(values["Control"][2][0], 2)

    def test_validate_participant_columns_requires_name_and_email(self):
        mapping = validate_participant_columns(["Nombre", "Correo", "Departamento"])
        self.assertEqual(mapping, {"nombre": 0, "correo": 1})
        with self.assertRaisesRegex(ValueError, "correo"):
            validate_participant_columns(["nombre"])

    def test_build_plan_does_not_create_participants_in_auto_mode(self):
        plan = build_plan(
            program_name="Programa A",
            folder_id="root-id",
            folder_url="https://drive.google.com/drive/folders/root-id",
            session_count=1,
            participant_mode="auto",
            imported_participants=[],
            existing_children={},
            existing_sheet_id=None,
        )
        self.assertEqual(plan.participants, [])

    def test_new_participant_sheet_uses_canonical_contract(self):
        from participacion.cli.init import PARTICIPANT_HEADERS
        self.assertEqual(PARTICIPANT_HEADERS, ["participant_id", "nombre", "correo", "aliases", "role", "source", "status", "enrollment_status", "start_session", "end_session"])


if __name__ == "__main__":
    unittest.main()
