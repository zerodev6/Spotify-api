import os
import time
from datetime import datetime, timedelta
from typing import Optional, Dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import logging
import requests

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
YOUTUBE_API_KEY = "AIzaSyDZqNa-pCcrDQDfo1PB5-LMoIk3mkC9gLg"
SAVENOW_API_KEY = "ca2e48e551709c20d4192854c6132309fa495303"

# ==================== CACHE MANAGER ====================
class CacheManager:
    def __init__(self, ttl_minutes: int = 30):
        self.cache = {}
        self.ttl = timedelta(minutes=ttl_minutes)
    
    def get(self, key: str):
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < self.ttl:
                return data
            else:
                del self.cache[key]
        return None
    
    def set(self, key: str, value):
        self.cache[key] = (value, datetime.now())

# ==================== SAVENOW ASYNC STREAM HANDLER ====================
class SaveNowAsyncStreamHandler:
    def __init__(self):
        self.stream_cache = {}
    
    def get_stream(self, video_id: str, format_type: str = "mp3") -> Optional[str]:
        """Initiates download job via SaveNow /ajax/download.php and polls /ajax/progress.php"""
        target_url = f"https://www.youtube.com/watch?v={video_id}"
        init_endpoint = "https://p.savenow.to/ajax/download.php"
        progress_endpoint = "https://p.savenow.to/ajax/progress.php"
        
        params = {
            "url": target_url,
            "format": format_type,
            "apikey": SAVENOW_API_KEY,
            "add_info": 1,
            "allow_extended_duration": 1
        }
        
        try:
            logger.info(f"Initiating SaveNow download job for {video_id} (format: {format_type})")
            response = requests.get(init_endpoint, params=params, timeout=15)
            
            if response.status_code != 200:
                logger.warning(f"SaveNow init failed with status {response.status_code}: {response.text}")
                return None
                
            data = response.json()
            if not data.get("success"):
                logger.error(f"SaveNow job creation rejected: {data}")
                return None
                
            job_id = data.get("id")
            if not job_id:
                logger.error("SaveNow response missing job ID.")
                return None
                
            logger.info(f"Job created successfully. ID: {job_id}. Polling for completion...")
            
            # Poll progress up to 30 times (~30-45 seconds timeout)
            max_retries = 30
            for attempt in range(max_retries):
                time.sleep(1.5)
                prog_resp = requests.get(progress_endpoint, params={"id": job_id}, timeout=10)
                
                if prog_resp.status_code != 200:
                    continue
                    
                prog_data = prog_resp.json()
                
                # Check for explicit failure markers
                if prog_data.get("success") == 0 or prog_data.get("text") == "Failed":
                    logger.error(f"SaveNow job failed during processing: {prog_data}")
                    return None
                
                progress_val = prog_data.get("progress", 0)
                download_url = prog_data.get("download_url")
                
                # 1000 represents 100% completion
                if progress_val >= 1000 and download_url:
                    logger.info(f"✅ Successfully resolved stream URL for {video_id}")
                    self.stream_cache[video_id] = (download_url, datetime.now())
                    return download_url
                    
            logger.warning(f"SaveNow job polling timed out for {video_id}")
            return None
            
        except Exception as e:
            logger.error(f"SaveNow workflow error for {video_id}: {str(e)}")
            return None
    
    def get_cached_stream(self, video_id: str) -> Optional[str]:
        if video_id in self.stream_cache:
            url, timestamp = self.stream_cache[video_id]
            # SaveNow dynamic download links typically expire after a period, cache for 15 mins max
            if datetime.now() - timestamp < timedelta(minutes=15):
                return url
            else:
                del self.stream_cache[video_id]
        return None

