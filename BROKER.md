# Linux reference stage broker

`factory_broker` implements the stdin/JSON receipt protocol in [FACTORY.md](FACTORY.md) for `implement`, `verify`, and `deploy`. `factory` still handles eligibility, PR creation, and merge. The broker never runs candidate code, tests, Git hooks, or repository commands on the controller. It is **not provisioned**: the isolation launchers, model gateway, branch publisher, and optional deploy authority below are required infrastructure, not bundled emulations. Without those guarantees, leave promotion disabled.

## Provision only from a trusted release

Use Linux, Python 3.11+, Git at `/usr/bin/git`, and a local filesystem with reliable locking and fsync. Install as root from a reviewed release, not from the target checkout or from a candidate. Run promotion as a dedicated user (`factory-promote` below), separate from the intake service and from all guests. The intake user must not be able to read the broker config or state, call privileged adapters, or acquire their credentials. The promotion controller retains its own approval policy and scoped GitHub identities as documented in [FACTORY.md](FACTORY.md). Do not put repository/project/signing/provider credentials in the broker config or worker environments.

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin factory-promote
sudo install -d -m 0755 /usr/local/libexec /opt/silentdeluge-broker/factory_broker
sudo install -m 0644 factory_broker/*.py /opt/silentdeluge-broker/factory_broker/
sudo install -d -m 0750 -o root -g factory-promote /etc/silentdeluge-broker
sudo install -d -m 0700 -o factory-promote -g factory-promote /var/lib/silentdeluge-broker
sudo tee /usr/local/libexec/silentdeluge-factory-stage >/dev/null <<'PY'
#!/usr/bin/python3 -IB
import sys
sys.path.insert(0, '/opt/silentdeluge-broker')
from factory_broker.__main__ import serve
sys.exit(serve(sys.argv[1:], sys.stdin.buffer, sys.stdout.buffer, sys.stderr))
PY
sudo chmod 0755 /usr/local/libexec/silentdeluge-factory-stage
sudo install -m 0600 -o factory-promote -g factory-promote reviewed-broker.json /etc/silentdeluge-broker/config.json
sudo -u factory-promote /usr/local/libexec/silentdeluge-factory-stage --initialize
```

Install each reviewed adapter as a regular executable under a trusted, absolute path (for example, `sudo install -o root -g factory-promote -m 0750 reviewed-adapter /opt/factory-authority/NAME`). Adapters that delegate to a privileged service must authorize the promotion principal and exact operation at that service. Do not grant generic shell, container daemon, VM management, filesystem, or sudo access. Protect path ancestors and pin binaries, guest images, client configuration, and service policies to reviewed revisions; a pathname check is not attestation. Only after checking all boundaries, point the promotion policy's `broker` to `/usr/local/libexec/silentdeluge-factory-stage` and align its `broker_revision`, target, models, tests, task and paths with the config.

`/etc/silentdeluge-broker/config.json` is fixed; no environment or CLI override. Example (`public_mirror: null` selects unauthenticated HTTPS to the exact GitHub repo; `deployer: null` disables deploy):

```json
{
  "target":{"repo":"example-owner/example-repo","project_owner":"example-organization","project_number":1,"project_title":"Inventory Foundations","base_ref":"main"},
  "models":{"implement":"openai-codex/gpt-6.2-sol","verify":"openai-codex/gpt-6.2-astra"},
  "revision":"reviewed-version-1",
  "state_dir":"/var/lib/silentdeluge-broker",
  "public_mirror":null,
  "omp":"/opt/omp/omp",
  "model_runner":"/opt/factory-authority/model-runner",
  "publisher":"/opt/factory-authority/branch-publisher",
  "workspace_runner":"/opt/factory-authority/workspace-runner",
  "tests":["/opt/factory-tests/check-inventory","--synthetic"],
  "test_timeout":300,
  "deployer":null,
  "approvals":[{"issue":123,"base":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","task":"Correct the documented inventory example without changing the collector contract.","paths":["examples/","README.md"],"stages":["implement","verify"]}]
}
```

Grants list only broker stages (ordered prefix `implement`, `verify`, `deploy`); the controller policy separately gates PR and merge. Enabling deploy requires its own fixed adapter and grant. Grant edits do not fence already-started actions. Task or scope changes require a new attempt. To use `public_mirror`, supply an absolute, operator-maintained bare repository with trusted Git configuration and objects from the public target only, no credentialed alternates, helpers or hooks. It must be authoritative for ref observations; never use a lagging copy for recovery or deploy. Production can use direct HTTPS instead. No clone checkout, Git attributes, submodules, symlinks, or nonportable filenames are supported. The broker reads raw blobs, not archive export rules or filters. Sol gets only bounded, scoped UTF-8 files and the curated task; edits replace 1–99 existing regular files without changing modes. No `.git` path or top-level `.github` edit is allowed. Astra gets before/after data for the exact candidate; it does not run tests.

## Required adapter interfaces

Each invocation uses a fixed executable and argv (no shell), an ephemeral empty working directory and home, and no ambient auth/environment forwarding. Stderr, model output and test logs never reach the controller. Child environment filtering alone does **not** establish isolation.

- **Model runner:** receives `run -- <guest-omp> <fixed args>` and a UTF-8 JSON prompt on stdin. In a fresh, nonprivileged guest, copy stdin to `/run/factory-model/prompt.json`, close stdin, and invoke that exact argv from a new empty temp directory with a clean guest home. No host mounts/sockets, credentials, agents, shell tools or other tools exposed to the model. Egress only to a scoped inference gateway that permits the requested model, never substitutes a model, and attests the actual served model bound to this invocation. Provider credentials stay at that gateway. The runner emits `{"model":"<attested-model>","output":"<OMP final text>"}`; the model may control only `output`. A successful OMP exit or its requested-model string alone is not proof of the actual model. `omp --help` from installed OMP v19.1.0 lists `--no-tools`, `--no-context-files`, `--no-extensions`, `--no-session`, `--no-skills`, `--no-rules`, `--no-prewalk`, `--no-lsp`, `--no-pty`, `--offline`, provider-qualified `--model`, and `@` file arguments. Recheck the pinned guest binary (`cd "$(mktemp -d)" && /opt/omp/omp --help`) and probe the sealed guest: no discovery/extensions or ambient config, no offered tools, no model fallback or reachable host services. `--offline` is not a network firewall. Do not point `model_runner` at OMP directly.
- **Workspace runner:** receives `--workspace <broker-owned tree> --timeout <seconds> -- <fixed test argv>`. Copy the input tree as data into a fresh, disposable unprivileged guest; do not mount the host tree or permit write-back, host sockets/devices, credentials, real data, or network. The trusted test executable and dependencies live outside the writable guest workspace on the reviewed image. Run the fixed argv in the guest workspace, enforce timeout and guest teardown even if the broker dies, and exit zero only for an observed passing test run. Never infer success from stdout. If wrapping `lab sandbox`, verify those semantics on the pinned installation before forwarding this interface; a same-host subprocess or a sandbox with network or writable host mounts is not acceptable. Use a test harness that candidate code cannot replace or cause to report false success merely by exiting zero.
- **Publisher:** receives `publish` and JSON `{target,issue,base,attempt,key,sha,ref,pack}`. `ref` is `refs/heads/silentdeluge-factory/i<issue>-<base>/a<attempt>`; `pack` is base64 Git pack data containing the candidate objects, relative to the approved base. Use an isolated bare object store with an exact base (no checkout; no hooks; validate/cap all input). Revalidate the exact repo, base tip, issue grant, derived ref, candidate object, direct parent and allowed replacements; authenticate this caller and operation. Atomically **create only** that ref if absent. Never update an existing ref, force push, write the default branch, or grant default-branch or PR/merge writes to this identity. A pre-read plus unconstrained `git push` is not create-only; use an atomic server-side absent-ref precondition. Independently verify remote rules and CI: candidate pushes/PRs must not obtain secrets, write tokens or privileged runners. Only this publisher may populate the namespace.
- **Optional deployer:** receives `deploy` and JSON `{target,issue,base,attempt,key,sha,candidate}`. Pin to that exact merge SHA (not a checkout or a moving branch); independently enforce destination allowlist, current base tip and ordered parents at admission under existing deployment serialization. Execute only a fixed, existing deploy hook outside the checkout. No candidate-controlled argv, deploy commands or destination. Never provision on an unverified deployment system.
- **Both effect adapters:** receive `observe` with the same input minus `pack`. Return only `{"key":"<key>","sha":"<sha>","status":"published"}` or `"deployed"` for an observed, durably recorded success of that request. `"fenced"` is a durable terminal tombstone for a request that had no effect and can never have one, even if a disconnected worker or a delayed request arrives. Unknown, missing, interrupted or merely queued is a nonzero result, not `fenced`. Persist and reconcile apply intent/receipt under the supplied deterministic key; enforce the same request binding and serialize with fencing. Exit zero from `publish`/`deploy` is not a receipt; the broker requires `observe` and, for publication, independently reads the exact external ref. No journal entry or Git ref created by an unknown writer is acceptable proof. These services must be idempotent/recoverable independently of broker process lifetime.

## Replay and recovery

Use one private journal per target on one host; never copy it into a second active broker, remove `broker.lock`, or reinitialize after state loss. Back it up with the broker idle. The broker takes one nonblocking target-wide lock and binds attempts to the private scope and authority revision. It persists a publication/deploy intent before invoking its adapter, and never reissues an ambiguous mutation. A replay returns a durable matching receipt after required ref checks, or asks the original adapter for an observation. A stale attempt cannot publish after a newer reservation. A new attempt cannot supersede unresolved publication/deploy for the same issue; unresolved deploy also fences other deploys of the target. A model preparation interrupted before any publication intent can be done again. A verify reservation with no durable pass never certifies the candidate; use the controller's new-attempt procedure, not a replay of its tests.

If an effect is ambiguous, inspect the adapter's records and authoritative ref/rollout; reconcile the same key in the adapter or durably fence it before superseding work. Do not turn an absent ref, a missing record, or a successful process exit into a receipt. Keep the same authority configuration while reconciling. A matching broker stage rerun can observe a pending effect even if it ultimately fails the current-ref check; a later implementation also attempts to settle older pending effects for the issue before it can act. On lost journal state, restore and reconcile with the adapters and remote before resuming; `--initialize` is for first provisioning only.

```sh
python3 -B -m unittest -v test_factory_broker
```

The suite uses local synthetic Git data and authority fixtures. It does not establish production isolation, GitHub authorization, inference provenance, or deployment authority.
