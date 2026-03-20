from __future__ import annotations
"""
FacelessAI Backend v1.5 — Simplified pipeline, full diagnostic logging
"""

import os, re, uuid, json, httpx, random, asyncio, tempfile, base64, subprocess, shutil
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="FacelessAI", version="1.5")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TEMP_DIR = Path(tempfile.gettempdir()) / "facelessai"
TEMP_DIR.mkdir(exist_ok=True)

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*", "Referer": "https://www.pexels.com/",
}

class VideoRequest(BaseModel):
    audio_url: str = ""
    audio_b64: Optional[str] = None
    pexels_clips: List[str]
    script: str = ""
    title: str = ""
    lang: str = "es"
    niche: str = "default"
    fps: int = 30
    subtitle_style: str = "capcut"
    add_music: bool = False      # disabled by default — enable once base works
    music_volume: float = 0.08
    zoom_effect: bool = True
    pixabay_key: str = ""
    kling_key: str = ""

class StatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    message: str
    download_url: Optional[str] = None
    thumbnail_url: Optional[str] = None

jobs: dict = {}

# ─── HEALTH + DIAGNOSTICS ────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "FacelessAI", "version": "1.5", "ffmpeg": check_ffmpeg()}

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.5", "ffmpeg": check_ffmpeg()}

def check_ffmpeg():
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        first_line = r.stdout.split("\n")[0] if r.stdout else "unknown"
        return first_line if r.returncode == 0 else "not found"
    except Exception as e:
        return f"error: {e}"

@app.get("/diag")
async def diag():
    """Diagnostic endpoint — call this to verify FFmpeg works on Railway."""
    results = {}

    # 1. FFmpeg version
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
    results["ffmpeg_version"] = r.stdout.split("\n")[0] if r.returncode == 0 else f"FAIL: {r.stderr[:100]}"

    # 2. FFprobe
    r2 = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=10)
    results["ffprobe"] = "ok" if r2.returncode == 0 else f"FAIL: {r2.stderr[:100]}"

    # 3. Generate a test video (black 3s)
    test_out = TEMP_DIR / "diag_test.mp4"
    r3 = subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=black:size=1080x1920:rate=30:duration=3",
        "-f", "lavfi", "-i", "aevalsrc=0:c=mono:r=44100:d=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(test_out)
    ], capture_output=True, timeout=30)
    if r3.returncode == 0 and test_out.exists():
        results["test_video"] = f"ok — {test_out.stat().st_size} bytes"
        test_out.unlink(missing_ok=True)
    else:
        results["test_video"] = f"FAIL: {r3.stderr.decode()[-200:]}"

    # 4. Available fonts
    r4 = subprocess.run(["fc-list"], capture_output=True, text=True, timeout=10)
    fonts = r4.stdout.strip().split("\n") if r4.returncode == 0 else []
    results["font_count"] = len(fonts)
    results["has_liberation"] = any("Liberation" in f for f in fonts)
    results["has_dejavu"]     = any("DejaVu" in f for f in fonts)
    results["sample_fonts"]   = fonts[:5]

    # 5. Temp dir
    results["temp_dir"] = str(TEMP_DIR)
    results["temp_writable"] = os.access(str(TEMP_DIR), os.W_OK)

    return JSONResponse(results)

# ─── ENDPOINTS ───────────────────────────────────────────────

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
    f = TEMP_DIR / f"{job_id}_output.mp4"
    if not f.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(str(f), media_type="video/mp4", filename=f"facelessai_{job_id}.mp4")

@app.get("/thumbnail/{job_id}")
async def get_thumbnail(job_id: str):
    f = TEMP_DIR / f"{job_id}_thumb.jpg"
    if not f.exists():
        raise HTTPException(status_code=404, detail="Thumbnail no disponible")
    return FileResponse(str(f), media_type="image/jpeg")

# ─── YOUTUBE ANALYTICS ───────────────────────────────────────

