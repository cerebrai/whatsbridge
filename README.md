# whatsbridge

Pushes WhatsApp messages into a running Claude Code session, and lets Claude reply in the same conversation.

Built as a [Claude Code channel](https://code.claude.com/docs/en/channels): a local MCP server that is allowed to speak first.

## What you need

- Claude Code 2.1.80 or newer, signed in with a `claude.ai` account
- Python 3.11 or newer
- A WhatsApp Agent Platform access token
- On a Team or Enterprise plan, channels enabled by an admin

The bridge is standard library only. There is nothing to install.

## Hosting status: do not use a remote server yet

Tested in October 2026 on a Hetzner Cloud server. It failed.

- **Singapore:** Hetzner does not offer the ARM `cax11` there. That location has only x86 plans, and the cheapest `4 GB` one is `cpx21` at €21.99/month.
- **Helsinki:** `cax11` was available, so the test ran there. Claude Code logged `channels feature is not currently available` and skipped every channel notification, so no WhatsApp message reached the session.
- The same account, token, config and version work on a laptop on another network. The block is a remote preview switch, not this code.
- The bridge still starts polling once Claude Code connects, even when the channel is skipped. It will consume WhatsApp messages and drop them.

Until the preview switch allows it, run the bridge on a machine where the channel registers, not on a Hetzner server.

## Setup

**1. Clone the repo.**

```bash
git clone git@github-personal:cerebrai/whatsbridge.git
cd whatsbridge
```

**2. Add your token.**

Create `.env` in the repo root:

```
WHATSAPP_ACCESS_TOKEN=your-token-here
```

The file is gitignored. The bridge loads it from the repo root, so it works no matter which directory Claude Code starts in.

**3. Register the bridge.**

In the project where you run Claude, add this to `.mcp.json`:

```json
{
  "mcpServers": {
    "whatsbridge": {
      "command": "python3",
      "args": ["-m", "whatsbridge"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/whatsbridge"
      }
    }
  }
}
```

**4. Install the skill (recommended).**

The skill tells Claude how to answer WhatsApp messages: short, plain text, one reply per message.

```bash
ln -s "$PWD/skills/whatsapp-voice" ~/.claude-personal/skills/whatsapp-voice
```

Use your own Claude config directory if it is not `~/.claude-personal`.

**5. Run it.**

```bash
CLAUDE_CONFIG_DIR=~/.claude-personal \
  claude --dangerously-load-development-channels server:whatsbridge
```

Send a WhatsApp message to the agent. It should appear in the session, and Claude should answer through the `reply` tool.

## How it works

```
whatsbridge/channel.py    MCP handshake, the push, and the reply tool
whatsbridge/bridge.py     polls WhatsApp and pushes each message
whatsbridge/whatsapp.py   the WhatsApp API client
```

The channel only starts polling after Claude Code sends `notifications/initialized`. Anything pushed earlier is discarded silently.

## Notes

- Channels are a research preview. Custom channels need the `--dangerously-load-development-channels` flag.
- Events only arrive while the session is open.
- The desktop app does not support channels; they need the command-line flag.
- If the session goes quiet after a network drop, check the bridge's stderr output.
