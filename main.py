import os
import sys
import uuid
import threading
import time
import glob
import tempfile
import webbrowser
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
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

# Store temporary downloads in system temp folder outside the workspace
DOWNLOADS_DIR = os.path.join(tempfile.gettempdir(), "pulsedl_temp_downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

app = FastAPI(title="PulseDL - YouTube Downloader", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

active_tasks: Dict[str, Dict[str, Any]] = {}

class InfoRequest(BaseModel):
    url: str

class DownloadRequest(BaseModel):
    url: str
    format_type: str = "video"
    quality: Optional[str] = "best"

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

# Embedded Full Frontend (Single-File Architecture for Render)
FRONTEND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PulseDL • Next-Gen YouTube Video & Audio Downloader</title>
  <meta name="description" content="Download YouTube videos and audio in ultra-high quality MP4 and MP3 with real-time FFmpeg processing.">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
  <style>
/* ==========================================================================
   Design System & Tokens (PulseDL)
   ========================================================================== */

:root {
  --font-heading: 'Outfit', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --font-body: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;

  /* Colors */
  --bg-dark: #070a12;
  --bg-card: rgba(15, 23, 42, 0.65);
  --bg-card-hover: rgba(23, 34, 60, 0.75);
  --bg-input: rgba(10, 16, 30, 0.75);
  --border-glass: rgba(255, 255, 255, 0.09);
  --border-focus: rgba(99, 102, 241, 0.55);

  --accent-primary: #6366f1;
  --accent-secondary: #06b6d4;
  --accent-yt: #f43f5e;
  --accent-success: #10b981;
  --accent-warning: #f59e0b;

  --text-main: #f8fafc;
  --text-muted: #94a3b8;
  --text-dim: #64748b;

  --glow-primary: rgba(99, 102, 241, 0.35);
  --glow-cyan: rgba(6, 182, 212, 0.25);
  --glow-yt: rgba(244, 63, 94, 0.25);

  --radius-sm: 8px;
  --radius-md: 14px;
  --radius-lg: 20px;
  --radius-full: 9999px;

  --shadow-card: 0 20px 40px -15px rgba(0, 0, 0, 0.5), 0 0 1px 1px var(--border-glass);
  --shadow-glow: 0 0 25px var(--glow-primary);

  --transition-fast: 0.18s ease;
  --transition-normal: 0.28s cubic-bezier(0.16, 1, 0.3, 1);
}

/* ==========================================================================
   Reset & Base Styles
   ========================================================================== */

*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: var(--font-body);
  background-color: var(--bg-dark);
  color: var(--text-main);
  min-height: 100vh;
  line-height: 1.5;
  overflow-x: hidden;
  position: relative;
  display: flex;
  flex-direction: column;
}

.container {
  width: 100%;
  max-width: 900px;
  margin-left: auto;
  margin-right: auto;
  padding-left: 24px;
  padding-right: 24px;
}

/* ==========================================================================
   Ambient Background Glows
   ========================================================================== */

.ambient-glow {
  position: fixed;
  border-radius: 50%;
  filter: blur(120px);
  pointer-events: none;
  z-index: 0;
  opacity: 0.45;
  animation: floatOrb 18s ease-in-out infinite alternate;
}

.glow-1 {
  width: 500px;
  height: 500px;
  background: radial-gradient(circle, #4f46e5 0%, transparent 70%);
  top: -100px;
  left: -150px;
}

.glow-2 {
  width: 450px;
  height: 450px;
  background: radial-gradient(circle, #0891b2 0%, transparent 70%);
  top: 30%;
  right: -150px;
  animation-duration: 22s;
  animation-delay: -5s;
}

.glow-3 {
  width: 400px;
  height: 400px;
  background: radial-gradient(circle, #e11d48 0%, transparent 70%);
  bottom: -100px;
  left: 20%;
  animation-duration: 20s;
  animation-delay: -10s;
  opacity: 0.25;
}

@keyframes floatOrb {
  0% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(40px, 30px) scale(1.1); }
  100% { transform: translate(-30px, 60px) scale(0.95); }
}

/* ==========================================================================
   Header
   ========================================================================== */

.app-header {
  position: relative;
  z-index: 10;
  padding: 24px 0 16px;
  backdrop-filter: blur(10px);
}

.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: linear-gradient(135deg, #ef4444, #8b5cf6);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  box-shadow: 0 4px 15px rgba(239, 68, 68, 0.4);
}

.brand-name {
  font-family: var(--font-heading);
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.accent-text {
  background: linear-gradient(135deg, #06b6d4, #818cf8);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 0.75rem;
  font-weight: 600;
  color: #34d399;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.25);
  padding: 6px 12px;
  border-radius: var(--radius-full);
}

.pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background-color: #10b981;
  box-shadow: 0 0 10px #10b981;
  animation: pulseLight 2s infinite;
}

@keyframes pulseLight {
  0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
  70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }
  100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
}

/* ==========================================================================
   Hero Section
   ========================================================================== */

.hero-section {
  position: relative;
  z-index: 5;
  text-align: center;
  padding: 36px 0 28px;
}

.hero-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
  font-weight: 600;
  color: #c7d2fe;
  background: rgba(99, 102, 241, 0.14);
  border: 1px solid rgba(99, 102, 241, 0.28);
  padding: 5px 14px;
  border-radius: var(--radius-full);
  margin-bottom: 16px;
}

.hero-title {
  font-family: var(--font-heading);
  font-size: 2.85rem;
  font-weight: 800;
  line-height: 1.15;
  letter-spacing: -0.03em;
  margin-bottom: 14px;
}

.gradient-text {
  background: linear-gradient(135deg, #38bdf8 0%, #818cf8 50%, #f43f5e 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero-subtitle {
  color: var(--text-muted);
  font-size: 1.05rem;
  max-width: 620px;
  margin: 0 auto;
  line-height: 1.6;
}

/* ==========================================================================
   Glassmorphic Cards
   ========================================================================== */

.card {
  position: relative;
  z-index: 5;
  background: var(--bg-card);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.card:hover {
  border-color: rgba(255, 255, 255, 0.14);
}

/* Input Card */
.input-card {
  padding: 24px;
  margin-bottom: 24px;
}

.url-form {
  display: flex;
  gap: 12px;
}

.input-wrapper {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
  background: var(--bg-input);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-md);
  transition: all var(--transition-fast);
  min-width: 0;
  width: 100%;
  max-width: 100%;
  overflow: hidden;
}

.input-wrapper:focus-within {
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.2);
  background: rgba(15, 23, 42, 0.95);
}

.input-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  padding-left: 14px;
  color: var(--text-dim);
}

#urlInput {
  flex: 1;
  min-width: 0;
  width: 100%;
  background: transparent;
  border: none;
  outline: none;
  font-family: var(--font-body);
  font-size: 0.95rem;
  color: var(--text-main);
  padding: 14px 10px 14px 8px;
}

