import os
import time
from typing import Optional, Dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from spotapi import Song
import logging
import requests

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
SAVENOW_API_KEY = "ca2e48e551709c20d4192854c6132309fa495303"

app = FastAPI(title="SpotAPI Pure Spotify Streamer", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

spot_song = Song()
stream_cache = {}

# ==================== STREAM RESOLVER VIA SAVE-NOW ====================
def resolve_stream_from_metadata(title: str, artist: str) -> Optional[str]:
    """Uses track title and artist from Spotify metadata to fetch direct stream URL"""
    cache_key = f"{artist}-{title}".lower()
    if cache_key in stream_cache:
        return stream_cache[cache_key]
        
    # Search query formatted strictly for media lookup matching the Spotify song
    search_query = f"{title} {artist} audio"
    target_url = f"https://www.youtube.com/results?search_query={requests.utils.quote(search_query)}"
    
    # Alternatively, you can use SaveNow's search or URL resolver directly with a query/name string if supported:
    init_endpoint = "https://p.savenow.to/ajax/download.php"
    progress_endpoint = "https://p.savenow.to/ajax/progress.php"
    
    params = {
        "url": f"https://open.spotify.com/search/{requests.utils.quote(search_query)}", # or general query format
        "format": "mp3",
        "apikey": SAVENOW_API_KEY,
        "add_info": 1
    }
    
    try:
        response = requests.get(init_endpoint, params=params, timeout=15)
        data = response.json()
        if not data.get("success"):
            return None
            
        job_id = data.get("id")
        for _ in range(30):
            time.sleep(1.5)
            prog_resp = requests.get(progress_endpoint, params={"id": job_id}, timeout=10)
            prog_data = prog_resp.json()
            
            if prog_data.get("progress", 0) >= 1000 and prog_data.get("download_url"):
                url = prog_data.get("download_url")
                stream_cache[cache_key] = url
                return url
        return None
    except Exception as e:
        logger.error(f"Stream resolution error: {e}")
        return None

# ==================== ENDPOINTS ====================
@app.get("/")
def home():
    return {
        "status": "✅ SpotAPI Gateway Active",
        "endpoints": {
            "search_spotify": "/api/spotify/search?q=song_name",
            "stream_spotify": "/api/spotify/stream?title=SongName&artist=ArtistName"
        }
    }

@app.get("/api/spotify/search")
def search_spotify_catalog(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=25)):
    """Fetch real-time Spotify track data via SpotAPI without requiring developer credentials"""
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
        logger.error(f"SpotAPI error: {e}")
        raise HTTPException(status_code=500, detail="Spotify catalog search failed")

@app.get("/api/spotify/stream")
async def stream_spotify_track(title: str, artist: str):
    """Directly converts Spotify song identity into a playable stream link"""
    stream_url = resolve_stream_from_metadata(title, artist)
    if not stream_url:
        raise HTTPException(status_code=500, detail="Could not generate stream URL for this Spotify track.")
        
    return RedirectResponse(url=stream_url, status_code=307)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
