# FacelessAI Backend

Servidor de generación de vídeos MP4 para FacelessAI.
FastAPI + FFmpeg + Pexels → MP4 1080x1920 real descargable.

## Deploy en Railway (5 minutos)

### 1. Crea repo en GitHub

1. Ve a github.com/new
2. Nombre: `facelessai-backend`
3. Privado o público (da igual)
4. Clic en "Create repository"

### 2. Sube estos archivos al repo

```
facelessai-backend/
  main.py
  requirements.txt
  Dockerfile
  railway.json
```

### 3. Conecta Railway

1. Ve a railway.app
2. "New Project" → "Deploy from GitHub repo"
3. Selecciona `facelessai-backend`
4. Railway detecta el Dockerfile automáticamente
5. Clic en "Deploy"

### 4. Obtén la URL pública

1. En Railway → tu servicio → "Settings" → "Networking"
2. Clic en "Generate Domain"
3. Copia la URL — será algo como: `facelessai-backend.up.railway.app`

### 5. Pon la URL en el dashboard

En el dashboard FacelessAI → ⚙️ Configuración → campo "Backend URL"
Pega tu URL de Railway.

## Endpoints

- `GET /` — Info del servidor
- `GET /health` — Health check
- `POST /generate` — Genera un vídeo (devuelve job_id)
- `GET /status/{job_id}` — Estado del job
- `GET /download/{job_id}` — Descarga el MP4

## Ejemplo de llamada

```json
POST /generate
{
  "audio_b64": "base64_del_mp3...",
  "pexels_clips": [
    "https://videos.pexels.com/...",
    "https://videos.pexels.com/..."
  ],
  "script": "Tu guión aquí...",
  "title": "Título del vídeo",
  "lang": "es"
}
```

## Coste en Railway

- Plan Hobby: gratis hasta $5/mes de uso
- Para 100 vídeos/mes: ~$0-2/mes
- Sin tarjeta de crédito para empezar