#urlInput::placeholder {
  color: var(--text-dim);
  text-overflow: ellipsis;
}

.btn-icon {
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border-glass);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  margin-right: 8px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 0.78rem;
  font-weight: 500;
  white-space: nowrap;
  transition: all var(--transition-fast);
}

.btn-icon:hover {
  background: rgba(255, 255, 255, 0.12);
  color: var(--text-main);
}

/* Buttons */
.btn {
  font-family: var(--font-heading);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 13px 24px;
  border-radius: var(--radius-md);
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
  border: none;
  transition: all var(--transition-normal);
  text-decoration: none;
  white-space: nowrap;
}

.btn-primary {
  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
  color: #fff;
  box-shadow: 0 4px 16px rgba(99, 102, 241, 0.35);
}

.btn-primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 22px rgba(99, 102, 241, 0.5);
  background: linear-gradient(135deg, #7175f3 0%, #5850ec 100%);
}

.btn-primary:active {
  transform: translateY(0);
}

.btn-secondary {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  color: #fff;
  box-shadow: 0 4px 16px rgba(16, 185, 129, 0.35);
}

.btn-secondary:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(16, 185, 129, 0.45);
}

.btn-outline {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border-glass);
  color: var(--text-main);
}

.btn-outline:hover {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.2);
}

.btn-download {
  width: 100%;
  padding: 15px;
  margin-top: 18px;
  font-size: 1.05rem;
  background: linear-gradient(135deg, #f43f5e 0%, #e11d48 50%, #be123c 100%);
  color: #fff;
  box-shadow: 0 6px 20px rgba(244, 63, 94, 0.4);
}

.btn-download:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 28px rgba(244, 63, 94, 0.55);
}



/* ==========================================================================
   Preview Card
   ========================================================================== */

.preview-card {
  padding: 24px;
  margin-bottom: 24px;
  animation: slideUp 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}

.preview-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 24px;
  align-items: start;
}

.thumbnail-wrapper {
  position: relative;
  border-radius: var(--radius-md);
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  aspect-ratio: 16 / 9;
  background: #000;
}

#videoThumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.4s ease;
}

.thumbnail-wrapper:hover #videoThumb {
  transform: scale(1.04);
}

.duration-badge {
  position: absolute;
  bottom: 10px;
  right: 10px;
  background: rgba(0, 0, 0, 0.8);
  backdrop-filter: blur(4px);
  color: #fff;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.15);
}

.video-meta {
  margin-bottom: 18px;
}

.video-title {
  font-family: var(--font-heading);
  font-size: 1.25rem;
  font-weight: 700;
  line-height: 1.35;
  margin-bottom: 8px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.meta-row {
  display: flex;
  gap: 16px;
  font-size: 0.85rem;
  color: var(--text-muted);
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.channel-name {
  color: #a5b4fc;
  font-weight: 500;
}

/* Tabs & Options */
.section-label {
  display: block;
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  margin-bottom: 8px;
}

.quality-label {
  margin-top: 14px;
}

.tab-group {
  display: flex;
  gap: 8px;
  background: rgba(0, 0, 0, 0.25);
  padding: 4px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-glass);
}

.tab-btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-family: var(--font-body);
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.tab-btn.active {
  background: rgba(99, 102, 241, 0.25);
  color: #fff;
  border: 1px solid rgba(99, 102, 241, 0.5);
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.2);
}

