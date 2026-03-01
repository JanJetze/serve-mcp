# serve-mcp

An MCP server that serves files over your local network. Open the QR page on your computer, scan with your phone, and the file downloads instantly. The server shuts down automatically after the transfer.

## Tools

### `serve_file`

Starts an HTTP server for a single file. Returns a download URL (for the LAN) and a local QR page URL (to open on this device and scan from a phone).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file_path` | `str` | required | Path to the file to serve |
| `auto_close` | `bool` | `True` | Stop the server after one download |

### `stop_server`

Stops the currently running file server.

## Setup

```sh
git clone https://github.com/janjetze/serve-mcp.git
cd serve-mcp
./setup.sh
```

This creates a `.venv` with Python 3.13 and installs dependencies (`mcp`, `qrcode`).

## Add to Claude Code

```sh
claude mcp add serve-mcp -s user -- /path/to/serve-mcp/.venv/bin/python /path/to/serve-mcp/serve_mcp/server.py
```

Replace `/path/to/serve-mcp` with the actual path where you cloned the repo.
