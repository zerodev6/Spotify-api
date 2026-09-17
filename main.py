import os
import re
import json
import asyncio
import subprocess
import traceback
from contextlib import asynccontextmanager

import httpx
import yt_dlp
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# ============================================================
# CONFIG
# ============================================================
YOUTUBE_API_KEY = "AIzaSyDZqNa-pCcrDQDfo1PB5-LMoIk3mkC9gLg"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
LRCLIB_BASE = "https://lrclib.net/api"
COOKIES_PATH = os.getenv("COOKIES_PATH", "cookies.txt")

# Public Piped instances (fallback resolvers — no video ID exposed to client)
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://api.piped.yt",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.reallyaweso.me",
    "https://pipedapi.ducks.party",
]

# ============================================================
# APP SETUP
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up yt-dlp in background so first request isn't slow
    print("[startup] Music API ready")
    yield
    print("[shutdown] Music API stopped")

app = FastAPI(lifespan=lifespan, title="Direct Audio Music API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

# ============================================================
# HELPERS — YouTube Data API
# ============================================================
_ISO_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_iso_duration(iso: str) -> int:
    if not iso:
        return 0
    m = _ISO_DUR_RE.match(iso)
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def fmt_duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def pick_thumbnail(thumbnails: dict) -> str:
    for key in ("high", "medium", "standard", "maxres", "default"):
        if key in thumbnails:
            return thumbnails[key]["url"]
    return ""


async def yt_search_music(query: str, limit: int = 10) -> list[dict]:
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "videoCategoryId": "10",  # Music
        "maxResults": limit,
        "key": YOUTUBE_API_KEY,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{YOUTUBE_API_BASE}/search", params=params)
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)
        return r.json().get("items", [])


async def yt_video_details(video_ids: list[str]) -> dict:
    if not video_ids:
        return {}
    params = {
        "part": "snippet,contentDetails",
        "id": ",".join(video_ids),
        "key": YOUTUBE_API_KEY,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{YOUTUBE_API_BASE}/videos", params=params)
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)
        items = r.json().get("items", [])
    return {it["id"]: it for it in items}


# ============================================================
# HELPERS — Stream Extraction (3-layer fallback)
# ============================================================

def _pick_best_audio(formats: list[dict]) -> dict | None:
    """Choose the highest-bitrate audio-only format."""
    audio_only = [
        f for f in formats
        if f.get("acodec") not in (None, "none")
        and f.get("vcodec") in (None, "none")
        and f.get("url")
    ]
    if not audio_only:
        # fallback: any format with audio
        audio_only = [
            f for f in formats
            if f.get("acodec") not in (None, "none") and f.get("url")
        ]
    if not audio_only:
        return None
    audio_only.sort(key=lambda f: f.get("abr") or 0, reverse=True)
    return audio_only[0]


def _format_stream(best: dict) -> dict:
    return {
        "url": best.get("url"),
        "mimeType": f"audio/{best.get('ext', 'webm')}",
        "bitrate": best.get("abr"),
        "ext": best.get("ext"),
        "filesize": best.get("filesize"),
    }


# -------- Layer 1: yt-dlp Python library --------
def extract_audio_ytdlp(video_id: str) -> dict | None:
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "quiet": True,
        "no_warnings": False,
        "skip_download": True,
        "noplaylist": True,
        "verbose": True,
        "extractor_retries": 5,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "tv_embedded", "web_safari"],
            }
        },
    }
    if os.path.exists(COOKIES_PATH):
        ydl_opts["cookiefile"] = COOKIES_PATH

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}",
                download=False,
            )
            if not info:
                return None
            best = _pick_best_audio(info.get("formats") or [])
            if not best:
                return None
            return _format_stream(best)
    except Exception as e:
        print(f"[layer1 yt-dlp lib] error for {video_id}: {e}")
        traceback.print_exc()
        return None


