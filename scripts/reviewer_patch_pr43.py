from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected patch anchor not found: {path}: {old[:80]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Preserve MiniMax reasoning-token evidence when the provider exposes it.
replace_once(
    "app/providers/minimax_provider.py",
    '''    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", usage.get("output_token_count", 0)))\n    def integer(value: object) -> int:\n''',
    '''    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", usage.get("output_token_count", 0)))\n    completion_details = usage.get("completion_tokens_details")\n    completion_details = completion_details if isinstance(completion_details, Mapping) else {}\n    reasoning_tokens = completion_details.get("reasoning_tokens", usage.get("reasoning_tokens", 0))\n    def integer(value: object) -> int:\n''',
)
replace_once(
    "app/providers/minimax_provider.py",
    '''        "output_tokens": integer(output_tokens),\n        "total_tokens": integer(usage.get("total_tokens", 0)),\n''',
    '''        "output_tokens": integer(output_tokens),\n        "reasoning_tokens": integer(reasoning_tokens),\n        "total_tokens": integer(usage.get("total_tokens", 0)),\n''',
)

# 2) urllib raises HTTPError for non-2xx responses. Classify the real exception
# path with the same fail-closed rules as tuple-returning test transports.
replace_once(
    "app/providers/minimax_provider.py",
    '''            except (HTTPError, URLError, TimeoutError, OSError) as exc:\n                kind = ProviderFailureKind.RATE_LIMIT if isinstance(exc, HTTPError) and exc.code == 429 else ProviderFailureKind.TRANSIENT_EXHAUSTED\n                last_error = MiniMaxProviderError(\n                    "MiniMax transport failed",\n                    kind=kind,\n                    status_code=int(exc.code) if isinstance(exc, HTTPError) else None,\n                )\n                attempts.append(\n                    {\n                        "attempt": attempt,\n                        "started_at": started_at,\n                        "completed_at": _iso(),\n                        "latency_ms": round((time.monotonic() - started) * 1000),\n                        "status_code": last_error.status_code,\n                        "failure_kind": kind.value,\n                    }\n                )\n                if attempt < self.max_attempts:\n                    self.sleeper(min(30.0, 2**attempt))\n''',
    '''            except HTTPError as exc:\n                status_code = int(exc.code)\n                try:\n                    raw_error = exc.read()\n                except OSError:\n                    raw_error = b""\n                try:\n                    error_payload: object = json.loads(raw_error.decode("utf-8")) if raw_error else {}\n                except (UnicodeDecodeError, json.JSONDecodeError):\n                    error_payload = raw_error[:2000].decode("utf-8", "replace")\n                kind = _failure_kind(status_code, error_payload)\n                last_error = MiniMaxProviderError(\n                    "MiniMax HTTP request failed",\n                    kind=kind,\n                    status_code=status_code,\n                    raw_response=error_payload,\n                )\n                attempts.append(\n                    {\n                        "attempt": attempt,\n                        "started_at": started_at,\n                        "completed_at": _iso(),\n                        "latency_ms": round((time.monotonic() - started) * 1000),\n                        "status_code": status_code,\n                        "failure_kind": kind.value,\n                    }\n                )\n                self._last_attempts = attempts\n                if kind in {ProviderFailureKind.AUTHENTICATION, ProviderFailureKind.USAGE_LIMIT}:\n                    raise last_error\n                if attempt < self.max_attempts:\n                    self.sleeper(min(30.0, 2**attempt))\n            except (URLError, TimeoutError, OSError) as exc:\n                kind = ProviderFailureKind.TRANSIENT_EXHAUSTED\n                last_error = MiniMaxProviderError(\n                    "MiniMax transport failed",\n                    kind=kind,\n                )\n                attempts.append(\n                    {\n                        "attempt": attempt,\n                        "started_at": started_at,\n                        "completed_at": _iso(),\n                        "latency_ms": round((time.monotonic() - started) * 1000),\n                        "status_code": None,\n                        "failure_kind": kind.value,\n                    }\n                )\n                self._last_attempts = attempts\n                if attempt < self.max_attempts:\n                    self.sleeper(min(30.0, 2**attempt))\n''',
)

# 3) Surface reasoning tokens through provider-neutral performance evidence.
replace_once(
    "app/jobs/performance.py",
    '''        provider = "minimax" if payload.get("provider") == "minimax" or kind == "correction-m3" else "google-vertex-ai"\n''',
    '''        reasoning_tokens = _token_value(\n            usage,\n            ("reasoning_tokens", "reasoningTokenCount"),\n        )\n        provider = "minimax" if payload.get("provider") == "minimax" or kind == "correction-m3" else "google-vertex-ai"\n''',
)
replace_once(
    "app/jobs/performance.py",
    '''                "outputTokens": output_tokens,\n                "estimatedCostUsd": _money(cost),\n''',
    '''                "outputTokens": output_tokens,\n                "reasoningTokens": reasoning_tokens,\n                "estimatedCostUsd": _money(cost),\n''',
)
replace_once(
    "app/jobs/performance_enhanced.py",
    '''        "outputTokens": sum(max(0, int(item.get("outputTokens") or 0)) for item in selected),\n        "latencyMs": sum(max(0, int(item.get("latencyMs") or 0)) for item in selected),\n''',
    '''        "outputTokens": sum(max(0, int(item.get("outputTokens") or 0)) for item in selected),\n        "reasoningTokens": sum(max(0, int(item.get("reasoningTokens") or 0)) for item in selected),\n        "latencyMs": sum(max(0, int(item.get("latencyMs") or 0)) for item in selected),\n''',
)
replace_once(
    "app/jobs/performance_enhanced.py",
    '''        "m3ReasoningSplit": routing.get("m3_reasoning_split") if isinstance(routing, dict) else None,\n        "minimaxInvalidResponseCount": len(invalid_minimax),\n''',
    '''        "m3ReasoningSplit": routing.get("m3_reasoning_split") if isinstance(routing, dict) else None,\n        "minimaxReasoningTokens": summary["providerCallBreakdown"]["minimax"]["reasoningTokens"],\n        "minimaxInvalidResponseCount": len(invalid_minimax),\n''',
)

# 4) Regression tests exercise the *actual* urllib HTTPError path and reasoning accounting.
replace_once(
    "tests/test_minimax_provider.py",
    '''import json\nimport tempfile\nimport unittest\nfrom pathlib import Path\n''',
    '''import io\nimport json\nimport tempfile\nimport unittest\nfrom pathlib import Path\nfrom urllib.error import HTTPError\n''',
)
replace_once(
    "tests/test_minimax_provider.py",
    '''                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},\n''',
    '''                    "usage": {\n                        "prompt_tokens": 11,\n                        "completion_tokens": 7,\n                        "completion_tokens_details": {"reasoning_tokens": 13},\n                    },\n''',
)
replace_once(
    "tests/test_minimax_provider.py",
    '''            self.assertEqual(record["usage_metadata"]["input_tokens"], 11)\n            self.assertTrue(record["reasoning_split"])\n''',
    '''            self.assertEqual(record["usage_metadata"]["input_tokens"], 11)\n            self.assertEqual(record["usage_metadata"]["reasoning_tokens"], 13)\n            self.assertTrue(record["reasoning_split"])\n''',
)
replace_once(
    "tests/test_minimax_provider.py",
    '''    def test_authentication_error_is_not_converted_to_quota_fallback(self) -> None:\n''',
    '''    def test_real_urllib_http_error_authentication_fails_closed_without_retry(self) -> None:\n        calls = 0\n        with tempfile.TemporaryDirectory() as directory:\n            key = Path(directory) / "key"\n            key.write_text("test-secret", encoding="utf-8")\n\n            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:\n                nonlocal calls\n                calls += 1\n                raise HTTPError(\n                    url,\n                    401,\n                    "Unauthorized",\n                    {},\n                    io.BytesIO(b'{"base_resp":{"status_code":1001,"status_msg":"unauthorized"}}'),\n                )\n\n            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)\n            with self.assertRaises(MiniMaxProviderError) as context:\n                client.correct_window(ITEMS, [])\n            self.assertEqual(calls, 1)\n            self.assertEqual(context.exception.kind, ProviderFailureKind.AUTHENTICATION)\n\n    def test_real_urllib_http_error_usage_limit_fails_closed_without_retry(self) -> None:\n        calls = 0\n        with tempfile.TemporaryDirectory() as directory:\n            key = Path(directory) / "key"\n            key.write_text("test-secret", encoding="utf-8")\n\n            def http_post(url: str, headers: object, body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:\n                nonlocal calls\n                calls += 1\n                raise HTTPError(\n                    url,\n                    402,\n                    "Payment Required",\n                    {},\n                    io.BytesIO(b'{"base_resp":{"status_code":1008,"status_msg":"quota exhausted"}}'),\n                )\n\n            client = MiniMaxCorrectionClient(key_file=key, http_post=http_post, sleeper=lambda _: None)\n            with self.assertRaises(MiniMaxProviderError) as context:\n                client.correct_window(ITEMS, [])\n            self.assertEqual(calls, 1)\n            self.assertEqual(context.exception.kind, ProviderFailureKind.USAGE_LIMIT)\n\n    def test_authentication_error_is_not_converted_to_quota_fallback(self) -> None:\n''',
)

replace_once(
    "tests/test_performance_enhanced_regressions.py",
    '''                        "output_tokens": 150,\n                        "billing_mode": "token_plan",\n''',
    '''                        "output_tokens": 150,\n                        "reasoning_tokens": 321,\n                        "billing_mode": "token_plan",\n''',
)
replace_once(
    "tests/test_performance_enhanced_regressions.py",
    '''        self.assertEqual(summary["providerCallBreakdown"]["minimax"]["retryCount"], 1)\n        self.assertEqual(summary["correctionRouting"]["requestedPolicy"], "M3_FIRST")\n''',
    '''        self.assertEqual(summary["providerCallBreakdown"]["minimax"]["retryCount"], 1)\n        self.assertEqual(summary["providerCallBreakdown"]["minimax"]["reasoningTokens"], 321)\n        self.assertEqual(summary["observability"]["minimaxReasoningTokens"], 321)\n        self.assertEqual(summary["correctionRouting"]["requestedPolicy"], "M3_FIRST")\n''',
)

# 5) Durable sanitized validation summary. Do not fabricate unavailable latency metrics.
report = Path("docs/M3_PHASE_B_VALIDATION_20260817.md")
report.write_text(
    '''# MiniMax M3 Phase B validation — 2026-08-17\n\n## Scope\n\nThis is a sanitized, repository-retained summary of the bounded Phase B validation.\nIt intentionally excludes transcript text, credentials, authorization headers, and private source names.\nThe detailed provider evidence remains on the VPS under:\n`/opt/course-transcript-source/data/m3-validation/phase-b-20260817/`.\n\n## Runtime and root cause\n\n- Validation base before PR #43 deployment: `3385c6d58ea571a4ea399711762fa9c515560d4f`.\n- MiniMax endpoint/account: configured CN Token Plan account; no credential material is retained here.\n- Confirmed root cause of the earlier invalid JSON failures: MiniMax-M3 reasoning was embedded in `message.content` and could consume the configured `4096` completion-token budget before final structured JSON was produced.\n- Capability canary confirmed `reasoning_split=true` separates provider reasoning from final `message.content` on the actual configured MiniMax-M3 account.\n- The fix does **not** increase `MINIMAX_M3_MAX_OUTPUT_TOKENS`.\n\n## 10-minute immutable-segment A/B\n\nThe same pre-existing Chirp segments and timestamps were reused; Chirp was not rerun.\n\n| Provider | Valid | Transport timeout | Output-limit hit |\n|---|---:|---:|---:|\n| Gemini 3.7 Flash | 10/10 | 0 | 0 |\n| MiniMax M3 | 4/10 | 6/10 | 0 |\n\n- MiniMax provider timeout limit during this validation: **60 seconds**.\n- The reasoning/output-ceiling failure was no longer observed after `reasoning_split=true`.\n- M3 transport reliability did not meet the production gate.\n\n## Long-course source samples\n\nThree existing long-course sources were sampled using bounded **5-minute** A/B windows. These were not full-course runs.\n\n| Sample | Gemini valid | MiniMax M3 valid | Result |\n|---|---:|---:|---|\n| A | 5/5 | 3/5 | M3 reliability gate failed |\n| B | 5/5 | 2/5 | M3 reliability gate failed |\n| C | 5/5 | 3/5 | M3 reliability gate failed |\n\nAggregate Gemini result: **15/15 valid**. MiniMax failures were transport timeouts; no M3 output-limit hit was reported.\n\n**Full long-course A/B status: BLOCKED / NOT COMPLETED.**\nThe bounded stop gate was intentionally applied after M3 transport reliability failed, avoiding unnecessary provider consumption.\n\n## Quota/fallback evidence\n\n- Controlled `usage_limit` fallback E2E: PASS.\n- Same source: one-way M3 → Gemini switch; no M3 re-entry.\n- Raw Chirp segment identity/timing invariants: preserved.\n- CN Token Plan `general` pool after bounded validation: interval remaining **96%**, weekly remaining **99%**.\n\n## Latency and reasoning-token evidence\n\n- P50/P95 latency values are **NOT_AVAILABLE in the PR-retained evidence** and are therefore not fabricated here.\n- Historical bounded calls did not retain normalized reasoning-token counts in the performance summary. PR #43 reviewer hardening adds `usage_metadata.reasoning_tokens` and provider-performance aggregation for future calls when the provider exposes `completion_tokens_details.reasoning_tokens`.\n\n## Production gate\n\n`READY_FOR_M3_PRODUCTION = NO`\n\nReason: structured-output reliability improved, but MiniMax-M3 transport/latency reliability under the real workload remains below the production-primary threshold.\n\nProduction requirements remain:\n\n- `MINIMAX_M3_ENABLED=false`\n- `MINIMAX_M3_QUOTA_CHECK_ENABLED=false`\n- Gemini remains the safe baseline.\n\nThe next investigation should measure non-stream TTFB/total latency versus streaming first-chunk/final-content timing before changing timeout or retry policy.\n''',
    encoding="utf-8",
)
