"""
FacelessAI Backend v1.3
FastAPI + FFmpeg + Pexels
New: CapCut subtitles, auto music, dynamic zoom, thumbnail generation
"""

import os, re, uuid, json, httpx, random, asyncio, tempfile, base64, subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="FacelessAI Video Generator", version="1.3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TEMP_DIR = Path(tempfile.gettempdir()) / "facelessai"
TEMP_DIR.mkdir(exist_ok=True)

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.pexels.com/", "Origin": "https://www.pexels.com",
}

class VideoRequest(BaseModel):
    audio_url: str = ""
    audio_b64: Optional[str] = None
    pexels_clips: list[str]
    script: str
    title: str
    lang: str = "es"
    niche: str = "default"
    ratio: str = "9:16"
    resolution: str = "1080x1920"
    fps: int = 30
    subtitle_style: str = "capcut"
    add_music: bool = True
    music_volume: float = 0.08
    zoom_effect: bool = True

class StatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    message: str
    download_url: Optional[str] = None
    thumbnail_url: Optional[str] = None

jobs: dict = {}

@app.get("/")
async def root():
    return {"service": "FacelessAI Video Generator", "version": "1.3",
            "features": ["capcut_subtitles", "auto_music", "dynamic_zoom", "thumbnail"],
            "ffmpeg": check_ffmpeg()}

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.3", "ffmpeg": check_ffmpeg()}

def check_ffmpeg():
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        return "available" if r.returncode == 0 else "not found"
    except FileNotFoundError:
        return "not installed"

@app.post("/generate", response_model=StatusResponse)
async def generate_video(req: VideoRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "pending", "progress": 0, "message": "Iniciando...",
                    "download_url": None, "thumbnail_url": None}
    background_tasks.add_task(process_video, job_id, req)
    return StatusResponse(job_id=job_id, status="pending", progress=0, message="Job creado")

@app.get("/status/{job_id}", response_model=StatusResponse)
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    j = jobs[job_id]
    return StatusResponse(job_id=job_id, status=j["status"], progress=j["progress"],
                          message=j["message"], download_url=j.get("download_url"),
                          thumbnail_url=j.get("thumbnail_url"))

@app.get("/download/{job_id}")
async def download_video(job_id: str):
    if job_id not in jobs or jobs[job_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="Video no disponible")
    filepath = TEMP_DIR / f"{job_id}_output.mp4"
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(str(filepath), media_type="video/mp4", filename=f"facelessai_{job_id}.mp4")

@app.get("/thumbnail/{job_id}")
async def get_thumbnail(job_id: str):
    filepath = TEMP_DIR / f"{job_id}_thumb.jpg"
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Thumbnail no disponible")
    return FileResponse(str(filepath), media_type="image/jpeg")

# ─── CORE PROCESSING ─────────────────────────────────────────

async def process_video(job_id: str, req: VideoRequest):
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    def upd(status, pct, msg):
        jobs[job_id].update({"status": status, "progress": pct, "message": msg})

    try:
        # STEP 1: Save audio
        upd("processing", 5, "Guardando audio...")
        audio_path = job_dir / "audio.mp3"
        if req.audio_b64:
            audio_path.write_bytes(base64.b64decode(req.audio_b64))
        elif req.audio_url and req.audio_url.startswith("http"):
            async with httpx.AsyncClient(timeout=60, headers=DOWNLOAD_HEADERS) as cl:
                r = await cl.get(req.audio_url)
                r.raise_for_status()
                audio_path.write_bytes(r.content)
        else:
            raise ValueError("Necesitas audio_b64 o audio_url valida")

        duration = get_audio_duration(str(audio_path))
        upd("processing", 12, f"Audio OK — {duration:.1f}s")

        # STEP 2: Download clips
        upd("processing", 15, "Descargando clips de Pexels...")
        clip_paths = await download_clips(req.pexels_clips[:6], job_dir, upd)
        if not clip_paths:
            raise ValueError("No se pudieron descargar clips")
        upd("processing", 40, f"{len(clip_paths)} clips descargados")

        # STEP 3: Process clips with zoom variants
        upd("processing", 42, "Procesando clips con zoom dinamico...")
        processed = process_clips(clip_paths, duration, job_dir, req.fps, req.zoom_effect)
        if not processed:
            raise ValueError("No se pudieron procesar los clips")
        upd("processing", 58, f"{len(processed)} clips procesados")

        # STEP 4: Concatenate
        upd("processing", 60, "Concatenando...")
        concat_path = concatenate_clips(processed, job_dir)

        # STEP 5: Background music
        music_path = None
        if req.add_music:
            upd("processing", 63, "Buscando musica de fondo...")
            music_path = await get_background_music(req.niche, job_dir)
            upd("processing", 68, "Musica lista" if music_path else "Sin musica disponible")

        # STEP 6: SRT subtitles
        upd("processing", 70, "Generando subtitulos animados...")
        srt_path = job_dir / "subs.srt"
        generate_srt(req.script, duration, str(srt_path), req.subtitle_style)

        # STEP 7: Final composition
        upd("processing", 75, "Composicion final con audio + musica + subtitulos...")
        output_path = TEMP_DIR / f"{job_id}_output.mp4"
        compose_final(str(concat_path), str(audio_path), str(srt_path),
                      music_path, str(output_path), duration,
                      req.subtitle_style, req.music_volume)

        # STEP 8: Thumbnail
        upd("processing", 95, "Generando thumbnail...")
        thumb_path = TEMP_DIR / f"{job_id}_thumb.jpg"
        generate_thumbnail(str(output_path), str(thumb_path))

        file_mb = output_path.stat().st_size / 1024 / 1024
        jobs[job_id].update({
            "status": "done", "progress": 100,
            "message": f"Video listo — {duration:.1f}s · {file_mb:.1f}MB",
            "download_url": f"/download/{job_id}",
            "thumbnail_url": f"/thumbnail/{job_id}" if thumb_path.exists() else None,
        })

        import shutil
        shutil.rmtree(str(job_dir), ignore_errors=True)

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if e.stderr else str(e)
        jobs[job_id].update({"status": "error", "progress": 0, "message": f"FFmpeg error: {err[-300:]}"})
    except Exception as e:
        jobs[job_id].update({"status": "error", "progress": 0, "message": f"Error: {str(e)}"})

