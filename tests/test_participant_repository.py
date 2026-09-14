import unittest

from src.sync.participants import Participant, ParticipantResolver
from src.sync.participant_repository import ParticipantRepository, decode_aliases, encode_aliases


class FakeSheetsValues:
    def __init__(self, values):
        self.values = [list(row) for row in values]
        self.writes = []
        self.appends = []

    def read_values(self):
        return [list(row) for row in self.values]

    def write_cells(self, start_row, start_column, values):
        self.writes.append((start_row, start_column, values))
        for row_offset, row_values in enumerate(values):
            row_index = start_row - 1 + row_offset
            while len(self.values) <= row_index:
                self.values.append([])
            row = self.values[row_index]
            while len(row) < start_column - 1:
                row.append("")
            for column_offset, value in enumerate(row_values):
                index = start_column - 1 + column_offset
                while len(row) <= index:
                    row.append("")
                row[index] = value

    def append_row(self, values):
        self.appends.append(list(values))
        self.values.append(list(values))


class ParticipantRepositoryTests(unittest.TestCase):
    def test_aliases_empty_round_trip(self):
        self.assertEqual(encode_aliases([]), "")
        self.assertEqual(decode_aliases(""), [])

    def test_multiple_aliases_use_lines(self):
        encoded = encode_aliases(["Alicia", "Ali"])
        self.assertEqual(encoded, "Alicia\nAli")
        self.assertEqual(decode_aliases(encoded), ["Alicia", "Ali"])

    def test_alias_with_comma_is_not_split(self):
        encoded = encode_aliases(["Apellido, Nombre"])
        self.assertEqual(decode_aliases(encoded), ["Apellido, Nombre"])

    def test_empty_alias_lines_are_ignored(self):
        self.assertEqual(decode_aliases(" Alicia \n\n  \nAli "), ["Alicia", "Ali"])

    def test_duplicate_aliases_are_removed_by_strict_key(self):
        self.assertEqual(encode_aliases([" Alicia ", "Alicia", "ALICIA"]), "Alicia")

    def test_legacy_sheet_migrates_to_new_contract(self):
        backend = FakeSheetsValues([["nombre", "correo", "aliases", "source", "status"], ["Alice", "a@example.com", "Alicia", "import", "new"]])
        repository = ParticipantRepository(backend)
        participants = repository.load()
        self.assertEqual(repository.headers, ["nombre", "correo", "aliases", "source", "status", "participant_id", "role"])
        self.assertEqual(participants[0].nombre, "Alice")
        self.assertTrue(participants[0].participant_id)
        self.assertEqual(participants[0].role, "participant")

    def test_repeated_migration_preserves_ids(self):
        backend = FakeSheetsValues([["nombre", "correo"], ["Alice", "a@example.com"]])
        repository = ParticipantRepository(backend)
        first = repository.load()[0].participant_id
        second = repository.load()[0].participant_id
        self.assertEqual(first, second)

    def test_unknown_columns_are_preserved(self):
        backend = FakeSheetsValues([["correo", "departamento", "nombre"], ["a@example.com", "Norte", "Alice"]])
        repository = ParticipantRepository(backend)
        repository.load()
        self.assertEqual(backend.values[0][1], "departamento")
        self.assertEqual(backend.values[1][1], "Norte")

    def test_existing_participant_id_is_preserved(self):
        backend = FakeSheetsValues([["participant_id", "nombre", "correo"], ["p1", "Alice", "a@example.com"]])
        participant = ParticipantRepository(backend).load()[0]
        self.assertEqual(participant.participant_id, "p1")

    def test_missing_id_is_created_once(self):
        backend = FakeSheetsValues([["nombre", "correo"], ["Alice", "a@example.com"]])
        repository = ParticipantRepository(backend)
        identifier = repository.load()[0].participant_id
        self.assertEqual(repository.load()[0].participant_id, identifier)
        self.assertEqual(len([cell for cell in backend.values[1] if cell == identifier]), 1)

    def test_missing_role_defaults_to_participant(self):
        backend = FakeSheetsValues([["participant_id", "nombre"], ["p1", "Alice"]])
        self.assertEqual(ParticipantRepository(backend).load()[0].role, "participant")

    def test_manual_fields_are_preserved_on_upsert(self):
        backend = FakeSheetsValues([["participant_id", "nombre", "correo", "aliases", "role", "source", "status"], ["p1", "Canonical", "manual@example.com", "Alias manual\nSegundo alias", "facilitator", "import", "verified"]])
        repository = ParticipantRepository(backend)
        repository.load()
        repository.upsert([Participant("p1", "Observed", "observed@example.com", ["Observed alias"], "participant", "auto", "unverified")])
        current = ParticipantRepository(backend).load()[0]
        self.assertEqual((current.nombre, current.correo, current.aliases, current.role, current.status), ("Canonical", "manual@example.com", ["Alias manual", "Segundo alias"], "facilitator", "verified"))

    def test_existing_participant_is_noop(self):
        backend = FakeSheetsValues([["participant_id", "nombre", "correo", "aliases", "role", "source", "status"], ["p1", "Canonical", "manual@example.com", "Alias manual", "participant", "import", "verified"]])
        repository = ParticipantRepository(backend)
        repository.upsert([Participant("p1", "Observed", "other@example.com", ["Other"], "facilitator", "auto", "unverified")])
        self.assertEqual(backend.writes, [])
        self.assertEqual(backend.appends, [])
        self.assertEqual(backend.values[1], ["p1", "Canonical", "manual@example.com", "Alias manual", "participant", "import", "verified"])

    def test_auto_new_participant_is_inserted_and_reused(self):
        backend = FakeSheetsValues([["participant_id", "nombre", "correo", "aliases", "role", "source", "status"]])
        repository = ParticipantRepository(backend)
        resolver = ParticipantResolver.auto(repository.load_records())
        created = resolver.resolve("Alice")
        repository.upsert([created.participant])
        restored = ParticipantResolver.auto(repository.load_records())
        reused = restored.resolve("Alice")
        self.assertEqual(created.participant_id, reused.participant_id)
        self.assertEqual(len(backend.values), 2)

    def test_repeated_upsert_does_not_duplicate(self):
        backend = FakeSheetsValues([["participant_id", "nombre", "correo", "aliases", "role", "source", "status"]])
        repository = ParticipantRepository(backend)
        participant = Participant("p1", "Alice")
        repository.upsert([participant])
        repository.upsert([participant])
        self.assertEqual(len(backend.values), 2)

    def test_new_participant_is_inserted_with_canonical_alias_codec(self):
        backend = FakeSheetsValues([["participant_id", "nombre", "correo", "aliases", "role", "source", "status"]])
        ParticipantRepository(backend).upsert([Participant("p1", "Alice", aliases=["Apellido, Nombre", "Alicia"])])
        self.assertEqual(backend.values[1][3], "Apellido, Nombre\nAlicia")

    def test_repository_never_replaces_participant_id(self):
        backend = FakeSheetsValues([["participant_id", "nombre"], ["p1", "Alice"]])
        repository = ParticipantRepository(backend)
        repository.upsert([Participant("p1", "Changed")])
        self.assertEqual(backend.values[1][0], "p1")

    def test_invalid_row_raises_explicit_error(self):
        backend = FakeSheetsValues([["nombre", "correo"], ["", "a@example.com"]])
        with self.assertRaisesRegex(ValueError, "fila 2.*nombre"):
            ParticipantRepository(backend).load()
        self.assertEqual(backend.writes, [])

    def test_duplicate_participant_ids_raise_error(self):
        backend = FakeSheetsValues([["participant_id", "nombre"], ["p1", "Alice"], ["p1", "Bob"]])
        with self.assertRaisesRegex(ValueError, "participant_id"):
            ParticipantRepository(backend).load()


if __name__ == "__main__":
    unittest.main()
