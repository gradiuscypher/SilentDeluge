# SilentDeluge factory

The GitHub repository and one organization Projects v2 board are the queue. The controller polls with two separate identities, records a local triage report, and journals promotion attempts. **The intake timer is read-only.** It cannot dispatch models, change cards, create PRs, merge, or deploy. GitHub remains authoritative for issues, cards, refs, PRs, and merges; SQLite stores observations, investigation packets, attempts, and receipts.

## Run intake

Use Python 3.11+ on Linux. The [sample config](factory/intake.example.json) uses `{"anonymous": true}` for public-repository issue and ref reads at `https://api.github.com`. This identity sends no bearer, permits no writes, cannot be used for promotion, and is never accepted as the project identity. No loopback adapter is needed. Anonymous reads share GitHub's unauthenticated quota for the egress IP.

Set the exact `owner/name`, organization login, Projects v2 number and title, base branch, and ready status. Keep the config and parent directories under operator control. Use a private, owner-only state directory on a local filesystem. Read its status as the same user that runs intake.

For manual authenticated reads, use distinct `FACTORY_REPO_TOKEN` (Issues and Contents read) and `FACTORY_PROJECT_TOKEN` (organization Projects read, with visibility of the board items); only variable names go in config. A trusted GitHub-compatible adapter can instead supply a scoped identity at an explicit `http://127.0.0.1:PORT` endpoint. Tokenless adapter roles must use distinct endpoints. Restrict the adapter locally to the scoped routes. Requests do not use proxy environment variables or follow redirects. The base CLI is stdlib-only and does not read an application signing key.

```sh
python3 -B -m factory --config /etc/silentdeluge-factory/intake.json intake
python3 -B -m factory --config /etc/silentdeluge-factory/intake.json status
python3 -B -m factory --config /etc/silentdeluge-factory/intake.json status 123
```

`intake` reads the repo's open issues, board, and base tip. It ignores PRs, draft cards, archived cards, and issues from other repositories. It records triage (`security`, `bug`, `enhancement`, `documentation`, `unclassified`), missing/duplicate cards, non-ready items, and ready items awaiting a maintainer. It stores no title, body, or comments. A failed read leaves the last complete snapshot intact.

On a complete snapshot, each open issue without a matching grant in the invocation's policy gets a local `investigation_packet` once per issue/base. Scheduled intake has no policy, so it treats every issue as unapproved. A packet contains two fixed questions selected by triage kind, an issue URL, and immutable commit and source tree URLs. It is bounded by the intake page limit and never changes merely because labels or cards changed. Generating packets does not fetch files, execute code, invoke a model, or create a task. `status ISSUE` shows all saved packets under `investigation_packets`; `status` displays the packet for the last observed base.

`status` does not contact GitHub or need tokens. Adding `--policy PATH` before `status` distinguishes a dispatchable issue from one still awaiting a grant. A ready status, label, or packet is never an approval. Local phases do not determine GitHub issue or card status.

### Install scheduled intake on the operator controller

**Operator: provision the issuer.** Create a dedicated organization-owned GitHub App with only **Organization permissions → Projects → Read-only**; leave other optional permissions and webhooks off. Install it on the project-owning organization. If prompted for repository access, select only the target repository. Record its numeric **App ID** (not the client or installation ID). Generate and download a private key through the App settings; keep it on the operator controller. Use systemd with `LoadCredential` support and an accurate system clock. Run from the qualified, trusted source tree, never from a candidate checkout:

When upgrading an active installation, stop the old timer and intake service before replacing their files: `sudo systemctl stop silentdeluge-factory-intake.timer silentdeluge-factory-intake.service`.

```sh
/usr/bin/python3 -I -c 'import cryptography'
org='REPLACE-WITH-PROJECT-ORG-LOGIN'
app_id='REPLACE-WITH-NUMERIC-APP-ID'
pem='/absolute/path/to/downloaded-github-app-key.pem'
chmod 0600 "$pem"
sudo install -d -o root -g root -m 0755 \
  /etc/silentdeluge-factory /opt/silentdeluge-factory /opt/silentdeluge-factory/factory
sudo install -o root -g root -m 0600 "$pem" /etc/silentdeluge-factory/github-app.pem
printf 'FACTORY_PROJECT_ORG=%s\nFACTORY_APP_ID=%s\n' "$org" "$app_id" |
  sudo tee /etc/silentdeluge-factory/project-app.env >/dev/null
sudo chown root:root /etc/silentdeluge-factory/project-app.env
sudo chmod 0644 /etc/silentdeluge-factory/project-app.env
sudo install -o root -g root -m 0644 factory/refresh_project_token.py /opt/silentdeluge-factory/factory/
sudo install -o root -g root -m 0644 \
  factory/silentdeluge-factory-project-refresh.service /etc/systemd/system/
```

