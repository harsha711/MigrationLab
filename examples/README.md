# Fixtures

Small sanitized copies of real runs from the `feasibility-v1` checkpoint. They use contract `migproto.events/v0` (see `docs/contracts.md`). Outcomes and provenance are copied unchanged. Each directory has a `manifest.json` with the source run id, verdict and sanitization notes. Generated with `scripts/export_example.py`.

| Fixture | Source run | Kind | Verdict | Checks |
|---|---|---|---|---|
| `unsafe-rename/` | `20261017-123935-unsafe` | scripted demonstration (manual patches) | `REPRODUCED` | 7 total, 0 failed (`*_fails` checks pass when the old app fails as expected) |
| `scripted-sync-failure/` | `20261017-123256-staged` | scripted plan v1, deliberately flawed | `FAIL` | 29 total, 9 failed, including the `stage.contract.preconditions` refusal |
| `scripted-staged-success/` | `20261017-123138-staged` | scripted plan v2 | `PASS` | 33 total, 0 failed |
| `live-ordering-revision/` | `20261017-123652-agent-loop-llm` | live `gpt-oss:120b-cloud`, development smoke test | `PASS` (final attempt) | 71 total: attempt 1 had 30 with 1 failed (`retirement_gate`), attempt 2 had 41 with 0 failed |

Sanitization: absolute paths replaced by `<repo>` / `<home>`, model transcripts reduced to `agents/model_calls.json` (role, latency, token usage, errors, without prompts or responses), and server, migration and pytest logs omitted. No credentials were present. The exporter refuses files that look like they contain one.

The live fixture's second plan id is `a2-a2-rename-note-002` because the model echoed the runner's prefix. The prefix logic was fixed after that run, and the fixture is kept as recorded.

Do not feed these outcomes, or the scripted plans, to an evaluated agent.
