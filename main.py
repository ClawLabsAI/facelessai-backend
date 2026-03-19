"""
FacelessAI Backend — Video Generation Server
FastAPI + FFmpeg + Pexels
Generates real MP4 videos from script + audio + stock clips
"""

import os
import re
import uuid
import httpx
import asyncio
import tempfile
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="FacelessAI Video Generator", version="1.0")

# Allow requests from GitHub Pages and localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Temp directory for generated files
TEMP_DIR = Path(tempfile.gettempdir()) / "facelessai"
TEMP_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────
# REQUEST MODELS
# ─────────────────────────────────────────

class VideoRequest(BaseModel):
    audio_url: str              # URL of the MP3 audio (blob URL from browser won't work — must be real URL)
    audio_b64: Optional[str]    # Base64 encoded audio (alternative to URL)
    pexels_clips: list[str]     # List of Pexels video URLs
    script: str                 # Full script text for subtitles
    title: str                  # Video title
    lang: str = "es"            # Language: es, en, lat
    ratio: str = "9:16"         # Aspect ratio
    resolution: str = "1080x1920"
    fps: int = 30
    subtitle_style: str = "viral"  # viral, minimal, classic


class StatusResponse(BaseModel):
    job_id: str
    status: str   # pending, processing, done, error
    progress: int
    message: str
    download_url: Optional[str] = None


# Job tracking
jobs: dict = {}

# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "FacelessAI Video Generator",
        "version": "1.0",
        "status": "running",
        "ffmpeg": check_ffmpeg()
    }

@app.get("/health")
async def health():
    return {"status": "ok", "ffmpeg": check_ffmpeg()}

def check_ffmpeg():
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        return "available" if result.returncode == 0 else "not found"
    except FileNotFoundError:
        return "not installed"


# ─────────────────────────────────────────
# VIDEO GENERATION ENDPOINT
# ─────────────────────────────────────────

@app.post("/generate", response_model=StatusResponse)
async def generate_video(req: VideoRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "pending", "progress": 0, "message": "Iniciando...", "download_url": None}
    background_tasks.add_task(process_video, job_id, req)
    return StatusResponse(job_id=job_id, status="pending", progress=0, message="Job creado — procesando...")


@app.get("/status/{job_id}", response_model=StatusResponse)
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    j = jobs[job_id]
    return StatusResponse(
        job_id=job_id,
        status=j["status"],
        progress=j["progress"],
        message=j["message"],
        download_url=j.get("download_url")
    )


@app.get("/download/{job_id}")
async def download_video(job_id: str):
    if job_id not in jobs or jobs[job_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="Vídeo no disponible aún")
    filepath = TEMP_DIR / f"{job_id}_output.mp4"
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(
        path=str(filepath),
        media_type="video/mp4",
        filename=f"facelessai_{job_id}.mp4"
    )


# ─────────────────────────────────────────
# CORE VIDEO PROCESSING
# ─────────────────────────────────────────

