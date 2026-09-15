import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import yt_dlp

# ==================== CACHE MANAGER ====================
class CacheManager:
    def __init__(self, ttl_minutes: int = 60):
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

# ==================== STREAMING HANDLER ====================
class StreamingHandler:
    def __init__(self):
        self.stream_cache = {}
    
    def get_best_stream(self, video_id: str) -> Optional[str]:
        """Get stream URL with multiple fallback strategies"""
        try:
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'extractor_args': {'youtube': {'player_client': ['android', 'web', 'tv']}},
                'socket_timeout': 30,
            }
            
            cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
            if os.path.exists(cookies_path):
                ydl_opts['cookiefile'] = cookies_path
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                
                url = info.get('url')
                if not url and 'formats' in info:
                    for fmt in info['formats']:
                        if fmt.get('ext') in ['m4a', 'webm', 'mp4'] and fmt.get('acodec') != 'none':
                            url = fmt.get('url')
                            if url:
                                break
                
                if url:
                    self.stream_cache[video_id] = (url, datetime.now())
                    return url
                else:
                    raise Exception("No suitable audio format found")
        
        except Exception as e:
            raise Exception(f"Streaming failed: {str(e)}")
    
    def get_cached_stream(self, video_id: str) -> Optional[str]:
        if video_id in self.stream_cache:
            url, timestamp = self.stream_cache[video_id]
            if datetime.now() - timestamp < timedelta(minutes=15):
                return url
            else:
                del self.stream_cache[video_id]
        return None

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="Music Streaming API",
    description="Full-featured music streaming with search, trending, recommendations, lyrics, and streaming.",
    version="4.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ytmusic = YTMusic()
cache_manager = CacheManager(ttl_minutes=60)
streaming_handler = StreamingHandler()

# ==================== HOME ====================
@app.get("/")
def home():
    return {
        "status": "online",
        "version": "4.0.0",
        "message": "Music Streaming API",
        "endpoints": {
            "search": "/api/search?q=artist_or_song",
            "trending": "/api/trending",
            "recommendations": "/api/recommendations/{video_id}",
            "lyrics": "/api/lyrics/{video_id}",
            "stream": "/api/stream/{video_id}",
            "artist": "/api/artist/{artist_name}",
            "album": "/api/album/{album_name}",
        }
    }

# ==================== SEARCH ====================
@app.get("/api/search")
def search_tracks(
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0)
):
    """Fast search with pagination and caching"""
    cache_key = f"search:{q}:{limit}:{offset}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached
    
    try:
        search_results = ytmusic.search(q, filter="songs", limit=limit)
        tracks = []
        
        for item in search_results[offset:offset+limit]:
            try:
                video_id = item.get("videoId")
                if not video_id:
                    continue
                
                thumbnails = item.get("thumbnails", [])
                thumb_url = thumbnails[-1]["url"] if thumbnails else ""
                artists = item.get("artists", [])
                artist_name = ", ".join([a["name"] for a in artists]) if artists else "Unknown"
                album = item.get("album", {})
                
                track = {
                    "id": video_id,
                    "title": item.get("title"),
                    "artist": artist_name,
                    "album": album.get("name", "Single"),
                    "duration": item.get("duration"),
                    "thumbnail": thumb_url,
                }
                tracks.append(track)
            except:
                continue
        
        result = {
            "query": q,
            "total": len(tracks),
            "limit": limit,
            "offset": offset,
            "tracks": tracks
        }
        
        cache_manager.set(cache_key, result)
        return result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

# ==================== ADVANCED SEARCH ====================
@app.get("/api/search/advanced")
def advanced_search(
    q: str = Query(...),
    type_filter: str = Query("songs", pattern="^(songs|artists|albums|playlists)$"),
    artist: Optional[str] = None,
    album: Optional[str] = None,
    limit: int = Query(20, le=50)
):
    """Advanced search with filters"""
    cache_key = f"advanced_search:{q}:{type_filter}:{artist}:{album}:{limit}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached
    
    try:
        search_query = q
        if artist:
            search_query += f" artist:{artist}"
        if album:
            search_query += f" album:{album}"
        
        results = ytmusic.search(search_query, filter=type_filter, limit=limit)
        
        formatted = {
            "query": search_query,
            "type": type_filter,
            "count": len(results),
            "results": results[:limit]
        }
        
        cache_manager.set(cache_key, formatted)
        return formatted
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== TRENDING ====================
@app.get("/api/trending")
def get_trending(limit: int = Query(20, le=50)):
    """Get trending songs globally"""
    cache_key = "trending_global"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached
    
    try:
        search_results = ytmusic.search("trending", filter="songs", limit=limit)
        tracks = []
        
        for i, item in enumerate(search_results[:limit]):
            try:
                video_id = item.get("videoId")
                if not video_id:
                    continue
                
                thumbnails = item.get("thumbnails", [])
                thumb_url = thumbnails[-1]["url"] if thumbnails else ""
                artists = item.get("artists", [])
                artist_name = ", ".join([a["name"] for a in artists]) if artists else "Unknown"
                
                tracks.append({
                    "rank": i + 1,
                    "id": video_id,
                    "title": item.get("title"),
                    "artist": artist_name,
                    "thumbnail": thumb_url,
                })
            except:
                continue
        
        result = {"type": "trending", "count": len(tracks), "tracks": tracks}
        cache_manager.set(cache_key, result)
        return result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== RECOMMENDATIONS ====================