Do not transfer the issuer or key to a worker or deployment target. Secure or remove the extra downloaded key copy after installation. The refresh service uses system Python `cryptography` for an RS256 App JWT, and systemd `LoadCredential` is its only key input. It uses verified TLS to `api.github.com`, without proxies or redirects. It checks the active installation's organization and App ID, then requests `organization_projects: read`. It rejects any other granted permission except implicit `metadata: read`, and rejects expired or non-short-lived tokens. Set no token or key in command arguments, config, or `project-app.env`.

**Astra: deploy intake after qualification**, on that same operator controller. Install the read-only CLI, config, intake unit, and timer. Do not provision the App key, a repository write credential, a promotion policy, or a broker for the intake account:

```sh
getent passwd silentdeluge-factory >/dev/null ||
  sudo useradd --system --user-group --home-dir /nonexistent \
    --shell /usr/sbin/nologin silentdeluge-factory
sudo install -o root -g root -m 0644 \
  factory/__init__.py factory/__main__.py factory/config.py factory/github.py \
  factory/pipeline.py factory/state.py /opt/silentdeluge-factory/factory/
sudo install -o root -g root -m 0644 factory/intake.example.json /etc/silentdeluge-factory/intake.json
sudoedit /etc/silentdeluge-factory/intake.json
sudo install -o root -g root -m 0644 \
  factory/silentdeluge-factory-intake.service factory/silentdeluge-factory-intake.timer /etc/systemd/system/
```

In `intake.json`, keep both sample identity settings and the sample state path, and set the exact target fields and ready status. `target.project_owner` must match `FACTORY_PROJECT_ORG`. After live acceptance is authorized, run one intake and inspect the result before enabling the timer:

```sh
sudo systemctl daemon-reload
sudo systemctl start silentdeluge-factory-intake.service
sudo journalctl -u silentdeluge-factory-project-refresh.service -u silentdeluge-factory-intake.service -n 40
sudo stat -c '%U:%G %a %n' /run/silentdeluge-factory-credentials /run/silentdeluge-factory-credentials/project.env
sudo -u silentdeluge-factory test ! -r /etc/silentdeluge-factory/github-app.pem
sudo -u silentdeluge-factory test ! -r /run/silentdeluge-factory-credentials/project.env
cd /opt/silentdeluge-factory
sudo -u silentdeluge-factory /usr/bin/python3 -B -m factory \
  --config /etc/silentdeluge-factory/intake.json status
sudo systemctl enable --now silentdeluge-factory-intake.timer
```

Expect `root:root 700` on the runtime directory, `root:root 600` on the file, and a non-null `last_intake`. Do not print the environment file. Each actual intake start requires and waits for the transient refresh oneshot; a failed refresh blocks intake even if a previously issued token file remains. The issuer atomically replaces `/run/silentdeluge-factory-credentials/project.env`; systemd reads it before switching to the intake user. Intake sees the short-lived installation token in its environment, not the file or the signing key. The timer polls every five minutes with jitter and catches up after downtime. No listener, webhook, or GitHub Actions runner is installed. On an upgrade from the legacy unit, remove the unused `/etc/silentdeluge-factory/intake.env` and revoke credentials no longer needed.

## Operator promotion (not enabled by intake)

Provision promotion only if an existing trusted maintainer policy and stage broker already authorize the target. Use a separate trusted operator account, config, state directory, and non-anonymous repository identity; do not share the intake account's writable state or provide it with promotion credentials. The policy is private (`0600` or more restrictive) and external to the checkout. The operator verifies the issue's scope, dependencies, and absence of competing work/PRs before recording a grant. No issue, user login, label, project field, remote repository file, PR body or comment can supply a grant, a command, a model name, a file scope, or trusted task instructions. Curate a clean task spec, including acceptance behavior and non-goals, instead of copying public text. Revocation is removing a grant; each invocation reloads policy and rechecks GitHub. Revocation does not cancel an invocation already underway.

Example policy shape (illustrative identifiers and paths, no installed broker or grant):

```json
{
  "target": {"repo": "example-owner/example-repo", "project_owner": "example-organization", "project_number": 1, "project_title": "Inventory Foundations", "base_ref": "main"},
  "broker": "/usr/local/libexec/silentdeluge-factory-stage",
  "broker_revision": "reviewed-version-1",
  "models": {"implement": "openai-codex/gpt-6.2-sol", "verify": "openai-codex/gpt-6.2-astra"},
  "tests": ["/opt/factory-tests/check-inventory", "--synthetic"],
  "approvals": [{"issue": 123, "base": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "stages": ["implement", "verify", "publish"], "task": "Correct the documented inventory example; the fixed synthetic check must pass. Do not change the collector contract.", "paths": ["examples/", "README.md"]}]
}
```