.quality-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 8px;
}

.quality-pill {
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border-glass);
  color: var(--text-muted);
  font-size: 0.82rem;
  font-weight: 600;
  text-align: center;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.quality-pill:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--text-main);
  border-color: rgba(255, 255, 255, 0.2);
}

.quality-pill.active {
  background: linear-gradient(135deg, rgba(6, 182, 212, 0.25), rgba(99, 102, 241, 0.25));
  border-color: var(--accent-secondary);
  color: #fff;
  box-shadow: 0 0 12px rgba(6, 182, 212, 0.2);
}

.quality-pill .sub-tag {
  display: block;
  font-size: 0.68rem;
  color: var(--text-dim);
  font-weight: 400;
  margin-top: 2px;
}

.quality-pill.active .sub-tag {
  color: #67e8f9;
}

/* ==========================================================================
   Progress Card
   ========================================================================== */

.progress-card {
  padding: 24px;
  margin-bottom: 24px;
  animation: slideUp 0.35s ease forwards;
}

.progress-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.progress-status-info {
  display: flex;
  align-items: center;
  gap: 14px;
}

.status-indicator-ring {
  width: 36px;
  height: 36px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ring-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid rgba(99, 102, 241, 0.2);
  border-top-color: var(--accent-primary);
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}

.progress-title {
  font-family: var(--font-heading);
  font-size: 1.15rem;
  font-weight: 700;
}

.progress-subtitle {
  font-size: 0.85rem;
  color: var(--text-muted);
}

.progress-badge {
  font-family: var(--font-heading);
  font-size: 1.5rem;
  font-weight: 800;
  color: #38bdf8;
}

.progress-track {
  width: 100%;
  height: 10px;
  background: rgba(0, 0, 0, 0.4);
  border-radius: var(--radius-full);
  overflow: hidden;
  position: relative;
  border: 1px solid var(--border-glass);
  margin-bottom: 16px;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #6366f1, #06b6d4, #10b981);
  background-size: 200% 100%;
  border-radius: var(--radius-full);
  transition: width 0.3s ease;
  animation: shimmerProgress 2s linear infinite;
  box-shadow: 0 0 14px rgba(6, 182, 212, 0.5);
}

@keyframes shimmerProgress {
  0% { background-position: 100% 0; }
  100% { background-position: -100% 0; }
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  background: rgba(0, 0, 0, 0.2);
  border-radius: var(--radius-md);
  padding: 12px 16px;
  border: 1px solid var(--border-glass);
}

.metric-item {
  display: flex;
  flex-direction: column;
}

.metric-label {
  font-size: 0.72rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-weight: 600;
}

.metric-val {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--text-main);
  margin-top: 2px;
}

/* Success Actions */
.success-actions {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid var(--border-glass);
  animation: fadeIn 0.4s ease;
}

.success-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.3);
  color: #34d399;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 0.9rem;
  font-weight: 600;
  margin-bottom: 16px;
}

.action-buttons {
  display: flex;
  gap: 12px;
}

/* ==========================================================================
   History Section
   ========================================================================== */

.history-section {
  position: relative;
  z-index: 5;
  margin-top: 36px;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.history-header h3 {
  font-family: var(--font-heading);
  font-size: 1.15rem;
  font-weight: 700;
}

.btn-text-action {
  background: transparent;
  border: none;
  color: var(--text-dim);
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  transition: color var(--transition-fast);
}

.btn-text-action:hover {
  color: var(--accent-yt);
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg-card);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-md);
  padding: 12px 18px;
  transition: all var(--transition-fast);
}

.history-item:hover {
  background: var(--bg-card-hover);
  border-color: rgba(255, 255, 255, 0.16);
}

.history-item-left {
  display: flex;
  align-items: center;
  gap: 12px;
  overflow: hidden;
}

.history-badge {
  font-size: 0.72rem;
  font-weight: 700;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(99, 102, 241, 0.18);
  color: #a5b4fc;
}

.history-badge.audio {
  background: rgba(244, 63, 94, 0.18);
  color: #fda4af;
}

.history-title {
  font-size: 0.88rem;
  font-weight: 500;
  color: var(--text-main);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 480px;
}

.history-size {
  font-size: 0.78rem;
  color: var(--text-dim);
}

.history-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border-glass);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 6px 12px;
  font-size: 0.8rem;
  text-decoration: none;
  cursor: pointer;
  transition: all var(--transition-fast);
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.history-btn:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}

/* ==========================================================================
   Toast Notification System
   ========================================================================== */

.toast-container {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 999;
  display: flex;
  flex-direction: column;
  gap: 10px;
  pointer-events: none;
}

