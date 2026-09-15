from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx
import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, all optional for a zero-config deployment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    allowed_origins: str = "*"
    cache_ttl_seconds: int = 300
    rate_limit_per_minute: int = 60
    yt_cookies_file: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        if self.allowed_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
USER_AGENT = "YouTubeMusicAPI/1.0 (+https://github.com/your-username/youtube-music-api)"


class TTLCache:
    """Small process-local cache; safe for a single Koyeb instance."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= time.monotonic():
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        if self.ttl_seconds <= 0:
            return
        async with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_seconds, value)


cache = TTLCache(settings.cache_ttl_seconds)
rate_windows: defaultdict[str, deque[float]] = defaultdict(deque)
rate_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    rate_windows.clear()


app = FastAPI(
    title="YouTube Music API",
    description="YouTube-backed music discovery, playback URL resolution, recommendations, artist search, and lyrics.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.allowed_origins.strip() != "*",
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def simple_rate_limit(request: Request, call_next):
    """A lightweight guard against accidental request floods."""
    if settings.rate_limit_per_minute > 0 and request.url.path.startswith("/api/"):
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        async with rate_lock:
            window = rate_windows[client_ip]
            while window and window[0] <= now - 60:
                window.popleft()
            if len(window) >= settings.rate_limit_per_minute:
                raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again shortly.")
            window.append(now)
    response = await call_next(request)
    response.headers["X-API-Version"] = "1.0.0"
    return response


def yt_options(*, format_selector: str | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "socket_timeout": 20,
        "http_headers": {"User-Agent": USER_AGENT},
    }
    if format_selector:
        options["format"] = format_selector
    if settings.yt_cookies_file:
        options["cookiefile"] = settings.yt_cookies_file
    return options


def extract_sync(url: str, *, format_selector: str | None = None) -> dict[str, Any]:
    with yt_dlp.YoutubeDL(yt_options(format_selector=format_selector)) as downloader:
        return downloader.extract_info(url, download=False)


async def extract(url: str, *, format_selector: str | None = None) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(extract_sync, url, format_selector=format_selector)
    except Exception as exc:
        message = str(exc).strip().splitlines()[-1] if str(exc).strip() else "YouTube extraction failed"
        raise HTTPException(status_code=502, detail=message[:400]) from exc


def validate_video_id(video_id: str) -> str:
    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise HTTPException(status_code=400, detail="Invalid YouTube video ID")
    return video_id


def iso_duration(seconds: Any) -> str | None:
    if seconds is None:
        return None
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def thumbnail(video_id: str, source: str | None = None) -> str:
    return source or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def normalize_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    video_id = entry.get("id") or entry.get("video_id")
    if not video_id:
        return None
    return {
        "video_id": video_id,
        "title": entry.get("title") or "Untitled",
        "artist": entry.get("artist") or entry.get("uploader") or entry.get("channel") or "Unknown artist",
        "channel": entry.get("channel") or entry.get("uploader"),
        "thumbnail": thumbnail(video_id, entry.get("thumbnail")),
        "duration": iso_duration(entry.get("duration")),
        "duration_seconds": entry.get("duration"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "view_count": entry.get("view_count"),
        "published_at": entry.get("upload_date"),
        "live": bool(entry.get("is_live")),
    }


def clean_title(title: str) -> str:
    title = re.sub(r"\[[^\]]+\]|\([^)]*(official|video|audio|lyrics|visualizer)[^)]*\)", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title)
    return title.strip(" -–|")


def entries_from_info(info: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    raw_entries = info.get("entries") or []
    results: list[dict[str, Any]] = []
    for raw_entry in raw_entries:
        if not raw_entry:
            continue
        item = normalize_entry(raw_entry)
        if item and item["video_id"] not in {result["video_id"] for result in results}:
            results.append(item)
        if len(results) >= limit:
            break
    return results


async def search_youtube(query: str, limit: int) -> list[dict[str, Any]]:
    key = f"search:{query.lower()}:{limit}"
    cached = await cache.get(key)
    if cached is not None:
        return cached
    info = await extract(f"ytsearch{limit}:{query}")
    results = entries_from_info(info, limit)
    await cache.set(key, results)
    return results


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": "YouTube Music API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "search": "/api/search?q=song",
            "trending": "/api/trending",
            "stream": "/api/stream/{video_id}",
            "recommendations": "/api/recommendations/{video_id}",
            "lyrics": "/api/lyrics/{video_id}",
            "artist": "/api/artist/{artist_name}",
        },
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "youtube-music-api"}


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200, description="Song, artist, or album to search for"),
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    results = await search_youtube(f"{q} music", limit)
    return {"query": q, "count": len(results), "results": results}


@app.get("/api/trending")
async def trending(limit: int = Query(20, ge=1, le=50)) -> dict[str, Any]:
    key = f"trending:{limit}"
    cached = await cache.get(key)
    if cached is not None:
        return cached

    source = "youtube_trending"
    try:
        info = await extract("https://www.youtube.com/feed/trending")
        results = entries_from_info(info, limit)
    except HTTPException:
        results = []

    # YouTube can hide the trending feed from unauthenticated requests. Keep
    # the endpoint useful with a music-focused discovery fallback.
    if not results:
        source = "youtube_music_search_fallback"
        results = await search_youtube("trending music official", limit)

    payload = {"source": source, "count": len(results), "results": results}
    await cache.set(key, payload)
    return payload


@app.get("/api/stream/{video_id}")
async def stream(video_id: str) -> dict[str, Any]:
    video_id = validate_video_id(video_id)
    info = await extract(
        f"https://www.youtube.com/watch?v={video_id}",
        format_selector="bestaudio[ext=m4a]/bestaudio/best",
    )
    stream_url = info.get("url")
    if not stream_url:
        raise HTTPException(status_code=404, detail="No playable audio stream was found")
    selected = info.get("requested_formats", [{}])[0] if info.get("requested_formats") else info
    return {
        "video_id": video_id,
        "title": info.get("title"),
        "artist": info.get("uploader") or info.get("channel"),
        "stream_url": stream_url,
        "mime_type": selected.get("mime_type") or info.get("mime_type"),
        "ext": selected.get("ext") or info.get("ext"),
        "bitrate_kbps": selected.get("abr") or info.get("abr"),
        "duration_seconds": info.get("duration"),
        "expires": info.get("expiry"),
        "is_live": bool(info.get("is_live")),
        "note": "This URL is temporary. Request this endpoint again after it expires.",
    }


@app.get("/api/recommendations/{video_id}")
async def recommendations(video_id: str, limit: int = Query(20, ge=1, le=50)) -> dict[str, Any]:
    video_id = validate_video_id(video_id)
    key = f"recommendations:{video_id}:{limit}"
    cached = await cache.get(key)
    if cached is not None:
        return cached

    info = await extract(f"https://www.youtube.com/watch?v={video_id}")
    results: list[dict[str, Any]] = []
    for entry in info.get("related_entries") or []:
        if not entry or entry.get("id") == video_id:
            continue
        item = normalize_entry(entry)
        if item:
            results.append(item)
        if len(results) >= limit:
            break

    # Related videos are not always returned to unauthenticated clients.
    if not results:
        title = clean_title(info.get("title") or "")
        artist = info.get("uploader") or info.get("channel") or ""
        results = await search_youtube(f"{artist} {title} similar songs", limit)
        results = [item for item in results if item["video_id"] != video_id]

    payload = {
        "video_id": video_id,
        "based_on": {"title": info.get("title"), "artist": info.get("uploader") or info.get("channel")},
        "count": len(results),
        "results": results[:limit],
    }
    await cache.set(key, payload)
    return payload


@app.get("/api/lyrics/{video_id}")
async def lyrics(video_id: str) -> dict[str, Any]:
    video_id = validate_video_id(video_id)
    key = f"lyrics:{video_id}"
    cached = await cache.get(key)
    if cached is not None:
        return cached

    info = await extract(f"https://www.youtube.com/watch?v={video_id}")
    title = clean_title(info.get("title") or "")
    artist = info.get("artist") or info.get("uploader") or info.get("channel") or ""
    if not title:
        raise HTTPException(status_code=404, detail="Track metadata was not found")

    params = {"track_name": title, "artist_name": artist}
    try:
        async with httpx.AsyncClient(
            timeout=12,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        ) as client:
            response = await client.get("https://lrclib.net/api/search", params=params)
            response.raise_for_status()
            matches = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Lyrics provider is temporarily unavailable") from exc

    match = matches[0] if isinstance(matches, list) and matches else None
    payload = {
        "video_id": video_id,
        "track": title,
        "artist": artist,
        "found": bool(match),
        "source": "lrclib" if match else None,
        "lyrics": (match or {}).get("plainLyrics"),
        "synced_lyrics": (match or {}).get("syncedLyrics"),
        "album": (match or {}).get("albumName"),
        "duration_seconds": (match or {}).get("duration"),
    }
    await cache.set(key, payload)
    return payload


@app.get("/api/artist/{artist_name}")
async def artist(
    artist_name: str,
    limit: int = Query(30, ge=1, le=50),
) -> dict[str, Any]:
    artist_name = artist_name.strip()
    if not artist_name or len(artist_name) > 100:
        raise HTTPException(status_code=400, detail="Artist name must be between 1 and 100 characters")
    results = await search_youtube(f"{artist_name} official songs", limit)
    return {
        "artist": artist_name,
        "count": len(results),
        "results": results,
        "search_url": f"https://www.youtube.com/results?search_query={quote(artist_name)}",
    }