# ─── HELPERS ─────────────────────────────────────────────────

async def download_clips(urls, job_dir, upd):
    paths = []
    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers=DOWNLOAD_HEADERS) as cl:
        for i, url in enumerate(urls):
            try:
                upd("processing", 15 + i * 4, f"Descargando clip {i+1}/{len(urls)}...")
                r = await cl.get(url)
                r.raise_for_status()
                if len(r.content) < 1000:
                    continue
                p = job_dir / f"clip_{i}.mp4"
                p.write_bytes(r.content)
                paths.append(str(p))
            except Exception as e:
                print(f"Clip {i} error: {e}")
    return paths


def process_clips(clip_paths, total_duration, job_dir, fps, zoom_effect):
    """Crop to 9:16 with zoom variation per clip."""
    processed = []
    clip_dur = total_duration / max(len(clip_paths), 1)

    # 5 zoom crop variants — create natural camera movement feeling
    zoom_vf = [
        "scale=1166:2074,crop=1080:1920:43:77",     # slight zoom in center
        "scale=1200:2133,crop=1080:1920:60:107",    # wider zoom out
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",  # static
        "scale=1166:2074,crop=1080:1920:86:77",     # offset right
        "scale=1166:2074,crop=1080:1920:0:77",      # offset left
    ]

    for i, path in enumerate(clip_paths):
        out = job_dir / f"proc_{i}.mp4"
        vf  = zoom_vf[i % len(zoom_vf)] if zoom_effect else zoom_vf[2]

        cmd = ["ffmpeg", "-y", "-i", path, "-t", str(clip_dur),
               "-vf", vf, "-r", str(fps), "-vsync", "cfr", "-an",
               "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
               str(out)]
        r = subprocess.run(cmd, capture_output=True)

        if r.returncode != 0:
            # Fallback: simple pad
            cmd2 = ["ffmpeg", "-y", "-fflags", "+genpts", "-i", path, "-t", str(clip_dur),
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
                    "-r", str(fps), "-vsync", "cfr", "-an",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
                    str(out)]
            r2 = subprocess.run(cmd2, capture_output=True)
            if r2.returncode != 0:
                print(f"Skipping clip {i}")
                continue

        processed.append(str(out))
    return processed


def concatenate_clips(paths, job_dir):
    cf = job_dir / "concat.txt"
    cf.write_text("\n".join([f"file '{p}'" for p in paths]))
    out = job_dir / "concat.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(cf), "-c", "copy", str(out)],
                   capture_output=True, check=True)
    return out


