"""MCP server for serving and receiving files over LAN with QR code page."""

import socket
import subprocess
import sys
import tempfile
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

_upload_state: dict = {
    "server": None,
    "thread": None,
    "url": None,
    "local_url": None,
    "port": None,
    "received_file": None,
    "received_filename": None,
    "error": None,
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


_UPLOAD_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upload File</title>
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
    margin: 0 0 1.5rem;
  }}
  .upload-area {{
    border: 2px dashed #ccc;
    border-radius: 8px;
    padding: 2rem 1rem;
    margin-bottom: 1rem;
    cursor: pointer;
    transition: border-color 0.2s;
  }}
  .upload-area:hover, .upload-area.dragover {{
    border-color: #0066cc;
  }}
  .upload-area p {{
    margin: 0;
    color: #888;
  }}
  .filename {{
    margin: 0.5rem 0;
    font-weight: 600;
    word-break: break-all;
  }}
  input[type="file"] {{
    display: none;
  }}
  button {{
    background: #0066cc;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 0.75rem 2rem;
    font-size: 1rem;
    cursor: pointer;
    width: 100%;
    transition: background 0.2s;
  }}
  button:hover {{
    background: #0052a3;
  }}
  button:disabled {{
    background: #ccc;
    cursor: not-allowed;
  }}
  .status {{
    margin-top: 1rem;
    font-size: 0.9rem;
    color: #888;
  }}
  .success {{
    color: #22863a;
    font-weight: 600;
  }}
  .error {{
    color: #cb2431;
  }}
  .progress-bar {{
    width: 100%;
    height: 6px;
    background: #eee;
    border-radius: 3px;
    margin-top: 1rem;
    overflow: hidden;
    display: none;
  }}
  .progress-bar .fill {{
    height: 100%;
    background: #0066cc;
    width: 0%;
    transition: width 0.3s;
  }}
</style>
</head>
<body>
<div class="card">
  <h1>Upload a file</h1>
  <form id="uploadForm" method="POST" enctype="multipart/form-data">
    <div class="upload-area" id="dropZone">
      <p id="dropText">Tap to select a file</p>
      <p class="filename" id="fileName" style="display:none"></p>
    </div>
    <input type="file" id="fileInput" name="file">
    <div class="progress-bar" id="progressBar"><div class="fill" id="progressFill"></div></div>
    <button type="submit" id="submitBtn" disabled>Upload</button>
  </form>
  <p class="status" id="status"></p>
</div>
<script>
  const dropZone = document.getElementById('dropZone');
  const fileInput = document.getElementById('fileInput');
  const fileName = document.getElementById('fileName');
  const dropText = document.getElementById('dropText');
  const submitBtn = document.getElementById('submitBtn');
  const status = document.getElementById('status');
  const form = document.getElementById('uploadForm');
  const progressBar = document.getElementById('progressBar');
  const progressFill = document.getElementById('progressFill');

  dropZone.addEventListener('click', () => fileInput.click());
  dropZone.addEventListener('dragover', (e) => {{
    e.preventDefault();
    dropZone.classList.add('dragover');
  }});
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', (e) => {{
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) {{
      fileInput.files = e.dataTransfer.files;
      showFile(e.dataTransfer.files[0]);
    }}
  }});

  fileInput.addEventListener('change', () => {{
    if (fileInput.files.length) showFile(fileInput.files[0]);
  }});

  function showFile(file) {{
    fileName.textContent = file.name;
    fileName.style.display = 'block';
    dropText.textContent = 'Selected:';
    submitBtn.disabled = false;
  }}

  form.addEventListener('submit', (e) => {{
    e.preventDefault();
    if (!fileInput.files.length) return;

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    submitBtn.disabled = true;
    submitBtn.textContent = 'Uploading...';
    progressBar.style.display = 'block';

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');

    xhr.upload.addEventListener('progress', (e) => {{
      if (e.lengthComputable) {{
        const pct = (e.loaded / e.total) * 100;
        progressFill.style.width = pct + '%';
      }}
    }});

    xhr.addEventListener('load', () => {{
      if (xhr.status === 200) {{
        status.className = 'status success';
        status.textContent = 'Upload complete! You can close this page.';
        progressFill.style.width = '100%';
      }} else {{
        status.className = 'status error';
        status.textContent = 'Upload failed: ' + xhr.statusText;
        submitBtn.disabled = false;
        submitBtn.textContent = 'Upload';
      }}
    }});

    xhr.addEventListener('error', () => {{
      status.className = 'status error';
      status.textContent = 'Upload failed. Please try again.';
      submitBtn.disabled = false;
      submitBtn.textContent = 'Upload';
    }});

    xhr.send(formData);
  }});
