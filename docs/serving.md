# Nemotron 3.5 Lightning Serving Record

For the full input, dataset, output, setup, and validation procedure, see
[`model-validation.md`](model-validation.md).

Status: **planned, not yet deployed or measured**. Complete every placeholder from the actual
Brev instance before treating a run as submission evidence.

## Intended deployment

- Checkpoint: `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`
- Revision: `bee7596271d1495f6992ae224aefde4410e816b8`
- Served name: `nemotron-3.5-lightning`
- Backend: vLLM 0.27.1 or a later verified compatible version
- Compute: event-provided NVIDIA Brev credits only
- Draft/speculative models: disabled

Use the no-speculative-decoding H100 recipe in `docs/bounties.md`, section 5. Re-check all
flags against the installed vLLM version before launch; that recipe is planned rather than a
record of a command already executed.

## Test-run procedure

vLLM runs on the Brev GPU instance; the runner, Saleor apps and Postgres stay on the Linux
runner machine, which already has Docker and the Saleor worktrees. vLLM listens on
`127.0.0.1` only, and the runner machine reaches it through an SSH tunnel, so the endpoint is
never public.

1. **Instance** (event credits only): create it in the event's Brev organization, not a personal
   one. The recipe is the model card's 1x H100 configuration. Note the instance type.
2. **Serve** (on the instance): copy `scripts/brev/serve_lightning.sh` there and run it in
   `tmux`. It installs vLLM, records `~/migproto-serving/environment.txt` and
   `migproto-serving.env`, then serves on `127.0.0.1:8000`. Wait for `Application startup
   complete` in `~/migproto-serving/vllm.log`. If a recipe flag is rejected by the installed
   vLLM, fix it in the script and record the change here.
3. **Tunnel** (on the runner machine): `ssh -N -L 8000:127.0.0.1:8000 <instance>` (Brev's CLI
   adds the SSH host alias after `brev login`), or `brev port-forward`.
4. **Record**: copy `~/migproto-serving/migproto-serving.env` and `environment.txt` to the
   runner machine outside the repository, `source` the env file, and set
   `MIGPROTO_BREV_INSTANCE` and `MIGPROTO_LLM_BASE_URL=http://localhost:8000/v1`.
5. **Validate**: `scripts/brev/check_endpoint.sh` must print `OK` (served name, non-empty JSON
   `message.content`, usage metadata).
6. **Test run**: one `uv run migproto agent-loop --proposals llm --topology roles`, then one
   with `--topology single`. Fill in the table below and `docs/results.md` from the saved runs.
7. **Stop the instance** when the run is done so credits are not spent idle.

## Deployment evidence to fill in

| Field | Recorded value |
|---|---|
| Brev instance type | pending |
| GPU model and count | pending |
| Driver version | pending |
| CUDA version | pending |
| vLLM version or image digest | pending |
| Exact checkpoint revision | pending |
| Exact launch command and environment | pending |
| Reasoning parser and thinking setting | pending |
| Prefix caching enabled | pending |
| Startup log excerpt | pending |
| Endpoint health check | pending |

Mirror these values into the `MIGPROTO_*` environment variables documented in `.env.example`.
The controller records them in `agent_config`; unset values remain `null` rather than being
inferred.

## Core validation before experiments

1. Confirm `/v1/models` reports the served Lightning name.
2. Confirm chat completions return non-empty `message.content` and usage metadata.
3. Run both `roles` and `single` topologies with the same request and total budget.
4. Preserve the run directories and promote representative runs through the existing export
   process after Harsha reviews them.
5. Only then measure prefix caching or concurrent independent reviews. Do not describe the
   sequential role chain as parallel execution.
