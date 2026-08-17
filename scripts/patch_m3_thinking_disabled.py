from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"anchor not found: {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Provider routing: distinguish an explicit provider output-limit stop from
# malformed structured output. It should switch to Gemini without repeating
# the same doomed request at the transport retry layer.
replace_once(
    "app/providers/correction_routing.py",
    '''    INVALID_RESPONSE = "invalid_response"\n    UNKNOWN = "unknown"\n''',
    '''    INVALID_RESPONSE = "invalid_response"\n    OUTPUT_LIMIT = "output_limit"\n    UNKNOWN = "unknown"\n''',
)
replace_once(
    "app/providers/correction_routing.py",
    '''        ProviderFailureKind.TRANSIENT_EXHAUSTED,\n        ProviderFailureKind.INVALID_RESPONSE,\n    }:\n''',
    '''        ProviderFailureKind.TRANSIENT_EXHAUSTED,\n        ProviderFailureKind.INVALID_RESPONSE,\n        ProviderFailureKind.OUTPUT_LIMIT,\n    }:\n''',
)

# MiniMax adapter: M3 now officially supports disabling thinking. For fixed
# segment text correction and terminology extraction, disable agentic thinking
# by default so reasoning cannot consume generation budget/latency. Keep the
# mode configurable for bounded future experiments.
replace_once(
    "app/providers/minimax_provider.py",
    '''class MiniMaxCompletion:\n    content: str\n    usage: dict[str, Any]\n    raw_payload: object\n    status_code: int\n    attempts: list[dict[str, Any]]\n''',
    '''class MiniMaxCompletion:\n    content: str\n    usage: dict[str, Any]\n    raw_payload: object\n    status_code: int\n    attempts: list[dict[str, Any]]\n    finish_reason: str | None\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''def _response_content(payload: object) -> str:\n''',
    '''def _finish_reason(payload: object) -> str | None:\n    if not isinstance(payload, Mapping):\n        return None\n    choices = payload.get("choices")\n    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):\n        return None\n    value = choices[0].get("finish_reason")\n    text = str(value or "").strip().lower()\n    return text or None\n\n\ndef _response_content(payload: object) -> str:\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''        self.max_output_tokens = max(256, int(os.getenv("MINIMAX_M3_MAX_OUTPUT_TOKENS", "4096")))\n        self.reasoning_split = os.getenv("MINIMAX_M3_REASONING_SPLIT", "true").strip().lower() in {\n''',
    '''        self.max_output_tokens = max(256, int(os.getenv("MINIMAX_M3_MAX_OUTPUT_TOKENS", "4096")))\n        raw_thinking_mode = os.getenv("MINIMAX_M3_THINKING_MODE", "disabled").strip().lower()\n        self.thinking_mode = raw_thinking_mode if raw_thinking_mode in {"disabled", "adaptive"} else "disabled"\n        self.reasoning_split = os.getenv("MINIMAX_M3_REASONING_SPLIT", "true").strip().lower() in {\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''                "stream": False,\n                "temperature": 0,\n                "max_tokens": self.max_output_tokens,\n                # The live CN MiniMax-M3 capability probe confirmed that this\n''',
    '''                "stream": False,\n                "temperature": 0,\n                # MiniMax-M3 officially supports disabling thinking. These\n                # deterministic text-only tasks do not need agentic reasoning.\n                "thinking": {"type": self.thinking_mode},\n                # max_tokens is legacy for M3; use the current generation-limit field.\n                "max_completion_tokens": self.max_output_tokens,\n                # reasoning_split remains useful if adaptive thinking is selected\n                # for a bounded experiment. It does not itself disable thinking.\n                # The live CN MiniMax-M3 capability probe confirmed that this\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''                try:\n                    content = _response_content(response_payload)\n                except (TypeError, ValueError) as exc:\n''',
    '''                finish_reason = _finish_reason(response_payload)\n                if finish_reason == "length":\n                    raise MiniMaxProviderError(\n                        "MiniMax generation reached the configured output limit",\n                        kind=ProviderFailureKind.OUTPUT_LIMIT,\n                        status_code=status,\n                        raw_response=response_payload,\n                    )\n                try:\n                    content = _response_content(response_payload)\n                except (TypeError, ValueError) as exc:\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''                        "status_code": status,\n                        "failure_kind": None,\n                    }\n                )\n                self._last_attempts = attempts\n                return MiniMaxCompletion(content, usage, response_payload, status, attempts)\n''',
    '''                        "status_code": status,\n                        "finish_reason": finish_reason,\n                        "failure_kind": None,\n                    }\n                )\n                self._last_attempts = attempts\n                return MiniMaxCompletion(content, usage, response_payload, status, attempts, finish_reason)\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''                if exc.kind in {ProviderFailureKind.AUTHENTICATION, ProviderFailureKind.USAGE_LIMIT}:\n                    raise\n                if attempt < self.max_attempts:\n                    self.sleeper(min(30.0, 2**attempt))\n''',
    '''                if exc.kind in {\n                    ProviderFailureKind.AUTHENTICATION,\n                    ProviderFailureKind.USAGE_LIMIT,\n                    ProviderFailureKind.INVALID_RESPONSE,\n                    ProviderFailureKind.OUTPUT_LIMIT,\n                }:\n                    raise\n                if attempt < self.max_attempts:\n                    self.sleeper(min(30.0, 2**attempt))\n''',
)