# -------- Layer 2: yt-dlp CLI (always-latest logic) --------
def extract_audio_cli(video_id: str) -> dict | None:
    cmd = [
        "yt-dlp",
        "-f", "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "--dump-json",
        "--no-warnings",
        "--no-playlist",
        "--extractor-args", "youtube:player_client=android,ios,tv_embedded",
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    if os.path.exists(COOKIES_PATH):
        cmd += ["--cookies", COOKIES_PATH]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=45
        )
        if result.returncode != 0:
            print(f"[layer2 yt-dlp CLI] failed for {video_id}")
            print(result.stderr[-800:])  # last 800 chars of stderr
            return None
        info = json.loads(result.stdout.strip().splitlines()[-1])
        best = {
            "url": info.get("url"),
            "ext": info.get("ext", "webm"),
            "abr": info.get("abr"),
            "filesize": info.get("filesize"),
        }
        if not best.get("url"):
            return None
        return _format_stream(best)
    except subprocess.TimeoutExpired:
        print(f"[layer2 yt-dlp CLI] timeout for {video_id}")
        return None
    except Exception as e:
        print(f"[layer2 yt-dlp CLI] error for {video_id}: {e}")
        return None


# -------- Layer 3: Piped public instances --------
async def extract_audio_piped(video_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=15) as client:
        for base in PIPED_INSTANCES:
            try:
                r = await client.get(f"{base}/streams/{video_id}")
                if r.status_code != 200:
                    continue
                data = r.json()
                audios = [s for s in data.get("audioStreams", []) if s.get("url")]
                if not audios:
                    continue
                audios.sort(key=lambda s: s.get("bitrate") or 0, reverse=True)
                best = audios[0]
                return {
                    "url": best["url"],
                    "mimeType": best.get("mimeType", "audio/webm"),
                    "bitrate": best.get("bitrate"),
                    "ext": best.get("format", "webm"),
                    "filesize": best.get("contentLength"),
                }
            except Exception as e:
                print(f"[layer3 piped {base}] failed: {e}")
                continue
    return None


# -------- Orchestrator --------
async def resolve_stream(video_id: str) -> dict | None:
    """Try lib → CLI → Piped in order."""
    # Layer 1
    stream = await asyncio.to_thread(extract_audio_ytdlp, video_id)
    if stream and stream.get("url"):
        print(f"[resolve] layer1 OK for {video_id}")
        return stream

    # Layer 2
    stream = await asyncio.to_thread(extract_audio_cli, video_id)
    if stream and stream.get("url"):
        print(f"[resolve] layer2 OK for {video_id}")
        return stream

    # Layer 3
    stream = await extract_audio_piped(video_id)
    if stream and stream.get("url"):
        print(f"[resolve] layer3 OK for {video_id}")
        return stream

    print(f"[resolve] ALL LAYERS FAILED for {video_id}")
    return None


# ============================================================
# HELPERS — Lyrics (LRCLIB)
# ============================================================
async def fetch_lyrics(title: str, artist: str | None = None) -> str | None:
    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{LRCLIB_BASE}/get", params=params)
            if r.status_code == 200:
                data = r.json()
                return data.get("plainLyrics") or data.get("syncedLyrics")
            r2 = await client.get(
                f"{LRCLIB_BASE}/search",
                params={"q": f"{artist or ''} {title}".strip()},
            )
            if r2.status_code == 200 and r2.json():
                first = r2.json()[0]
                return first.get("plainLyrics") or first.get("syncedLyrics")
    except Exception as e:
        print(f"[lyrics] error: {e}")
    return None


# ============================================================
# ENDPOINT 1 — Health
# ============================================================
@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "Direct Audio Music API",
        "version": "2.0",
        "ytdlp_version": yt_dlp.version.__version__,
        "cookies_present": os.path.exists(COOKIES_PATH),
    }


