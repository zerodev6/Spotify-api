import os
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

# Official YouTube API Key configuration
YOUTUBE_API_KEY = "AIzaSyDZqNa-pCcrDQDfo1PB5-LMoIk3mkC9gLg"

# List of public Piped API instances to query for streams
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.r4fo.com",
    "https://api.piped.privacy.com.de",
    "https://piped-api.garudalinux.org"
]

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

# ==================== THIRD-PARTY STREAM HANDLER ====================
class ThirdPartyStreamHandler:
    def __init__(self):
        self.stream_cache = {}
    
    def get_stream_from_piped(self, video_id: str) -> Optional[str]:
        """Fetch stream links using public Piped API instances as a third-party gateway"""
        for base_url in PIPED_INSTANCES:
            try:
                url = f"{base_url}/streams/{video_id}"
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    audio_streams = data.get("audioStreams", [])
                    # Find the best adaptive or regular audio stream URL
                    if audio_streams:
                        # Sort by bitrate or just grab the first valid working stream URL
                        stream_url = audio_streams[0].get("url")
                        if stream_url:
                            logger.info(f"✅ Successfully fetched stream from Piped instance: {base_url}")
                            self.stream_cache[video_id] = (stream_url, datetime.now())
                            return stream_url
            except Exception as e:
                logger.warning(f"Piped instance {base_url} failed: {e}")
                continue
        return None
    
    def get_cached_stream(self, video_id: str) -> Optional[str]:
        if video_id in self.stream_cache:
            url, timestamp = self.stream_cache[video_id]
            if datetime.now() - timestamp < timedelta(minutes=15):
                return url
            else:
                del self.stream_cache[video_id]
        return None

# ==================== FASTAPI APP ====================
app = FastAPI(title="Third-Party Gateway Music API", version="1.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ytmusic = YTMusic()
cache = CacheManager(ttl_minutes=30)
streamer = ThirdPartyStreamHandler()

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
        "status": "✅ Online via Third-Party Gateway API",
        "endpoints": {
            "search": "/api/search?q=song",
            "trending": "/api/trending",
            "stream": "/api/stream/{video_id}",
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
async def stream_track(video_id: str):
    """Instantly redirects to a direct stream link retrieved via decentralized third-party gateway nodes"""
    try:
        cached_url = streamer.get_cached_stream(video_id)
        if cached_url:
            return RedirectResponse(url=cached_url, status_code=307)
        
        url = streamer.get_stream_from_piped(video_id)
        if not url:
            raise HTTPException(status_code=500, detail="All third-party stream gateways exhausted.")
        
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