# Record thinking mode in immutable per-job routing provenance and reports.
replace_once(
    "app/providers/correction_runtime.py",
    '''        self.m3_reasoning_split = bool(getattr(self.m3_client, "reasoning_split", False))\n''',
    '''        self.m3_reasoning_split = bool(getattr(self.m3_client, "reasoning_split", False))\n        self.m3_thinking_mode = _safe_runtime_ref(getattr(self.m3_client, "thinking_mode", None))\n''',
)
replace_once(
    "app/providers/correction_runtime.py",
    '''                "m3_reasoning_split": self.m3_reasoning_split,\n                "chirp_raw_immutable": True,\n''',
    '''                "m3_reasoning_split": self.m3_reasoning_split,\n                "m3_thinking_mode": self.m3_thinking_mode,\n                "chirp_raw_immutable": True,\n''',
)
replace_once(
    "app/jobs/performance_enhanced.py",
    '''        "m3ReasoningSplit": routing.get("m3_reasoning_split") if isinstance(routing, dict) else None,\n        "minimaxReasoningTokens": summary["providerCallBreakdown"]["minimax"]["reasoningTokens"],\n''',
    '''        "m3ReasoningSplit": routing.get("m3_reasoning_split") if isinstance(routing, dict) else None,\n        "m3ThinkingMode": routing.get("m3_thinking_mode") if isinstance(routing, dict) else None,\n        "minimaxReasoningTokens": summary["providerCallBreakdown"]["minimax"]["reasoningTokens"],\n''',
)

# Configuration: preserve the safe Gemini baseline while documenting the M3
# deterministic-correction mode. Existing output-token env name remains stable.
replace_once(
    ".env.example",
    '''MINIMAX_M3_MAX_OUTPUT_TOKENS=4096\n# Live CN MiniMax-M3 capability testing confirmed this separates reasoning\n''',
    '''MINIMAX_M3_MAX_OUTPUT_TOKENS=4096\n# MiniMax-M3 officially supports disabled/adaptive thinking. Fixed-segment\n# correction and terminology are deterministic transformations, so keep\n# thinking disabled unless a bounded quality experiment proves it is needed.\nMINIMAX_M3_THINKING_MODE=disabled\n# Live CN MiniMax-M3 capability testing confirmed this separates reasoning\n''',
)