@app.get("/yt/channel-stats")
async def yt_channel_stats(channel_id: str, access_token: str):
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "statistics,snippet", "id": channel_id},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            if r.status_code == 401:
                raise HTTPException(status_code=401, detail="Token OAuth expirado")
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                raise HTTPException(status_code=404, detail="Canal no encontrado")
            ch = items[0]
            s  = ch.get("statistics", {})
            return {
                "channel_id": channel_id,
                "title":       ch.get("snippet", {}).get("title", ""),
                "subscribers": int(s.get("subscriberCount", 0)),
                "total_views": int(s.get("viewCount", 0)),
                "video_count": int(s.get("videoCount", 0)),
                "thumbnail":   ch.get("snippet", {}).get("thumbnails", {}).get("default", {}).get("url", ""),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/yt/recent-videos")
async def yt_recent_videos(channel_id: str, access_token: str, max_results: int = 10):
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            ch_r = await cl.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "contentDetails", "id": channel_id},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            ch_r.raise_for_status()
            uploads_id = (ch_r.json().get("items", [{}])[0]
                          .get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", ""))
            if not uploads_id:
                return {"videos": []}
            pl_r = await cl.get(
                "https://www.googleapis.com/youtube/v3/playlistItems",
                params={"part": "contentDetails,snippet", "playlistId": uploads_id, "maxResults": max_results},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            pl_r.raise_for_status()
            video_ids = [it["contentDetails"]["videoId"] for it in pl_r.json().get("items", [])]
            if not video_ids:
                return {"videos": []}
            sr = await cl.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics,snippet", "id": ",".join(video_ids)},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            sr.raise_for_status()
            return {"videos": [
                {"video_id": v["id"],
                 "title":  v.get("snippet", {}).get("title", ""),
                 "views":  int(v.get("statistics", {}).get("viewCount", 0)),
                 "likes":  int(v.get("statistics", {}).get("likeCount", 0)),
                 "thumbnail": v.get("snippet", {}).get("thumbnails", {}).get("medium", {}).get("url", "")}
                for v in sr.json().get("items", [])
            ], "channel_id": channel_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── CORE PROCESSING ─────────────────────────────────────────

async def process_video(job_id: str, req: VideoRequest):
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    log = []  # collect all log lines for debugging

    def upd(status, pct, msg):
        log.append(f"[{pct}%] {msg}")
        print(f"JOB {job_id} [{pct}%] {msg}")
        jobs[job_id].update({"status": status, "progress": pct, "message": msg})

    try:
        # STEP 1: Save audio
        upd("processing", 5, "Guardando audio...")
        audio_path = job_dir / "audio.mp3"
        if req.audio_b64:
            audio_path.write_bytes(base64.b64decode(req.audio_b64))
        elif req.audio_url.startswith("http"):
            async with httpx.AsyncClient(timeout=60, headers=DOWNLOAD_HEADERS) as cl:
                r = await cl.get(req.audio_url)
                r.raise_for_status()
                audio_path.write_bytes(r.content)
        else:
            raise ValueError("Necesitas audio_b64 o audio_url valida")

        audio_size = audio_path.stat().st_size
        duration   = get_audio_duration(str(audio_path))
        upd("processing", 12, f"Audio OK — {duration:.1f}s · {audio_size//1024}KB")

        if duration < 1:
            raise ValueError(f"Audio invalido o muy corto: {duration}s")

        # STEP 2: Download clips
        upd("processing", 15, f"Descargando {len(req.pexels_clips)} clips...")
        raw_clips = await download_clips(req.pexels_clips[:5], job_dir, upd)
        upd("processing", 38, f"{len(raw_clips)}/{len(req.pexels_clips)} clips descargados")

        if not raw_clips:
            raise ValueError(
                f"0 clips descargados de {len(req.pexels_clips)} URLs. "
                "Verifica que la Pexels API key es válida y las URLs son accesibles."
            )

        # STEP 3: Process clips to 9:16 portrait, no audio
        upd("processing", 40, f"Procesando {len(raw_clips)} clips a 9:16...")
        processed = []
        clip_dur   = duration / len(raw_clips)

        for i, clip_path in enumerate(raw_clips):
            out_clip = job_dir / f"clip_proc_{i}.mp4"
            ok = process_one_clip(clip_path, str(out_clip), clip_dur, req.fps)
            if ok:
                processed.append(str(out_clip))
                size_kb = Path(out_clip).stat().st_size // 1024
                upd("processing", 40 + i*3, f"Clip {i+1} OK ({size_kb}KB)")
            else:
                upd("processing", 40 + i*3, f"Clip {i+1} FALLIDO — omitiendo")

        if not processed:
            raise ValueError(
                f"0/{len(raw_clips)} clips procesados correctamente. "
                "FFmpeg no pudo convertir ningún clip a formato 9:16."
            )
        upd("processing", 58, f"{len(processed)} clips procesados OK")

        # STEP 4: Concatenate
        upd("processing", 60, f"Concatenando {len(processed)} clips...")
        concat_path = job_dir / "concat.mp4"
        concat_ok   = concatenate_clips(processed, str(concat_path))
        if not concat_ok or not concat_path.exists() or concat_path.stat().st_size < 1000:
            raise ValueError(
                f"Concatenación fallida. concat.mp4 size={concat_path.stat().st_size if concat_path.exists() else 0}"
            )
        concat_dur = get_audio_duration(str(concat_path))
        upd("processing", 65, f"Concat OK — {concat_path.stat().st_size//1024}KB · {concat_dur:.1f}s")

        # STEP 5: Compose — video + audio (simplest possible first)
        upd("processing", 70, "Composicion: video + audio...")
        output_path = TEMP_DIR / f"{job_id}_output.mp4"

        composed = compose_video_audio(str(concat_path), str(audio_path), str(output_path), duration)
        if not composed:
            raise ValueError("compose_video_audio falló — ver logs de Railway para detalle")

        out_size = output_path.stat().st_size
        upd("processing", 90, f"Video compuesto OK — {out_size//1024}KB")

        # STEP 6: Thumbnail
        upd("processing", 95, "Generando thumbnail...")
        thumb_path = TEMP_DIR / f"{job_id}_thumb.jpg"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(output_path),
            "-ss", "1", "-vframes", "1", "-vf", "scale=540:960", "-q:v", "3",
            str(thumb_path)
        ], capture_output=True, timeout=15)

        jobs[job_id].update({
            "status": "done", "progress": 100,
            "message": f"Video listo — {duration:.1f}s · {out_size//1024}KB",
            "download_url": f"/download/{job_id}",
            "thumbnail_url": f"/thumbnail/{job_id}" if thumb_path.exists() else None,
        })
        print(f"JOB {job_id} DONE — {out_size//1024}KB")
        shutil.rmtree(str(job_dir), ignore_errors=True)

    except Exception as e:
        err_msg = str(e)
        print(f"JOB {job_id} ERROR: {err_msg}")
        print(f"LOG:\n" + "\n".join(log))
        jobs[job_id].update({
            "status": "error", "progress": 0,
            "message": f"Error: {err_msg[:400]}"
        })

# ─── HELPERS ─────────────────────────────────────────────────

async def download_clips(urls: list, job_dir: Path, upd) -> list:
    paths = []
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=DOWNLOAD_HEADERS) as cl:
        for i, url in enumerate(urls):
            try:
                upd("processing", 15 + i*4, f"Descargando clip {i+1}/{len(urls)}...")
                r = await cl.get(url, timeout=30)
                r.raise_for_status()
                if len(r.content) < 5000:
                    print(f"Clip {i} too small ({len(r.content)} bytes) — skipping")
                    continue
                p = job_dir / f"raw_{i}.mp4"
                p.write_bytes(r.content)
                print(f"Clip {i} downloaded: {len(r.content)//1024}KB")
                paths.append(str(p))
            except Exception as e:
                print(f"Clip {i} download error: {e}")
    return paths