The stage list is an ordered prefix of `implement`, `verify`, `publish`, `merge`, `deploy`. Extend it only after the relevant authority grants that action. An approval is exact to issue number and base SHA. Changing the task, file scope, trusted tests, broker path, or broker revision invalidates that attempt's fingerprint; extending or shortening stage grants does not. The broker and configured test harness must live in operator-managed paths, not in the target repository. For publish and merge, provide separately scoped repository PR/Contents write permissions to the **operator invocation**, never the timer or model. Do not allow the factory identity to bypass branch protections. Restrict the `silentdeluge-factory/` branch namespace to its trusted publisher, and configure GitHub rules and existing CI so that candidate pushes/PRs cannot execute with secrets, write tokens, or privileged runners. The repo must allow merge commits. Require up-to-date/reviewed changes and serialize base writes under existing GitHub/operator policy: GitHub's merge API atomically accepts the expected **head** SHA, not an expected base. The factory checks the current base before merge and checks both parents afterwards; a concurrent base change that slips through can result in a GitHub merge that will not qualify for deployment.

For `promotion.json`, use the intake config shape with the same target and ready status. Set `repo_identity` to `{"token_env": "FACTORY_REPO_TOKEN"}`, and set `state_dir` to an absolute mode-`0700` directory owned by the promotion account. Supply that account's separate repository token and a Projects read token through the operator environment. Do not add either to the intake unit.

From the trusted checkout, in the operator environment with the scoped identities, run one reconciliation or advance an individual issue manually:

```sh
config=/etc/silentdeluge-factory/promotion.json
policy=/etc/silentdeluge-factory/policy.json
python3 -B -m factory --config "$config" --policy "$policy" reconcile
python3 -B -m factory --config "$config" --policy "$policy" dispatch 123
python3 -B -m factory --config "$config" --policy "$policy" verify 123
python3 -B -m factory --config "$config" --policy "$policy" publish 123
python3 -B -m factory --config "$config" --policy "$policy" merge 123
python3 -B -m factory --config "$config" --policy "$policy" deploy 123
```

`reconcile` takes a fresh snapshot, visits issues in number order, and advances eligible issues only through their granted stage prefix for the exact base. It uses the same live issue, ready-card, SHA, provenance, PR, and deployment checks as manual stages. It retries a failed or interrupted stage at most once per invocation under the same attempt identity; successful stages are not repeated. A recorded merge or deploy can be recovered after the issue closes or the base moves, but only under the original grant and the existing PR and SHA gates. It never automatically replaces an attempt. It prints per-issue JSON outcomes and exits `2` if any issue failed; other issues are still considered. The cached view is the last complete read, not a projection of actions just performed.

Without a promotion broker, run the refresh helper, read-only `intake` (including scheduled intake and packet generation), and cached `status` without `--policy`. `reconcile`, manual stages, and `retry` require the separately provisioned promotion policy and broker. `retry` reserves a journal entry without invoking the broker.

- **Dispatch:** one matching ready card, open repo issue, current approved base, and a scoped grant. Sol produces a single candidate commit on the deterministic attempt branch. The controller checks GitHub for that exact SHA, direct parent, and allowed paths (including both sides of renames). `.github` changes are never allowed. Candidates must have 1–99 changed files so a truncated GitHub file list cannot be certified.
- **Verify:** Astra reviews the exact SHA and the fixed test command must pass in isolation. A changed branch head, stale base, wrong model or missing passing receipt blocks promotion.
- **Publish:** creates a GitHub PR only for a verified candidate and confirms its refs and SHA. This stage can run without a merge grant, leaving the PR open for GitHub checks and review.
- **Merge:** asks GitHub to merge the recorded PR using the verified head SHA. Existing GitHub rules still apply. A recorded merge must have the approved base and verified candidate as ordered parents.
- **Deploy:** re-reads the GitHub merge and requires that exact merge commit to still be the configured base tip. It passes the immutable merged commit to the authorized deployment broker, never a feature branch or caller-supplied deploy target. There is no default deployment route.

### Broker and isolation contract

The controller invokes only `[absolute_policy_broker, "implement" | "verify" | "deploy"]`, with JSON on stdin, `cwd=/`, a minimal environment (`PATH`, `LANG`), no shell, and no controller token, home, or SSH agent in the environment. This is environment filtering, **not a sandbox**. The trusted broker is an operator-owned adapter to existing isolation, publishing and deploy policy; do not substitute a shell script that runs candidate code on the controller.

