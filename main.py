from __future__ import annotations
"""
FacelessAI Backend v1.4 — Fix concat error
FastAPI + FFmpeg + Pexels
Fix: clips procesados siempre sin audio (-an), concat video-only, audio en compose final
"""

import os, re, uuid, json, httpx, random, asyncio, tempfile, base64, subprocess, shutil
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="FacelessAI Video Generator", version="1.4")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

TEMP_DIR = Path(tempfile.gettempdir()) / "facelessai"
TEMP_DIR.mkdir(exist_ok=True)

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.pexels.com/", "Origin": "https://www.pexels.com",
}

# ─── LOCAL MUSIC LIBRARY ──────────────────────────────────────
MUSIC_LIBRARY = {
    "finanzas":   ["https://cdn.pixabay.com/audio/2024/01/08/audio_d0c6ff1c60.mp3"],
    "motivacion": ["https://cdn.pixabay.com/audio/2023/03/09/audio_42009f8537.mp3"],
    "truecrime":  ["https://cdn.pixabay.com/audio/2023/10/30/audio_831c9b03d6.mp3"],
    "tecnologia": ["https://cdn.pixabay.com/audio/2023/05/16/audio_7d1ef0b4a3.mp3"],
    "historia":   ["https://cdn.pixabay.com/audio/2023/09/04/audio_3c7b1e5f8a.mp3"],
    "cripto":     ["https://cdn.pixabay.com/audio/2023/06/12/audio_8e4f2b6c9d.mp3"],
    "drama":      ["https://cdn.pixabay.com/audio/2023/08/21/audio_2f9b4c7e1a.mp3"],
    "default":    ["https://cdn.pixabay.com/audio/2023/04/18/audio_5b8c3a1e7f.mp3"],
}

class VideoRequest(BaseModel):
    audio_url: str = ""
    audio_b64: Optional[str] = None
    pexels_clips: List[str]
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

# ─── ENDPOINTS ───────────────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "FacelessAI", "version": "1.4",
            "features": ["capcut_subtitles", "auto_music", "dynamic_zoom", "thumbnail", "yt_analytics"],
            "ffmpeg": check_ffmpeg()}

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.4", "ffmpeg": check_ffmpeg()}

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

# ─── YOUTUBE ANALYTICS (OAuth proxy) ─────────────────────────

