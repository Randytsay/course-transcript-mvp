"""Real Google API shape tests with fake clients, no credentials or charges."""
from __future__ import annotations

from types import SimpleNamespace as NS
import unittest

from app.providers.golden_v2_smoke_providers import (
    SmokeProviderError, chirp_smoke_call, gemini_smoke_call,
    require_exact_smoke_uri, smoke_cost_cents,
)

CLIP_SHA = "a" * 64
BUCKET = "private-test"
URI = f"gs://{BUCKET}/golden-v2-smoke/{CLIP_SHA}/clip.flac"


class ProviderAdapterTests(unittest.TestCase):
    def test_real_sdk_primary_disables_hidden_automatic_retries(self):
        requests = []
        client = NS(models=NS(generate_content=lambda **kwargs: (
            requests.append(kwargs) or NS(text="如是我聞", parts=[],
                model_dump=lambda **_: {"candidates": [{"finish_reason": "STOP"}],
                                       "usage_metadata": {"prompt_token_count": 100,
                                                          "candidates_token_count": 10}}))))
        gemini_smoke_call(client=client, model="gemini-3.8-flash", gcs_uri=URI,
                          bucket=BUCKET, clip_sha256=CLIP_SHA)
        self.assertEqual(requests[0]["config"].http_options.retry_options.attempts, 1)
        self.assertEqual(requests[0]["config"].http_options.timeout, 180000)
    def test_chirp_operation_saved_before_timeout(self):
        events = []
        def wait(**_):
            self.assertEqual(events[0]["kind"], "chirp_submitted")
            raise TimeoutError("pending provider operation")
        client = NS(batch_recognize=lambda **_: NS(
            operation=NS(name="projects/test-project/locations/us/operations/123"),
            result=wait))
        with self.assertRaises(TimeoutError):
            chirp_smoke_call(speech_client=client, project="test-project",
                            gcs_uri=URI, bucket=BUCKET, clip_sha256=CLIP_SHA,
                            request_factory=lambda *_: {}, evidence_sink=events.append)
        self.assertEqual(events[0]["operation_name"].split("/")[-1], "123")

    def test_gemini_invalid_usage_raw_saved_before_rejection(self):
        events = []
        client = NS(models=NS(generate_content=lambda **_: NS(
            text="raw text", parts=[], model_dump=lambda **_:
                {"candidates": [{"finish_reason": "MAX_TOKENS"}]})))
        with self.assertRaises(SmokeProviderError):
            gemini_smoke_call(client=client, model="gemini-3.8-flash", gcs_uri=URI,
                              bucket=BUCKET, clip_sha256=CLIP_SHA,
                              part_factory=lambda x: x, config_factory=lambda x: x,
                              evidence_sink=events.append)
        self.assertEqual(events[0]["raw_response"]["candidates"][0]["finish_reason"],
                         "MAX_TOKENS")

    def test_failed_operation_audit_stops_before_poll(self):
        polls = []
        client = NS(batch_recognize=lambda **_: NS(
            operation=NS(name="projects/test-project/locations/us/operations/123"),
            result=lambda **_: polls.append(1)))
        def failed_sink(_):
            raise OSError("disk sync failed")
        with self.assertRaises(OSError):
            chirp_smoke_call(speech_client=client, project="test-project",
                            gcs_uri=URI, bucket=BUCKET, clip_sha256=CLIP_SHA,
                            request_factory=lambda *_: {}, evidence_sink=failed_sink)
        self.assertEqual(polls, [])

    def test_uri_must_match_derived_clip(self):
        self.assertEqual(
            require_exact_smoke_uri(URI, bucket=BUCKET, clip_sha256=CLIP_SHA),
            f"golden-v2-smoke/{CLIP_SHA}/clip.flac",
        )
        for uri in (
            "gs://not-my-bucket/golden-v2-smoke/" + CLIP_SHA + "/clip.flac",
            URI + "?generation=2",
            URI.replace(CLIP_SHA, "b" * 64),
            "https://storage.googleapis.com/private-test",
            "gs://private-test/golden-v2-smoke/" + CLIP_SHA + "/../clip.flac",
        ):
            with self.assertRaises(SmokeProviderError):
                require_exact_smoke_uri(
                    uri, bucket=BUCKET, clip_sha256=CLIP_SHA,
                )

    def test_primary_and_third_dispatch_one_call_exactly(self):
        calls = []
        class FakeModels:
            def generate_content(self, **kwargs):
                calls.append(kwargs)
                return NS(text="佛法說明與示例", parts=[],
                          model_dump=lambda **_: {
                              "candidates": [{"finish_reason": "STOP"}],
                              "usage_metadata": {
                                  "prompt_token_count": 6300,
                                  "candidates_token_count": 660,
                              },
                          })
        client = NS(models=FakeModels())
        for model in ("gemini-3.8-flash", "gemini-3.5-transcribe"):
            result = gemini_smoke_call(
                client=client, model=model, gcs_uri=URI,
                bucket=BUCKET, clip_sha256=CLIP_SHA,
                part_factory=lambda url: ("audio/flac", url),
                config_factory=lambda selected: {"model": selected},
            )
            self.assertEqual(result["model"], model)
            self.assertEqual(result["clip_sha256"], CLIP_SHA)
            self.assertEqual(result["text"], "佛法說明與示例")
            self.assertGreater(smoke_cost_cents(result), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["model"], "gemini-3.8-flash")
        self.assertEqual(calls[1]["model"], "gemini-3.5-transcribe")
        self.assertEqual(calls[0]["contents"][-1], ("audio/flac", URI))
        self.assertEqual(len(calls[1]["contents"]), 1)

    def test_model_result_without_usage_blocks_accounting(self):
        client = NS(models=NS(
            generate_content=lambda **_: NS(
                text="text", parts=[], model_dump=lambda **_:
                    {"candidates": [{"finish_reason": "STOP"}]},
            )
        ))
        with self.assertRaisesRegex(SmokeProviderError, "token usage"):
            gemini_smoke_call(
                client=client, model="gemini-3.8-flash",
                gcs_uri=URI, bucket=BUCKET, clip_sha256=CLIP_SHA,
                part_factory=lambda url: url,
                config_factory=lambda model: model,
            )

    def test_actual_installed_sdk_schema_compatibility_before_paid_call(self):
        """V1 genai 1.75 must fail BEFORE call; isolated 2.29 must build config."""
        from google.genai import types
        calls = []
        client = NS(models=NS(generate_content=lambda **kwargs: (
            calls.append(kwargs) or NS(
                text="真實 SDK 產生的假回應", parts=[],
                model_dump=lambda **_: {
                    "candidates": [{"finish_reason": "STOP"}],
                    "usage_metadata": {
                        "prompt_token_count": 1500,
                        "candidates_token_count": 200,
                    },
                },
            )
        )))
        capable = ("audio_transcription_config" in
                   types.GenerateContentConfig.model_fields
                   and {"mode", "word_timestamp"}.issubset(
                       types.AudioTranscriptionConfig.model_fields))
        if not capable:
            with self.assertRaisesRegex(
                    SmokeProviderError, "google-genai >=2.29"):
                gemini_smoke_call(
                    client=client, model="gemini-3.5-transcribe",
                    gcs_uri=URI, bucket=BUCKET, clip_sha256=CLIP_SHA,
                )
            self.assertEqual(calls, [])
            return
        result = gemini_smoke_call(
            client=client, model="gemini-3.5-transcribe",
            gcs_uri=URI, bucket=BUCKET, clip_sha256=CLIP_SHA,
        )
        self.assertEqual(result["model"], "gemini-3.5-transcribe")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["config"].audio_transcription_config.mode,
                         "VERBATIM")
        self.assertEqual(calls[0]["config"].audio_transcription_config.word_timestamp,
                         False)

    def test_speech_v2_official_request_factory_without_paid_call(self):
        from google.cloud.speech_v2.types import cloud_speech
        requests = []
        result = NS(
            total_billed_duration=NS(seconds=240, nanos=0),
            results={URI: NS(
                transcript=NS(results=[NS(alternatives=[
                    NS(transcript="這是一段測試", words=[NS(
                        word="這是一段測試", start_offset=0, end_offset=1)]),
                ])]), error=NS(code=0),
            )},
        )
        client = NS(batch_recognize=lambda **kwargs: (
            requests.append(kwargs) or NS(
                operation=NS(
                    name="projects/test-project/locations/us/operations/123"),
                result=lambda **__: result,
            )
        ))
        record = chirp_smoke_call(
            speech_client=client, project="test-project",
            gcs_uri=URI, bucket=BUCKET, clip_sha256=CLIP_SHA,
        )
        self.assertEqual(record["billed_duration_seconds"], 240)
        self.assertEqual(len(requests), 1)
        request = requests[0]["request"]
        self.assertIsInstance(request, cloud_speech.BatchRecognizeRequest)
        self.assertEqual(request.config.model, "chirp_3")
        self.assertTrue(request.config.features.enable_word_time_offsets)
        self.assertIn("locations/us", request.recognizer)

    def test_chirp_inline_batch_word_timing_and_billed_duration(self):
        seen = []
        result = NS(
            total_billed_duration=NS(seconds=240, nanos=0),
            results={
                URI: NS(transcript=NS(results=[NS(alternatives=[
                    NS(transcript="如是我聞", words=[
                        NS(word="如是", start_offset=NS(seconds=0, nanos=0),
                           end_offset=NS(seconds=1, nanos=0)),
                        NS(word="我聞", start_offset=NS(seconds=1, nanos=0),
                           end_offset=NS(seconds=2, nanos=0)),
                    ])
                ])]), error=NS(code=0)),
            },
        )
        operation = NS(operation=NS(
            name="projects/test-project/locations/us/operations/123"
        ), result=lambda **kwargs: result)
        client = NS(batch_recognize=lambda **kwargs:
                    seen.append(kwargs) or operation)
        transcript = chirp_smoke_call(
            speech_client=client, project="test-project",
            gcs_uri=URI, bucket=BUCKET, clip_sha256=CLIP_SHA,
            request_factory=lambda uri, project: {
                "uri": uri, "project": project, "word_time_offsets": True,
            },
        )
        self.assertEqual(transcript["transcript"], "如是我聞")
        self.assertEqual(transcript["words"][1]["start_seconds"], 1.0)
        self.assertEqual(transcript["billed_duration_seconds"], 240)
        self.assertEqual(smoke_cost_cents(transcript), 7)
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0]["retry"])
        self.assertEqual(seen[0]["timeout"], 60)

    def test_chirp_inconsistent_or_missing_evidence_blocks(self):
        result = NS(
            total_billed_duration=None,
            results={URI: NS(transcript=NS(results=[]), error=NS(code=0))},
        )
        client = NS(batch_recognize=lambda **_: NS(
            operation=NS(name="projects/test-project/locations/us/operations/123"),
            result=lambda **kwargs: result,
        ))
        with self.assertRaisesRegex(SmokeProviderError, "billed Speech"):
            chirp_smoke_call(
                speech_client=client, project="test-project",
                gcs_uri=URI, bucket=BUCKET, clip_sha256=CLIP_SHA,
                request_factory=lambda *_: {},
            )

    def test_usage_cost_rates_and_fail_closed(self):
        self.assertEqual(smoke_cost_cents({
            "model": "gemini-3.8-flash",
            "usage_metadata": {
                "prompt_token_count": 20000,
                "candidates_token_count": 1000,
                "thoughts_token_count": 3000,
            },
        }), 3)
        self.assertEqual(smoke_cost_cents({
            "model": "gemini-3.5-transcribe",
            "usage_metadata": {
                "prompt_token_count": 6000,
                "candidates_token_count": 700,
            },
        }), 3)
        for record in (
            {"model": "chirp_3", "billed_duration_seconds": None},
            {"model": "gemini-3.8-flash", "usage_metadata": {}},
            {"model": "gemini-3.5-transcribe",
             "usage_metadata": {"prompt_token_count": -3,
                                "candidates_token_count": 300}},
        ):
            with self.assertRaises(SmokeProviderError):
                smoke_cost_cents(record)


if __name__ == "__main__":
    unittest.main()
