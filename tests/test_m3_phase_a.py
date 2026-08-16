from __future__ import annotations
import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from app.providers.correction_evidence import summarize_routing
from app.providers.correction_routing import ProviderFailureKind
from app.providers.minimax_provider import MiniMaxCorrectionClient, MiniMaxProviderError
from app.providers.terminology_consistency import build_report

def response(content:str)->bytes:
    return json.dumps({"base_resp":{"status_code":0},"choices":[{"message":{"content":content}}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}},ensure_ascii=False).encode()

class PhaseATests(unittest.TestCase):
    def client(self,payloads,root):
        key=root/'key'; key.write_text('test-key'); calls=iter(payloads)
        def post(_u,_h,_b,_t): return 200,{},next(calls)
        return MiniMaxCorrectionClient(key_file=key,http_post=post,sleeper=lambda _s:None,audit_dir=root/'audit')
    def test_structured_invalid_retries_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS":"2"}):
            root=Path(tmp); c=self.client([response('not-json'),response(json.dumps({"segments":[{"segment_id":"s1","corrected_text":"原文","uncertain_terms":[]}]}))],root)
            self.assertEqual(c.correct_window([{"segment_id":"s1","raw_text":"原文"}],[])["s1"]["corrected_text"],"原文")
            self.assertGreaterEqual(len(list((root/'audit').glob('*.json'))),2)
    def test_repeated_invalid_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{"MINIMAX_M3_INVALID_RESPONSE_MAX_ATTEMPTS":"2"}):
            c=self.client([response('bad'),response('bad2')],Path(tmp))
            with self.assertRaises(MiniMaxProviderError) as ctx: c.correct_window([{"segment_id":"s1","raw_text":"原文"}],[])
            self.assertIs(ctx.exception.kind,ProviderFailureKind.INVALID_RESPONSE)
    def test_m3_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=self.client([response(json.dumps({"terms":[{"canonical":"彌勒大成佛經","variants":["彌勒大乘佛經"],"confidence":"high"}]},ensure_ascii=False))],Path(tmp))
            record=c.extract_terms([{"segment_id":"s1","raw_text":"彌勒大成佛經"}]); self.assertEqual(record["terms"][0]["canonical"],"彌勒大成佛經"); self.assertEqual(record["usage_metadata"]["billing_mode"],"token_plan")

    def test_long_terminology_is_chunked_and_merged_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MINIMAX_M3_TERMINOLOGY_WINDOW_SEGMENTS": "50"}):
            payloads = [
                response(json.dumps({"terms":[{"canonical":"彌勒大成佛經","variants":["彌勒大乘佛經"],"confidence":"medium"}]}, ensure_ascii=False)),
                response(json.dumps({"terms":[{"canonical":"彌勒大成佛經","variants":["彌勒佛經"],"confidence":"high"},{"canonical":"般若波羅蜜多","variants":[],"confidence":"high"}]}, ensure_ascii=False)),
            ]
            items = [{"segment_id": f"s{i}", "raw_text": "課程內容"} for i in range(51)]
            record = self.client(payloads, Path(tmp)).extract_terms(items)
            self.assertEqual(record["chunk_count"], 2)
            self.assertEqual([term["canonical"] for term in record["terms"]], ["彌勒大成佛經", "般若波羅蜜多"])
            self.assertEqual(record["terms"][0]["variants"], ["彌勒佛經", "彌勒大乘佛經"])
            self.assertEqual(record["terms"][0]["confidence"], "high")

    def test_reasoning_and_inner_fenced_json_are_removed_for_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = "<think>internal reasoning</think>\n```json\n" + json.dumps({"terms":[{"canonical":"般若波羅蜜多","variants":[],"confidence":"high"}]}, ensure_ascii=False) + "\n```"
            record = self.client([response(content)], Path(tmp)).extract_terms([{"segment_id":"s1","raw_text":"般若波羅蜜多"}])
            self.assertEqual(record["terms"][0]["canonical"], "般若波羅蜜多")
    def test_actual_route_and_full_m3(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'correction-routing.json').write_text(json.dumps({"requested_policy":"M3_FIRST","initial_provider":"minimax-m3","provider_switches":[],"segment_counts":{"minimax-m3":3}})); self.assertEqual(summarize_routing(root)["correction_route"],"minimax-m3")
            (root/'correction-routing.json').write_text(json.dumps({"requested_policy":"M3_FIRST","initial_provider":"minimax-m3","provider_switches":[{"to":"gemini-3.7-flash"}],"segment_counts":{"minimax-m3":2,"gemini-3.7-flash":1}})); self.assertEqual(summarize_routing(root)["correction_route"],"minimax-m3 -> gemini-3.7-flash")
    def test_consistency_is_report_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'glossary').mkdir(); (root/'glossary'/'global-terms.json').write_text(json.dumps({"terms":[{"canonical":"UnFranchise","variants":["Unfranchise"],"confidence":"high"}]})); (root/'subtitles-corrected.json').write_text(json.dumps({"segments":[{"segment_id":"s1","corrected_text":"UnFranchise"},{"segment_id":"s2","corrected_text":"Unfranchise"}]})); report=build_report(root); self.assertEqual(report["issue_count"],1); self.assertFalse(report["timestamps_modified"]); self.assertFalse(report["segments_modified"])
