"""Fail-closed tests for Golden V2 publisher-model readiness."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "golden_v2_model_catalog_gate.py"
spec = importlib.util.spec_from_file_location("golden_model_gate", MODULE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
N = "publishers/google/models/"


class ModelCatalogGateTests(unittest.TestCase):
    def test_ga_absent_blocks_before_paid_claim(self):
        doc = {"publisherModels": [
            {"name": N + gate.CONTROL},
            {"name": N + gate.PREVIEW}]}
        with patch.object(gate, "fetch_catalog", return_value=doc):
            result = gate.examine_catalog(doc, gate.GA)
            self.assertTrue(result["preview_listed"])
            self.assertFalse(result["ga_listed"])
            self.assertFalse(result["is_inference_entitlement_proven"])
            with self.assertRaises(gate.ModelNotListed):
                gate.assert_model_available("xenon-chain-506409-c3", gate.GA)

    def test_preview_listed_but_no_entitlement_claim(self):
        doc = {"publisherModels": [
            {"name": N + gate.CONTROL},
            {"name": N + gate.PREVIEW}]}
        with patch.object(gate, "fetch_catalog", return_value=doc):
            result = gate.assert_model_available("project", gate.PREVIEW)
            self.assertTrue(result["required_listed"])
            self.assertFalse(result["is_inference_entitlement_proven"])

    def test_control_absent_or_pagination_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "control"):
            gate.examine_catalog({"publisherModels": [{"name":N + gate.GA}]}, gate.GA)
        with self.assertRaisesRegex(ValueError, "paginated"):
            gate.examine_catalog({"publisherModels": [{"name":N + gate.CONTROL}],
                                  "nextPageToken":"more"},gate.GA)

    def test_empty_catalog_fails_closed(self):
        for d in ({}, {"publisherModels":None}, {"publisherModels":[] }):
            with self.assertRaises(ValueError):
                gate.examine_catalog(d,gate.GA)

if __name__ == "__main__":
    unittest.main()
