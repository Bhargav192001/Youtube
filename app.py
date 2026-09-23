import os
import sys
import uuid
import threading
import time
import glob
import tempfile
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from yt_dlp import YoutubeDL

# Ensure FFmpeg is found across local Windows and Linux cloud hosts (Render)
def setup_ffmpeg_path():
    import shutil

    # 1. Check if FFmpeg is already available in PATH
    if shutil.which("ffmpeg"):
        return

    # 2. Check user PATH in Windows Registry / Environment
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                user_path, _ = winreg.QueryValueEx(key, "Path")
                for p in user_path.split(";"):
                    if p.strip() and os.path.isdir(p.strip()) and os.path.exists(os.path.join(p.strip(), "ffmpeg.exe")):
                        os.environ["PATH"] = p.strip() + os.pathsep + os.environ["PATH"]
                        return
        except Exception:
            pass

        # 3. Check default winget yt-dlp.FFmpeg path
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            pattern = os.path.join(local_app_data, "Microsoft", "WinGet", "Packages", "*ffmpeg*", "**", "ffmpeg.exe")
            matches = glob.glob(pattern, recursive=True)
            if matches:
                ffmpeg_bin_dir = os.path.dirname(matches[0])
                os.environ["PATH"] = ffmpeg_bin_dir + os.pathsep + os.environ["PATH"]
                return

    # 4. Fallback for cloud platforms (e.g. Render Linux) using static-ffmpeg
    try:
        import importlib
        static_ff = importlib.import_module("static_ffmpeg")
        static_ff.add_paths()
    except Exception:
        pass

setup_ffmpeg_path()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Store temporary downloads in system temp folder outside the workspace
DOWNLOADS_DIR = os.path.join(tempfile.gettempdir(), "pulsedl_temp_downloads")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app = FastAPI(title="YouTube Downloader", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active download tasks storage
# task_id -> { "status", "progress", "speed", "eta", "downloaded_str", "total_str", "filename", "filepath", "error" }
active_tasks: Dict[str, Dict[str, Any]] = {}

class InfoRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    format_type: str = "video"  # "video" or "audio"
    quality: Optional[str] = "best"  # "best", "1080", "720", "480", "360", or audio "320", "192", "128"

def format_bytes(bytes_val):
    if not bytes_val or bytes_val <= 0:
        return "0 MB"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"

def format_duration(seconds):
    if not seconds:
        return "00:00"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    sec = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"

def format_views(count):
    if not count:
        return "0"
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)

