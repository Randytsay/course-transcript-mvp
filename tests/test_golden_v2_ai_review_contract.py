import unittest

from app.jobs.golden_v2_ledger import V2Conflict
from app.pipeline.golden_v2_ai_review_contract import (
    QUALITY_TIER, build_ai_review_certificate, validate_ai_publication_intent,
)


class MachineReviewContractTests(unittest.TestCase):
    def setUp(self):
        self.job_id = "v2p_" + "a" * 32
        self.shas = dict(source_sha256="1"*64, chirp_sha256="2"*64,
                         acoustic_qa_sha256="3"*64, render_sha256="4"*64)
        self.report = {**self.shas, "acoustic_gate": "PASS",
                       "timeline_gate": "PASS", "srt_txt_consistency": "PASS",
                       "semantic_gate": "PASS", "unverified_audio_intervals": 0,
                       "missing_windows": 0, "unresolved_semantic_conflicts": 0,
                       "unverified_srt_cues": 0, "cue_count": 12, "word_count": 124}

    def certificate(self):
        return build_ai_review_certificate(job_id=self.job_id, report=self.report,
                                           **self.shas)

    def test_machine_qa_is_explicitly_not_golden(self):
        cert = self.certificate()
        self.assertEqual(cert["quality_tier"], QUALITY_TIER)
        self.assertEqual(cert["golden_acceptance"], "NOT_CLAIMED")
        self.assertFalse(cert["delivery_authorized"])

    def test_no_uncertainty_or_tampered_source_allowed(self):
        for key, value in [("missing_windows", 1),
                           ("unverified_audio_intervals", 1),
                           ("unverified_srt_cues", 2),
                           ("unresolved_semantic_conflicts", 1),
                           ("acoustic_gate", "REVIEW"),
                           ("semantic_gate", "REVIEW"),
                           ("human_listened", True),
                           ("source_sha256", "f"*64)]:
            with self.subTest(key=key):
                old = self.report.get(key)
                self.report[key] = value
                with self.assertRaises(V2Conflict):
                    self.certificate()
                if old is None:
                    self.report.pop(key)
                else:
                    self.report[key] = old

    def test_separate_signed_grant_contract_no_human_shortcut(self):
        cert = self.certificate()
        names = ["語音 250503__AI校對.srt", "語音 250503__AI校對.txt"]
        grant = dict(quality_tier=QUALITY_TIER, job_id=self.job_id,
                     source_sha256=self.shas["source_sha256"],
                     certificate_sha256=cert["certificate_sha256"],
                     render_sha256=self.shas["render_sha256"],
                     actor="owner", owner_confirm=True, revision=4,
                     filenames=names)
        validate_ai_publication_intent(
            certificate=cert, grant=grant, filenames=names,
            existing_target_count=0)
        for key, bad in [("confirm_listened", True),
                         ("certificate_sha256", "f"*64),
                         ("owner_confirm", False)]:
            with self.subTest(key=key):
                other = dict(grant, **{key: bad})
                with self.assertRaises(V2Conflict):
                    validate_ai_publication_intent(
                        certificate=cert, grant=other,
                        filenames=names, existing_target_count=0)
        with self.assertRaises(V2Conflict):
            validate_ai_publication_intent(
                certificate=cert, grant=grant,
                filenames=names, existing_target_count=1)
        with self.assertRaises(V2Conflict):
            validate_ai_publication_intent(
                certificate=cert, grant=grant,
                filenames=["語音 250503_.srt", "語音 250503_逐字稿.txt"],
                existing_target_count=0)