# Tests: request payload must disable thinking and use the current M3 generation
# limit field; explicit finish=length must be rejected without transport retries.
replace_once(
    "tests/test_minimax_provider.py",
    '''                self.assertTrue(request["reasoning_split"])\n                payload = {\n''',
    '''                self.assertTrue(request["reasoning_split"])\n                self.assertEqual(request["thinking"], {"type": "disabled"})\n                self.assertEqual(request["max_completion_tokens"], 4096)\n                self.assertNotIn("max_tokens", request)\n                payload = {\n''',
)
replace_once(
    "tests/test_minimax_provider.py",
    '''    def test_terminology_aggregates_reasoning_tokens(self) -> None:\n''',
    '''    def test_finish_reason_length_is_output_limit_without_transport_retry(self) -> None:\n        calls = 0\n        with tempfile.TemporaryDirectory() as directory:\n            key = Path(directory) / "key"\n            key.write_text("test-secret", encoding="utf-8")\n\n            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:\n                nonlocal calls\n                calls += 1\n                payload = {\n                    "choices": [{\n                        "finish_reason": "length",\n                        "message": {"content": "{\\\"segments\\\":["},\n                    }],\n                    "usage": {"prompt_tokens": 10, "completion_tokens": 4096},\n                }\n                return 200, {}, json.dumps(payload).encode()\n\n            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)\n            with self.assertRaises(MiniMaxProviderError) as context:\n                client.correct_window(ITEMS, [])\n            self.assertEqual(calls, 1)\n            self.assertEqual(context.exception.kind, ProviderFailureKind.OUTPUT_LIMIT)\n\n    def test_invalid_structured_response_retries_only_at_structured_layer(self) -> None:\n        calls = 0\n        with tempfile.TemporaryDirectory() as directory:\n            key = Path(directory) / "key"\n            key.write_text("test-secret", encoding="utf-8")\n\n            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:\n                nonlocal calls\n                calls += 1\n                payload = {\n                    "choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}],\n                }\n                return 200, {}, json.dumps(payload).encode()\n\n            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)\n            with self.assertRaises(MiniMaxProviderError) as context:\n                client.correct_window(ITEMS, [])\n            self.assertEqual(calls, 2)\n            self.assertEqual(context.exception.kind, ProviderFailureKind.INVALID_RESPONSE)\n\n    def test_terminology_aggregates_reasoning_tokens(self) -> None:\n''',
)

replace_once(
    "tests/test_correction_routing.py",
    '''    def test_auth_failure_is_not_silently_hidden(self) -> None:\n''',
    '''    def test_output_limit_switches_rest_of_job_to_gemini_without_same_provider_retry(self) -> None:\n        decision = decide_provider_failure(\n            CorrectionProvider.MINIMAX_M3,\n            ProviderFailureKind.OUTPUT_LIMIT,\n        )\n        self.assertTrue(decision.switch_to_gemini_for_rest_of_job)\n        self.assertFalse(decision.retry_same_provider)\n        self.assertFalse(decision.fail_closed)\n\n    def test_auth_failure_is_not_silently_hidden(self) -> None:\n''',
)

replace_once(
    "tests/test_correction_runtime.py",
    '''        self.reasoning_split = True\n''',
    '''        self.reasoning_split = True\n        self.thinking_mode = "disabled"\n''',
)
replace_once(
    "tests/test_correction_runtime.py",
    '''            self.assertTrue(manifest["m3_reasoning_split"])\n''',
    '''            self.assertTrue(manifest["m3_reasoning_split"])\n            self.assertEqual(manifest["m3_thinking_mode"], "disabled")\n''',
)

# Performance regression: expose thinking mode alongside historical provider evidence.
replace_once(
    "tests/test_performance_enhanced_regressions.py",
    '''                "m3_max_output_tokens": 4096,\n            }),\n''',
    '''                "m3_max_output_tokens": 4096,\n                "m3_thinking_mode": "disabled",\n            }),\n''',
)
replace_once(
    "tests/test_performance_enhanced_regressions.py",
    '''        self.assertEqual(observed["m3OutputTokenLimit"], 4096)\n''',
    '''        self.assertEqual(observed["m3OutputTokenLimit"], 4096)\n        self.assertEqual(observed["m3ThinkingMode"], "disabled")\n''',
)
