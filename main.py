import os
import re
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


# ============================== CONFIG ==============================

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "AIzaSyDZqNa-pCcrDQDfo1PB5-LMoIk3mkC9gLg")
YOUTUBE_BASE = "https://www.googleapis.com/youtube/v3"
MUSIC_CATEGORY_ID = "10"

_CACHE: dict[str, tuple[float, object]] = {}
CACHE_MAX = 1000


# ============================== APP ==============================

app = FastAPI(title="YouTube Music API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================== CACHE ==============================

def cache_get(key: str):
    entry = _CACHE.get(key)
    if not entry:
        return None
    exp, value = entry
    if time.time() > exp:
        _CACHE.pop(key, None)
        return None
    return value


def cache_set(key: str, value, ttl: float = 120.0):
    if len(_CACHE) >= CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[key] = (time.time() + ttl, value)
    return value


# ============================== YOUTUBE CLIENT ==============================

async def yt_get(endpoint: str, params: dict) -> dict:
    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    clean["key"] = YOUTUBE_API_KEY

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{YOUTUBE_BASE}/{endpoint}", params=clean)

    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}

    if r.status_code >= 400:
        msg = (data.get("error") or {}).get("message") or f"YouTube API error {r.status_code}"
        status = 429 if r.status_code == 403 else r.status_code
        raise HTTPException(status_code=status, detail=msg)

    return data


# ============================== HELPERS ==============================

_ISO_RE = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def parse_duration(iso: str) -> dict:
    m = _ISO_RE.match(iso or "")
    if not m:
        return {"seconds": 0, "text": "0:00"}
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    seconds = d * 86400 + h * 3600 + mi * 60 + s
    hh, rem = divmod(seconds, 3600)
    mm, ss = divmod(rem, 60)
    text = f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"
    return {"seconds": seconds, "text": text}


def build_links(video_id: str) -> dict:
    return {
        "watch": f"https://www.youtube.com/watch?v={video_id}",
        "short": f"https://youtu.be/{video_id}",
        "embed": f"https://www.youtube.com/embed/{video_id}",
        "embedNoCookie": f"https://www.youtube-nocookie.com/embed/{video_id}",
        "shorts": f"https://www.youtube.com/shorts/{video_id}",
        "mobile": f"https://m.youtube.com/watch?v={video_id}",
        "thumbnails": {
            "default":  f"https://i.ytimg.com/vi/{video_id}/default.jpg",
            "medium":   f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
            "high":     f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "standard": f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
            "maxres":   f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        },
        "iframe": (
            f'<iframe width="560" height="315" '
            f'src="https://www.youtube.com/embed/{video_id}" '
            f'title="YouTube music player" frameborder="0" '
            f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; '
            f'gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>'
        ),
        "audio": (
            f'<audio controls src="https://www.youtube.com/embed/{video_id}"></audio>'
        ),
    }


_ARTIST_SPLIT_RE = re.compile(r"\s*[-–—|:]\s*|\s+ft\.?\s+|\s+feat\.?\s+", re.IGNORECASE)


