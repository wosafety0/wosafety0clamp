# AI Semantic Reviewer — Prototype

## Purpose

This prototype adds an evidence-validated, provider-agnostic semantic review client. It is deliberately separate from the required CI gate until a model endpoint, model identifier, credential storage, and failure policy are explicitly configured and verified.

It does not replace the pinned global-rules gate, deterministic tests, or human review. It does not perform gameplay actions.

## Input contract

The caller supplies a UTF-8 JSON file with these fields:

- `task`: expected outcome and relevant task context.
- `diff`: unified diff for the proposed change.
- `changed_files`: exact paths changed in the PR.
- `global_rules`: pinned global rules text.
- `project_instructions`: applicable project-specific instructions.

The caller must obtain the diff and rules from authoritative sources and identify the project instructions explicitly. Do not silently substitute an unrelated/default branch's instructions.

## Model configuration

The client uses an OpenAI-compatible chat-completions endpoint, configured only through environment variables:

- `AI_REVIEW_API_BASE`: API base URL (for example, a provider's `/v1` base); do not put credentials in this URL.
- `AI_REVIEW_API_KEY`: secret key, supplied through the runner's secret store.
- `AI_REVIEW_MODEL`: exact model identifier supported by that endpoint.

No provider is selected by default and no secret is committed. Never print these variables or include them in an artifact. Before enabling automatic PR gating, configure the provider secret and model variable, verify terms/data handling for repository diffs, and run an end-to-end test against a non-sensitive fixture.

## Output and fail-closed behavior

The model must return a JSON object with exactly these top-level fields:

- `status`: `PASS`, `NEEDS_CHANGES`, or `INCONCLUSIVE`.
- `summary`: non-empty summary.
- `checked_scope`: non-empty list of reviewed areas.
- `limitations`: list of uncertainty or untested areas.
- `findings`: structured findings with severity, changed-file path, positive line range, rule reference and quote, diff evidence, rationale, and recommendation.

The validator checks exact schema keys, known status/severity values, changed-file membership, integer line ranges, and that each finding's evidence and rule quote are exact substrings of the supplied diff and rules. `PASS` with findings and `NEEDS_CHANGES` without findings are rejected. API, JSON, or validation errors block the review; they are not converted to a pass.

These checks prove only that the report is structurally valid and quotes supplied material. They do not prove that the model's reasoning is correct, that the quoted line range matches the quote, or that the model found every defect. Human review remains required.

## Local / CI invocation

Prepare an input JSON file using the contract above, configure the three environment variables in a secure environment, then run:

```powershell
python scripts/ai_semantic_review.py --input review-input.json --output review-result.json
```

The CI test suite exercises the validator offline and makes no network/API calls. The current CI does **not** invoke a live model or require a provider secret.

## Before making it a required PR gate

1. Select an approved endpoint/model and store its key in repository or environment secrets.
2. Build the PR input from the exact PR head/base diff, pinned global rules, and the correct project instruction file.
3. Bound input size and response time; redact secrets and avoid sending unrelated private files.
4. Publish the review artifact and fail closed on missing configuration, provider errors, invalid JSON, or evidence validation failure.
5. Evaluate representative clean, defective, malformed-output, timeout, and prompt-injection fixtures.
6. Only then decide whether AI review is required for merge; do not claim semantic coverage based on the prototype's offline tests alone.