@app.post("/api/info")
def get_video_info(req: InfoRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    ydl_opts = {
        "skip_download": True,
        "extract_flat": False,
        "js_runtimes": {"node": {}},
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch video: {str(e)}")

    if not info:
        raise HTTPException(status_code=404, detail="No video information found")

    formats = info.get("formats", [])
    available_resolutions = set()
    has_audio = False

    for f in formats:
        height = f.get("height")
        if height and f.get("vcodec") != "none":
            available_resolutions.add(height)
        if f.get("acodec") != "none":
            has_audio = True

    sorted_resolutions = sorted(list(available_resolutions), reverse=True)

    # Pick highest resolution thumbnail
    thumbnails = info.get("thumbnails", [])
    thumbnail_url = info.get("thumbnail")
    if thumbnails:
        best_thumb = max(thumbnails, key=lambda t: t.get("preference", 0) or t.get("height", 0) or 0)
        thumbnail_url = best_thumb.get("url") or thumbnail_url

    return {
        "id": info.get("id"),
        "title": info.get("title", "Untitled Video"),
        "channel": info.get("uploader") or info.get("channel") or "Unknown Channel",
        "channel_url": info.get("uploader_url") or info.get("channel_url"),
        "duration": info.get("duration"),
        "duration_str": format_duration(info.get("duration")),
        "view_count": info.get("view_count"),
        "view_count_str": format_views(info.get("view_count")),
        "thumbnail": thumbnail_url,
        "description": (info.get("description") or "")[:200],
        "resolutions": sorted_resolutions,
        "has_audio": has_audio,
        "webpage_url": info.get("webpage_url", url),
    }

def run_download_thread(task_id: str, url: str, format_type: str, quality: str):
    task = active_tasks[task_id]

    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            percentage = 0.0
            if total > 0:
                percentage = round((downloaded / total) * 100, 1)

            speed = d.get('speed')
            speed_str = f"{format_bytes(speed)}/s" if speed else ""

            eta = d.get('eta')
            eta_str = f"{eta}s" if eta else ""

            task["status"] = "downloading"
            task["progress"] = percentage
            task["downloaded_str"] = format_bytes(downloaded)
            task["total_str"] = format_bytes(total)
            task["speed"] = speed_str
            task["eta"] = eta_str

        elif d['status'] == 'finished':
            task["status"] = "processing"
            task["progress"] = 99.0
            task["speed"] = "Merging video & audio with FFmpeg..."
            task["eta"] = ""

    out_template = os.path.join(DOWNLOADS_DIR, f"{task_id}_%(title).150B.%(ext)s")

    ydl_opts: Dict[str, Any] = {
        "outtmpl": out_template,
        "progress_hooks": [progress_hook],
        "js_runtimes": {"node": {}},
        "quiet": True,
        "no_warnings": True,
    }

    if format_type == "audio":
        bitrate = quality if quality in ["128", "192", "320"] else "192"
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": bitrate,
            }],
        })
    else:
        if not quality or quality == "best":
            ydl_opts["format"] = "bv*+ba/b"
        else:
            try:
                max_h = int(quality)
                ydl_opts["format"] = f"bv*[height<={max_h}]+ba/b[height<={max_h}]/best"
            except ValueError:
                ydl_opts["format"] = "bv*+ba/b"
        ydl_opts["merge_output_format"] = "mp4"

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            final_files = glob.glob(os.path.join(DOWNLOADS_DIR, f"{task_id}_*"))
            if final_files:
                actual_file = final_files[0]
                filename = os.path.basename(actual_file)
                clean_name = filename[len(task_id) + 1:]
                task["filepath"] = actual_file
                task["filename"] = clean_name
                task["file_size_str"] = format_bytes(os.path.getsize(actual_file))
                task["status"] = "completed"
                task["progress"] = 100.0
                task["speed"] = ""
                task["eta"] = ""
            else:
                task["status"] = "failed"
                task["error"] = "Downloaded file could not be located on disk."
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)

@app.post("/api/download")
def start_download(req: DownloadRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    task_id = str(uuid.uuid4())
    active_tasks[task_id] = {
        "task_id": task_id,
        "url": url,
        "format_type": req.format_type,
        "quality": req.quality,
        "status": "queued",
        "progress": 0.0,
        "downloaded_str": "0 MB",
        "total_str": "...",
        "speed": "",
        "eta": "",
        "filename": "",
        "filepath": "",
        "file_size_str": "",
        "error": "",
        "created_at": time.time(),
    }

    t = threading.Thread(target=run_download_thread, args=(task_id, url, req.format_type, req.quality), daemon=True)
    t.start()

    return {"task_id": task_id}

@app.get("/api/progress/{task_id}")
def get_progress(task_id: str):
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="Download task not found")
    return active_tasks[task_id]

@app.get("/api/file/{task_id}")
def download_file(task_id: str):
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task = active_tasks[task_id]
    if task["status"] != "completed" or not task.get("filepath"):
        raise HTTPException(status_code=400, detail="File is not ready yet")
    filepath = task["filepath"]
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File missing on server")

    return FileResponse(
        path=filepath,
        filename=task.get("filename") or os.path.basename(filepath),
        media_type="application/octet-stream",
    )

def cleanup_old_files():
    while True:
        try:
            now = time.time()
            for f in glob.glob(os.path.join(DOWNLOADS_DIR, "*")):
                if os.path.isfile(f) and (now - os.path.getmtime(f)) > 7200:
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(1800)

threading.Thread(target=cleanup_old_files, daemon=True).start()

# Mount frontend static directory
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    uvicorn.run("app:app", host=host, port=port, reload=True)