# ==================== FASTAPI APP ====================
app = FastAPI(title="SaveNow Official API Gateway Music Service", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ytmusic = YTMusic()
cache = CacheManager(ttl_minutes=30)
streamer = SaveNowAsyncStreamHandler()

# ==================== HELPER ====================
def extract_track(item: Dict) -> Optional[Dict]:
    try:
        video_id = item.get("videoId")
        if not video_id:
            return None
        
        artists = item.get("artists", [])
        artist_name = artists[0]["name"] if artists else "Unknown"
        
        return {
            "id": video_id,
            "title": item.get("title", "Unknown"),
            "artist": artist_name,
            "album": item.get("album", {}).get("name", ""),
            "duration": item.get("duration"),
            "thumbnail": item.get("thumbnails", [{}])[-1].get("url", "")
        }
    except:
        return None

# ==================== ENDPOINTS ====================
@app.get("/")
def home():
    return {
        "status": "✅ Online via Official SaveNow API v2",
        "endpoints": {
            "search": "/api/search?q=song",
            "trending": "/api/trending",
            "stream": "/api/stream/{video_id}?format=mp3",
            "recommendations": "/api/recommendations/{video_id}",
            "lyrics": "/api/lyrics/{video_id}",
            "artist": "/api/artist/{artist_name}",
        }
    }

@app.get("/api/search")
def search_tracks(q: str = Query(..., min_length=1), limit: int = Query(15, ge=1, le=30)):
    cache_key = f"search:{q}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        results = ytmusic.search(q, filter="songs", limit=limit)
        tracks = [extract_track(item) for item in results if extract_track(item)]
        response = {"query": q, "count": len(tracks), "tracks": tracks}
        cache.set(cache_key, response)
        return response
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail="Search failed")

@app.get("/api/trending")
def get_trending(limit: int = Query(20, ge=1, le=30)):
    cache_key = "trending"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        results = ytmusic.search("", filter="songs", limit=limit)
        tracks = []
        for i, item in enumerate(results[:limit]):
            track = extract_track(item)
            if track:
                track["rank"] = i + 1
                tracks.append(track)
        response = {"type": "trending", "count": len(tracks), "tracks": tracks}
        cache.set(cache_key, response)
        return response
    except Exception as e:
        logger.error(f"Trending error: {e}")
        raise HTTPException(status_code=500, detail="Trending failed")

@app.get("/api/stream/{video_id}")
async def stream_track(video_id: str, format: str = Query("mp3", description="mp3, 128, 360, 720, etc.")):
    """Triggers SaveNow job, polls completion, and redirects client to the direct output stream url"""
    try:
        # Check cache first
        cached_url = streamer.get_cached_stream(video_id)
        if cached_url:
            return RedirectResponse(url=cached_url, status_code=307)
        
        url = streamer.get_stream(video_id, format_type=format)
        if not url:
            raise HTTPException(status_code=500, detail="Could not resolve or poll stream via SaveNow API gateway.")
        
        return RedirectResponse(url=url, status_code=307)
    
    except Exception as e:
        logger.error(f"Stream error: {e}")
        raise HTTPException(status_code=500, detail=f"Cannot play: {str(e)}")

@app.get("/api/recommendations/{video_id}")
def get_recommendations(video_id: str, limit: int = Query(15, le=30)):
    cache_key = f"rec:{video_id}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        playlist = ytmusic.get_watch_playlist(videoId=video_id, limit=limit)
        tracks = [extract_track(item) for item in playlist.get("tracks", [])[:limit] if extract_track(item)]
        response = {"seed_id": video_id, "count": len(tracks), "tracks": tracks}
        cache.set(cache_key, response)
        return response
    except Exception:
        raise HTTPException(status_code=500, detail="Failed")

@app.get("/api/lyrics/{video_id}")
def get_lyrics(video_id: str):
    cache_key = f"lyrics:{video_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        playlist = ytmusic.get_watch_playlist(videoId=video_id)
        lyrics_id = playlist.get("lyrics")
        if not lyrics_id:
            return {"video_id": video_id, "lyrics": None}
        
        lyrics = ytmusic.get_lyrics(lyrics_id)
        response = {"video_id": video_id, "lyrics": lyrics.get("lyrics")}
        cache.set(cache_key, response)
        return response
    except Exception:
        return {"video_id": video_id, "lyrics": None}

@app.get("/api/artist/{artist_name}")
def get_artist(artist_name: str, limit: int = Query(10, le=20)):
    cache_key = f"artist:{artist_name}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        results = ytmusic.search(artist_name, filter="artists", limit=1)
        if not results:
            raise HTTPException(status_code=404, detail="Artist not found")
        
        artist = results[0]
        tracks_results = ytmusic.search(artist_name, filter="songs", limit=limit)
        tracks = [extract_track(item) for item in tracks_results if extract_track(item)]
        
        response = {
            "name": artist.get("name", artist_name),
            "thumbnail": artist.get("thumbnails", [{}])[-1].get("url", ""),
            "top_tracks": tracks[:limit]
        }
        cache.set(cache_key, response)
        return response
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed")

@app.get("/health")
def health():
    return {
        "status": "✅ Healthy",
        "timestamp": datetime.now().isoformat(),
        "cache_entries": len(cache.cache),
        "active_streams": len(streamer.stream_cache)
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