def process_one_clip(input_path: str, output_path: str, duration: float, fps: int) -> bool:
    """Convert one clip to 1080x1920 portrait, no audio. 3 attempts."""

    # Probe input
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
        capture_output=True, text=True, timeout=10
    )
    try:
        streams = json.loads(probe.stdout).get("streams", [])
        vs = next((s for s in streams if s.get("codec_type") == "video"), {})
        w, h = int(vs.get("width", 0)), int(vs.get("height", 0))
        print(f"  Clip {input_path[-20:]}: {w}x{h}")
    except Exception:
        w, h = 0, 0

    # Build vf based on orientation
    if w > 0 and h > 0 and w > h:
        # Landscape → crop to portrait then scale
        vf = "crop=ih*9/16:ih,scale=1080:1920"
    else:
        # Portrait or unknown → scale with padding
        vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"

    base_cmd = [
        "ffmpeg", "-y",
        "-fflags", "+genpts+igndts",
        "-i", input_path,
        "-t", str(min(duration + 1, 60)),
        "-r", str(fps), "-vsync", "cfr",
        "-an",
        "-pix_fmt", "yuv420p",
    ]

    # Attempt 1: with smart vf
    r1 = subprocess.run(
        base_cmd + ["-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "23", output_path],
        capture_output=True, timeout=60
    )
    if r1.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 1000:
        return True
    print(f"  Attempt 1 fail: {r1.stderr.decode()[-150:]}")

    # Attempt 2: simple scale only
    if Path(output_path).exists():
        Path(output_path).unlink()
    r2 = subprocess.run(
        base_cmd + ["-vf", "scale=1080:1920", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", output_path],
        capture_output=True, timeout=60
    )
    if r2.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 1000:
        return True
    print(f"  Attempt 2 fail: {r2.stderr.decode()[-150:]}")

    # Attempt 3: no vf at all, just re-encode
    if Path(output_path).exists():
        Path(output_path).unlink()
    r3 = subprocess.run(
        ["ffmpeg", "-y", "-fflags", "+genpts+igndts+discardcorrupt",
         "-i", input_path, "-t", str(min(duration + 1, 60)),
         "-r", str(fps), "-an", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "30", "-pix_fmt", "yuv420p", output_path],
        capture_output=True, timeout=60
    )
    if r3.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 1000:
        return True
    print(f"  Attempt 3 fail: {r3.stderr.decode()[-150:]}")
    return False


def concatenate_clips(paths: list, output: str) -> bool:
    """Concatenate clips. Returns True on success."""
    if len(paths) == 1:
        shutil.copy2(paths[0], output)
        return True

    # Method 1: filter_complex (most reliable with mixed clips)
    inputs, vparts = [], []
    for i, p in enumerate(paths):
        inputs += ["-i", p]
        vparts.append(f"[{i}:v]")
    filt = "".join(vparts) + f"concat=n={len(paths)}:v=1:a=0[outv]"
    r1 = subprocess.run(
        ["ffmpeg", "-y"] + inputs +
        ["-filter_complex", filt, "-map", "[outv]",
         "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
         output],
        capture_output=True, timeout=120
    )
    if r1.returncode == 0 and Path(output).exists() and Path(output).stat().st_size > 1000:
        print(f"concat filter_complex OK: {Path(output).stat().st_size//1024}KB")
        return True
    print(f"concat filter_complex fail: {r1.stderr.decode()[-200:]}")

    # Method 2: demuxer
    if Path(output).exists():
        Path(output).unlink()
    cf = Path(output).parent / "cl.txt"
    cf.write_text("\n".join(f"file '{p}'" for p in paths))
    r2 = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(cf),
         "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
         output],
        capture_output=True, timeout=120
    )
    if r2.returncode == 0 and Path(output).exists() and Path(output).stat().st_size > 1000:
        print(f"concat demuxer OK")
        return True
    print(f"concat demuxer fail: {r2.stderr.decode()[-200:]}")
    return False