@app.get("/yt/channel-stats")
async def yt_channel_stats(channel_id: str, access_token: str):
    if not access_token or not channel_id:
        raise HTTPException(status_code=400, detail="channel_id y access_token requeridos")
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
            stats = ch.get("statistics", {})
            return {
                "channel_id": channel_id,
                "title": ch.get("snippet", {}).get("title", ""),
                "subscribers": int(stats.get("subscriberCount", 0)),
                "total_views": int(stats.get("viewCount", 0)),
                "video_count": int(stats.get("videoCount", 0)),
                "thumbnail": ch.get("snippet", {}).get("thumbnails", {}).get("default", {}).get("url", ""),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/yt/recent-videos")
async def yt_recent_videos(channel_id: str, access_token: str, max_results: int = 10):
    if not access_token or not channel_id:
        raise HTTPException(status_code=400, detail="channel_id y access_token requeridos")
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
            stats_r = await cl.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics,snippet,contentDetails", "id": ",".join(video_ids)},
                headers={"Authorization": f"Bearer {access_token}"}
            )
            stats_r.raise_for_status()
            videos = []
            for v in stats_r.json().get("items", []):
                s = v.get("statistics", {})
                sn = v.get("snippet", {})
                videos.append({
                    "video_id": v["id"],
                    "title": sn.get("title", ""),
                    "published_at": sn.get("publishedAt", ""),
                    "views": int(s.get("viewCount", 0)),
                    "likes": int(s.get("likeCount", 0)),
                    "comments": int(s.get("commentCount", 0)),
                    "thumbnail": sn.get("thumbnails", {}).get("medium", {}).get("url", ""),
                })
            return {"videos": videos, "channel_id": channel_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

        # STEP 2: Get clips
        clip_paths = []
        if req.kling_key and req.script:
            upd("processing", 15, "Generando clips con Kling AI...")
            sentences = [s.strip() for s in req.script.split('.') if len(s.strip()) > 20][:4]
            kling_dur = max(3, int(duration / max(len(sentences), 1)))
            for i, sentence in enumerate(sentences):
                upd("processing", 15 + i*5, f"Generando clip Kling {i+1}/{len(sentences)}...")
                kling_url = await generate_kling_video(sentence[:200], kling_dur, req.kling_key)
                if kling_url:
                    async with httpx.AsyncClient(timeout=60) as cl:
                        r = await cl.get(kling_url)
                        if r.status_code == 200 and len(r.content) > 1000:
                            p = job_dir / f"clip_{i}.mp4"
                            p.write_bytes(r.content)
                            clip_paths.append(str(p))

        if not clip_paths:
            upd("processing", 15, "Descargando clips de Pexels...")
            clip_paths = await download_clips(req.pexels_clips[:6], job_dir, upd)
            if not clip_paths:
                raise ValueError("No se pudieron descargar clips. Verifica las URLs de Pexels.")
            upd("processing", 38, f"{len(clip_paths)} clips descargados")

        # STEP 3: Process clips — ALWAYS strip audio (-an)
        # This ensures consistent video-only streams for clean concatenation
        upd("processing", 40, "Procesando clips...")
        processed = process_clips_no_audio(clip_paths, duration, job_dir, req.fps, req.zoom_effect)
        if not processed:
            raise ValueError(
                f"Todos los clips fallaron al procesarse. "
                f"Descargados: {len(clip_paths)}, procesados: 0. "
                f"Verifica que las URLs de Pexels son válidas y accesibles."
            )
        upd("processing", 55, f"{len(processed)}/{len(clip_paths)} clips procesados")

        # STEP 4: Concatenate video-only
        upd("processing", 57, "Concatenando video...")
        concat_path = concatenate_video_only(processed, job_dir)
        upd("processing", 65, "Video concatenado")

        # STEP 5: Background music
        music_path = None
        if req.add_music:
            upd("processing", 67, "Buscando musica de fondo...")
            music_path = await get_background_music(req.niche, job_dir, req.pixabay_key)
            upd("processing", 70, "Musica OK" if music_path else "Sin musica")

        # STEP 6: SRT subtitles
        upd("processing", 72, "Generando subtitulos...")
        srt_path = job_dir / "subs.srt"
        generate_srt(req.script, duration, str(srt_path), req.subtitle_style)

        # STEP 7: Final composition — add voice + music + subtitles to video
        upd("processing", 75, "Composicion final...")
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


def probe_video(path: str) -> dict:
    """Get video info via ffprobe. Returns dict with width, height, duration."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", path],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(r.stdout)
        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        return {
            "width":    int(video_stream.get("width", 0)),
            "height":   int(video_stream.get("height", 0)),
            "duration": float(data.get("format", {}).get("duration", 0)),
            "codec":    video_stream.get("codec_name", "unknown"),
            "valid":    bool(video_stream),
        }
    except Exception as e:
        print(f"probe_video error: {e}")
        return {"width": 0, "height": 0, "duration": 0, "codec": "unknown", "valid": False}


def process_clips_no_audio(clip_paths: list, total_duration: float,
                            job_dir: Path, fps: int, zoom_effect: bool) -> list:
    """
    Process clips to 9:16 vertical, always strip audio (-an).
    Probes each clip first and picks the safest filter for its dimensions.
    """
    processed = []
    clip_dur = total_duration / max(len(clip_paths), 1)

    for i, path in enumerate(clip_paths):
        out  = job_dir / f"proc_{i}.mp4"

        # Probe clip to understand its dimensions
        info = probe_video(path)
        print(f"Clip {i}: {info['width']}x{info['height']} {info['codec']} {info['duration']:.1f}s valid={info['valid']}")

        if not info["valid"]:
            print(f"Clip {i} invalid — skipping")
            continue

        w, h = info["width"], info["height"]

        # Choose safest vf based on actual dimensions
        # Goal: get 1080x1920 (9:16 portrait)
        if w > 0 and h > 0:
            if w >= h:
                # Landscape — crop center portrait slice
                vf = "crop=ih*9/16:ih,scale=1080:1920"
            else:
                # Portrait — just scale
                vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"

            # Add subtle zoom if requested (only on landscape clips)
            if zoom_effect and w >= h:
                zooms = [
                    "crop=ih*9/16:ih,scale=1166:2074,crop=1080:1920:43:77",
                    "crop=ih*9/16:ih,scale=1200:2133,crop=1080:1920:60:107",
                    "crop=ih*9/16:ih,scale=1080:1920",
                    "crop=ih*9/16:ih,scale=1166:2074,crop=1080:1920:86:77",
                    "crop=ih*9/16:ih,scale=1166:2074,crop=1080:1920:0:77",
                ]
                vf = zooms[i % len(zooms)]
        else:
            vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2"

        # Attempt 1: smart vf
        cmd1 = [
            "ffmpeg", "-y",
            "-fflags", "+genpts+igndts",
            "-i", path,
            "-t", str(clip_dur),
            "-vf", vf,
            "-r", str(fps), "-vsync", "cfr",
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
            str(out)
        ]
        r1 = subprocess.run(cmd1, capture_output=True)
        if r1.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            print(f"Clip {i} OK (smart vf)")
            processed.append(str(out))
            continue
        print(f"Clip {i} attempt 1 failed: {r1.stderr.decode()[-150:]}")

        # Attempt 2: scale only, no crop
        cmd2 = [
            "ffmpeg", "-y",
            "-fflags", "+genpts+igndts",
            "-i", path,
            "-t", str(clip_dur),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "-r", str(fps), "-vsync", "cfr",
            "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
            str(out)
        ]
        r2 = subprocess.run(cmd2, capture_output=True)
        if r2.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            print(f"Clip {i} OK (scale fallback)")
            processed.append(str(out))
            continue
        print(f"Clip {i} attempt 2 failed: {r2.stderr.decode()[-150:]}")

        # Attempt 3: re-encode with minimal options
        cmd3 = [
            "ffmpeg", "-y",
            "-fflags", "+genpts+igndts+discardcorrupt",
            "-err_detect", "ignore_err",
            "-i", path,
            "-t", str(clip_dur),
            "-vf", "scale=1080:1920",
            "-r", str(fps),
            "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
            str(out)
        ]
        r3 = subprocess.run(cmd3, capture_output=True)
        if r3.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            print(f"Clip {i} OK (minimal fallback)")
            processed.append(str(out))
            continue

        print(f"Clip {i} ALL ATTEMPTS FAILED — skipping entirely")

    print(f"process_clips_no_audio: {len(processed)}/{len(clip_paths)} clips OK")
    return processed


def concatenate_video_only(paths: list, job_dir: Path) -> Path:
    """
    Concatenate video-only streams using concat demuxer.
    Since all clips are video-only (no audio), this is always clean.
    """
    if not paths:
        raise ValueError("No hay clips para concatenar")

    out = job_dir / "concat.mp4"

    if len(paths) == 1:
        shutil.copy2(paths[0], str(out))
        return out

    # Write concat.txt with proper path escaping
    cf = job_dir / "concat.txt"
    lines = []
    for p in paths:
        # Escape single quotes in path (rare but safe)
        safe_p = str(p).replace("'", "'\\''")
        lines.append(f"file '{safe_p}'")
    cf.write_text("\n".join(lines), encoding="utf-8")

    print(f"concat.txt contents:\n{cf.read_text()}")

    # Method 1: concat demuxer with copy (fastest, no re-encode needed
    # since all clips are already h264 yuv420p from process_clips_no_audio)
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(cf),
        "-c", "copy",
        str(out)
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
        return out

    print(f"concat copy failed: {r.stderr.decode()[-200:]}")

    # Method 2: concat demuxer with re-encode
    cmd2 = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(cf),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
        str(out)
    ]
    r2 = subprocess.run(cmd2, capture_output=True)
    if r2.returncode == 0 and out.exists() and out.stat().st_size > 1000:
        return out

    print(f"concat re-encode failed: {r2.stderr.decode()[-200:]}")

    # Method 3: filter_complex concat (no audio streams needed)
    inputs = []
    filter_parts = []
    for i, p in enumerate(paths):
        inputs += ["-i", p]
        filter_parts.append(f"[{i}:v]")
    filter_str = "".join(filter_parts) + f"concat=n={len(paths)}:v=1:a=0[outv]"
    cmd3 = (["ffmpeg", "-y"] + inputs +
            ["-filter_complex", filter_str, "-map", "[outv]",
             "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
             str(out)])
    subprocess.run(cmd3, capture_output=True, check=True)
    return out


def compose_final(video: str, audio: str, srt: str, music: Optional[str],
                  output: str, duration: float, style: str, music_vol: float):
    """Add voice + optional music + subtitles to the silent video."""

    # Verify inputs exist
    if not Path(video).exists() or Path(video).stat().st_size < 100:
        raise ValueError(f"Video input invalid or empty: {video}")
    if not Path(audio).exists() or Path(audio).stat().st_size < 100:
        raise ValueError(f"Audio input invalid or empty: {audio}")

    sub_filter = get_subtitle_filter(srt, style)
    has_subs   = Path(srt).exists() and Path(srt).stat().st_size > 10

    def run_compose(vf_filter: Optional[str], use_music: bool) -> bool:
        """Try one compose variant. Returns True on success."""
        inputs = ["-i", video, "-i", audio]
        maps   = ["-map", "0:v:0"]
        audio_filter = None

        if use_music and music and Path(music).exists():
            inputs += ["-i", music]
            audio_filter = (
                f"[1:a]volume=1.0[v];"
                f"[2:a]volume={music_vol},aloop=loop=-1:size=2e+09[m];"
                f"[v][m]amix=inputs=2:duration=first[aout]"
            )
            maps += ["-map", "[aout]"]
        else:
            maps += ["-map", "1:a:0"]

        cmd = ["ffmpeg", "-y"] + inputs
        if audio_filter:
            cmd += ["-filter_complex", audio_filter]
        cmd += maps
        cmd += ["-t", str(duration)]
        if vf_filter:
            cmd += ["-vf", vf_filter]
        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart", output
        ]

        # Remove any partial output from previous attempt
        out_path = Path(output)
        if out_path.exists():
            out_path.unlink()

        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 1000:
            return True
        stderr_tail = r.stderr.decode()[-300:]
        print(f"compose attempt failed (rc={r.returncode}): {stderr_tail}")
        return False

    # Try in order: most reliable first
    # 1. No subs, no music (baseline — should always work)
    # 2. No subs, with music
    # 3. Subs, no music
    # 4. Subs, with music
    attempts = [
        (None,                             False),  # baseline — always works
        (None,                             True),   # add music
        (sub_filter if has_subs else None, False),  # add subs
        (sub_filter if has_subs else None, True),   # full
    ]

    for vf, use_music in attempts:
        if run_compose(vf, use_music):
            return

    raise RuntimeError("compose_final: all attempts failed")


async def get_background_music(niche: str, job_dir: Path, api_key: str = "") -> Optional[str]:
    library_urls = MUSIC_LIBRARY.get(niche, MUSIC_LIBRARY["default"])
    queries = {"finanzas": "corporate+background", "motivacion": "motivational+upbeat",
               "truecrime": "dark+suspense", "tecnologia": "electronic+tech",
               "historia": "epic+cinematic", "cripto": "electronic+beat",
               "drama": "emotional+piano", "default": "background+calm+music"}
    query = queries.get(niche, queries["default"])

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cl:
        for url in library_urls:
            try:
                r = await cl.get(url, timeout=15)
                if r.status_code == 200 and len(r.content) > 5000:
                    p = job_dir / "music.mp3"
                    p.write_bytes(r.content)
                    return str(p)
            except Exception as e:
                print(f"Library track failed: {e}")

        if api_key or os.environ.get("PIXABAY_KEY"):
            try:
                key = api_key or os.environ.get("PIXABAY_KEY", "")
                r = await cl.get(f"https://pixabay.com/api/music/?key={key}&q={query}&per_page=5", timeout=10)
                if r.status_code == 200:
                    hits = r.json().get("hits", [])
                    if hits:
                        track = random.choice(hits[:3])
                        audio_url = (track.get("audio", {}).get("medium", {}).get("url")
                                     or track.get("preview_url", ""))
                        if audio_url:
                            mr = await cl.get(audio_url, timeout=25)
                            if mr.status_code == 200 and len(mr.content) > 5000:
                                p = job_dir / "music.mp3"
                                p.write_bytes(mr.content)
                                return str(p)
            except Exception as e:
                print(f"Pixabay API error: {e}")

    return None


async def generate_kling_video(prompt: str, duration: int, api_key: str) -> Optional[str]:
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=120) as cl:
            r = await cl.post(
                "https://api.klingai.com/v1/videos/text2video",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"prompt": prompt, "model": "kling-v1", "duration": min(duration, 10),
                      "aspect_ratio": "9:16", "mode": "std"}
            )
            if r.status_code != 200:
                return None
            task_id = r.json().get("data", {}).get("task_id")
            if not task_id:
                return None
            for _ in range(40):
                await asyncio.sleep(3)
                sr = await cl.get(f"https://api.klingai.com/v1/videos/text2video/{task_id}",
                                  headers={"Authorization": f"Bearer {api_key}"})
                data = sr.json().get("data", {})
                if data.get("task_status") == "succeed":
                    videos = data.get("task_result", {}).get("videos", [])
                    return videos[0].get("url") if videos else None
                if data.get("task_status") in ("failed", "error"):
                    return None
    except Exception as e:
        print(f"Kling error: {e}")
    return None


def generate_thumbnail(video: str, output: str):
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
    clean = re.sub(r'\[.*?\]|\#\w+|//.*', '', script)
    words = ' '.join(clean.split()).split()
    if not words:
        words = ["FacelessAI"]
    cs = {"capcut": 3, "viral": 5, "minimal": 7}.get(style, 3)
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
    # Use fonts guaranteed available on Railway/Nix Linux:
    # Liberation Sans Bold (replaces Arial Bold), DejaVu Sans Bold
    # Impact is NOT installed by default — avoid it
    if style == "capcut":
        return (f"subtitles='{safe}':force_style='"
                "FontName=Liberation Sans,FontSize=26,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,Outline=4,Shadow=1,"
                "Alignment=2,MarginV=120,Bold=1,BorderStyle=1'")
    elif style == "viral":
        return (f"subtitles='{safe}':force_style='"
                "FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H0000FFFF,"
                "OutlineColour=&H00000000,Outline=3,Shadow=2,"
                "Alignment=2,MarginV=60,Bold=1'")
    else:
        return (f"subtitles='{safe}':force_style='"
                "FontName=DejaVu Sans,FontSize=18,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=40'")
