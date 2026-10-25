# Free Model Response Parser Diagnostic

Date: 2026-10-25
Branch: `dev/sparkling-planner-mvp`
Scope: source/history-based diagnosis only. No provider API requests were made during this diagnostic.

## Known smoke-test behavior

The 2026-10-25 inference smoke test reported:
- Groq: HTTP success, but the extracted output was empty.
- OpenRouter Free: HTTP success, but the extracted output was empty.
- Cerebras: HTTP success, but the extracted output was empty.
- Mistral: non-empty text was extracted successfully.

The script's non-Gemini extraction path used PowerShell JSON parsing and selected `choices[0].message.content`. The report did not preserve the complete sanitized provider response shape, so the precise reason for the empty fields cannot be established from existing evidence.

## Assessment

- HTTP success plus empty extracted text does not establish that a model failed to generate output.
- The current evidence supports a parser/response-shape investigation, but does not prove that parsing is the root cause.
- Potential response differences to handle explicitly include missing/null `message.content`, array-form content blocks, alternate text fields, and provider-specific error objects returned with an HTTP success status.
- Do not print full provider responses or authorization headers. A future diagnostic should emit only response keys, choices count, message/content types, finish reason, and a short redacted preview if needed.
- Do not send additional requests until a deterministic parser is tested against synthetic fixtures. Then, if a live confirmation is still useful, use at most one request to one provider, with no automatic retries.

## Recommended next steps

1. Create an offline parser helper and synthetic fixtures for a normal string content response, array-form content, missing content, null content, and an empty choices list.
2. Assert that valid synthetic responses yield text and malformed/empty responses yield explicit diagnostic statuses rather than a generic empty-output label.
3. Review the provider-specific API response shape only after the offline tests pass. Preserve the previous rule: one request per provider at most, no retry loops, no real game data, and no secrets in logs.
4. Keep the current results unchanged in the record; do not retroactively label Groq, OpenRouter, or Cerebras as model failures or successful text generation.

## Safety and scope

No network requests, real game data, credentials, Poi runtime calls, or game actions were involved in this diagnostic. No local files were modified.
