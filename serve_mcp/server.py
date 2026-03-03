"""MCP server for serving files over LAN with QR code page."""

import os
import socket
import subprocess
import sys
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote, unquote

import qrcode
import qrcode.image.svg
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("serve-mcp")

_state: dict = {
    "server": None,
    "thread": None,
    "file_path": None,
    "url": None,
    "local_url": None,
    "port": None,
}


def _format_size(size: int) -> str:
    """Format a byte count as a human-readable string."""
    if size >= 1_048_576:
        return f"{size / 1_048_576:.1f} MB"
    elif size >= 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size} bytes"


def _generate_qr_svg(url: str) -> str:
    """Generate an SVG QR code for the given URL."""
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathFillImage)
    return img.to_string(encoding="unicode")


_QR_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Download {filename}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    margin: 0;
    background: #f5f5f5;
    color: #333;
  }}
  .card {{
    background: white;
    border-radius: 12px;
    padding: 2rem;
    box-shadow: 0 2px 12px rgba(0,0,0,0.1);
    text-align: center;
    max-width: 400px;
    width: 90%;
  }}
  h1 {{
    font-size: 1.2rem;
    margin: 0 0 0.25rem;
    word-break: break-all;
  }}
  .size {{
    color: #888;
    font-size: 0.9rem;
    margin-bottom: 1.5rem;
  }}
  .qr svg {{
    width: 240px;
    height: 240px;
  }}
  .hint {{
    margin-top: 1rem;
    font-size: 0.85rem;
    color: #888;
  }}
  a {{
    color: #0066cc;
    text-decoration: none;
  }}
  a:hover {{
    text-decoration: underline;
  }}
</style>
</head>
<body>
<div class="card">
  <h1>{filename}</h1>
  <p class="size">{size}</p>
  <div class="qr">{qr_svg}</div>
  <p class="hint">Scan to download, or <a href="{download_url}">direct link</a></p>
</div>
</body>
</html>
"""


class _FileShareHandler(SimpleHTTPRequestHandler):
    """Multi-route handler: QR page at / and file download at /download/<filename>."""

    served_path: str = ""
    download_url: str = ""
    qr_svg: str = ""
    auto_close: bool = True
    server_ref: HTTPServer | None = None
    filename: str = ""
    file_size_str: str = ""

    def do_GET(self):
        if self.path == "/":
            self._serve_qr_page()
        elif self.path.startswith("/download/"):
            self._serve_file()
        else:
            self.send_error(404, "Not found")

    def _serve_qr_page(self):
        html = _QR_PAGE_TEMPLATE.format(
            filename=self.filename,
            size=self.file_size_str,
            qr_svg=self.qr_svg,
            download_url=self.download_url,
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self):
        path = Path(self.served_path)
        if not path.exists():
            self.send_error(404, "File not found")
            return

        # Verify the requested filename matches
        requested_name = unquote(self.path[len("/download/"):])
        if requested_name != path.name:
            self.send_error(404, "File not found")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header(
            "Content-Disposition", f'attachment; filename="{path.name}"'
        )
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()

        try:
            with open(path, "rb") as f:
                while chunk := f.read(8192):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

        if self.auto_close and self.server_ref is not None:
            threading.Timer(0.5, self.server_ref.shutdown).start()

    def log_message(self, format, *args):
        """Redirect logs to stderr so stdout stays clean for MCP JSON-RPC."""
        print(format % args, file=sys.stderr)


def _get_lan_ip() -> str:
    """Auto-detect LAN IP via UDP socket trick, fallback to ifconfig parsing."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        pass

    try:
        result = subprocess.run(
            ["ifconfig"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet ") and "127.0.0.1" not in line:
                return line.split()[1]
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return "127.0.0.1"


def _stop_existing_server():
    """Stop the currently running server if any."""
    if _state["server"] is not None:
        _state["server"].shutdown()
        _state["server"].server_close()
        _state["server"] = None
        _state["thread"] = None
        _state["file_path"] = None
        _state["url"] = None
        _state["local_url"] = None
        _state["port"] = None


@mcp.tool()
def serve_file(file_path: str, auto_close: bool = True) -> str:
    """Serve a file over HTTP on the LAN.

    Starts a local HTTP server that serves the specified file.
    Any previously running server is stopped first.

    Args:
        file_path: Absolute or relative path to the file to serve.
        auto_close: If True, stop the server after the file is downloaded once.

    Returns:
        Download URL, filename, file size, and port.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        return f"Error: file not found: {path}"
    if not path.is_file():
        return f"Error: not a file: {path}"

    _stop_existing_server()

    lan_ip = _get_lan_ip()
    encoded_name = quote(path.name)

    # Create handler class with file info
    server = HTTPServer(("0.0.0.0", 0), _FileShareHandler)
    port = server.server_address[1]

    download_url = f"http://{lan_ip}:{port}/download/{encoded_name}"
    local_url = f"http://127.0.0.1:{port}/"
    size_str = _format_size(path.stat().st_size)

    qr_svg = _generate_qr_svg(download_url)

    _FileShareHandler.served_path = str(path)
    _FileShareHandler.download_url = download_url
    _FileShareHandler.qr_svg = qr_svg
    _FileShareHandler.auto_close = auto_close
    _FileShareHandler.server_ref = server
    _FileShareHandler.filename = path.name
    _FileShareHandler.file_size_str = size_str

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    _state["server"] = server
    _state["thread"] = thread
    _state["file_path"] = str(path)
    _state["url"] = download_url
    _state["local_url"] = local_url
    _state["port"] = port

    auto_close_str = "yes" if auto_close else "no"

    return (
        f"Serving: {path.name}\n"
        f"Size: {size_str}\n"
        f"Port: {port}\n"
        f"Download URL: {download_url}\n"
        f"QR page (open on this device): {local_url}\n"
        f"Auto-close after download: {auto_close_str}"
    )


@mcp.tool()
def stop_server() -> str:
    """Stop the currently running file server.

    Returns:
        Confirmation of what was stopped.
    """
    if _state["server"] is None:
        return "No server is currently running."

    file_path = _state["file_path"]
    port = _state["port"]
    _stop_existing_server()
    return f"Stopped server for {file_path} on port {port}."


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
