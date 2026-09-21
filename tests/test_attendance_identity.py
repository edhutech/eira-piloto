import tempfile
import unittest
from pathlib import Path

from participacion.application.attendance_identity import load_attendance_identity_policy
from participacion.application.modules.resolution import ResolutionResult, ResolutionStatus
from participacion.application.modules.attendance import AttendanceConfig, AttendanceInput, AttendanceModule
from participacion.application.modules.models import ApplicabilityResult, ApplicabilityStatus
from participacion.application.external_data.models import AvailabilityStatus, CanonicalFact, SourceProvenance



class ResolverFake:
    def resolve(self, value: str) -> ResolutionResult:
        return ResolutionResult(ResolutionStatus.RESOLVED, "p1") if value == "official@example.com" else ResolutionResult(ResolutionStatus.NOT_FOUND)


class SessionFake:
    def resolve(self, value: str) -> ResolutionResult:
        return ResolutionResult(ResolutionStatus.RESOLVED, "s1")


class ApplicabilityFake:
    def is_applicable(self, participant_id: str, session_id: str) -> ApplicabilityResult:
        return ApplicabilityResult(ApplicabilityStatus.APPLICABLE)


def fact(email):
    return CanonicalFact(email, "s1", "metric", 1, AvailabilityStatus.AVAILABLE,
                         SourceProvenance("source.xlsx", "sheet", 2, "metric"))


class AttendanceIdentityPolicyTests(unittest.TestCase):
    def write_config(self, content):
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".csv", delete=False)
        handle.write(content)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_direct_alias_external_and_unknown_are_deterministic(self):
        path = self.write_config(
            "external_id,status,canonical_external_id,reason,provenance\n"
            "Alias@Example.com,RESOLVED,official@example.com,alias,source\n"
            "external@example.com,IGNORED,,external,source\n"
        )
        policy = load_attendance_identity_policy(path, ResolverFake())
        self.assertEqual(policy.resolve(" alias@example.COM ").status, ResolutionStatus.RESOLVED)
        self.assertEqual(policy.resolve("external@example.com").status, ResolutionStatus.IGNORED)
        self.assertEqual(policy.resolve("unknown@example.com").status, ResolutionStatus.NOT_FOUND)

    def test_ignored_does_not_create_observation_or_issue(self):
        path = self.write_config(
            "external_id,status,canonical_external_id,reason,provenance\n"
            "external@example.com,IGNORED,,external,source\n"
        )
        policy = load_attendance_identity_policy(path, ResolverFake())
        module = AttendanceModule(policy, SessionFake(), ApplicabilityFake(),
                                  AttendanceConfig({"metric": "metric"}))
        result = module.build_observations(AttendanceInput((fact("external@example.com"),)))
        self.assertEqual(result.observations, ())
        self.assertEqual(result.issues, ())

    def test_unknown_remains_needs_review(self):
        module = AttendanceModule(ResolverFake(), SessionFake(), ApplicabilityFake(),
                                  AttendanceConfig({"metric": "metric"}))
        result = module.build_observations(AttendanceInput((fact("unknown@example.com"),)))
        self.assertEqual(result.observations, ())
        self.assertEqual(result.issues[0].code, "PARTICIPANT_NOT_RESOLVED")

    def test_config_fails_closed(self):
        path = self.write_config(
            "external_id,status,canonical_external_id,reason,provenance\n"
            "bad@example.com,RESOLVED,,missing,source\n"
        )
        with self.assertRaises(ValueError):
            load_attendance_identity_policy(path, ResolverFake())


if __name__ == "__main__":
    unittest.main()
