from flask import Flask, render_template_string, redirect, url_for
import qrcode
import io
import base64
import socket
import threading

app = Flask(__name__)

# Global counter and scan log
scan_count = 0
scanned_ids = set()

def get_local_ip():
    """Get the computer's local IP so the phone can reach it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

LOCAL_IP = get_local_ip()
PORT = 5000
BASE_URL = f"http://{LOCAL_IP}:{PORT}"

# ---------- QR code generation ----------
def generate_qr(data: str) -> str:
    """Return a base64 PNG of a QR code for `data`."""
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ---------- Routes ----------
@app.route("/")
def index():
    """Computer screen: shows QR code + live counter."""
    scan_url = f"{BASE_URL}/scan"
    qr_b64 = generate_qr(scan_url)

    html = """
    <!doctype html>
    <html>
      <head>
        <title>QR Scanner Counter</title>
        <meta http-equiv="refresh" content="2">
        <style>
          body { font-family: sans-serif; text-align: center; padding: 40px;
                 background: #111; color: #eee; }
          .counter { font-size: 120px; font-weight: bold; color: #4ade80;
                     margin: 20px 0; }
          img { background: white; padding: 20px; border-radius: 12px; }
          .url { color: #888; font-size: 14px; margin-top: 20px; }
        </style>
      </head>
      <body>
        <h1>Scan the QR code</h1>
        <img src="data:image/png;base64,{{ qr }}" width="300" height="300"/>
        <div class="counter">{{ count }}</div>
        <div class="url">Phone must be on the same Wi-Fi.<br>
          URL: {{ url }}</div>
      </body>
    </html>
    """
    return render_template_string(html, qr=qr_b64, count=scan_count, url=scan_url)


@app.route("/scan")
def scan():
    """Phone hits this when scanning the QR."""
    global scan_count
    scan_count += 1
    return """
    <!doctype html>
    <html>
      <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
      <body style="font-family:sans-serif; text-align:center; padding:60px;
                   background:#111; color:#eee;">
        <h1>✅ Scanned!</h1>
        <p>You can close this tab.</p>
      </body>
    </html>
    """


if __name__ == "__main__":
    print(f"\n→ Open this on your computer: {BASE_URL}\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)