.toast {
  pointer-events: auto;
  min-width: 280px;
  max-width: 420px;
  background: rgba(15, 23, 42, 0.95);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-md);
  padding: 12px 18px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
  color: var(--text-main);
  font-size: 0.88rem;
  display: flex;
  align-items: center;
  gap: 12px;
  animation: toastIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

.toast.toast-error {
  border-color: rgba(244, 63, 94, 0.4);
  background: rgba(30, 10, 15, 0.95);
}

.toast.toast-success {
  border-color: rgba(16, 185, 129, 0.4);
}

@keyframes toastIn {
  from { opacity: 0; transform: translateY(12px) scale(0.95); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

@keyframes toastOut {
  from { opacity: 1; transform: translateY(0) scale(1); }
  to { opacity: 0; transform: translateY(12px) scale(0.95); }
}

/* ==========================================================================
   Footer
   ========================================================================== */

.app-footer {
  position: relative;
  z-index: 5;
  margin-top: auto;
  padding: 32px 0 24px;
  text-align: center;
  color: var(--text-dim);
  font-size: 0.82rem;
}

.app-footer strong {
  color: var(--text-muted);
}

/* Utilities */
.hidden {
  display: none !important;
}

.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

/* Responsive */
@media (max-width: 768px) {
  .container {
    padding-left: 14px;
    padding-right: 14px;
  }

  .app-header {
    padding: 16px 0 10px;
  }

  .brand-name {
    font-size: 1.3rem;
  }

  .hero-section {
    padding: 20px 0 18px;
  }

  .hero-title {
    font-size: 1.85rem;
    line-height: 1.2;
    margin-bottom: 10px;
  }

  .hero-subtitle {
    font-size: 0.88rem;
  }

  .input-card {
    padding: 14px;
  }

  .url-form {
    flex-direction: column;
    gap: 10px;
  }

  .input-wrapper {
    width: 100%;
    min-width: 0;
  }

  #urlInput {
    font-size: 0.88rem;
    padding: 12px 6px 12px 4px;
  }

  .input-icon {
    padding-left: 10px;
  }

  .btn {
    width: 100%;
    padding: 12px 18px;
  }

  .preview-card {
    padding: 14px;
  }

  .preview-layout {
    grid-template-columns: 1fr;
    gap: 16px;
  }

  .thumbnail-wrapper {
    max-width: 100%;
  }

  .video-title {
    font-size: 1.05rem;
  }

  .quality-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .metrics-grid {
    grid-template-columns: 1fr;
    gap: 8px;
    padding: 10px 12px;
  }

  .action-buttons {
    flex-direction: column;
  }
}

@media (max-width: 480px) {
  .container {
    padding-left: 10px;
    padding-right: 10px;
  }

  .hero-title {
    font-size: 1.6rem;
  }

  .status-badge {
    font-size: 0.68rem;
    padding: 4px 8px;
  }

  .btn-icon {
    padding: 5px 8px;
    margin-right: 6px;
    font-size: 0.72rem;
  }

  .btn-icon svg {
    width: 14px;
    height: 14px;
  }

  #urlInput {
    font-size: 0.82rem;
  }

  #urlInput::placeholder {
    font-size: 0.8rem;
  }

  .quality-pill {
    padding: 7px 8px;
    font-size: 0.78rem;
  }
}

