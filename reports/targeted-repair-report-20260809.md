# Targeted repair report

Generated (UTC): 2026-08-09T07:35:48.191722Z

## Totals

- job_count: 14
- audio_minutes: 1887.23
- patch_count: 20
- patch_audio_seconds: 923.25
- patch_words: 2551
- segments: 37883
- words: 349691
- qa_pass_count: 14
- qa_error_count: 0
- gemini_corrected_segments: 5753
- raw_fallback_segments: 29902
- timing_repairs: 192
- dropped_anomalies: 724
- gemini_prompt_tokens: 3151119
- gemini_candidate_tokens: 2168655
- gemini_thoughts_tokens: 4781116
- gemini_total_tokens: 10100890
- gemini_usage_records: 2441

## Per job

| job | audio min | segments | words | patches | patch sec | QA | fallback | Gemini tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 01-20260308-20260808-155020-c0a93c | 136.55 | 2568 | 23216 | 1 | 22.4 | PASS | 2568 | 675139 |
| 02-20260315-20260808-155020-4a542a | 132.36 | 2794 | 25986 | 1 | 21.56 | PASS | 2794 | 536675 |
| 03-20260322-20260808-155020-53e1d1 | 156.9 | 3208 | 31185 | 1 | 23.13 | PASS | 3208 | 920467 |
| 06-20260412-20260808-154719-153686 | 133.08 | 2767 | 25306 | 1 | 24.54 | PASS | 2767 | 523879 |
| 07-20260419-20260808-154719-f275cc | 134.96 | 2086 | 22465 | 1 | 24.37 | PASS | 2086 | 409090 |
| 08-20260426-20260808-154719-10c58a | 125.85 | 2829 | 24336 | 1 | 24.18 | PASS | 2829 | 521279 |
| 09-20260510-20260808-154719-24752c | 150.76 | 3158 | 27867 | 1 | 22.36 | PASS | 3158 | 544897 |
| 10-20260524-20260808-154719-c413be | 123.33 | 2588 | 22837 | 1 | 22.54 | PASS | 2588 | 538290 |
| 11-20260531-20260808-154719-7a7d18 | 121.48 | 2637 | 23009 | 1 | 174.0 | PASS | 2637 | 484564 |
| 12-20260607-20260808-154719-d1e0fe | 137.69 | 3023 | 27153 | 3 | 118.19 | PASS | 1 | 952801 |
| 13-20260614-20260808-154719-14c8cf | 138.32 | 2689 | 26277 | 1 | 21.64 | PASS | 2689 | 841068 |
| 14-20260621-20260808-154719-4db4db | 131.21 | 2595 | 23337 | 3 | 210.0 | PASS | 2 | 1247293 |
| 15-20260628-20260808-154719-993bc2 | 124.89 | 2367 | 22288 | 3 | 190.0 | PASS | 1 | 1077933 |
| 20260705-20260808-154719-741cd9 | 139.85 | 2574 | 24429 | 1 | 24.34 | PASS | 2574 | 827515 |

## Notes

- Original base chunks and raw words.json were preserved; patch chunks use indices 910000-910019.
- All 20 targeted Batch operations succeeded and GCS result objects were cleaned after recovery.
- QA/validation passed for all 14 selected jobs; warnings remain for long silent/low-confidence tails and subtitle gaps.
- Jobs 12, 14, 15 received Gemini 3.6 Flash segment correction; other jobs use explicit raw fallback to avoid stale segment-ID mapping.
- Provider cost is estimated from submitted patch audio duration and Gemini usage metadata; Cloud Billing is authoritative.