async def process_video(job_id: str, req: VideoRequest):
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    def update(status, progress, message):
        jobs[job_id] = {"status": status, "progress": progress, "message": message, "download_url": jobs[job_id].get("download_url")}

    try:
        # ── STEP 1: Download / save audio ──
        update("processing", 10, "Descargando audio...")
        audio_path = job_dir / "audio.mp3"

        if req.audio_b64:
            import base64
            audio_data = base64.b64decode(req.audio_b64)
            audio_path.write_bytes(audio_data)
        elif req.audio_url.startswith("http"):
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(req.audio_url)
                audio_path.write_bytes(r.content)
        else:
            raise ValueError("Necesitas audio_url (http) o audio_b64")

        # Get audio duration
        duration = get_audio_duration(str(audio_path))
        update("processing", 20, f"Audio: {duration:.1f}s descargado")

        # ── STEP 2: Download video clips ──
        update("processing", 30, f"Descargando {len(req.pexels_clips)} clips...")
        clip_paths = []

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            for i, clip_url in enumerate(req.pexels_clips[:6]):  # max 6 clips
                try:
                    r = await client.get(clip_url)
                    clip_path = job_dir / f"clip_{i}.mp4"
                    clip_path.write_bytes(r.content)
                    clip_paths.append(str(clip_path))
                    update("processing", 30 + i * 5, f"Clip {i+1}/{len(req.pexels_clips)} descargado")
                except Exception as e:
                    print(f"Error downloading clip {i}: {e}")

        if not clip_paths:
            raise ValueError("No se pudieron descargar clips de Pexels")

        update("processing", 55, f"{len(clip_paths)} clips listos — preparando montaje...")

        # ── STEP 3: Process clips — crop to 9:16, normalize ──
        processed_clips = []
        clip_duration = duration / len(clip_paths)

        for i, clip_path in enumerate(clip_paths):
            out_path = job_dir / f"proc_{i}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-i", clip_path,
                "-t", str(clip_duration),
                "-vf", (
                    # Crop to 9:16 ratio (vertical), scale to 1080x1920
                    "crop=in_h*9/16:in_h,"
                    "scale=1080:1920:force_original_aspect_ratio=increase,"
                    "crop=1080:1920,"
                    # Zoom effect: subtle ken burns
                    f"zoompan=z='min(zoom+0.0015,1.5)':d={int(clip_duration*30)}:s=1080x1920"
                ),
                "-r", str(req.fps),
                "-an",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                str(out_path)
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            processed_clips.append(str(out_path))

        update("processing", 65, "Clips procesados — concatenando...")

        # ── STEP 4: Concatenate clips ──
        concat_list = job_dir / "concat.txt"
        concat_list.write_text("\n".join([f"file '{p}'" for p in processed_clips]))

        concat_path = job_dir / "concat.mp4"
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(concat_path)
        ], capture_output=True, check=True)

        update("processing", 72, "Clips concatenados — generando subtítulos...")

        # ── STEP 5: Generate SRT subtitles from script ──
        srt_path = job_dir / "subs.srt"
        generate_srt(req.script, duration, str(srt_path))

        update("processing", 78, "Subtítulos generados — montaje final...")

        # ── STEP 6: Final composition — concat + audio + subtitles ──
        output_path = TEMP_DIR / f"{job_id}_output.mp4"

        # Subtitle style
        subtitle_filter = get_subtitle_filter(str(srt_path), req.subtitle_style)

        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(concat_path),
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-t", str(duration),
            "-vf", subtitle_filter,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "21",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-movflags", "+faststart",
            str(output_path)
        ], capture_output=True, check=True)

        # ── DONE ──
        file_size_mb = output_path.stat().st_size / 1024 / 1024
        jobs[job_id]["status"] = "done"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["message"] = f"✅ Vídeo listo — {duration:.1f}s · {file_size_mb:.1f}MB"
        jobs[job_id]["download_url"] = f"/download/{job_id}"

        # Cleanup temp files (keep output)
        import shutil
        shutil.rmtree(str(job_dir), ignore_errors=True)

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if e.stderr else str(e)
        jobs[job_id] = {"status": "error", "progress": 0, "message": f"FFmpeg error: {err[-200:]}", "download_url": None}
    except Exception as e:
        jobs[job_id] = {"status": "error", "progress": 0, "message": f"Error: {str(e)}", "download_url": None}


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def get_audio_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True
    )
    import json
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def generate_srt(script: str, duration: float, output_path: str):
    """Split script into subtitle chunks and generate SRT file"""
    # Clean script — remove tags and annotations
    clean = re.sub(r'\[.*?\]', '', script)
    clean = re.sub(r'#\w+', '', clean)
    clean = re.sub(r'//.*', '', clean)
    clean = ' '.join(clean.split())

    # Split into chunks of ~5 words
    words = clean.split()
    chunk_size = 5
    chunks = [' '.join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]

    if not chunks:
        chunks = ["FacelessAI"]

    time_per_chunk = duration / len(chunks)
    srt_content = ""

    for i, chunk in enumerate(chunks):
        start = i * time_per_chunk
        end = (i + 1) * time_per_chunk
        srt_content += f"{i+1}\n{format_time(start)} --> {format_time(end)}\n{chunk.upper()}\n\n"

    Path(output_path).write_text(srt_content, encoding='utf-8')


def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def get_subtitle_filter(srt_path: str, style: str) -> str:
    """Return FFmpeg subtitle filter string based on style"""
    # Escape path for FFmpeg
    safe_path = srt_path.replace('\\', '/').replace(':', '\\:')

    if style == "viral":
        return (
            f"subtitles='{safe_path}':force_style='"
            "FontName=Impact,FontSize=22,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,Outline=3,Shadow=2,"
            "Alignment=2,MarginV=60,"
            "Bold=1'"
        )
    elif style == "minimal":
        return (
            f"subtitles='{safe_path}':force_style='"
            "FontName=Arial,FontSize=16,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,Outline=2,"
            "Alignment=2,MarginV=40'"
        )
    else:  # classic
        return (
            f"subtitles='{safe_path}':force_style='"
            "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFF00,"
            "OutlineColour=&H00000000,Outline=2,"
            "Alignment=2,MarginV=50'"
        )
