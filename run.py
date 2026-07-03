"""
FreshGuard Launcher
====================
Run this file to start FreshGuard.

Usage:
    python run.py

Requirements:
    pip install flask waitress pyngrok
"""

import os
import sys
import threading
import webbrowser
import time

def install_if_missing(package, import_name=None):
    import importlib
    try:
        importlib.import_module(import_name or package)
    except ImportError:
        print(f"  Installing {package}...")
        os.system(f"{sys.executable} -m pip install {package} -q")

# Auto-install required packages
install_if_missing('flask')
install_if_missing('waitress')
install_if_missing('pyngrok')

from app import app

def start_ngrok(port):
    """Start ngrok tunnel and return the public HTTPS URL."""
    try:
        from pyngrok import ngrok
        tunnel = ngrok.connect(port, "http")
        public_url = tunnel.public_url
        if public_url.startswith("http://"):
            public_url = public_url.replace("http://", "https://")
        return public_url
    except Exception as e:
        print(f"  ngrok not available: {e}")
        return None

def open_browser(url, delay=2):
    """Open browser after a short delay."""
    time.sleep(delay)
    webbrowser.open(url)

def run_server(port):
    """Start Waitress production server — no scary warnings."""
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port, threads=4)
    except ImportError:
        # Fallback to Flask dev server if Waitress not installed
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == '__main__':
    PORT = 5000

    print("\n" + "="*60)
    print("  🥦  FreshGuard — Food Expiry Tracker")
    print("="*60)
    print("  Starting server...")

    # Try to start ngrok for HTTPS (camera barcode scanning needs HTTPS)
    public_url = start_ngrok(PORT)

    if public_url:
        print(f"\n  ✅ App is ready!")
        print(f"  📱 Open this link in ANY browser or on your phone:")
        print(f"\n     👉  {public_url}\n")
        print(f"  ✅ Camera barcode scanning works on this link")
        print(f"  Local access: http://localhost:{PORT}")
    else:
        local_url = f"http://localhost:{PORT}"
        print(f"\n  ✅ App is ready!")
        print(f"\n     👉  {local_url}\n")
        print(f"  ⚠️  Camera scanning needs HTTPS.")
        print(f"  Install ngrok from ngrok.com for camera support on phone.")

    print("="*60)
    print("  Press Ctrl+C to stop the server")
    print("="*60 + "\n")

    # Open browser automatically
    url_to_open = public_url or f"http://localhost:{PORT}"
    t = threading.Thread(target=open_browser, args=(url_to_open,))
    t.daemon = True
    t.start()

    # Start production server
    run_server(PORT)