</script>
</body>
</html>
"""

_UPLOAD_QR_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upload File</title>
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
    margin: 0 0 1.5rem;
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
  <h1>Scan to upload a file</h1>
  <div class="qr">{qr_svg}</div>
  <p class="hint">Scan with your phone, or <a href="{upload_url}">open directly</a></p>
</div>
</body>
</html>
"""


_UPLOAD_SUCCESS_RESPONSE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upload Complete</title>
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex; justify-content: center; align-items: center;
    min-height: 100vh; margin: 0; background: #f5f5f5; color: #333;
  }
  .card {
    background: white; border-radius: 12px; padding: 2rem;
    box-shadow: 0 2px 12px rgba(0,0,0,0.1); text-align: center;
    max-width: 400px; width: 90%;
  }
  .success { color: #22863a; font-size: 1.2rem; font-weight: 600; }
</style>
</head>
<body>
<div class="card">
  <p class="success">Upload complete!</p>
  <p>You can close this page.</p>
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


class _FileUploadHandler(SimpleHTTPRequestHandler):
    """Handler for receiving file uploads."""

    upload_dir: str = ""
    qr_svg: str = ""
    server_ref: HTTPServer | None = None

    def do_GET(self):
        if self.path == "/":
            self._serve_qr_page()
        elif self.path == "/upload":
            self._serve_upload_page()
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        if self.path == "/upload":
            self._handle_upload()
        else:
            self.send_error(404, "Not found")

    def _serve_qr_page(self):
        lan_ip = _get_lan_ip()
        port = self.server.server_address[1]
        upload_url = f"http://{lan_ip}:{port}/upload"
        html = _UPLOAD_QR_PAGE_TEMPLATE.format(
            qr_svg=self.qr_svg,
            upload_url=upload_url,
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_upload_page(self):
        html = _UPLOAD_PAGE_TEMPLATE.format()
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error(400, "Expected multipart/form-data")
            return

        # Parse the multipart form data
        boundary = content_type.split("boundary=")[-1].encode()
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Find the file in the multipart data
        filename, file_data = _parse_multipart(body, boundary)

        if filename is None or file_data is None:
            self.send_error(400, "No file found in upload")
            return

        # Save the file
        save_path = Path(self.upload_dir) / filename
        save_path.write_bytes(file_data)

        _upload_state["received_file"] = str(save_path)
        _upload_state["received_filename"] = filename

        # Send success response
        body_bytes = _UPLOAD_SUCCESS_RESPONSE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

        # Auto-close server after upload
        if self.server_ref is not None:
            threading.Timer(0.5, self.server_ref.shutdown).start()

    def log_message(self, format, *args):
        """Redirect logs to stderr so stdout stays clean for MCP JSON-RPC."""
        print(format % args, file=sys.stderr)


def _parse_multipart(body: bytes, boundary: bytes) -> tuple:
    """Parse multipart form data and extract the uploaded file."""
    delimiter = b"--" + boundary
    parts = body.split(delimiter)

    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        if b'name="file"' not in part:
            continue

        # Extract filename
        header_end = part.index(b"\r\n\r\n")
        header = part[:header_end].decode("utf-8", errors="replace")
        file_data = part[header_end + 4:]  # skip \r\n\r\n

        # Remove trailing \r\n
        if file_data.endswith(b"\r\n"):
            file_data = file_data[:-2]

        # Parse filename from Content-Disposition
        filename = None
        for line in header.split("\r\n"):
            if "filename=" in line:
                # Handle both filename="name" and filename=name
                parts_line = line.split("filename=")
                if len(parts_line) > 1:
                    fname = parts_line[1].strip().strip('"').split('"')[0]
                    # Sanitize: take only the basename
                    filename = Path(fname).name
                break

        if filename:
            return filename, file_data

    return None, None


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
    """Stop the currently running file-serve server if any."""
    if _state["server"] is not None:
        _state["server"].shutdown()
        _state["server"].server_close()
        _state["server"] = None
        _state["thread"] = None
        _state["file_path"] = None
        _state["url"] = None
        _state["local_url"] = None
        _state["port"] = None


def _stop_upload_server():
    """Stop the currently running upload server if any."""
    if _upload_state["server"] is not None:
        _upload_state["server"].shutdown()
        _upload_state["server"].server_close()
        _upload_state["server"] = None
        _upload_state["thread"] = None
        _upload_state["url"] = None
        _upload_state["local_url"] = None
        _upload_state["port"] = None


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
        f"Phone URL: {download_url}\n"
        f"QR page: {local_url}\n"
        f"Auto-close after download: {auto_close_str}"
    )


@mcp.tool()
def receive_file() -> str:
    """Start an upload server so a phone can send a file to this computer.

    Opens a temporary HTTP server with a mobile-friendly upload page.
    Scan the QR code on your phone to open the page, pick a file, and upload it.
    After calling this tool, open the QR page URL on this computer so the user
    can scan it. Then call check_received_file to poll for the uploaded file.

    Returns:
        The upload page URL and QR page URL.
    """
    _stop_upload_server()
    _upload_state["received_file"] = None
    _upload_state["received_filename"] = None
    _upload_state["error"] = None

    upload_dir = tempfile.mkdtemp(prefix="serve-mcp-upload-")

    lan_ip = _get_lan_ip()

    server = HTTPServer(("0.0.0.0", 0), _FileUploadHandler)
    port = server.server_address[1]

    upload_url = f"http://{lan_ip}:{port}/upload"
    local_url = f"http://127.0.0.1:{port}/"

    qr_svg = _generate_qr_svg(upload_url)

    _FileUploadHandler.upload_dir = upload_dir
    _FileUploadHandler.qr_svg = qr_svg
    _FileUploadHandler.server_ref = server

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    _upload_state["server"] = server
    _upload_state["thread"] = thread
    _upload_state["url"] = upload_url
    _upload_state["local_url"] = local_url
    _upload_state["port"] = port

    return (
        f"Upload server started.\n"
        f"Port: {port}\n"
        f"Phone URL: {upload_url}\n"
        f"QR page: {local_url}\n"
        f"Waiting for file upload... Call check_received_file to check status."
    )


@mcp.tool()
def check_received_file() -> str:
    """Check if a file has been uploaded via the receive_file upload server.

    Call this after receive_file to check if the user has uploaded a file.
    Returns the file path if a file was received, or a waiting status.

    Returns:
        File path and name if received, or waiting status.
    """
    if _upload_state["server"] is None and _upload_state["received_file"] is None:
        return "No upload server is running. Call receive_file first."

    if _upload_state["received_file"] is not None:
        file_path = _upload_state["received_file"]
        filename = _upload_state["received_filename"]
        size_str = _format_size(Path(file_path).stat().st_size)
        return (
            f"File received!\n"
            f"Filename: {filename}\n"
            f"Size: {size_str}\n"
            f"Path: {file_path}"
        )

    return (
        f"Still waiting for upload...\n"
        f"Phone URL: {_upload_state['url']}\n"
        f"QR page: {_upload_state['local_url']}"
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


@mcp.tool()
def stop_upload_server() -> str:
    """Stop the currently running upload server.

    Returns:
        Confirmation of what was stopped.
    """
    if _upload_state["server"] is None:
        return "No upload server is currently running."

    port = _upload_state["port"]
    _stop_upload_server()
    return f"Stopped upload server on port {port}."


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
