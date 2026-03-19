# ACP Bridge

> **This README is optimized for AI agents (Kiro CLI, Claude Code, Codex, etc.).**
> Human-readable version: [README.human.md](README.human.md) | [中文文档](README.zh-CN.md)

A bridge service that exposes local CLI agents via [ACP (Agent Client Protocol)](https://agentclientprotocol.com/) over HTTP, with async job support and webhook push notifications.

---

## Agent Execution Guide — Read This First

> **This section is for you, the executing agent. Follow it strictly.**

### Step 1: Understand the Architecture

```
CLI Agents (kiro/claude/codex)
        ↕ stdio JSON-RPC
   ACP Bridge (uvicorn :8001)
        ↕ HTTP
   OpenClaw Gateway → Discord/Feishu/...
```

Key concepts:
- ACP Bridge manages a **process pool** of CLI agent subprocesses
- Each `(agent, session_id)` pair maps to one subprocess with retained context
- Two agent modes: **ACP** (structured JSON-RPC, process reuse) and **PTY** (subprocess per call, stateless)
- Auth: Bearer Token + IP allowlist (both required)

### Step 2: Diagnose Current State

Before any setup or modification, run this diagnostic:

```bash
echo "====== Runtime ======"
python3 --version 2>&1 || echo "PYTHON3_NOT_FOUND"
uv --version 2>&1 || echo "UV_NOT_FOUND"
node --version 2>&1 || echo "NODE_NOT_FOUND"

echo ""
echo "====== Service ======"
curl -s http://127.0.0.1:8001/health 2>/dev/null || echo "BRIDGE_NOT_RUNNING"

echo ""
echo "====== Config ======"
cat config.yaml 2>/dev/null | head -5 || echo "CONFIG_NOT_FOUND"

echo ""
echo "====== Agents ======"
which kiro-cli 2>/dev/null && kiro-cli --version 2>/dev/null || echo "KIRO_NOT_FOUND"
which claude-agent-acp 2>/dev/null || echo "CLAUDE_ACP_NOT_FOUND"
which codex 2>/dev/null || echo "CODEX_NOT_FOUND"

echo ""
echo "====== Docker ======"
sudo docker ps --filter "name=acp-bridge" --format "{{.Names}} {{.Status}} {{.Image}}" 2>/dev/null || echo "DOCKER_NOT_AVAILABLE"
```

### Step 3: Execute by Phase

Based on diagnostic results, execute phases in order. **Report ✅/❌ after each phase before proceeding.**

| Phase | Task | Section | Prerequisite | Skip If |
|---|---|---|---|---|
| 1 | Install prerequisites | [Prerequisites](#prerequisites) | None | `python3 --version` ≥ 3.12 + `uv --version` OK |
| 2 | Install ≥1 CLI agent | [Prerequisites](#prerequisites) | Phase 1 | At least one agent found in diagnostic |
| 3 | Create config | [Configuration](#configuration-reference) | Phase 2 | `config.yaml` exists with correct `auth_token` |
| 4a | Start Bridge (native) | [Quick Start](#quick-start) | Phase 3 | Health returns `{"status":"ok"}` |
| 4b | Start Bridge (Docker) | [Docker Quick Start](#docker-quick-start) | Phase 3 + Docker | Health returns `{"status":"ok"}` |
| 5 | Verify | [Verification](#verification-checklist) | Phase 4 | — |

> Phase 4a and 4b are mutually exclusive — pick one based on Docker availability.
> If diagnostic shows Bridge already running, skip to Phase 5.

### Execution Principles

| Principle | Detail |
|---|---|
| Diagnose before act | Always run Step 2 before making changes |
| Idempotent operations | Check state before writing; skip if already correct |
| Fail fast | Stop and report on any non-zero exit code or unexpected output |
| Minimal user interaction | Only ask user for secrets (tokens, API keys) |
| Report per phase | Summarize each phase with ✅/❌ before proceeding |
| Completed phases can be skipped | If diagnostic shows a phase is done, mark ✅ and move on |

---

## Project Structure

```
acp-bridge/
├── main.py              # Entry: process pool, handler registration, job/health endpoints
├── config.yaml          # Service configuration (copy from config.yaml.example)
├── src/
│   ├── acp_client.py    # ACP process pool + JSON-RPC connection management
│   ├── agents.py        # Agent handlers (ACP mode + PTY fallback)
│   ├── jobs.py          # Async job manager (submit, monitor, webhook callback)
│   ├── sse.py           # ACP session/update → SSE event conversion
│   └── security.py      # Security middleware (IP allowlist + Bearer Token)
├── skill/
│   ├── SKILL.md         # Kiro/OpenClaw skill definition
│   └── acp-client.sh    # Agent client script (bash + jq)
├── tools/
│   └── tools-client.sh  # OpenClaw tools client (debug + integration)
├── examples/
│   └── echo-agent.py    # Minimal ACP-compliant reference agent
├── test/
│   ├── lib.sh                     # Test helpers (assertions, env init)
│   ├── test.sh                    # Full test suite runner (31 cases)
│   ├── test_agent_compliance.sh   # Agent compliance test (direct stdio)
│   ├── test_common.sh             # Common tests (agent listing, error handling)
│   ├── test_tools.sh              # OpenClaw tools proxy tests
│   ├── test_kiro.sh               # Kiro agent tests
│   ├── test_claude.sh             # Claude agent tests
│   ├── test_codex.sh              # Codex agent tests
│   └── reports/                   # Test reports
├── docker/light/
│   ├── Dockerfile                 # Multi-stage build (debian:bookworm-slim, ~439MB)
│   ├── docker-compose.yml         # Mount host agents into container
│   └── .env.example               # Environment variable template
├── AGENT_SPEC.md        # ACP agent integration specification (JSON-RPC protocol)
├── pyproject.toml       # Python deps: acp-sdk, pyyaml
└── uv.lock
```

---

## Prerequisites

| Requirement | Version | Check Command |
|---|---|---|
| Python | ≥ 3.12 | `python3 --version` |
| uv | latest | `uv --version` |
| curl, jq, uuidgen | any | `which curl jq uuidgen` |
| At least one CLI agent | — | See agent table below |

| Agent | Install | Mode | Notes |
|---|---|---|---|
| Kiro CLI | `curl -fsSL https://cli.kiro.dev/install \| bash` | ACP | Requires `kiro-cli login` |
| Claude Code | `npm i -g @zed-industries/claude-agent-acp` | ACP | Requires Anthropic API key or Bedrock |
| Codex | `npm i -g @openai/codex` | PTY | Requires OpenAI key or LiteLLM proxy |

---

## Quick Start

```bash
cd acp-bridge
cp config.yaml.example config.yaml
# Edit config.yaml — set auth_token and agent working_dir at minimum
uv sync
uv run main.py
```

Verify:
```bash
curl -s http://127.0.0.1:8001/health
# → {"status":"ok","version":"0.8.0","uptime":...}
```

---

## Docker Quick Start

Lightweight image containing only the ACP Bridge gateway. Agent CLIs stay on host — mount them into the container.

```bash
# 1. Prepare config
cp config.yaml.example config.yaml
# Edit config.yaml

# 2. Set environment variables
cp docker/light/.env.example docker/light/.env
# Edit docker/light/.env with your tokens

# 3. Edit docker/light/docker-compose.yml
#    Uncomment volume mounts for the agents you have installed

# 4. Build and run
sudo docker compose -f docker/light/docker-compose.yml up -d --build

# 5. Verify
curl -s http://127.0.0.1:8001/health
sudo docker compose -f docker/light/docker-compose.yml logs -f
```

> **Note:** When using `sudo`, shell env vars and `~` paths are NOT passed to Docker. Use a `.env` file or pass variables inline.

---

## Configuration Reference

```yaml
server:
  host: "0.0.0.0"
  port: 8001
  session_ttl_hours: 24          # Idle session cleanup
  shutdown_timeout: 30

pool:
  max_processes: 20              # Total concurrent agent subprocesses
  max_per_agent: 10              # Per-agent limit

webhook:
  url: "http://<openclaw-ip>:18789/tools/invoke"
  token: "${OPENCLAW_TOKEN}"
  account_id: "default"          # OpenClaw bot account: "default" for Discord, "main" for Feishu
  target: "channel:<channel-id>" # Default push target

security:
  auth_token: "${ACP_BRIDGE_TOKEN}"   # Supports ${ENV_VAR} references
  allowed_ips:
    - "127.0.0.1"

litellm:                         # Only needed for Codex with non-OpenAI models
  url: "http://localhost:4000"
  required_by: ["codex"]
  env:
    LITELLM_API_KEY: "${LITELLM_API_KEY}"

agents:
  kiro:
    enabled: true
    mode: "acp"                  # "acp" = JSON-RPC stdio | "pty" = subprocess per call
    command: "kiro-cli"
    acp_args: ["acp", "--trust-all-tools"]
    working_dir: "/tmp"
    description: "Kiro CLI agent"
  claude:
    enabled: true
    mode: "acp"
    command: "claude-agent-acp"
    acp_args: []
    working_dir: "/tmp"
    description: "Claude Code agent (via ACP adapter)"
  codex:
    enabled: true
    mode: "pty"
    command: "codex"
    args: ["exec", "--full-auto", "--skip-git-repo-check"]
    working_dir: "/tmp"
    description: "OpenAI Codex CLI agent"
```

---

## API Endpoints

| Method | Path | Description | Auth | Example |
|---|---|---|---|---|
| GET | `/health` | Health check | No | `curl http://host:8001/health` |
| GET | `/health/agents` | Agent status | Yes | |
| GET | `/agents` | List registered agents | Yes | |
| POST | `/runs` | Sync/streaming agent call | Yes | See [Client Usage](#client-usage) |
| POST | `/jobs` | Submit async job | Yes | See [Async Jobs](#async-jobs) |
| GET | `/jobs` | List all jobs + stats | Yes | |
| GET | `/jobs/{job_id}` | Query single job | Yes | |
| GET | `/tools` | List OpenClaw tools | Yes | |
| POST | `/tools/invoke` | Invoke OpenClaw tool | Yes | See [Tools Proxy](#openclaw-tools-proxy) |
| DELETE | `/sessions/{agent}/{session_id}` | Close session | Yes | |

All authenticated endpoints require: `Authorization: Bearer <token>` header + client IP in allowlist.

---

## Client Usage

### acp-client.sh

```bash
export ACP_BRIDGE_URL=http://<bridge-ip>:8001
export ACP_TOKEN=<your-token>

# List agents
./skill/acp-client.sh -l

# Sync call (default agent: kiro)
./skill/acp-client.sh "Explain the project structure"

# Specify agent
./skill/acp-client.sh -a claude "hello"
./skill/acp-client.sh -a codex "Reply with ok"

# Streaming
./skill/acp-client.sh --stream "Analyze this code"

# Markdown card output (for IM display)
./skill/acp-client.sh --card -a kiro "Introduce yourself"

# Multi-turn conversation (same session_id = same context)
./skill/acp-client.sh -s 00000000-0000-0000-0000-000000000001 "first message"
./skill/acp-client.sh -s 00000000-0000-0000-0000-000000000001 "follow up"
```

### Direct curl

```bash
# Sync call
curl -X POST http://host:8001/runs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"kiro","prompt":"hello","session_id":"<uuid>"}'

# Streaming (SSE)
curl -X POST http://host:8001/runs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"kiro","prompt":"hello","stream":true}'
```

---

## Async Jobs

Submit long-running tasks. Results are pushed via webhook (Discord/Feishu/etc.) on completion.

### Submit

```bash
curl -X POST http://host:8001/jobs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "kiro",
    "prompt": "Refactor the module",
    "target": "user:<user-id>",
    "channel": "discord",
    "callback_meta": {"account_id": "default"}
  }'
# → {"job_id": "xxx", "status": "pending"}
```

### Query

```bash
curl http://host:8001/jobs/<job_id> -H "Authorization: Bearer <token>"
```

### Target Format

| Scenario | Format | Example |
|---|---|---|
| Discord channel | `channel:<id>` | `channel:1477514611317145732` |
| Discord DM | `user:<user_id>` | `user:123456789` |
| Feishu user | `user:<open_id>` | `user:ou_2dfd02ef...` |
| Feishu group | `<chat_id>` | `oc_xxx` |

### Job Monitoring

- `GET /jobs` — list all jobs + status stats
- Patrol every 60s: jobs stuck >10min auto-marked failed + notified
- Failed webhooks retried automatically

---

## OpenClaw Tools Proxy

Unified entry point for OpenClaw tool invocations.

| Tool | Description |
|---|---|
| `message` | Send messages (Discord/Telegram/Slack/WhatsApp/Signal/iMessage) |
| `tts` | Text to speech |
| `web_search` | Web search |
| `web_fetch` | Fetch and extract URL content |
| `nodes` | Control paired devices (notify, run commands, camera) |
| `cron` | Manage scheduled jobs |
| `image` | Analyze image with AI |
| `browser` | Browser control (open, screenshot, navigate) |

```bash
# List tools
./tools/tools-client.sh -l

# Send message
./tools/tools-client.sh message send \
  --arg channel=discord \
  --arg target="channel:123456" \
  --arg message="Hello from ACP Bridge"

# Direct API
curl -X POST http://host:8001/tools/invoke \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"tool":"message","action":"send","args":{"channel":"discord","target":"channel:123456","message":"Hello"}}'
```

Requires `webhook.url` configured pointing to an OpenClaw Gateway.

---

## Codex + LiteLLM Setup

Codex doesn't support ACP natively — runs in PTY mode. For non-OpenAI models (e.g. Kimi K2.5 on Bedrock), use [LiteLLM](https://github.com/BerriAI/litellm) as proxy.

```
acp-bridge ──(PTY)──► codex exec ──(HTTP)──► LiteLLM :4000 ──(Bedrock API)──► Model
```

### Install

```bash
npm i -g @openai/codex
pip install 'litellm[proxy]'
```

### Codex config (`~/.codex/config.toml`)

```toml
model = "bedrock/moonshotai.kimi-k2.5"
model_provider = "bedrock"

[model_providers.bedrock]
name = "AWS Bedrock via LiteLLM"
base_url = "http://localhost:4000/v1"
env_key = "LITELLM_API_KEY"
```

### LiteLLM config (`~/.codex/litellm-config.yaml`)

```yaml
model_list:
  - model_name: "bedrock/moonshotai.kimi-k2.5"
    litellm_params:
      model: "bedrock/moonshotai.kimi-k2.5"
      aws_region_name: "us-east-1"
general_settings:
  master_key: "sk-litellm-bedrock"
litellm_settings:
  drop_params: true    # Required — Codex sends params Bedrock doesn't support
```

### Start LiteLLM

```bash
LITELLM_API_KEY="sk-litellm-bedrock" litellm --config ~/.codex/litellm-config.yaml --port 4000
```

---

## Testing

### Agent Compliance Test (no Bridge needed)

Verify a CLI agent implements ACP protocol correctly via direct stdio:

```bash
bash test/test_agent_compliance.sh kiro-cli acp --trust-all-tools
bash test/test_agent_compliance.sh claude-agent-acp
bash test/test_agent_compliance.sh python3 examples/echo-agent.py
```

Covers: initialize, session/new, session/prompt (notifications + result), ping.
Full protocol spec: [AGENT_SPEC.md](AGENT_SPEC.md)

### Integration Tests (requires running Bridge)

```bash
# Full suite (31 test cases)
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:8001

# Single agent
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:8001 --only kiro
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:8001 --only claude
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:8001 --only codex
```

### Test Coverage

| Suite | Cases | What's Tested |
|---|---|---|
| Common | 4 | Agent listing, error handling |
| Tools Proxy | 9 | Tool listing, API endpoints, invocation, client script |
| Kiro | 7 | Sync, streaming, multi-turn context, async job submit + query |
| Claude | 5 | Sync, streaming, multi-turn context, async job |
| Codex | 6 | Sync, streaming, multi-turn (PTY stateless), async job |

---

## Process Pool Behavior

- Each `(agent, session_id)` → independent CLI subprocess
- Same session reuses subprocess, context retained across turns
- Crashed subprocesses rebuilt automatically (context lost, user notified)
- Idle sessions cleaned after TTL expiry
- `session/request_permission` auto-replied with `allow_always` (Claude compatibility)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `403 forbidden` | IP not in allowlist | Add IP to `security.allowed_ips` |
| `401 unauthorized` | Wrong token | Check `ACP_BRIDGE_TOKEN` env var and `security.auth_token` |
| `pool_exhausted` | Concurrency limit | Increase `pool.max_processes` |
| Claude hangs | Permission prompt | Already handled (auto-allow) |
| Discord push fails | Wrong `account_id` | Use `default` for Discord, `main` for Feishu |
| Discord 500 | Bad target format | DM: `user:<id>`, channel: `channel:<id>` |
| Job stuck >10min | Agent process anomaly | Auto-marked failed by patrol |
| Codex: not trusted dir | `/tmp` not a git repo | Add `--skip-git-repo-check` to args |
| Codex: missing LITELLM_API_KEY | Env var not passed | Set in `litellm.env` in config.yaml |
| Codex: unsupported params | Bedrock rejects Codex params | Set `drop_params: true` in LiteLLM config |
| Bridge won't start in Docker | `.python-version` mismatch | Ensure Dockerfile uses `uv sync --frozen` with `.python-version` |

---

## Verification Checklist

After setup, run this to confirm everything works:

```bash
echo "=== 1. Health ==="
curl -s http://127.0.0.1:8001/health | python3 -m json.tool

echo ""
echo "=== 2. Agents ==="
curl -s http://127.0.0.1:8001/agents \
  -H "Authorization: Bearer $ACP_TOKEN" | python3 -m json.tool

echo ""
echo "=== 3. Quick call ==="
./skill/acp-client.sh -a kiro "Reply with ok"

echo ""
echo "=== 4. Full test suite ==="
ACP_TOKEN=$ACP_TOKEN bash test/test.sh http://127.0.0.1:8001
```

Expected: health returns `ok`, agents list shows enabled agents, call returns response, 31/31 tests pass.

---

## Links

- [ACP Protocol](https://agentclientprotocol.com/)
- [Agent Integration Spec](AGENT_SPEC.md)
- [Echo Agent Reference Implementation](examples/echo-agent.py)
- [GitHub](https://github.com/xiwan/acp-bridge)

## License

MIT-0 — see [LICENSE](LICENSE).