def compose_video_audio(video: str, audio: str, output: str, duration: float) -> bool:
    """Simplest possible compose: video + audio, no filters."""
    # Verify inputs
    v_size = Path(video).stat().st_size if Path(video).exists() else 0
    a_size = Path(audio).stat().st_size if Path(audio).exists() else 0
    print(f"compose_video_audio: video={v_size//1024}KB audio={a_size//1024}KB duration={duration:.1f}s")

    if v_size < 1000:
        print(f"ERROR: video input too small: {v_size} bytes")
        return False
    if a_size < 100:
        print(f"ERROR: audio input too small: {a_size} bytes")
        return False

    cmd = [
        "ffmpeg", "-y",
        "-i", video,
        "-i", audio,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        output
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    if r.returncode == 0 and Path(output).exists() and Path(output).stat().st_size > 1000:
        print(f"compose OK: {Path(output).stat().st_size//1024}KB")
        return True
    print(f"compose FAIL: {r.stderr.decode()[-400:]}")
    return False


def get_audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True, timeout=10
    )
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def generate_srt(script: str, duration: float, output: str):
    clean  = re.sub(r'\[.*?\]|\#\w+|//.*', '', script)
    words  = ' '.join(clean.split()).split() or ["FacelessAI"]
    chunks = [' '.join(words[i:i+3]) for i in range(0, len(words), 3)]
    tpc    = duration / len(chunks)
    srt    = ""
    for i, chunk in enumerate(chunks):
        s, e = i * tpc, min((i+1) * tpc, duration - 0.05)
        h_s, m_s, sec_s, ms_s = int(s//3600), int((s%3600)//60), int(s%60), int((s%1)*1000)
        h_e, m_e, sec_e, ms_e = int(e//3600), int((e%3600)//60), int(e%60), int((e%1)*1000)
        srt += f"{i+1}\n{h_s:02d}:{m_s:02d}:{sec_s:02d},{ms_s:03d} --> {h_e:02d}:{m_e:02d}:{sec_e:02d},{ms_e:03d}\n{chunk.upper()}\n\n"
    Path(output).write_text(srt, encoding="utf-8")
