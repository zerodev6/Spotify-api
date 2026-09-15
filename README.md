# YouTube Music API

An API-only music discovery and playback backend built with FastAPI and `yt-dlp`.
It exposes the endpoints needed by a music-listening client without requiring a
YouTube API key.

## Endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/api/search?q=song` | Search YouTube music videos |
| GET | `/api/trending` | Get current YouTube trending videos |
| GET | `/api/stream/{video_id}` | Resolve a playable audio URL |
| GET | `/api/recommendations/{video_id}` | Find similar tracks |
| GET | `/api/lyrics/{video_id}` | Find lyrics through LRCLIB |
| GET | `/api/artist/{artist_name}` | Find an artist's tracks |
| GET | `/health` | Health check for Koyeb |

Interactive API documentation is available at `/docs`.

## Important stream behavior

`/api/stream/{video_id}` returns a direct YouTube media URL. That URL is
temporary and expires, so the client should request it again when playback
fails or the URL expires. The API does not download or permanently store
YouTube media.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open <http://localhost:8000/docs>.

## Docker / Koyeb

The included `Dockerfile` listens on port `8000`, which is the default Koyeb
service port.

```bash
docker build -t youtube-music-api .
docker run --rm -p 8000:8000 youtube-music-api
```

For Koyeb, create a service from the GitHub repository, choose **Docker** as
the build method, and set the exposed/service port to `8000`. No environment
variables are required for the default configuration.

Optional environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins |
| `CACHE_TTL_SECONDS` | `300` | In-memory cache duration |
| `RATE_LIMIT_PER_MINUTE` | `60` | Per-IP request limit |
| `YT_COOKIES_FILE` | unset | Optional path to a cookies file for environments where YouTube requires it |

## Example requests

```bash
curl "http://localhost:8000/api/search?q=daft%20punk&limit=10"
curl "http://localhost:8000/api/trending?limit=10"
curl "http://localhost:8000/api/stream/dQw4w9WgXcQ"
curl "http://localhost:8000/api/recommendations/dQw4w9WgXcQ"
curl "http://localhost:8000/api/lyrics/dQw4w9WgXcQ"
curl "http://localhost:8000/api/artist/Daft%20Punk"
```

## Responsible use

This project is intended for metadata discovery and playback of content the
user is allowed to access. Respect YouTube's Terms of Service, copyright,
robots/rate limits, and the rights of artists and rights holders. Do not use
this service to download, rehost, or redistribute content without permission.