@app.get("/api/recommendations/{video_id}")
def get_recommendations(video_id: str, limit: int = Query(20, le=50)):
    """Get song recommendations based on a track"""
    cache_key = f"recommendations:{video_id}:{limit}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached
    
    try:
        watch_playlist = ytmusic.get_watch_playlist(videoId=video_id, limit=limit)
        tracks = []
        
        for item in watch_playlist.get("tracks", [])[:limit]:
            try:
                vid = item.get("videoId")
                if not vid:
                    continue
                
                thumbnails = item.get("thumbnails", [])
                thumb_url = thumbnails[-1]["url"] if thumbnails else ""
                artists = item.get("artists", [])
                artist_name = ", ".join([a["name"] for a in artists]) if artists else "Unknown"
                
                tracks.append({
                    "id": vid,
                    "title": item.get("title"),
                    "artist": artist_name,
                    "thumbnail": thumb_url,
                })
            except:
                continue
        
        result = {
            "seed_track_id": video_id,
            "recommendation_count": len(tracks),
            "tracks": tracks
        }
        
        cache_manager.set(cache_key, result)
        return result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recommendations failed: {str(e)}")

# ==================== LYRICS ====================
@app.get("/api/lyrics/{video_id}")
def get_lyrics(video_id: str):
    """Get lyrics for a track"""
    cache_key = f"lyrics:{video_id}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached
    
    try:
        watch_playlist = ytmusic.get_watch_playlist(videoId=video_id)
        lyrics_browse_id = watch_playlist.get("lyrics")
        
        if not lyrics_browse_id:
            return {"video_id": video_id, "lyrics": "Lyrics not available"}
        
        lyrics_data = ytmusic.get_lyrics(lyrics_browse_id)
        result = {
            "video_id": video_id,
            "lyrics": lyrics_data.get("lyrics", "No lyrics available")
        }
        
        cache_manager.set(cache_key, result)
        return result
    
    except Exception as e:
        return {"video_id": video_id, "lyrics": f"Could not fetch lyrics"}

# ==================== STREAMING ====================
@app.get("/api/stream/{video_id}")
async def stream_track(video_id: str):
    """Get streaming URL"""
    try:
        cached_url = streaming_handler.get_cached_stream(video_id)
        if cached_url:
            return RedirectResponse(url=cached_url, status_code=307)
        
        url = streaming_handler.get_best_stream(video_id)
        
        if not url:
            raise HTTPException(status_code=404, detail="Could not extract stream URL")
        
        return RedirectResponse(url=url, status_code=307)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Streaming error: {str(e)}"
        )

# ==================== ARTIST ====================
@app.get("/api/artist/{artist_name}")
def get_artist(artist_name: str, limit: int = Query(10, le=30)):
    """Get artist information and top tracks"""
    cache_key = f"artist:{artist_name}:{limit}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached
    
    try:
        search_results = ytmusic.search(f"artist:{artist_name}", filter="artists", limit=1)
        
        if not search_results:
            raise HTTPException(status_code=404, detail="Artist not found")
        
        artist_info = search_results[0]
        top_tracks = ytmusic.search(artist_name, filter="songs", limit=limit)
        
        result = {
            "name": artist_info.get("name", artist_name),
            "thumbnail": artist_info.get("thumbnails", [{}])[-1].get("url", ""),
            "description": artist_info.get("description", ""),
            "top_tracks_count": len(top_tracks),
            "top_tracks": [
                {
                    "id": t.get("videoId"),
                    "title": t.get("title"),
                    "artist": artist_name
                } for t in top_tracks if t.get("videoId")
            ]
        }
        
        cache_manager.set(cache_key, result)
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== ALBUM ====================
@app.get("/api/album/{album_name}")
def get_album(album_name: str):
    """Get album information"""
    cache_key = f"album:{album_name}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached
    
    try:
        search_results = ytmusic.search(f"album:{album_name}", filter="albums", limit=1)
        
        if not search_results:
            raise HTTPException(status_code=404, detail="Album not found")
        
        album_info = search_results[0]
        
        result = {
            "name": album_info.get("name", album_name),
            "artist": album_info.get("artist", "Unknown"),
            "thumbnail": album_info.get("thumbnails", [{}])[-1].get("url", ""),
            "year": album_info.get("year", ""),
            "description": album_info.get("description", "")
        }
        
        cache_manager.set(cache_key, result)
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== HEALTH ====================
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "cache_size": len(cache_manager.cache)
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