# ============================================================
# ENDPOINT 2 — Search (metadata only)
# ============================================================
@app.get("/api/search")
async def search_songs(
    q: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=25),
):
    items = await yt_search_music(q, limit)
    ids = [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]
    details = await yt_video_details(ids)

    results = []
    for vid in ids:
        d = details.get(vid)
        if not d:
            continue
        sn = d["snippet"]
        dur = parse_iso_duration(d["contentDetails"]["duration"])
        results.append({
            "title": sn["title"],
            "artist": sn["channelTitle"],
            "poster": pick_thumbnail(sn.get("thumbnails", {})),
            "duration": dur,
            "duration_formatted": fmt_duration(dur),
        })
    return {"success": True, "query": q, "count": len(results), "results": results}


# ============================================================
# ENDPOINT 3 — Song (metadata + stream + lyrics)  ⭐
# ============================================================
@app.get("/api/song")
async def get_song(
    q: str = Query(..., description="Song name / artist / query"),
    include_lyrics: bool = Query(True),
):
    # 1. Search top result
    items = await yt_search_music(q, limit=1)
    if not items:
        raise HTTPException(404, "Song not found")

    internal_video_id = items[0]["id"]["videoId"]

    # 2. Metadata
    details = await yt_video_details([internal_video_id])
    d = details.get(internal_video_id)
    if not d:
        raise HTTPException(404, "Metadata not found")

    sn = d["snippet"]
    title = sn["title"]
    artist = sn["channelTitle"]
    poster = pick_thumbnail(sn.get("thumbnails", {}))
    duration = parse_iso_duration(d["contentDetails"]["duration"])

    # 3. Resolve stream via multi-layer fallback
    stream = await resolve_stream(internal_video_id)
    if not stream or not stream.get("url"):
        raise HTTPException(
            500,
            "Could not extract audio stream after trying yt-dlp (lib), "
            "yt-dlp (CLI), and Piped fallback. "
            "Check Koyeb logs for details, or add cookies.txt."
        )

    # 4. Lyrics
    lyrics_text = None
    if include_lyrics:
        lyrics_text = await fetch_lyrics(title, artist)

    return {
        "success": True,
        "title": title,
        "artist": artist,
        "duration": duration,
        "duration_formatted": fmt_duration(duration),
        "poster": poster,
        "stream_url": stream["url"],       # direct audio-only URL — NO video ID
        "stream_mime": stream["mimeType"],
        "stream_bitrate": stream["bitrate"],
        "lyrics": lyrics_text,
    }


# ============================================================
# ENDPOINT 4 — Trending
# ============================================================
@app.get("/api/trending")
async def trending(
    region: str = Query("US", description="ISO country code"),
    limit: int = Query(20, ge=1, le=50),
):
    params = {
        "part": "snippet,contentDetails",
        "chart": "mostPopular",
        "videoCategoryId": "10",  # Music
        "regionCode": region,
        "maxResults": limit,
        "key": YOUTUBE_API_KEY,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{YOUTUBE_API_BASE}/videos", params=params)
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)
        items = r.json().get("items", [])

    result = []
    for i, it in enumerate(items, start=1):
        sn = it["snippet"]
        dur = parse_iso_duration(it["contentDetails"]["duration"])
        result.append({
            "rank": i,
            "title": sn["title"],
            "artist": sn["channelTitle"],
            "poster": pick_thumbnail(sn.get("thumbnails", {})),
            "duration": dur,
            "duration_formatted": fmt_duration(dur),
        })
    return {"success": True, "region": region, "trending": result}


# ============================================================
# ENDPOINT 5 — CORS Proxy for audio streams
# ============================================================
@app.get("/api/proxy")
async def proxy_audio(url: str = Query(..., description="Encoded audio URL")):
    async def stream_gen():
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.youtube.com/",
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        stream_gen(),
        media_type="audio/webm",
        headers={
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        },
    )


# ============================================================
# LOCAL DEV
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
