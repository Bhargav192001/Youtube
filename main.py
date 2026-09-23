import os
import sys
import webbrowser
import threading
import time
import uvicorn
from yt_dlp import YoutubeDL
from app import app

def open_browser(port):
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{port}")

def run_cli():
    url = input("Enter Youtube URL: ").strip()
    if not url:
        print("URL cannot be empty.")
        return
    options = {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "js_runtimes": {"node": {}},
    }
    with YoutubeDL(options) as ydl:
        ydl.download([url])

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["--cli", "-c"]:
        run_cli()
    else:
        port = int(os.environ.get("PORT", 8000))
        is_cloud = "PORT" in os.environ
        host = "0.0.0.0" if is_cloud else "127.0.0.1"

        print("=" * 60)
        print("🚀 Starting PulseDL - YouTube Video & Audio Downloader")
        print(f"🌐 Server running at: http://{host}:{port}")
        print("💡 Tip: Run 'python main.py --cli' for terminal mode.")
        print("=" * 60)
        
        # Only open browser on local development machines
        if not is_cloud:
            threading.Thread(target=open_browser, args=(port,), daemon=True).start()
        
        uvicorn.run("app:app", host=host, port=port, reload=False)

