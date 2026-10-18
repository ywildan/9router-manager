# 9router-manager

**English** | [Bahasa Indonesia](README.id.md#top)

A dependency-free manager for the **9router** gateway: custom endpoints (provider
nodes), API keys, and models, from a menu or from the command line.

The tool talks to 9router's **admin REST API**, not to its SQLite file, so it is safe
to use while the server is running and your routing keeps working.

Runs on **Linux**, **macOS** and **Windows** with Python 3.8+ and the standard library
only. No `pip install`, no Node, no build step.

```
9router-manager/
├── 9router-manager.py        # engine (single file, stdlib only)
├── 9router-manager.sh        # launcher: Linux / macOS / WSL / Git Bash
├── 9router-manager.ps1       # launcher: Windows PowerShell
├── 9router-manager.cmd       # launcher: Windows cmd.exe
├── install.sh                # setup for Linux / macOS (adds a `9router-manager` symlink)
├── install.ps1               # setup for Windows (adds a shim + PATH hint)
└── tests/
    ├── smoke_test.py             # 27 tests against a mock 9router (portable)
    ├── test_platform_paths.py    # 22 tests: Windows/macOS/Linux path logic
    ├── mock_9router_server.py    # in-memory 9router admin API, real auth rules
    ├── run_all.py                 # run every suite
    ├── verify_with_live_9router.py # opt-in end-to-end check against a real server
    └── smoke-test.sh             # convenience wrapper
```

## What it does

| Menu | Function |
| ---- | -------- |
| 1 | **Add custom endpoint** - name, prefix, type (openai/anthropic compatible, embedding), base URL |
| 2 | **List everything** - endpoints, bound connections, 9router client keys, custom models, aliases, disabled models, combos, local backups |
| 3 | **Edit custom endpoint** - name, prefix, api type, base URL (9router propagates prefix/base URL to every connection bound to that node) |
| 4 | **Add or replace an API key** - add a second key to an endpoint, or replace/rename/reprioritize an existing connection |
| 5 | **Delete** - a single connection, or a whole endpoint (cascades its connections and custom models) |
| 6 | **Models** - add/remove custom models, alias a model, disable/enable models per provider |
| 7 | **9router client keys** - list (masked), create (shown once), enable/disable, delete |
| 8 | **Test & generate** - test a connection, test a model, print ready-to-paste client snippets |
| 9 | **Backup & restore** - export/import the whole 9router config |
| I | **Info & doctor** - OS, paths, server version, auth method in use, config counts |

Every write is preceded by an automatic full-config backup, and nothing is written
without a confirmation unless you pass `--yes`.

## Install

**Linux / macOS / WSL**

```bash
git clone https://github.com/ywildan/9router-manager
cd 9router-manager
./install.sh                  # checks python + 9router, adds ~/.local/bin/9router-manager
./install.sh --no-link        # only run the checks
sudo ./install.sh --bin-dir /usr/local/bin
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/ywildan/9router-manager
cd 9router-manager
.\install.ps1                 # creates a shim in %LOCALAPPDATA%\9router-manager\bin
.\9router-manager.ps1         # or run the launcher directly
.\9router-manager.cmd         # from cmd.exe
```

If the execution policy blocks the script:
`powershell -ExecutionPolicy Bypass -File .\9router-manager.ps1`

**Requirements**

| Needed | Notes |
| ------ | ----- |
| 9router server | the tool only manages a *running* server (`npx 9router`, default `http://127.0.0.1:20128`) |
| Python 3.8+ | Linux, macOS and Windows. The launchers find `py` / `python` / `python3` for you |

## Usage

```bash
./9router-manager.sh                    # menu (default)
python3 9router-manager.py info         # doctor
python3 9router-manager.py list         # everything, human readable
```

## Non-interactive mode

Every feature is available without the menu, which is handy for scripts and CI:

```bash
python3 9router-manager.py --list --json
python3 9router-manager.py info --json

# custom endpoint
python3 9router-manager.py add-endpoint "Gateway Lokal" mygw https://api.example.com/v1
python3 9router-manager.py add-endpoint "Anthropic GW" claude https://api.anthropic.com/v1 \
    --type anthropic-compatible
python3 9router-manager.py edit-endpoint mygw --url https://api.example.com/v2
python3 9router-manager.py edit-endpoint mygw --prefix mygw2 --name "GW 2"
python3 9router-manager.py delete-endpoint mygw --yes

# API keys (keep them out of shell history with --api-key-stdin)
printf '%s' "$MY_KEY" | python3 9router-manager.py add-key mygw --api-key-stdin
python3 9router-manager.py set-key <connection-id> --api-key sk-new --name primary --priority 1
python3 9router-manager.py set-key <connection-id> --inactive
python3 9router-manager.py delete-key <connection-id> --yes
python3 9router-manager.py test-connection <connection-id> --json
python3 9router-manager.py fetch-models <connection-id>     # live list from the endpoint

# models
python3 9router-manager.py add-model mygw gpt-4o-mini --name "GPT-4o mini" --vision
python3 9router-manager.py delete-model mygw gpt-4o-mini --yes
python3 9router-manager.py set-alias mygw/gpt-4o-mini mini
python3 9router-manager.py disable-model mygw gpt-4o gpt-4o-mini
python3 9router-manager.py enable-model mygw

# 9router client keys
python3 9router-manager.py add-router-key laptop
python3 9router-manager.py list-keys
python3 9router-manager.py toggle-router-key laptop --disable
python3 9router-manager.py delete-router-key laptop --yes

# backups
python3 9router-manager.py backup
python3 9router-manager.py list-backups
python3 9router-manager.py restore 9router-20261018-191539.json --yes

# client snippets for an endpoint
python3 9router-manager.py generate mygw
```

Global flags: `--base-url`, `--port`, `--token`, `--password`, `--data-dir`,
`--manager-home`, `--timeout`, `--json`, `--yes`, `--no-backup`, `--no-color`.
Legacy aliases `--list`, `--info`, `--doctor` and `--menu` also work.

## How authentication works

9router's admin API is protected. The manager tries, in order:

| # | Method | How |
| - | ------ | --- |
| 1 | Explicit token | `--token` or `NINEROUTER_TOKEN` |
| 2 | **Derived CLI token** | `sha256(machine-id + "9r-cli-auth" + auth/cli-secret)[:16]` sent as `x-9r-cli-token` |
| 3 | Peer token | `NINEROUTER_PEER_TOKEN` sent as `x-9r-peer-token` (only works when the server sets `NINEROUTER_PEER_TOKEN`) |
| 4 | Dashboard password | `--password` / `NINEROUTER_PASSWORD` -> `POST /api/auth/login` -> session cookie |

Method 2 is what the official 9router CLI uses for local commands, so on the machine
that runs 9router you normally need no password at all. It needs
`<data-dir>/machine-id` and `<data-dir>/auth/cli-secret` to be readable; if either is
missing, pass `--password`. Password login is rate limited by 9router (5 failures
trigger a lockout), so the tool never retries a bad password in a loop.

## Endpoints it calls

| Action | HTTP |
| ------ | ---- |
| List/inspect | `GET /api/health`, `/api/version`, `/api/provider-nodes`, `/api/providers`, `/api/keys`, `/api/combos`, `/api/models`, `/api/models/custom`, `/api/models/disabled`, `/api/models/alias` |
| Add endpoint | `POST /api/provider-nodes` |
| Edit endpoint | `PUT /api/provider-nodes/{id}` (full body: name + prefix + baseUrl + apiType) |
| Delete endpoint | `DELETE /api/provider-nodes/{id}` (9router also drops its connections and custom models) |
| Add key | `POST /api/providers` |
| Edit/remove key | `PUT /api/providers/{id}`, `DELETE /api/providers/{id}` |
| Test | `POST /api/providers/{id}/test`, `POST /api/models/test` |
| Live model list | `GET /api/providers/{id}/models` (server calls the endpoint with the stored key) |
| Models | `POST|DELETE /api/models/custom`, `PUT|DELETE /api/models/alias`, `POST|DELETE /api/models/disabled` |
| Client keys | `GET|POST /api/keys`, `PUT|DELETE /api/keys/{id}` |
| Combos | `GET|POST /api/combos`, `DELETE /api/combos/{id}` |
| Backup / restore | `GET /api/settings/database`, `POST /api/settings/database` |

Verified against **9router 0.5.104**; the payload shapes are documented in
`tests/mock_9router_server.py`, which fails the same way the real routes do.

## Safety

- **Masked secrets.** API keys are shown as `sk-a...9f3c`. The admin API never returns
  stored credentials, so for display the manager reads `data.sqlite` **read-only**
  (best effort, silently skipped if unavailable). Nothing is ever printed in full
  except a freshly created 9router client key, which is shown once on purpose.
- **Automatic backup before every write**, exported through the API:
  `<manager-home>/backups/9router-YYYYMMDD-HHMMSS.json`, keeping the newest 20.
  `--no-backup` skips it.
- **Restore is atomic from the server's point of view**: the file is posted to
  `/api/settings/database`, and the current config is backed up first.
- **Confirmations** on every delete/restore; `--yes` for scripting.
- **Clean errors.** `401` prints which auth method was tried and where the token comes
  from; an unreachable server prints each URL it tried.

## Paths per OS

| What | Linux | macOS | Windows |
| ---- | ----- | ----- | ------- |
| 9router data dir | `~/.9router` | `~/.9router` | `%APPDATA%\9router` |
| 9router database | `<data-dir>/db/data.sqlite` | same | same |
| CLI secret | `<data-dir>/auth/cli-secret` | same | same |
| Manager home | `~/.9router-manager` | same | same |
| Backups | `<manager-home>/backups` | same | same |

Windows note: `~` means `%USERPROFILE%`. File modes (`600`) are only applied on POSIX;
Windows inherits ACLs from `%APPDATA%`. If you run 9router in Docker with a custom
`DATA_DIR`, pass `--data-dir` or set `NINEROUTER_DATA_DIR`.

## Environment variables

| Var | Purpose |
| --- | ------- |
| `NINEROUTER_URL` / `NINEROUTER_BASE_URL` | 9router base URL. When set, the tool does **not** fall back to other ports, so a typo fails loudly |
| `NINEROUTER_PORT` / `PORT` | port to try first during auto-detection |
| `NINEROUTER_TOKEN` | override the derived CLI token |
| `NINEROUTER_PASSWORD` | dashboard password (fallback auth) |
| `NINEROUTER_PEER_TOKEN` | send `x-9r-peer-token` instead of the CLI token |
| `NINEROUTER_DATA_DIR` / `DATA_DIR` | 9router data dir (same variable the 9router server itself honours) |
| `NINEROUTER_MANAGER_HOME` | where the manager keeps `state.json` and backups |
| `NINEROUTER_MANAGER_NO_COLOR` / `NO_COLOR` | disable ANSI colors |
| `NINEROUTER_MANAGER_PYTHON` | force a specific interpreter in the launchers |

## Testing

```bash
python3 tests/run_all.py            # both suites, ~25s
bash tests/smoke-test.sh            # same thing on Linux/macOS
python3 tests/smoke_test.py add     # only tests whose name contains "add"
```

Against your own running 9router (opt-in: it writes a disposable endpoint and removes
it again, then checks a backup/restore round trip):

```bash
python3 tests/verify_with_live_9router.py --allow-writes
```

That is 15 checks: derived-token auth, add endpoint, bind key, add model, edit endpoint
(and 9router propagating the new base URL to the bound connection), generate snippets,
test connection, delete endpoint with cascade, config counts back to baseline, and
restore leaving the config identical.

Everything runs against a throwaway mock server on `127.0.0.1` with a temporary data
dir and manager home, so your real 9router config is never touched. Pure Python, no
shell, no external services, so the same commands work on Windows:

```powershell
python tests\run_all.py
```

Coverage highlights: derived-token auth, password fallback with 401 handling, peer
token, endpoint/key/model/alias/disabled/combo CRUD, key masking (and the assertion
that a real key never reaches stdout), backup-before-every-write, `--no-backup`,
backup/restore round trip, `--json` stdout purity, friendly offline errors, menu exit,
Windows/macOS path resolution, and Python 3.8 syntax compatibility.

Last run (9router 0.5.104, Linux): **27 ok / 0 fail** (smoke), **22 ok / 0 fail**
(platform), **15 ok / 0 fail** (live). CI runs the two offline suites on
ubuntu 3.8 + ubuntu 3.12 + macOS + Windows (each reported **27 ok / 0 fail** and
**22 ok / 0 fail** there) and checks all three launchers per OS.

## Limits (honest)

- **OAuth connections are read-only here.** Connections created through a provider
  OAuth flow (for example a Qoder subscription) can be listed, deactivated and deleted,
  but their tokens are refreshed by 9router itself. Use the dashboard to add them.
- **Stored API keys cannot be read back through the API.** The tool shows a masked key
  by reading `data.sqlite` read-only; if that file is unreadable (Docker volume
  permissions, network share) the column shows `(tersimpan)` instead.
- **No per-project split.** 9router is one gateway per data dir. For isolated setups run
  a second instance with its own `DATA_DIR` and manage it with
  `--data-dir` / `--base-url`. Per-project behaviour comes from combos/aliases.
- **No model catalog sync.** `/api/models/catalog-sync` is a server-side operation; the
  manager adds/deletes custom models and aliases only.
- **Clipboard** copy is best-effort (`clip` on Windows, `pbcopy` on macOS, `wl-copy` /
  `xclip` / `xsel` on Linux) and never fails the command.

## Working together with jcode

9router can also write a jcode provider profile itself
(`POST /api/cli-tools/jcode-settings`). If you prefer to do it by hand, `generate`
prints a ready snippet:

```bash
$ python3 9router-manager.py generate mygw
  base url OpenAI    http://127.0.0.1:20128/v1
  api key 9router    sk-f...348b
  # jcode
  jcode provider add mygw --base-url http://127.0.0.1:20128/v1 --model mygw/gpt-4o-mini
  jcode --provider-profile mygw
```

For the TOML side of jcode (adding/replacing providers in `~/.jcode/config.toml`) use
[jcode-manager](https://github.com/ywildan/jcode-manager); this tool owns the 9router
side.

## Troubleshooting

**`9router server tidak terjangkau`**
The server is not running or is on another port: `npx 9router`, then check
`curl http://127.0.0.1:20128/api/health`. Use `--base-url` for a remote/tunnelled 9router.

**`401 Unauthorized`**
The derived token failed. Either the data dir is not the one the server uses
(`--data-dir`, `DATA_DIR`, Docker mount) or `auth/cli-secret` is unreadable. Fall back
with `--password`.

**`HTTP 400 ... Name is required` / `Prefix is required`**
You hit it with an older build; current versions always send the full node body.
Data is untouched because the write was rejected before any change.

**`HTTP 405` on a legacy route**
Very old 9router builds used the file-based JSON store. Update 9router
(`npm i -g 9router`) or use the dashboard.

**Restore did nothing visible**
`POST /api/settings/database` replaces the whole config, so the settings you restored
must exist in the file. Check with `list-backups` and inspect the JSON: it contains
`providerConnections`, `providerNodes`, `apiKeys`, `customModels`, `modelAliases`,
`combos`.

## License

MIT