For implementation and verification, the broker must provide a fresh, disposable unprivileged VM/sandbox for a clean checkout at the immutable requested SHA (base for Sol, candidate for Astra/tests). No host filesystem mounts, host sockets, SSH, app/GitHub credentials, credential helpers, real data, or unrestricted network. Supply model access through a narrowly scoped gateway outside the worker; never expose provider or other secrets to the model or guest. Check the model actually used against the exact requested ID and fail if unavailable. Treat all checked-out code, build hooks, tool output and model output as untrusted. Supply candidate code as data to Astra; execute tests in the sandbox, using only the orchestrator-selected trusted test command. Give model outputs no tools or channels that can cause a privileged action. The trusted publisher outside the guest must validate the bounded patch, both source/destination paths, file types, and ancestry **before pushing**, without executing guest-supplied hooks/config, allowing workflow changes, or force-pushing. Seal the sandbox, policy, and publisher setup independently before approving the first issue.

The broker receives `target`, `issue`, `base`, `attempt`, and (for implementation and verification) the controller-derived `branch`. The request/response additions are:

| Stage | Additional input | Required stdout JSON (exit zero) |
| --- | --- | --- |
| `implement` | `model`, trusted `task`, approved `paths` | `{"model":"openai-codex/gpt-6.2-sol","sha":"<published-candidate>"}` |
| `verify` | `model`, trusted `task`, approved `paths`, candidate `sha`, fixed `tests` argv | `{"model":"openai-codex/gpt-6.2-astra","sha":"<candidate>","tests":"passed","review":"passed"}` |
| `deploy` | merged `sha` (no candidate branch, task, model, test, or issue prose) | `{"sha":"<merged-commit>","deployed":true}` |

Only the trusted adapter may emit receipts, based on observed results, never on claims printed by a model or candidate. Stdout is restricted to the receipt; stderr is not forwarded. The deployment adapter must independently enforce its existing allowlist and pin deployment to the requested immutable SHA of `target.base_ref`, never resolve a moving branch in its place. Its privileged preflight and deploy command come from the existing system outside this repo. Neither the checkout nor the model can change that command or destination.

The broker must durably deduplicate each `(target repo, issue, base, attempt, stage)` across disconnects and controller restarts, bind verification/deployment receipts to their SHA, and only recover implementation refs that its own durable publisher recorded. If it can outlive the controller, it must enforce one active implementation per repo/issue, refuse a new attempt until older work is fenced, and prevent superseded workers from publishing. Deployment must be idempotent or have durable recovery evidence under the same key so a lost receipt cannot trigger a second rollout. The CLI holds a local lock; it cannot enforce these invariants across brokers.

## Recovery

One promotion controller and one promotion journal per target; the read-only timer uses a separate cache. Back up the SQLite journal while the controller is idle; do not remove `controller.lock`, duplicate the directory across active controllers, or reset the DB to clear an error. Concurrent commands against the same state exit 2 with `controller busy`. A killed process releases its OS lock; the attempt identity and action intent survive. `status ISSUE` shows the latest phase, exact SHAs, PR number, attempt history, and investigation packets without invoking any tool.

1. After a failed intake, fix the relevant read identity or endpoint and rerun `intake`. The timer will also retry at its next tick. A cached view does not authorize an action.
2. If a stage was interrupted or its receipt was lost, rerun `reconcile` or the **same** stage against the same state and policy. Publication looks up the deterministic PR; merge checks the recorded PR before writing; the broker must recover the same implementation/deploy receipt. An already completed stage returns `duplicate` and does not repeat it.
3. If implementation or verification failed and needs replacement rather than resumption, inspect the prior candidate and broker status, fence outstanding work, then run `python3 -B -m factory --config "$config" --policy "$policy" retry 123`. This reserves the next attempt for that issue/base; it does not launch it. Run `reconcile` or `dispatch` to start. A stale, unpublished candidate can also be replaced after approval of the new base. A published or active attempt cannot be replaced this way. Do not delete branches or PRs to bypass a gate.
4. For head/base mismatch, closed PR, or a merge with unexpected parents, inspect GitHub and the journal before acting. The factory will not reverify changed PR heads or deploy a mismatched or superseded merge. If the journal was lost or manually modified, stop; restore it and reconcile GitHub and broker records before restarting promotion.

## Synthetic verification

```sh
/usr/bin/python3 -B -m unittest -v test_factory
systemd-analyze --man=no --generators=no verify \
  factory/silentdeluge-factory-project-refresh.service \
  factory/silentdeluge-factory-intake.service factory/silentdeluge-factory-intake.timer
```

The suite uses synthetic GitHub responses and a synthetic broker, exercises anonymous intake, packet durability, policy gates, journal retries, lock exclusion, and lost receipts, and tests issuer validation, signing, and atomic file replacement. It does not run a real model, validate a production sandbox, contact GitHub, or deploy to a live system.
