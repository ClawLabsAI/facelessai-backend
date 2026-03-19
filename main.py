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

app = FastAPI(title="FacelessAI Video Generator", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = Path(tempfile.gettempdir()) / "facelessai"
TEMP_DIR.mkdir(exist_ok=True)

# Headers that mimic a real browser — fixes Pexels blocking
DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.pexels.com/",
    "Origin": "https://www.pexels.com",
}

# ─────────────────────────────────────────
# REQUEST MODELS
# ─────────────────────────────────────────

class VideoRequest(BaseModel):
    audio_url: str = ""         # Optional URL of the MP3 audio
    audio_b64: Optional[str] = None  # Base64 encoded audio (preferred)
    pexels_clips: list[str]     # List of Pexels video URLs
    script: str                 # Full script text for subtitles
    title: str                  # Video title
    lang: str = "es"
    ratio: str = "9:16"
    resolution: str = "1080x1920"
    fps: int = 30
    subtitle_style: str = "viral"


class StatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    message: str
    download_url: Optional[str] = None


jobs: dict = {}

# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "FacelessAI Video Generator",
        "version": "1.1",
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
        jobs[job_id] = {
            "status": status, "progress": progress,
            "message": message, "download_url": jobs[job_id].get("download_url")
        }

    try:
        # ── STEP 1: Save audio ──
        update("processing", 10, "Guardando audio...")
        audio_path = job_dir / "audio.mp3"

        if req.audio_b64:
            import base64
            audio_data = base64.b64decode(req.audio_b64)
            audio_path.write_bytes(audio_data)
        elif req.audio_url and req.audio_url.startswith("http"):
            async with httpx.AsyncClient(timeout=30, headers=DOWNLOAD_HEADERS) as client:
                r = await client.get(req.audio_url)
                r.raise_for_status()
                audio_path.write_bytes(r.content)
        else:
            raise ValueError("Necesitas audio_b64 o audio_url válida")

        duration = get_audio_duration(str(audio_path))
        update("processing", 20, f"Audio OK — {duration:.1f}s")

        # ── STEP 2: Download Pexels clips with browser headers ──
        update("processing", 25, f"Descargando {len(req.pexels_clips)} clips de Pexels...")
        clip_paths = []

        async with httpx.AsyncClient(
            timeout=60,
            follow_redirects=True,
            headers=DOWNLOAD_HEADERS   # ← FIX: browser headers so Pexels allows download
        ) as client:
            for i, clip_url in enumerate(req.pexels_clips[:6]):
                try:
                    update("processing", 25 + i * 5, f"Descargando clip {i+1}/{len(req.pexels_clips)}...")
                    r = await client.get(clip_url)
                    r.raise_for_status()
                    if len(r.content) < 1000:
                        print(f"Clip {i} demasiado pequeño ({len(r.content)} bytes) — omitido")
                        continue
                    clip_path = job_dir / f"clip_{i}.mp4"
                    clip_path.write_bytes(r.content)
                    clip_paths.append(str(clip_path))
                    update("processing", 25 + i * 5, f"✓ Clip {i+1} ({len(r.content)//1024}KB)")
                except Exception as e:
                    print(f"Error clip {i} ({clip_url[:60]}): {e}")

        if not clip_paths:
            raise ValueError("No se pudieron descargar clips. Verifica las URLs de Pexels.")

        update("processing", 55, f"{len(clip_paths)} clips listos — procesando...")

        # ── STEP 3: Crop clips to 9:16 vertical ──
        processed_clips = []
        clip_duration = duration / len(clip_paths)

        for i, clip_path in enumerate(clip_paths):
            out_path = job_dir / f"proc_{i}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-i", clip_path,
                "-t", str(clip_duration),
                "-vf", (
                    "crop=in_h*9/16:in_h,"
                    "scale=1080:1920:force_original_aspect_ratio=increase,"
                    "crop=1080:1920,"
                    f"zoompan=z='min(zoom+0.0015,1.5)':d={int(clip_duration*30)}:s=1080x1920"
                ),
                "-r", str(req.fps),
                "-an",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                str(out_path)
            ]
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode != 0:
                print(f"Warning: clip {i} FFmpeg error — {result.stderr.decode()[-100:]}")
                # Try simpler fallback without zoompan
                cmd_simple = [
                    "ffmpeg", "-y", "-i", clip_path, "-t", str(clip_duration),
                    "-vf", "crop=in_h*9/16:in_h,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                    "-r", str(req.fps), "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    str(out_path)
                ]
                subprocess.run(cmd_simple, capture_output=True, check=True)
            processed_clips.append(str(out_path))

        update("processing", 65, "Clips procesados — concatenando...")

        # ── STEP 4: Concatenate ──
        concat_list = job_dir / "concat.txt"
        concat_list.write_text("\n".join([f"file '{p}'" for p in processed_clips]))

        concat_path = job_dir / "concat.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-c", "copy", str(concat_path)
        ], capture_output=True, check=True)

        update("processing", 72, "Generando subtítulos...")

        # ── STEP 5: SRT subtitles ──
        srt_path = job_dir / "subs.srt"
        generate_srt(req.script, duration, str(srt_path))

        update("processing", 78, "Montaje final con audio y subtítulos...")

        # ── STEP 6: Final composition ──
        output_path = TEMP_DIR / f"{job_id}_output.mp4"
        subtitle_filter = get_subtitle_filter(str(srt_path), req.subtitle_style)

        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(concat_path),
            "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-t", str(duration),
            "-vf", subtitle_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart",
            str(output_path)
        ], capture_output=True, check=True)

        file_size_mb = output_path.stat().st_size / 1024 / 1024
        jobs[job_id]["status"] = "done"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["message"] = f"✅ Vídeo listo — {duration:.1f}s · {file_size_mb:.1f}MB"
        jobs[job_id]["download_url"] = f"/download/{job_id}"

        import shutil
        shutil.rmtree(str(job_dir), ignore_errors=True)

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if e.stderr else str(e)
        jobs[job_id] = {"status": "error", "progress": 0,
                        "message": f"FFmpeg error: {err[-300:]}", "download_url": None}
    except Exception as e:
        jobs[job_id] = {"status": "error", "progress": 0,
                        "message": f"Error: {str(e)}", "download_url": None}


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
    clean = re.sub(r'\[.*?\]', '', script)
    clean = re.sub(r'#\w+', '', clean)
    clean = re.sub(r'//.*', '', clean)
    clean = ' '.join(clean.split())

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
    safe_path = srt_path.replace('\\', '/').replace(':', '\\:')
    if style == "viral":
        return (f"subtitles='{safe_path}':force_style='"
                "FontName=Impact,FontSize=22,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,Outline=3,Shadow=2,"
                "Alignment=2,MarginV=60,Bold=1'")
    elif style == "minimal":
        return (f"subtitles='{safe_path}':force_style='"
                "FontName=Arial,FontSize=16,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=40'")
    else:
        return (f"subtitles='{safe_path}':force_style='"
                "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFF00,"
                "OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=50'")