</style>
</head>
<body>
  <!-- Background ambient glows -->
  <div class="ambient-glow glow-1"></div>
  <div class="ambient-glow glow-2"></div>
  <div class="ambient-glow glow-3"></div>

  <!-- Header -->
  <header class="app-header">
    <div class="header-content container">
      <div class="brand">
        <div class="brand-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="5 3 19 12 5 21 5 3"></polygon>
          </svg>
        </div>
        <span class="brand-name">Pulse<span class="accent-text">DL</span></span>
      </div>
      <div class="header-badges">
        <span class="status-badge pulse-badge">
          <span class="pulse-dot"></span>
          FFmpeg Engine Ready
        </span>
      </div>
    </div>
  </header>

  <main class="main-content container">
    <!-- Hero Section -->
    <section class="hero-section">
      <div class="hero-tag">
        <span class="sparkle-icon">✨</span> Free • No Ads • 4K & MP3
      </div>
      <h1 class="hero-title">Download YouTube Videos <span class="gradient-text">In Pure Quality</span></h1>
      <p class="hero-subtitle">
        Paste any YouTube or Shorts link below to fetch metadata, choose your preferred resolution or audio format, and download directly to your device.
      </p>
    </section>

    <!-- URL Input Card -->
    <section class="card input-card">
      <form id="urlForm" class="url-form" onsubmit="event.preventDefault();">
        <div class="input-wrapper">
          <div class="input-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>
            </svg>
          </div>
          <input 
            type="url" 
            id="urlInput" 
            placeholder="Paste YouTube video or Shorts link here..." 
            autocomplete="off" 
            spellcheck="false"
            required
          />
          <button type="button" id="pasteBtn" class="btn-icon" title="Paste from clipboard">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="8" y="2" width="8" height="4" rx="1" ry="1"></rect>
              <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path>
            </svg>
            <span>Paste</span>
          </button>
          <button type="button" id="clearBtn" class="btn-icon hidden" title="Clear input">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>

        <button type="submit" id="analyzeBtn" class="btn btn-primary">
          <span class="btn-label">Analyze Video</span>
          <span class="btn-icon-right">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
          </span>
          <span class="spinner hidden"></span>
        </button>
      </form>

    </section>

    <!-- Video Preview & Format Selection Card (Hidden initially) -->
    <section id="previewCard" class="card preview-card hidden">
      <div class="preview-layout">
        <!-- Thumbnail Column -->
        <div class="preview-media">
          <div class="thumbnail-wrapper">
            <img id="videoThumb" src="" alt="Video Thumbnail" loading="lazy" />
            <span id="videoDuration" class="duration-badge">00:00</span>
          </div>
        </div>

        <!-- Info & Config Column -->
        <div class="preview-details">
          <div class="video-meta">
            <h2 id="videoTitle" class="video-title">Fetching video details...</h2>
            <div class="meta-row">
              <span id="channelName" class="meta-item channel-name">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                  <circle cx="12" cy="7" r="4"></circle>
                </svg>
                Channel
              </span>
              <span id="viewCount" class="meta-item">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                  <circle cx="12" cy="12" r="3"></circle>
                </svg>
                0 views
              </span>
            </div>
          </div>

          <!-- Format Choice Tabs -->
          <div class="format-section">
            <label class="section-label">Select Output Type</label>
            <div class="tab-group">
              <button type="button" class="tab-btn active" data-type="video">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polygon points="23 7 16 12 23 17 23 7"></polygon>
                  <rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect>
                </svg>
                Video (MP4)
              </button>
              <button type="button" class="tab-btn" data-type="audio">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M9 18V5l12-2v13"></path>
                  <circle cx="6" cy="18" r="3"></circle>
                  <circle cx="18" cy="16" r="3"></circle>
                </svg>
                Audio (MP3)
              </button>
            </div>

            <!-- Quality Pills Container -->
            <label class="section-label quality-label">Select Quality</label>
            <div id="qualityOptions" class="quality-grid">
              <!-- Dynamically populated -->
            </div>
          </div>

          <!-- Download Action Button -->
          <button type="button" id="startDownloadBtn" class="btn btn-download">
            <span class="btn-icon-left">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
            </span>
            <span class="btn-text">Download Now</span>
          </button>
        </div>
      </div>
    </section>

    <!-- Real-time Progress Card (Hidden until download starts) -->
    <section id="progressCard" class="card progress-card hidden">
      <div class="progress-header">
        <div class="progress-status-info">
          <div class="status-indicator-ring">
            <div class="ring-spinner"></div>
          </div>
          <div>
            <h3 id="progressStatusText" class="progress-title">Starting Download...</h3>
            <p id="progressDetailText" class="progress-subtitle">Connecting to high-speed streams...</p>
          </div>
        </div>
        <div id="progressPercentage" class="progress-badge">0%</div>
      </div>

      <!-- Animated Progress Bar -->
      <div class="progress-track">
        <div id="progressBar" class="progress-fill" style="width: 0%;"></div>
      </div>

      <!-- Live Download Metrics -->
      <div class="metrics-grid">
        <div class="metric-item">
          <span class="metric-label">Speed</span>
          <span id="metricSpeed" class="metric-val">-- MB/s</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">Downloaded</span>
          <span id="metricDownloaded" class="metric-val">0 / 0 MB</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">Time Remaining</span>
          <span id="metricEta" class="metric-val">--</span>
        </div>
      </div>

      <!-- Success State Actions (Hidden during download) -->
      <div id="successActions" class="success-actions hidden">
        <div class="success-banner">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
            <polyline points="22 4 12 14.01 9 11.01"></polyline>
          </svg>
          <span>Download finished! File has been saved to your downloads.</span>
        </div>
        <div class="action-buttons">
          <a id="saveFileLink" href="#" class="btn btn-secondary" download>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            Save File Again
          </a>
          <button type="button" id="resetBtn" class="btn btn-outline">
            Download Another Video
          </button>
        </div>
      </div>
    </section>

    <!-- Session Download History -->
    <section id="historySection" class="history-section hidden">
      <div class="history-header">
        <h3>Recent Downloads</h3>
        <button type="button" id="clearHistoryBtn" class="btn-text-action">Clear</button>
      </div>
      <div id="historyList" class="history-list">
        <!-- Dynamically populated -->
      </div>
    </section>
  </main>

  <!-- Toast Notification Container -->
  <div id="toastContainer" class="toast-container" aria-live="polite"></div>

  <footer class="app-footer container">
    <p>Powered by <strong>yt-dlp</strong> &amp; <strong>FFmpeg</strong>. Local processing with zero external ads.</p>
  </footer>

  <script>
// ==========================================================================
// PulseDL Frontend Controller
// ==========================================================================

