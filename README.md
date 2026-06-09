# lore-kernel-mcp

A lightweight [MCP](https://modelcontextprotocol.io/) server for reading [lore.kernel.org](https://lore.kernel.org/) Linux kernel mailing list threads.

Instead of scraping Anubis-protected HTML pages, it downloads public-inbox thread mbox archives directly — fast, reliable, and cacheable.

```
https://lore.kernel.org/<list>/<message-id>/t.mbox.gz
```

## What it does

Give your AI assistant the ability to **read and analyze Linux kernel mailing list discussions** — patch series, review threads, bug reports, and any lore-hosted conversation.

The server parses mbox files and extracts structured data: message headers, participants, subjects, diff file lists, trailers (Signed-off-by, Reviewed-by, etc.), and full plain-text bodies.

## Tools

| Tool | Description |
|------|-------------|
| `lore_fetch_thread` | Download and cache a thread mbox |
| `lore_summarize_thread` | Summarize messages, participants, subjects, trailers, and patched files |
| `lore_extract_message` | Return one message's headers and full plain-text body |

All tools accept a full lore URL or a bare message-id as `ref`.

## Install

### pip (recommended)

```bash
pip install lore-kernel-mcp
```

### From source

```bash
git clone https://github.com/soolaugust/lore-kernel-mcp.git
cd lore-kernel-mcp
pip install .
```

## Configure

### Claude Code

Add to `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "lore-kernel-mcp": {
      "command": "lore-kernel-mcp"
    }
  }
}
```

Or use the Python path directly (no pip install needed):

```json
{
  "mcpServers": {
    "lore-kernel-mcp": {
      "command": "python3",
      "args": ["-m", "lore_kernel_mcp.server"]
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "lore-kernel-mcp": {
      "command": "lore-kernel-mcp"
    }
  }
}
```

Restart Claude after changing MCP config.

## Usage

Once configured, just ask your AI assistant:

```
Summarize this lore thread:
https://lore.kernel.org/lkml/20260504020003.71306-1-qyousef@layalina.io/
```

Or with a bare message-id:

```
Use lore-kernel-mcp to fetch 20260504020003.71306-1-qyousef@layalina.io from lkml
```

## Configuration

All settings are optional, via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LORE_LITE_CACHE_DIR` | `~/.cache/lore-lite-mcp` | Cache directory for downloaded mbox files |
| `LORE_LITE_MAX_COMPRESSED_BYTES` | `52428800` (50 MB) | Max compressed download size |
| `LORE_LITE_MAX_DECOMPRESSED_BYTES` | `209715200` (200 MB) | Max decompressed mbox size |
| `LORE_LITE_MAX_MESSAGES` | `500` | Max messages per thread |

## How it works

1. Parses the lore URL or message-id to extract the mailing list name and message-id
2. Downloads the gzip-compressed mbox from the public-inbox `t.mbox.gz` endpoint
3. Streams and decompresses with size limits (no unbounded downloads)
4. Caches the mbox locally — subsequent reads are instant
5. Parses with Python's `mailbox` module and returns structured JSON

## Requirements

- Python >= 3.10
- `mcp` (MCP Python SDK)
- `requests`

## License

MIT
