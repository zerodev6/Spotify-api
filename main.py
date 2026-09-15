import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from spotapi import Song
import yt-dlp as yt_dlp
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="SpotAPI + yt-dlp Pure Streamer", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

spot_song = Song()
stream_cache = {}

def get_stream_via_ytdlp(title: str, artist: str) -> Optional[str]:
    """Uses yt-dlp to search and extract a direct audio stream URL based on Spotify metadata."""
    cache_key = f"{artist} - {title}".lower()
    if cache_key in stream_cache:
        return stream_cache[cache_key]
        
    query = f"ytsearch1:{title} {artist} audio"
    ydl_opts = {
        'format': 'bestaudio/best',
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info and info['entries']:
                entry = info['entries'][0]
                stream_url = entry.get('url')
                if stream_url:
                    stream_cache[cache_key] = stream_url
                    return stream_url
    except Exception as e:
        logger.error(f"yt-dlp extraction error: {e}")
        
    return None

# ==================== ENDPOINTS ====================
@app.get("/")
def home():
    return {
        "status": "✅ SpotAPI + yt-dlp Engine Active",
        "endpoints": {
            "search": "/api/search?q=track_name",
            "stream": "/api/stream?title=SongName&artist=ArtistName"
        }
    }

@app.get("/api/search")
def search_tracks(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=25)):
    """Search Spotify catalog smoothly using SpotAPI"""
    try:
        results = spot_song.query_songs(q, limit=limit)
        items = results.get("data", {}).get("searchV2", {}).get("tracksV2", {}).get("items", [])
        
        tracks = []
        for item in items:
            track_data = item.get("item", {}).get("data", {})
            title = track_data.get("name")
            album = track_data.get("albumOfTrack", {}).get("name", "")
            artists = [a.get("profile", {}).get("name") for a in track_data.get("artists", {}).get("items", [])]
            artist_name = ", ".join(artists) if artists else "Unknown"
            duration_ms = track_data.get("duration", {}).get("totalMilliseconds", 0)
            
            images = track_data.get("albumOfTrack", {}).get("coverArt", {}).get("sources", [])
            thumbnail = images[0].get("url") if images else ""
            
            tracks.append({
                "title": title,
                "artist": artist_name,
                "album": album,
                "duration_seconds": duration_ms // 1000,
                "thumbnail": thumbnail
            })
            
        return {"query": q, "count": len(tracks), "tracks": tracks}
    except Exception as e:
        logger.error(f"SpotAPI search error: {e}")
        raise HTTPException(status_code=500, detail="Spotify catalog search failed")

@app.get("/api/stream")
async def stream_track(title: str, artist: str):
    """Maps Spotify song identity to an immediate stream URL via yt-dlp"""
    stream_url = get_stream_via_ytdlp(title, artist)
    if not stream_url:
        raise HTTPException(status_code=404, detail="Could not resolve playable audio link.")
        
    return RedirectResponse(url=stream_url, status_code=307)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
