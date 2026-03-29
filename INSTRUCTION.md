# ACP Bridge — EC2 Automated Deployment Guide

> Lessons learned from real deployments. For AI agents executing on behalf of humans.
> Complements [AGENT.md](https://github.com/xiwan/acp-bridge/blob/main/AGENT.md) with EC2-specific automation via SSM.

## Overview

This guide automates the full lifecycle: **provision EC2 → install dependencies → deploy ACP Bridge → verify end-to-end** — all without SSH, using SSM Run Command.

```
Local Machine                          EC2 Instance (us-west-2)
┌─────────┐   SSM Run Command    ┌──────────────────────────┐
│ AI Agent ├─────────────────────►│ 1. dnf install deps      │
│ (Kiro)   │                      │ 2. npm i claude-agent-acp│
│          │   SSH -L tunnel      │ 3. git clone acp-bridge  │
│          ├─────────────────────►│ 4. systemd start         │
│          │   :18010 ◄──────────►│ 5. ACP Bridge :18010     │
└─────────┘                       └──────────────────────────┘
```

---

## Pre-flight Checklist

Collect from human **once** before starting:

| Item | Required | Example | Notes |
|------|----------|---------|-------|
| Instance type | ✅ | `t3.medium` | Minimum for Claude Code |
| Subnet ID | ✅ | `subnet-0de83ce...` | Must have internet access |
| Security Group | ✅ | `sg-034d9d3...` | Or create new |
| IAM Instance Profile | ✅ | `agent-model-2` | Needs SSM + Bedrock permissions |
| SSH key path | Optional | `~/.ssh/id_ed25519_ec2` | Only if SSH tunnel needed |
| `ACP_BRIDGE_TOKEN` | ✅ | `my-secret-token` | Bridge auth token |
| `OPENCLAW_TOKEN` | Optional | `9f4334dc...` | For Discord/Feishu push |
| Agent choice | ✅ | `claude` | kiro / claude / codex / qwen / opencode |
| Bedrock model | If Claude | `us.anthropic.claude-sonnet-4-20250514` | Region-prefixed model ID |
| Startup method | ✅ | `systemd` | systemd / docker / nohup |

---

## Phase 0: Provision EC2

### 0.1 Find latest AMI

```bash
aws ec2 describe-images --region us-west-2 --owners amazon \
  --filters "Name=name,Values=al2023-ami-2023*-x86_64" "Name=state,Values=available" \
  --query "sort_by(Images, &CreationDate)[-1].[ImageId,Name]" --output text
```

### 0.2 Verify subnet

```bash
aws ec2 describe-subnets --region us-west-2 --subnet-ids <SUBNET_ID>
```

> Check: `MapPublicIpOnLaunch` = true (needed for SSH tunnel later).

### 0.3 Find IAM Instance Profile

Look up from an existing instance if unsure:

```bash
aws ec2 describe-instances --region us-west-2 --instance-ids <REFERENCE_INSTANCE> \
  --query "Reservations[0].Instances[0].IamInstanceProfile.Arn" --output text
```

> ⚠️ The profile must have: `AmazonSSMManagedInstanceCore` (for SSM) + `BedrockFullAccess` or equivalent (for Claude on Bedrock).

### 0.4 Launch instance

```bash
aws ec2 run-instances --region us-west-2 \
  --image-id <AMI_ID> \
  --instance-type t3.medium \
  --subnet-id <SUBNET_ID> \
  --security-group-ids <SG_ID> \
  --iam-instance-profile Name=<PROFILE_NAME> \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=acp-bridge}]' \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled \
  --count 1
```

### 0.5 Wait for SSM Online

```bash
# Poll until PingStatus = Online
aws ssm describe-instance-information --region us-west-2 \
  --filters Key=InstanceIds,Values=<INSTANCE_ID> \
  --query "InstanceInformationList[0].PingStatus" --output text
```

> Typically 30-60 seconds after launch. Do NOT proceed until `Online`.

---

## Phase 1: Install Dependencies (SSM)

Single SSM command, ~30 seconds on AL2023:

```bash
aws ssm send-command --region us-west-2 \
  --instance-ids <INSTANCE_ID> \
  --document-name AWS-RunShellScript \
  --timeout-seconds 600 \
  --parameters commands='[
    "set -ex",
    "dnf install -y python3.12 python3.12-pip git nodejs20 npm",
    "curl -LsSf https://astral.sh/uv/install.sh | sh",
    "export PATH=/root/.local/bin:$PATH",
    "npm i -g @anthropic-ai/claude-code @agentclientprotocol/claude-agent-acp",
    "cd /opt && git clone https://github.com/xiwan/acp-bridge.git",
    "cd /opt/acp-bridge && uv sync"
  ]'
```

### Gotchas

| Issue | Cause | Fix |
|-------|-------|-----|
| `uv` not found after install | PATH not updated in same shell | `export PATH=/root/.local/bin:$PATH` before using uv |
| `@zed-industries/claude-agent-acp` deprecated | Package renamed | Use `@agentclientprotocol/claude-agent-acp` instead |
| `uv sync` downloads Python 3.14 | pyproject.toml `requires-python` | This is expected — uv manages its own Python |
| npm warns about version | AL2023 ships npm 10.x | Non-blocking, ignore |

### Check command status

```bash
aws ssm get-command-invocation --region us-west-2 \
  --command-id <COMMAND_ID> --instance-id <INSTANCE_ID> \
  --query "[Status, StandardOutputContent, StandardErrorContent]"
```

> Poll until `Status` = `Success`. Install takes 20-40 seconds typically.

---

## Phase 2: Configure & Start (SSM)

Second SSM command — writes config.yaml, creates systemd service, starts it:

```bash
aws ssm send-command --region us-west-2 \
  --instance-ids <INSTANCE_ID> \
  --document-name AWS-RunShellScript \
  --timeout-seconds 120 \
  --parameters commands='[
    "set -ex",
    "cd /opt/acp-bridge",

    "cat > config.yaml << '\''EOFCFG'\''",
    "server:",
    "  host: \"0.0.0.0\"",
    "  port: 18010",
    "  session_ttl_hours: 24",
    "  shutdown_timeout: 30",
    "  ui: true",
    "",
    "pool:",
    "  max_processes: 20",
    "  max_per_agent: 10",
    "",
    "webhook:",
    "  url: \"http://127.0.0.1:18789/tools/invoke\"",
    "  token: \"${OPENCLAW_TOKEN}\"",
    "  account_id: \"default\"",
    "  target: \"channel:default\"",
    "",
    "security:",
    "  auth_token: \"${ACP_BRIDGE_TOKEN}\"",
    "  allowed_ips:",
    "    - \"127.0.0.1\"",
    "    - \"0.0.0.0/0\"",
    "",
    "agents:",
    "  claude:",
    "    enabled: true",
    "    mode: \"acp\"",
    "    command: \"claude-agent-acp\"",
    "    acp_args: []",
    "    working_dir: \"/tmp\"",
    "    description: \"Claude Code agent (Bedrock)\"",
    "EOFCFG",

    "cat > /etc/systemd/system/acp-bridge.service << '\''EOFSVC'\''",
    "[Unit]",
    "Description=ACP Bridge",
    "After=network.target",
    "[Service]",
    "Type=simple",
    "WorkingDirectory=/opt/acp-bridge",
    "Environment=ACP_BRIDGE_TOKEN=<YOUR_TOKEN>",
    "Environment=OPENCLAW_TOKEN=<YOUR_OPENCLAW_TOKEN>",
    "Environment=CLAUDE_CODE_USE_BEDROCK=1",
    "Environment=ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-20250514",
    "Environment=PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
    "ExecStart=/root/.local/bin/uv run main.py --ui",
    "Restart=on-failure",
    "RestartSec=5",
    "[Install]",
    "WantedBy=multi-user.target",
    "EOFSVC",

    "systemctl daemon-reload",
    "systemctl enable --now acp-bridge",
    "sleep 3",
    "curl -s http://127.0.0.1:18010/health || echo HEALTH_FAILED"
  ]'
```

### Gotchas

| Issue | Cause | Fix |
|-------|-------|-----|
| Health check returns curl exit 7 | Service not fully started in 3s | Increase `sleep` or check separately |
| `set -ex` causes SSM "Failed" | Any non-zero exit (including curl) fails the whole command | Use `curl ... \|\| echo FAILED` to avoid |
| Tokens visible in SSM command history | SSM logs parameters | Use SSM SecureString parameters for production |

---

## Phase 3: Verify (SSM)

```bash
aws ssm send-command --region us-west-2 \
  --instance-ids <INSTANCE_ID> \
  --document-name AWS-RunShellScript \
  --timeout-seconds 60 \
  --parameters commands='[
    "echo === Health === && curl -s http://127.0.0.1:18010/health",
    "echo === Agents === && curl -s http://127.0.0.1:18010/agents -H \"Authorization: Bearer <YOUR_TOKEN>\"",
    "echo === Service === && systemctl status acp-bridge --no-pager -l"
  ]'
```

### Expected output

```
=== Health ===
{"status":"ok","version":"0.9.2","uptime":19}
=== Agents ===
{"agents":[{"name":"claude","description":"Claude Code agent (Bedrock)",...}]}
=== Service ===
● acp-bridge.service - ACP Bridge
     Active: active (running)
```

---

## Phase 4: End-to-End Test (SSM)

```bash
aws ssm send-command --region us-west-2 \
  --instance-ids <INSTANCE_ID> \
  --document-name AWS-RunShellScript \
  --timeout-seconds 180 \
  --parameters commands='[
    "curl -s --max-time 120 -X POST http://127.0.0.1:18010/runs \
      -H \"Authorization: Bearer <YOUR_TOKEN>\" \
      -H \"Content-Type: application/json\" \
      -d '\''{\"agent_name\":\"claude\",\"input\":[{\"parts\":[{\"content\":\"Say hello in one sentence\",\"content_type\":\"text/plain\"}]}]}'\''"
  ]'
```

### Gotchas

| Issue | Cause | Fix |
|-------|-------|-----|
| `invalid_input: Field required: input` | Used `prompt` instead of `input` | ACP protocol requires `input` with `parts` array |
| First call takes 30-60s | Claude ACP subprocess cold start | Normal — subsequent calls are fast (~5s) |
| Timeout on SSM command | Default 60s too short for first Claude call | Set `--timeout-seconds 180` |

### Expected output

```json
{
  "status": "completed",
  "output": [{"parts": [{"content": "Hello! I'm Claude, ready to help..."}]}]
}
```

---

## Phase 5: Local Access via SSH Tunnel

The bridge listens on the EC2 instance. To access from your local machine:

### 5.1 Inject SSH public key (if no key pair was set at launch)

```bash
aws ssm send-command --region us-west-2 \
  --instance-ids <INSTANCE_ID> \
  --document-name AWS-RunShellScript \
  --timeout-seconds 30 \
  --parameters commands='[
    "mkdir -p /home/ec2-user/.ssh",
    "echo '\''<YOUR_PUBLIC_KEY>'\'' >> /home/ec2-user/.ssh/authorized_keys",
    "chmod 700 /home/ec2-user/.ssh",
    "chmod 600 /home/ec2-user/.ssh/authorized_keys",
    "chown -R ec2-user:ec2-user /home/ec2-user/.ssh"
  ]'
```

> ⚠️ Ensure SG allows inbound TCP 22 from your IP.

### 5.2 SSH Local Forward (recommended)

Forward remote 18010 to local 18010:

```bash
ssh -i ~/.ssh/<KEY> -L 18010:127.0.0.1:18010 ec2-user@<PUBLIC_IP> -N
```

Then access locally:
- Health: `curl http://127.0.0.1:18010/health`
- Web UI: http://127.0.0.1:18010/ui
- Agents: `curl http://127.0.0.1:18010/agents -H "Authorization: Bearer <TOKEN>"`

### 5.3 SSH Reverse Tunnel (alternative)

If the bridge runs locally and you need to expose it to a remote server:

```bash
ssh -i ~/.ssh/<KEY> \
    -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=30 \
    -R 18010:127.0.0.1:18010 \
    ec2-user@<PUBLIC_IP> -N
```

### Gotchas

| Issue | Cause | Fix |
|-------|-------|-----|
| `Permission denied (publickey)` | No key pair on instance | Inject pubkey via SSM (step 5.1) |
| SSH timeout | SG doesn't allow port 22 | Add inbound rule for TCP 22 |
| `bind: Address already in use` | Stale tunnel on remote | `fuser -k 18010/tcp` on remote |

---

## Multi-Agent Configuration

To enable additional agents, add to `config.yaml` and install their CLIs:

```yaml
agents:
  kiro:
    enabled: true
    mode: "acp"
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
    description: "Claude Code agent (Bedrock)"
  codex:
    enabled: true
    mode: "pty"
    command: "codex"
    args: ["exec", "--full-auto", "--skip-git-repo-check"]
    working_dir: "/tmp"
    description: "OpenAI Codex CLI agent"
```

> Codex requires LiteLLM proxy — see [README](https://github.com/xiwan/acp-bridge#codex--litellm-setup).
> Kiro requires `kiro-cli login` interactively first (use SSM Session Manager).

---

## Quick Reference: SSM Command Pattern

All remote operations follow this pattern:

```bash
# Send
COMMAND_ID=$(aws ssm send-command --region us-west-2 \
  --instance-ids <ID> \
  --document-name AWS-RunShellScript \
  --timeout-seconds <TIMEOUT> \
  --parameters commands='[...]' \
  --query "Command.CommandId" --output text)

# Poll
aws ssm get-command-invocation --region us-west-2 \
  --command-id $COMMAND_ID --instance-id <ID> \
  --query "[Status, StandardOutputContent, StandardErrorContent]"
```

> SSM output is truncated at 24KB. For large outputs, configure `--output-s3-bucket-name`.

---

## Teardown

```bash
# Stop service
aws ssm send-command --region us-west-2 \
  --instance-ids <ID> --document-name AWS-RunShellScript \
  --parameters commands='["systemctl stop acp-bridge"]'

# Terminate instance
aws ec2 terminate-instances --region us-west-2 --instance-ids <ID>
```
