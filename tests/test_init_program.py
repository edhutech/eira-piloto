import unittest

from src.init_program import (
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
        from src.init_program import _sheet_values

        plan = build_plan(
            program_name="Programa A", folder_id="root-id", folder_url="url",
            session_count=2, participant_mode="auto", imported_participants=[],
            existing_children={}, existing_sheet_id=None,
        )
        values = _sheet_values(plan, [
            {"session_number": "1", "session_name": "01 - Sesión 1", "folder_id": "f1"},
            {"session_number": "2", "session_name": "02 - Sesión 2", "folder_id": "f2"},
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


if __name__ == "__main__":
    unittest.main()