document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const urlForm = document.getElementById('urlForm');
  const urlInput = document.getElementById('urlInput');
  const pasteBtn = document.getElementById('pasteBtn');
  const clearBtn = document.getElementById('clearBtn');
  const analyzeBtn = document.getElementById('analyzeBtn');

  // Preview elements
  const previewCard = document.getElementById('previewCard');
  const videoThumb = document.getElementById('videoThumb');
  const videoDuration = document.getElementById('videoDuration');
  const videoTitle = document.getElementById('videoTitle');
  const channelName = document.getElementById('channelName');
  const viewCount = document.getElementById('viewCount');
  const tabBtns = document.querySelectorAll('.tab-btn');
  const qualityOptions = document.getElementById('qualityOptions');
  const startDownloadBtn = document.getElementById('startDownloadBtn');

  // Progress elements
  const progressCard = document.getElementById('progressCard');
  const progressStatusText = document.getElementById('progressStatusText');
  const progressDetailText = document.getElementById('progressDetailText');
  const progressPercentage = document.getElementById('progressPercentage');
  const progressBar = document.getElementById('progressBar');
  const metricSpeed = document.getElementById('metricSpeed');
  const metricDownloaded = document.getElementById('metricDownloaded');
  const metricEta = document.getElementById('metricEta');
  const successActions = document.getElementById('successActions');
  const saveFileLink = document.getElementById('saveFileLink');
  const resetBtn = document.getElementById('resetBtn');

  // History elements
  const historySection = document.getElementById('historySection');
  const historyList = document.getElementById('historyList');
  const clearHistoryBtn = document.getElementById('clearHistoryBtn');
  const toastContainer = document.getElementById('toastContainer');

  // App State
  let currentVideo = null;
  let selectedFormatType = 'video';
  let selectedQuality = 'best';
  let activePollInterval = null;
  let downloadHistory = JSON.parse(sessionStorage.getItem('pulse_history') || '[]');

  // Initialize
  renderHistory();

  // URL Input event listeners
  urlInput.addEventListener('input', () => {
    if (urlInput.value.trim().length > 0) {
      clearBtn.classList.remove('hidden');
    } else {
      clearBtn.classList.add('hidden');
    }
  });

  clearBtn.addEventListener('click', () => {
    urlInput.value = '';
    clearBtn.classList.add('hidden');
    urlInput.focus();
  });

  // Paste from clipboard
  pasteBtn.addEventListener('click', async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text && text.trim()) {
        urlInput.value = text.trim();
        clearBtn.classList.remove('hidden');
        showToast('URL pasted from clipboard', 'success');
        analyzeVideo(text.trim());
      } else {
        showToast('Clipboard is empty', 'info');
      }
    } catch (err) {
      urlInput.focus();
      showToast('Please press Ctrl+V to paste your link', 'info');
    }
  });



  // Form submission (Analyze)
  urlForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const url = urlInput.value.trim();
    if (!url) {
      showToast('Please enter a YouTube video URL', 'error');
      return;
    }
    analyzeVideo(url);
  });

  // Analyze video function
  async function analyzeVideo(url) {
    setAnalyzeLoading(true);
    previewCard.classList.add('hidden');
    progressCard.classList.add('hidden');

    try {
      const res = await fetch('/api/info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Could not fetch video info');
      }

      currentVideo = data;
      renderPreview(data);
      previewCard.classList.remove('hidden');
      previewCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (err) {
      showToast(err.message || 'Failed to fetch video details', 'error');
    } finally {
      setAnalyzeLoading(false);
    }
  }

  function setAnalyzeLoading(isLoading) {
    const spinner = analyzeBtn.querySelector('.spinner');
    const iconRight = analyzeBtn.querySelector('.btn-icon-right');
    const label = analyzeBtn.querySelector('.btn-label');

    if (isLoading) {
      analyzeBtn.disabled = true;
      spinner.classList.remove('hidden');
      iconRight.classList.add('hidden');
      label.textContent = 'Analyzing...';
    } else {
      analyzeBtn.disabled = false;
      spinner.classList.add('hidden');
      iconRight.classList.remove('hidden');
      label.textContent = 'Analyze Video';
    }
  }

  // Render Video Preview
  function renderPreview(info) {
    videoThumb.src = info.thumbnail || '';
    videoDuration.textContent = info.duration_str || '00:00';
    videoTitle.textContent = info.title || 'Untitled Video';
    channelName.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
        <circle cx="12" cy="7" r="4"></circle>
      </svg>
      ${escapeHtml(info.channel || 'Channel')}
    `;
    viewCount.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8z"></path>
        <circle cx="12" cy="12" r="3"></circle>
      </svg>
      ${info.view_count_str} views
    `;

    renderQualityOptions();
  }

  // Format type tab switching
  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      selectedFormatType = btn.getAttribute('data-type');
      renderQualityOptions();
    });
  });

  // Render Quality Options dynamically based on type and detected resolutions
  function renderQualityOptions() {
    qualityOptions.innerHTML = '';

    if (selectedFormatType === 'video') {
      const resolutions = currentVideo?.resolutions || [];
      const presets = [
        { id: 'best', label: 'Best Quality', sub: 'Max Available' },
        { id: '1080', label: '1080p FHD', sub: 'High Definition' },
        { id: '720', label: '720p HD', sub: 'Standard HD' },
        { id: '480', label: '480p SD', sub: 'Medium' },
        { id: '360', label: '360p', sub: 'Data Saver' },
      ];

      // Filter or highlight available options
      const availableItems = presets.filter(p => {
        if (p.id === 'best') return true;
        const h = parseInt(p.id);
        // Include if any format has resolution >= this or close
        return resolutions.length === 0 || resolutions.some(r => r >= h);
      });

      selectedQuality = availableItems[0]?.id || 'best';

      availableItems.forEach(item => {
        const pill = document.createElement('div');
        pill.className = `quality-pill ${item.id === selectedQuality ? 'active' : ''}`;
        pill.innerHTML = `
          <span>${item.label}</span>
          <span class="sub-tag">${item.sub}</span>
        `;
        pill.addEventListener('click', () => {
          document.querySelectorAll('.quality-pill').forEach(p => p.classList.remove('active'));
          pill.classList.add('active');
          selectedQuality = item.id;
        });
        qualityOptions.appendChild(pill);
      });

    } else {
      // Audio MP3 Bitrates
      const audioPresets = [
        { id: '320', label: '320 kbps', sub: 'Studio Audio' },
        { id: '192', label: '192 kbps', sub: 'High Quality' },
        { id: '128', label: '128 kbps', sub: 'Standard' },
      ];

      selectedQuality = '320';

      audioPresets.forEach(item => {
        const pill = document.createElement('div');
        pill.className = `quality-pill ${item.id === selectedQuality ? 'active' : ''}`;
        pill.innerHTML = `
          <span>${item.label}</span>
          <span class="sub-tag">${item.sub}</span>
        `;
        pill.addEventListener('click', () => {
          document.querySelectorAll('.quality-pill').forEach(p => p.classList.remove('active'));
          pill.classList.add('active');
          selectedQuality = item.id;
        });
        qualityOptions.appendChild(pill);
      });
    }
  }

  // Start Download
  startDownloadBtn.addEventListener('click', async () => {
    if (!currentVideo) return;

    startDownloadBtn.disabled = true;
    progressCard.classList.remove('hidden');
    successActions.classList.add('hidden');
    progressCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    // Reset progress UI
    updateProgressUI({
      status: 'starting',
      progress: 0,
      speed: 'Connecting...',
      eta: '--',
      downloaded_str: '0 MB',
      total_str: 'Calculating...',
    });

    try {
      const res = await fetch('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: currentVideo.webpage_url,
          format_type: selectedFormatType,
          quality: selectedQuality,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Could not start download');
      }

      const taskId = data.task_id;
      pollDownloadProgress(taskId);
    } catch (err) {
      showToast(err.message || 'Failed to start download', 'error');
      startDownloadBtn.disabled = false;
      progressCard.classList.add('hidden');
    }
  });

  // Poll progress
  function pollDownloadProgress(taskId) {
    if (activePollInterval) clearInterval(activePollInterval);

    activePollInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/progress/${taskId}`);
        if (!res.ok) {
          clearInterval(activePollInterval);
          throw new Error('Task lost');
        }

        const task = await res.json();
        updateProgressUI(task);

        if (task.status === 'completed') {
          clearInterval(activePollInterval);
          onDownloadFinished(taskId, task);
        } else if (task.status === 'failed') {
          clearInterval(activePollInterval);
          onDownloadFailed(task.error);
        }
      } catch (err) {
        clearInterval(activePollInterval);
        onDownloadFailed('Connection lost with download server');
      }
    }, 600);
  }

  function updateProgressUI(task) {
    const pct = Math.min(100, Math.max(0, task.progress || 0));
    progressBar.style.width = `${pct}%`;
    progressPercentage.textContent = `${Math.round(pct)}%`;

    if (task.status === 'starting' || task.status === 'queued') {
      progressStatusText.textContent = 'Queueing Stream...';
      progressDetailText.textContent = 'Resolving video manifest and audio streams...';
    } else if (task.status === 'downloading') {
      progressStatusText.textContent = selectedFormatType === 'audio' ? 'Downloading Audio Stream...' : 'Downloading Video & Audio...';
      progressDetailText.textContent = `Streaming chunks (${task.downloaded_str || '0 MB'} of ${task.total_str || '...'})`;
    } else if (task.status === 'processing') {
      progressStatusText.textContent = 'Processing & Muxing with FFmpeg...';
      progressDetailText.textContent = 'Merging video and audio into high-fidelity container...';
    } else if (task.status === 'completed') {
      progressStatusText.textContent = 'Download Complete!';
      progressDetailText.textContent = `Ready: ${task.filename} (${task.file_size_str || ''})`;
      progressBar.style.width = '100%';
      progressPercentage.textContent = '100%';
    }

    metricSpeed.textContent = task.speed || '-- MB/s';
    metricDownloaded.textContent = `${task.downloaded_str || '0 MB'} / ${task.total_str || '0 MB'}`;
    metricEta.textContent = task.eta ? `${task.eta} left` : '--';
  }

  function onDownloadFinished(taskId, task) {
    startDownloadBtn.disabled = false;
    const downloadUrl = `/api/file/${taskId}`;
    saveFileLink.href = downloadUrl;
    saveFileLink.setAttribute('download', task.filename || 'download');

    // Trigger automatic browser save
    const trigger = document.createElement('a');
    trigger.href = downloadUrl;
    trigger.setAttribute('download', task.filename || 'download');
    document.body.appendChild(trigger);
    trigger.click();
    document.body.removeChild(trigger);

    showToast('Download ready! File saved.', 'success');
    successActions.classList.remove('hidden');

    // Add to session history
    addToHistory({
      title: task.filename || currentVideo.title,
      format: selectedFormatType.toUpperCase(),
      size: task.file_size_str || '',
      taskId: taskId,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    });
  }

  function onDownloadFailed(errorMsg) {
    startDownloadBtn.disabled = false;
    progressStatusText.textContent = 'Download Failed';
    progressDetailText.textContent = errorMsg || 'An error occurred during extraction.';
    showToast(errorMsg || 'Download failed', 'error');
  }

  // Reset for another video
  resetBtn.addEventListener('click', () => {
    progressCard.classList.add('hidden');
    urlInput.value = '';
    clearBtn.classList.add('hidden');
    urlInput.focus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // History management
  function addToHistory(item) {
    downloadHistory.unshift(item);
    if (downloadHistory.length > 8) downloadHistory.pop();
    sessionStorage.setItem('pulse_history', JSON.stringify(downloadHistory));
    renderHistory();
  }

  function renderHistory() {
    if (!downloadHistory || downloadHistory.length === 0) {
      historySection.classList.add('hidden');
      return;
    }

    historySection.classList.remove('hidden');
    historyList.innerHTML = '';

    downloadHistory.forEach(item => {
      const el = document.createElement('div');
      el.className = 'history-item';
      el.innerHTML = `
        <div class="history-item-left">
          <span class="history-badge ${item.format === 'AUDIO' ? 'audio' : ''}">${item.format}</span>
          <span class="history-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</span>
          <span class="history-size">${item.size ? '• ' + item.size : ''}</span>
        </div>
        <a href="/api/file/${item.taskId}" class="history-btn" download>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          Download
        </a>
      `;
      historyList.appendChild(el);
    });
  }

  clearHistoryBtn.addEventListener('click', () => {
    downloadHistory = [];
    sessionStorage.removeItem('pulse_history');
    renderHistory();
  });

  // Toast Notification
  function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    let iconSvg = '';
    if (type === 'success') {
      iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
    } else if (type === 'error') {
      iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f43f5e" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>`;
    } else {
      iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`;
    }

    toast.innerHTML = `
      ${iconSvg}
      <span>${escapeHtml(message)}</span>
    `;

    toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.style.animation = 'toastOut 0.3s ease forwards';
      setTimeout(() => {
        if (toastContainer.contains(toast)) {
          toastContainer.removeChild(toast);
        }
      }, 300);
    }, 3500);
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
});

</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTMLResponse(content=FRONTEND_HTML, status_code=200)

@app.get("/healthz")
def health_check():
    return {"status": "ok"}

@app.get("/.well-known/{path:path}")
def well_known_probe(path: str):
    return {}

def get_cookie_file():
    # 1. Check local cookies.txt file
    local_cookie = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    if os.path.exists(local_cookie) and os.path.getsize(local_cookie) > 0:
        return local_cookie

    # 2. Check environment variable YTDLP_COOKIES (for Render dashboard)
    raw_cookies = os.environ.get("YTDLP_COOKIES")
    if raw_cookies:
        temp_cookie_path = os.path.join(tempfile.gettempdir(), "youtube_cookies.txt")
        try:
            with open(temp_cookie_path, "w", encoding="utf-8") as f:
                f.write(raw_cookies)
            return temp_cookie_path
        except Exception:
            pass

    return None

@app.post("/api/info")
def get_video_info(req: InfoRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    ydl_opts: Dict[str, Any] = {
        "skip_download": True,
        "extract_flat": False,
        "js_runtimes": {"node": {}},
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "web"]
            }
        },
    }

    cookie_file = get_cookie_file()
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        err_msg = str(e)
        if "Sign in to confirm" in err_msg or "bot" in err_msg.lower():
            raise HTTPException(
                status_code=400,
                detail="YouTube datacenter bot-check triggered. Add your YTDLP_COOKIES environment variable in Render, or run locally using 'python main.py'."
            )
        raise HTTPException(status_code=400, detail=f"Failed to fetch video: {err_msg}")

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
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "web"]
            }
        },
    }

    cookie_file = get_cookie_file()
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

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
    import uvicorn
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

        if not is_cloud:
            threading.Thread(target=open_browser, args=(port,), daemon=True).start()

        uvicorn.run("main:app", host=host, port=port, reload=False)
