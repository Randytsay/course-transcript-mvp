# MiniMax M3 content-rejection policy

## Live evidence

The historical Course B blocker window containing `seg-0338`
(`corr-v2:rt:seg-0337..seg-0360`) was diagnosed against the exact production
adapter and exact production prompt with one bounded paid call.

The provider returned HTTP 422 with sanitized metadata including:

```text
error_type=unprocessable_entity_error
error_type=error
category=content_rejected
```

The request contract itself had already been separately verified against the
current MiniMax M3 OpenAI-compatible API contract:

- `POST /v1/chat/completions`
- `model=MiniMax-M3`
- `thinking={"type":"disabled"}`
- `reasoning_split=true`
- `temperature=0.2`
- `max_completion_tokens=4096`

Therefore the `seg-0338` failure is treated as a provider content-policy
rejection, not a request-schema defect.

## Production behavior

A provider-confirmed moderation/content-policy rejection is represented as:

```text
ProviderError.kind = content_rejected
```

It is **window-local and non-retryable**.

When `fallback_policy=RAW_CHIRP_FALLBACK`, the correction orchestrator must:

1. preserve the immutable Chirp text for the entire rejected window;
2. record `reason=content_rejected` in `window_results`;
3. make no same-window retry;
4. not increment the transport/service circuit-breaker count;
5. continue to later normal windows with MiniMax M3.

## No moderation bypass

The application must not try to evade provider moderation after
`content_rejected` by:

- splitting the rejected window into smaller calls;
- paraphrasing, deleting, masking, or rewriting source transcript text;
- changing prompts solely to bypass the provider policy;
- changing endpoint, region, model, or API shape for the rejected content;
- automatically retrying the same content;
- transferring an unused call allowance to another variant of the same content.

If an operator believes the provider rejection is a false positive, the safe
runtime behavior remains raw-Chirp fallback. Provider support/escalation is a
separate administrative path and does not alter accepted transcript evidence.

## Observability

Safe error output may retain bounded metadata such as HTTP status, provider
code, error type, parameter, validation location/type, and the categorical
marker `category=content_rejected`.

It must never retain or expose raw provider messages, validation input, prompt,
transcript, corrected text, request body, API key, Authorization header, or
other arbitrary provider payload text.

## Gate implications

The historical native-M3 blocker remains `FAIL`: the target window did not
complete natively with M3.

This policy does **not** redefine that failure as native success. It only makes
the production fallback semantics explicit and testable.

Any future sample or long-run acceptance gate must count content-rejected
windows as fallback/non-M3 route coverage. It must not exclude them from the
denominator to inflate coverage.

Production M3 enablement remains a separate explicit gate.
