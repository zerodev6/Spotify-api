import os
import re
import httpx
import yt_dlp
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# ============================================================
# CONFIG
# ============================================================
YOUTUBE_API_KEY = "AIzaSyDZqNa-pCcrDQDfo1PB5-LMoIk3mkC9gLg"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
LRCLIB_BASE = "https://lrclib.net/api"

# ============================================================
# APP SETUP
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

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
# HELPERS
# ============================================================
_ISO_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")

def parse_iso_duration(iso: str) -> int:
    """Convert ISO8601 (PT3M45S) to total seconds."""
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
    """Prefer high → medium → default."""
    for key in ("high", "medium", "standard", "maxres", "default"):
        if key in thumbnails:
            return thumbnails[key]["url"]
    return ""


async def yt_search_music(query: str, limit: int = 10) -> list[dict]:
    """Search YouTube Data API limited to Music category."""
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
    """Fetch snippet + contentDetails for one or many video IDs."""
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


def extract_audio_stream(video_id: str) -> dict | None:
    """Use yt-dlp to get a direct audio-only stream URL (no video)."""
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        # Optional: provide cookies for age/region restricted content
        # "cookiefile": "cookies.txt",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}",
                download=False,
            )
            if not info:
                return None
            # pick best audio format
            formats = info.get("formats") or []
            audio_formats = [
                f for f in formats if f.get("acodec") not in (None, "none")
                and f.get("vcodec") in (None, "none")
            ]
            if not audio_formats:
                return None
            audio_formats.sort(key=lambda f: f.get("abr") or 0, reverse=True)
            best = audio_formats[0]
            return {
                "url": best.get("url"),
                "mimeType": f"audio/{best.get('ext', 'webm')}",
                "bitrate": best.get("abr"),
                "ext": best.get("ext"),
                "filesize": best.get("filesize"),
            }
    except Exception as e:
        print("yt-dlp error:", e)
        return None


async def fetch_lyrics(title: str, artist: str | None = None) -> str | None:
    """Fetch lyrics from LRCLIB (free, no key required)."""
    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{LRCLIB_BASE}/get", params=params)
            if r.status_code == 200:
                data = r.json()
                return data.get("plainLyrics") or data.get("syncedLyrics")
            # fallback to search
            r2 = await client.get(f"{LRCLIB_BASE}/search", params={"q": f"{artist or ''} {title}".strip()})
            if r2.status_code == 200 and r2.json():
                first = r2.json()[0]
                return first.get("plainLyrics") or first.get("syncedLyrics")
    except Exception as e:
        print("lyrics error:", e)
    return None


# ============================================================
# 1. SEARCH  (metadata only — no stream URL)
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
        results.append({
            "title": sn["title"],
            "artist": sn["channelTitle"],
            "poster": pick_thumbnail(sn.get("thumbnails", {})),
            "duration": parse_iso_duration(d["contentDetails"]["duration"]),
            "duration_formatted": fmt_duration(
                parse_iso_duration(d["contentDetails"]["duration"])
            ),
        })
    return {"success": True, "query": q, "count": len(results), "results": results}


# ============================================================
# 2. SONG  (metadata + direct audio stream + optional lyrics)
#    — NO videoId ever returned
# ============================================================
@app.get("/api/song")
async def get_song(
    q: str = Query(..., description="Song name / artist / query"),
    include_lyrics: bool = Query(True),
):
    # 1. Search for the top result
    items = await yt_search_music(q, limit=1)
    if not items:
        raise HTTPException(404, "Song not found")

    internal_video_id = items[0]["id"]["videoId"]

    # 2. Fetch metadata from YouTube Data API
    details = await yt_video_details([internal_video_id])
    d = details.get(internal_video_id)
    if not d:
        raise HTTPException(404, "Metadata not found")

    sn = d["snippet"]
    title = sn["title"]
    artist = sn["channelTitle"]
    poster = pick_thumbnail(sn.get("thumbnails", {}))
    duration = parse_iso_duration(d["contentDetails"]["duration"])

    # 3. Extract direct audio stream (audio-only, no video URL)
    stream = extract_audio_stream(internal_video_id)
    if not stream or not stream.get("url"):
        raise HTTPException(500, "Could not extract audio stream")

    # 4. Optional lyrics
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
        "stream_url": stream["url"],            # direct audio-only URL
        "stream_mime": stream["mimeType"],
        "stream_bitrate": stream["bitrate"],
        "lyrics": lyrics_text,
    }


# ============================================================
# 3. TRENDING MUSIC  (YouTube Data API mostPopular chart)
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
# 4. CORS PROXY  (for the googlevideo.com audio URL)
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
# HEALTH
# ============================================================
@app.get("/")
async def root():
    return {"status": "ok", "service": "Direct Audio Music API"}
