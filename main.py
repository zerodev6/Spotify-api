import os
import time
from typing import Optional, Dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from spotapi import Song
from ytmusicapi import YTMusic
import logging
import requests

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
SAVENOW_API_KEY = "ca2e48e551709c20d4192854c6132309fa495303"

app = FastAPI(title="SpotAPI + SaveNow Hybrid Streamer", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

spot_song = Song()
ytmusic = YTMusic()
stream_cache = {}

# ==================== SAVENOW ASYNC STREAM RESOLVER ====================
def get_savenow_stream(video_id: str) -> Optional[str]:
    """Triggers SaveNow download job, polls progress.php until 1000, returns final stream URL."""
    if video_id in stream_cache:
        return stream_cache[video_id]
        
    target_url = f"https://www.youtube.com/watch?v={video_id}"
    init_endpoint = "https://p.savenow.to/ajax/download.php"
    progress_endpoint = "https://p.savenow.to/ajax/progress.php"
    
    params = {
        "url": target_url,
        "format": "mp3",
        "apikey": SAVENOW_API_KEY,
        "add_info": 1,
        "allow_extended_duration": 1
    }
    
    try:
        logger.info(f"Initiating SaveNow job for video ID: {video_id}")
        response = requests.get(init_endpoint, params=params, timeout=15)
        if response.status_code != 200:
            return None
            
        data = response.json()
        if not data.get("success"):
            logger.error(f"SaveNow job rejected: {data}")
            return None
            
        job_id = data.get("id")
        
        # Poll progress endpoint (up to 30 tries ~ 45 seconds timeout)
        for _ in range(30):
            time.sleep(1.5)
            prog_resp = requests.get(progress_endpoint, params={"id": job_id}, timeout=10)
            if prog_resp.status_code != 200:
                continue
                
            prog_data = prog_resp.json()
            if prog_data.get("success") == 0 or prog_data.get("text") == "Failed":
                return None
                
            progress_val = prog_data.get("progress", 0)
            download_url = prog_data.get("download_url")
            
            # 1000 indicates 100% completion
            if progress_val >= 1000 and download_url:
                stream_cache[video_id] = download_url
                return download_url
                
        return None
    except Exception as e:
        logger.error(f"SaveNow gateway error: {e}")
        return None

# ==================== ENDPOINTS ====================
@app.get("/")
def home():
    return {
        "status": "✅ SpotAPI + SaveNow Gateway Online",
        "endpoints": {
            "search": "/api/search?q=track_name",
            "stream_by_metadata": "/api/stream?title=SongName&artist=ArtistName",
            "stream_by_id": "/api/stream/video/{video_id}"
        }
    }

@app.get("/api/search")
def search_tracks(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=25)):
    """Search tracks seamlessly using SpotAPI without requiring developer tokens"""
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
            
            # Grab high-res cover art if available
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
async def stream_by_metadata(title: str, artist: str):
    """Maps a Spotify track (Title + Artist) to YouTube, then streams via SaveNow"""
    search_query = f"{title} {artist}"
    try:
        search_results = ytmusic.search(search_query, filter="songs", limit=1)
        if not search_results:
            raise HTTPException(status_code=404, detail="Could not map track to a media source.")
            
        video_id = search_results[0].get("videoId")
        stream_url = get_savenow_stream(video_id)
        
        if not stream_url:
            raise HTTPException(status_code=500, detail="Failed to resolve stream link through SaveNow.")
            
        return RedirectResponse(url=stream_url, status_code=307)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Streaming mapping error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