async def get_background_music(niche: str, job_dir: Path) -> Optional[str]:
    """Fetch royalty-free music from Pixabay free API."""
    queries = {
        "finanzas": "corporate+background", "motivacion": "motivational+upbeat",
        "truecrime": "dark+suspense", "tecnologia": "electronic+tech",
        "historia": "epic+cinematic", "cripto": "electronic+beat",
        "drama": "emotional+piano", "salud": "calm+positive",
        "default": "background+calm+music",
    }
    query = queries.get(niche, queries["default"])

    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            # Pixabay music API — free tier, public tracks
            r = await cl.get(
                f"https://pixabay.com/api/music/?key=46960046-3e4a2cf52a9afc0dc49e7f8a9&q={query}&per_page=5"
            )
            if r.status_code == 200:
                hits = r.json().get("hits", [])
                if hits:
                    # Pick a random track from top 3
                    track = random.choice(hits[:3])
                    audio_url = (track.get("audio", {}).get("medium", {}).get("url")
                                 or track.get("preview_url", ""))
                    if audio_url:
                        mr = await cl.get(audio_url, timeout=30)
                        if mr.status_code == 200 and len(mr.content) > 5000:
                            p = job_dir / "music.mp3"
                            p.write_bytes(mr.content)
                            return str(p)
    except Exception as e:
        print(f"Music error: {e}")
    return None


def compose_final(video, audio, srt, music, output, duration, style, music_vol):
    """FFmpeg final composition: video + voice + optional music + subtitles."""
    sub_filter = get_subtitle_filter(srt, style)

    if music and Path(music).exists():
        cmd = [
            "ffmpeg", "-y",
            "-i", video, "-i", audio, "-i", music,
            "-filter_complex",
            (f"[1:a]volume=1.0[v];"
             f"[2:a]volume={music_vol},aloop=loop=-1:size=2e+09[m];"
             f"[v][m]amix=inputs=2:duration=first[aout]"),
            "-map", "0:v:0", "-map", "[aout]",
            "-t", str(duration),
            "-vf", sub_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart", output
        ]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0:
            return

    # No music or music failed — fallback without music
    cmd2 = [
        "ffmpeg", "-y",
        "-i", video, "-i", audio,
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(duration),
        "-vf", sub_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart", output
    ]
    r2 = subprocess.run(cmd2, capture_output=True)
    if r2.returncode != 0:
        # Last resort: no subtitles
        cmd3 = [
            "ffmpeg", "-y", "-i", video, "-i", audio,
            "-map", "0:v:0", "-map", "1:a:0",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", output
        ]
        subprocess.run(cmd3, capture_output=True, check=True)


def generate_thumbnail(video: str, output: str):
    """Extract a frame at 1s as JPEG thumbnail."""
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video, "-ss", "1", "-vframes", "1",
            "-vf", "scale=540:960", "-q:v", "3", output
        ], capture_output=True, timeout=15)
    except Exception as e:
        print(f"Thumbnail error: {e}")


def get_audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def generate_srt(script: str, duration: float, output: str, style: str = "capcut"):
    """Generate SRT. CapCut: 3 words/chunk. Viral: 5. Minimal: 7."""
    clean = re.sub(r'\[.*?\]|\#\w+|//.*', '', script)
    words = ' '.join(clean.split()).split()
    if not words:
        words = ["FacelessAI"]

    chunk_sizes = {"capcut": 3, "viral": 5, "minimal": 7}
    cs = chunk_sizes.get(style, 3)
    chunks = [' '.join(words[i:i+cs]) for i in range(0, len(words), cs)]
    tpc = duration / len(chunks)

    srt = ""
    for i, chunk in enumerate(chunks):
        start, end = i * tpc, min((i+1) * tpc, duration - 0.05)
        text = chunk.upper() if style in ("capcut", "viral") else chunk
        srt += f"{i+1}\n{fmt_t(start)} --> {fmt_t(end)}\n{text}\n\n"

    Path(output).write_text(srt, encoding="utf-8")


def fmt_t(s: float) -> str:
    h, m = int(s // 3600), int((s % 3600) // 60)
    sec, ms = int(s % 60), int((s % 1) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def get_subtitle_filter(srt: str, style: str) -> str:
    safe = srt.replace('\\', '/').replace(':', '\\:')
    if style == "capcut":
        return (f"subtitles='{safe}':force_style='"
                "FontName=Impact,FontSize=26,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,Outline=4,Shadow=1,"
                "Alignment=2,MarginV=120,Bold=1,BorderStyle=1'")
    elif style == "viral":
        return (f"subtitles='{safe}':force_style='"
                "FontName=Impact,FontSize=22,PrimaryColour=&H0000FFFF,"
                "OutlineColour=&H00000000,Outline=3,Shadow=2,"
                "Alignment=2,MarginV=60,Bold=1'")
    else:
        return (f"subtitles='{safe}':force_style='"
                "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=40'")