def guess_song_artist(title: str, channel_title: str) -> dict:
    """
    Guess song name and artist from title.
    Common patterns:
      "Artist - Song"
      "Artist – Song (Official Video)"
      "Song | Artist"
      "Song (Official Music Video) - Artist"
    """
    if not title:
        return {"song": None, "artist": channel_title, "raw_title": title}

    # Strip common suffixes / brackets
    cleaned = re.sub(
        r"[\(\[]\s*(official|lyric|lyrics|audio|video|hd|4k|mv|m/v|music video|visualizer|prod\.?[^\)\]]*)[^\)\]]*[\)\]]",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    parts = _ARTIST_SPLIT_RE.split(cleaned, maxsplit=1)
    if len(parts) == 2 and all(p.strip() for p in parts):
        a, b = parts[0].strip(), parts[1].strip()
        # Heuristic: if left side looks like artist name (short, no spaces-heavy)
        # In most music videos it's "Artist - Song"
        if len(a.split()) <= 5:
            return {"artist": a, "song": b, "raw_title": title}
        return {"song": a, "artist": b, "raw_title": title}

    return {"song": cleaned or title, "artist": channel_title, "raw_title": title}


def build_metadata(v: dict) -> dict:
    """Build a rich music metadata object from a videos.list resource."""
    vid = v.get("id")
    s = v.get("snippet") or {}
    c = v.get("contentDetails") or {}
    st = v.get("statistics") or {}
    thumbs = s.get("thumbnails") or {}

    title = s.get("title") or ""
    channel_title = s.get("channelTitle") or ""
    parsed = guess_song_artist(title, channel_title)

    # Best available poster
    poster = (
        (thumbs.get("maxres") or {}).get("url")
        or (thumbs.get("standard") or {}).get("url")
        or (thumbs.get("high") or {}).get("url")
        or (thumbs.get("medium") or {}).get("url")
        or (thumbs.get("default") or {}).get("url")
    )

    # Extract year
    published = s.get("publishedAt") or ""
    year = published[:4] if len(published) >= 4 else None

    return {
        "id": vid,
        "song": parsed["song"],
        "artist": parsed["artist"],
        "channel": {
            "id": s.get("channelId"),
            "name": channel_title,
            "url": f"https://www.youtube.com/channel/{s.get('channelId')}" if s.get("channelId") else None,
        },
        "title": title,
        "description": s.get("description"),
        "publishedAt": published,
        "year": year,
        "duration": parse_duration(c.get("duration", "")),
        "poster": poster,
        "thumbnails": {
            "default":  (thumbs.get("default") or {}).get("url"),
            "medium":   (thumbs.get("medium") or {}).get("url"),
            "high":     (thumbs.get("high") or {}).get("url"),
            "standard": (thumbs.get("standard") or {}).get("url"),
            "maxres":   (thumbs.get("maxres") or {}).get("url"),
        },
        "tags": s.get("tags") or [],
        "categoryId": s.get("categoryId"),
        "language": s.get("defaultAudioLanguage") or s.get("defaultLanguage"),
        "stats": {
            "views": int(st.get("viewCount") or 0),
            "likes": int(st.get("likeCount") or 0),
            "comments": int(st.get("commentCount") or 0),
        },
        "links": build_links(vid),
    }


def music_from_search_item(item: dict) -> Optional[dict]:
    """Compact item used in search/trending listing (no extra API call)."""
    vid = (item.get("id") or {}).get("videoId") or item.get("id")
    if not vid:
        return None
    s = item.get("snippet") or {}
    thumbs = s.get("thumbnails") or {}
    title = s.get("title") or ""
    channel_title = s.get("channelTitle") or ""
    parsed = guess_song_artist(title, channel_title)

    poster = (
        (thumbs.get("high") or {}).get("url")
        or (thumbs.get("medium") or {}).get("url")
        or (thumbs.get("default") or {}).get("url")
    )

    return {
        "id": vid,
        "song": parsed["song"],
        "artist": parsed["artist"],
        "title": title,
        "channelTitle": channel_title,
        "channelId": s.get("channelId"),
        "publishedAt": s.get("publishedAt"),
        "poster": poster,
        "links": build_links(vid),
    }


async def fetch_metadata(ids: list[str]) -> list[dict]:
    """Fetch full metadata (with duration, stats) for up to 50 ids."""
    raw = await yt_get("videos", {
        "part": "snippet,contentDetails,statistics",
        "id": ",".join(ids[:50]),
        "maxResults": 50,
    })
    return [build_metadata(v) for v in raw.get("items", [])]


# ============================== ROUTES ==============================

@app.get("/")
def root():
    return {
        "ok": True,
        "service": "youtube-music-api",
        "endpoints": {
            "search":         "/api/music/search?q=song+name",
            "trending_music": "/api/music/trending?regionCode=US",
            "by_id":          "/api/music/{video_id}",
            "by_ids":         "/api/music?ids=ID1,ID2",
            "meta_and_links": "/api/music/links/{video_id}",
            "embed":          "/api/music/embed/{video_id}",
            "docs":           "/docs",
        },
    }


@app.get("/healthz")
def health():
    return {"ok": True}


# ------------------------------ SEARCH MUSIC ------------------------------

@app.get("/api/music/search")
async def search_music(
    q: str = Query(..., description="Song name, artist, album..."),
    limit: int = Query(10, ge=1, le=50),
    order: str = Query("relevance", description="relevance | viewCount | rating | date"),
    regionCode: Optional[str] = None,
    pageToken: Optional[str] = None,
):
    limit = max(1, min(limit, 50))
    ck = f"search|{q}|{limit}|{order}|{regionCode}|{pageToken}"
    if (cached := cache_get(ck)) is not None:
        return {"ok": True, "cached": True, **cached}

    raw = await yt_get("search", {
        "part": "snippet",
        "q": q,
        "type": "video",
        "videoCategoryId": MUSIC_CATEGORY_ID,
        "order": order,
        "maxResults": limit,
        "regionCode": regionCode,
        "pageToken": pageToken,
        "safeSearch": "none",
    })

    items = [x for x in (music_from_search_item(i) for i in raw.get("items", [])) if x]

    payload = {
        "query": q,
        "totalResults": raw.get("pageInfo", {}).get("totalResults", 0),
        "nextPageToken": raw.get("nextPageToken"),
        "prevPageToken": raw.get("prevPageToken"),
        "items": items,
    }
    cache_set(ck, payload, ttl=120)
    return {"ok": True, **payload}


# ------------------------------ TRENDING MUSIC ------------------------------

@app.get("/api/music/trending")
async def trending_music(
    regionCode: str = Query("US", description="ISO 3166-1 alpha-2, e.g. US, IN, GB, LK"),
    limit: int = Query(10, ge=1, le=50),
):
    limit = max(1, min(limit, 50))
    ck = f"trending|{regionCode}|{limit}"
    if (cached := cache_get(ck)) is not None:
        return {"ok": True, "cached": True, "regionCode": regionCode, "items": cached}

    raw = await yt_get("videos", {
        "part": "snippet,contentDetails,statistics",
        "chart": "mostPopular",
        "videoCategoryId": MUSIC_CATEGORY_ID,
        "regionCode": regionCode,
        "maxResults": limit,
    })

    items = [build_metadata(v) for v in raw.get("items", [])]
    cache_set(ck, items, ttl=180)
    return {"ok": True, "regionCode": regionCode, "items": items}


# ------------------------------ GET BY IDs ------------------------------

@app.get("/api/music")
async def music_by_ids(ids: str = Query(..., description="Comma-separated video IDs")):
    id_list = [s.strip() for s in ids.split(",") if s.strip()][:50]
    if not id_list:
        raise HTTPException(400, "At least one id is required")

    ck = "ids|" + ",".join(id_list)
    if (cached := cache_get(ck)) is not None:
        return {"ok": True, "cached": True, "items": cached}

    items = await fetch_metadata(id_list)
    cache_set(ck, items, ttl=300)
    return {"ok": True, "items": items}


# ------------------------------ SINGLE VIDEO (metadata only) ------------------------------

@app.get("/api/music/{video_id}")
async def single_music(video_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(400, "Invalid video id")

    ck = f"video|{video_id}"
    if (cached := cache_get(ck)) is not None:
        return {"ok": True, "cached": True, "data": cached}

    items = await fetch_metadata([video_id])
    if not items:
        raise HTTPException(404, "Video not found")

    cache_set(ck, items[0], ttl=300)
    return {"ok": True, "data": items[0]}


# ------------------------------ METADATA + LINKS (the main one) ------------------------------

@app.get("/api/music/links/{video_id}")
async def music_links(video_id: str):
    """
    Returns full metadata (song, artist, duration, poster, stats)
    AND all embed/watch/thumbnail links in one response.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(400, "Invalid video id")

    ck = f"video|{video_id}"
    if (cached := cache_get(ck)) is not None:
        return {"ok": True, "cached": True, "data": cached}

    items = await fetch_metadata([video_id])
    if not items:
        raise HTTPException(404, "Video not found")

    data = items[0]
    cache_set(ck, data, ttl=300)
    return {"ok": True, "data": data}


# ------------------------------ EMBED ------------------------------

@app.get("/api/music/embed/{video_id}")
async def music_embed(video_id: str, autoplay: bool = False, width: int = 560, height: int = 315):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(400, "Invalid video id")

    params = f"?autoplay={1 if autoplay else 0}"
    embed_url = f"https://www.youtube.com/embed/{video_id}{params}"

    return {
        "ok": True,
        "id": video_id,
        "embed_url": embed_url,
        "iframe": (
            f'<iframe width="{width}" height="{height}" src="{embed_url}" '
            f'title="YouTube music player" frameborder="0" '
            f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; '
            f'gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>'
        ),
        "audio_tag": f'<audio controls src="{embed_url}"></audio>',
    }


# ============================== ERROR HANDLER ==============================

@app.exception_handler(HTTPException)
async def http_exc(_req: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail})
