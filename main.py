import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import yt_dlp
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        logger.info(f"Cache set: {key}")

# ==================== STREAMING HANDLER ====================
class StreamingHandler:
    def __init__(self):
        self.stream_cache = {}
    
    def get_best_stream(self, video_id: str) -> Optional[str]:
        """Get working stream URL"""
        try:
            ydl_opts = {
                'format': 'bestaudio',
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'socket_timeout': 30,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            }
            
            cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
            if os.path.exists(cookies_path):
                ydl_opts['cookiefile'] = cookies_path
                logger.info("Using cookies.txt")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                url = info.get('url')
                
                if url:
                    self.stream_cache[video_id] = (url, datetime.now())
                    logger.info(f"Stream found for {video_id}")
                    return url
                else:
                    raise Exception("No URL in response")
        
        except Exception as e:
            logger.error(f"Stream error for {video_id}: {str(e)}")
            raise Exception(f"Cannot stream: {str(e)}")
    
    def get_cached_stream(self, video_id: str) -> Optional[str]:
        if video_id in self.stream_cache:
            url, timestamp = self.stream_cache[video_id]
            if datetime.now() - timestamp < timedelta(minutes=10):
                logger.info(f"Using cached stream for {video_id}")
                return url
            else:
                del self.stream_cache[video_id]
        return None

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="Music API",
    description="Fast, working music streaming API",
    version="1.0.0"
)

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

# ==================== HELPER FUNCTIONS ====================
def extract_track(item: Dict) -> Optional[Dict]:
    """Safely extract track data"""
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

# ==================== HOME ====================
@app.get("/")
def home():
    return {
        "status": "✅ Working",
        "version": "1.0.0",
        "api": "Music Streaming API",
        "endpoints": {
            "search": "/api/search?q=song",
            "trending": "/api/trending",
            "stream": "/api/stream/{video_id}",
            "recommendations": "/api/recommendations/{video_id}",
            "lyrics": "/api/lyrics/{video_id}",
            "artist": "/api/artist/{artist_name}",
            "album": "/api/album/{album_name}"
        }
    }

# ==================== SEARCH ====================
@app.get("/api/search")
def search_tracks(
    q: str = Query(..., min_length=1),
    limit: int = Query(15, ge=1, le=30)
):
    """Search for tracks"""
    cache_key = f"search:{q}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"Cache hit: {cache_key}")
        return cached
    
    try:
        logger.info(f"Searching: {q}")
        results = ytmusic.search(q, filter="songs", limit=limit)
        
        tracks = []
        for item in results:
            track = extract_track(item)
            if track:
                tracks.append(track)
        
        response = {
            "query": q,
            "count": len(tracks),
            "tracks": tracks
        }
        
        cache.set(cache_key, response)
        return response
    
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail="Search failed")

# ==================== TRENDING ====================
@app.get("/api/trending")
def get_trending(limit: int = Query(20, ge=1, le=30)):
    """Get trending songs"""
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
        
        response = {
            "type": "trending",
            "count": len(tracks),
            "tracks": tracks
        }
        
        cache.set(cache_key, response)
        return response
    
    except Exception as e:
        logger.error(f"Trending error: {e}")
        raise HTTPException(status_code=500, detail="Trending failed")

# ==================== STREAM ====================
@app.get("/api/stream/{video_id}")
async def stream_track(video_id: str):
    """Stream audio - returns working URL"""
    try:
        # Check cache
        cached_url = streamer.get_cached_stream(video_id)
        if cached_url:
            return RedirectResponse(url=cached_url, status_code=307)
        
        # Get fresh stream
        logger.info(f"Getting stream for {video_id}")
        url = streamer.get_best_stream(video_id)
        
        return RedirectResponse(url=url, status_code=307)
    
    except Exception as e:
        logger.error(f"Stream error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Video unavailable or region restricted"
        )

# ==================== RECOMMENDATIONS ====================
@app.get("/api/recommendations/{video_id}")
def get_recommendations(video_id: str, limit: int = Query(15, le=30)):
    """Get recommendations for a track"""
    cache_key = f"rec:{video_id}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        logger.info(f"Getting recommendations for {video_id}")
        playlist = ytmusic.get_watch_playlist(videoId=video_id, limit=limit)
        
        tracks = []
        for item in playlist.get("tracks", [])[:limit]:
            track = extract_track(item)
            if track:
                tracks.append(track)
        
        response = {
            "seed_id": video_id,
            "count": len(tracks),
            "tracks": tracks
        }
        
        cache.set(cache_key, response)
        return response
    
    except Exception as e:
        logger.error(f"Recommendations error: {e}")
        raise HTTPException(status_code=500, detail="Recommendations failed")

# ==================== LYRICS ====================
@app.get("/api/lyrics/{video_id}")
def get_lyrics(video_id: str):
    """Get song lyrics"""
    cache_key = f"lyrics:{video_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        logger.info(f"Getting lyrics for {video_id}")
        playlist = ytmusic.get_watch_playlist(videoId=video_id)
        lyrics_id = playlist.get("lyrics")
        
        if not lyrics_id:
            return {"video_id": video_id, "lyrics": None}
        
        lyrics = ytmusic.get_lyrics(lyrics_id)
        response = {
            "video_id": video_id,
            "lyrics": lyrics.get("lyrics")
        }
        
        cache.set(cache_key, response)
        return response
    
    except Exception as e:
        logger.error(f"Lyrics error: {e}")
        return {"video_id": video_id, "lyrics": None}

# ==================== ARTIST ====================
@app.get("/api/artist/{artist_name}")
def get_artist(artist_name: str, limit: int = Query(10, le=20)):
    """Get artist info and top tracks"""
    cache_key = f"artist:{artist_name}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        logger.info(f"Getting artist: {artist_name}")
        results = ytmusic.search(artist_name, filter="artists", limit=1)
        
        if not results:
            raise HTTPException(status_code=404, detail="Artist not found")
        
        artist = results[0]
        
        # Get top tracks
        tracks_results = ytmusic.search(artist_name, filter="songs", limit=limit)
        tracks = []
        for item in tracks_results:
            track = extract_track(item)
            if track:
                tracks.append(track)
        
        response = {
            "name": artist.get("name", artist_name),
            "thumbnail": artist.get("thumbnails", [{}])[-1].get("url", ""),
            "top_tracks": tracks[:limit]
        }
        
        cache.set(cache_key, response)
        return response
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Artist error: {e}")
        raise HTTPException(status_code=500, detail="Artist search failed")

# ==================== ALBUM ====================
@app.get("/api/album/{album_name}")
def get_album(album_name: str):
    """Get album info"""
    cache_key = f"album:{album_name}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        logger.info(f"Getting album: {album_name}")
        results = ytmusic.search(album_name, filter="albums", limit=1)
        
        if not results:
            raise HTTPException(status_code=404, detail="Album not found")
        
        album = results[0]
        
        response = {
            "name": album.get("name", album_name),
            "artist": album.get("artist", "Unknown"),
            "thumbnail": album.get("thumbnails", [{}])[-1].get("url", ""),
            "year": album.get("year", ""),
        }
        
        cache.set(cache_key, response)
        return response
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Album error: {e}")
        raise HTTPException(status_code=500, detail="Album search failed")

# ==================== HEALTH ====================
@app.get("/health")
def health():
    return {
        "status": "✅ Healthy",
        "timestamp": datetime.now().isoformat(),
        "cache_entries": len(cache.cache),
        "stream_cache": len(streamer.stream_cache)
    }

# ==================== MAIN ====================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting API on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
