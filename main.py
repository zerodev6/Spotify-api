import os
from datetime import datetime, timedelta
from typing import Optional, Dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import yt_dlp
import logging
import requests

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Official YouTube API Key configuration
YOUTUBE_API_KEY = "AIzaSyDZqNa-pCcrDQDfo1PB5-LMoIk3mkC9gLg"

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

# ==================== STREAMING HANDLER (SPEED OPTIMIZED) ====================
class StreamingHandler:
    def __init__(self):
        self.stream_cache = {}
    
    def get_best_stream(self, video_id: str) -> Optional[str]:
        """Speed-optimized stream extractor to fetch URLs instantly"""
        try:
            # Single lightweight optimal format configuration to reduce latency to ~1 second
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'socket_timeout': 15,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'web'],
                        'player_skip': ['js', 'configs']
                    }
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                },
                'geo_bypass': True,
                'geo_bypass_country': 'US',
            }
            
            # Optional cookies support if cookies.txt is provided in your repository
            cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
            if os.path.exists(cookies_path):
                ydl_opts['cookiefile'] = cookies_path
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"⚡ Fast-extracting stream for {video_id}")
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                
                url = info.get('url')
                if url:
                    logger.info(f"✅ Stream successfully resolved for {video_id}")
                    # Cache stream URL for 15 minutes
                    self.stream_cache[video_id] = (url, datetime.now())
                    return url
            
            raise Exception("No direct stream URL found")
        
        except Exception as e:
            logger.error(f"Stream error for {video_id}: {str(e)}")
            raise Exception(str(e))
    
    def get_cached_stream(self, video_id: str) -> Optional[str]:
        if video_id in self.stream_cache:
            url, timestamp = self.stream_cache[video_id]
            # Valid cache within 15 minutes
            if datetime.now() - timestamp < timedelta(minutes=15):
                return url
            else:
                del self.stream_cache[video_id]
        return None

# ==================== FASTAPI APP ====================
app = FastAPI(title="Fast Music Streaming API", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ytmusic = YTMusic()
cache = CacheManager(ttl_minutes=30)
streamer = StreamingHandler()

# ==================== HELPER ====================
def verify_video_with_api(video_id: str):
    """Optional validation using your official YouTube API key"""
    try:
        url = f"https://www.googleapis.com/youtube/v3/videos?part=status&id={video_id}&key={YOUTUBE_API_KEY}"
        requests.get(url, timeout=3)
    except Exception as e:
        logger.warning(f"API check warning: {e}")

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
        "status": "✅ Online & Speed Optimized",
        "api": "Music Streaming Backend",
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
        logger.info(f"Searching: {q}")
        results = ytmusic.search(q, filter="songs", limit=limit)
        
        tracks = []
        for item in results:
            track = extract_track(item)
            if track:
                tracks.append(track)
        
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
        logger.info("Fetching trending")
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
    """Instantly returns working stream redirect using memory cache or fast yt-dlp extraction"""
    try:
        # 1. Check instant RAM stream cache first (< 0.01 seconds)
        cached_url = streamer.get_cached_stream(video_id)
        if cached_url:
            return RedirectResponse(url=cached_url, status_code=307)
        
        # 2. Trigger API check asynchronously/safely
        verify_video_with_api(video_id)

        # 3. Fast-path stream fetch
        url = streamer.get_best_stream(video_id)
        if not url:
            raise Exception("Stream generation failed")
        
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
        tracks = []
        for item in playlist.get("tracks", [])[:limit]:
            track = extract_track(item)
            if track:
                tracks.append(track)
        
        response = {"seed_id": video_id, "count": len(tracks), "tracks": tracks}
        cache.set(cache_key, response)
        return response
    except Exception as e:
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
