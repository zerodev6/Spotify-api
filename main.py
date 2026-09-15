import os
import asyncio
import json
from datetime import datetime, timedelta
from typing import List, Optional
from functools import lru_cache
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from ytmusicapi import YTMusic
import yt_dlp
import aiohttp
from pydantic import BaseModel

# ==================== DATA MODELS ====================
class Track(BaseModel):
    id: str
    title: str
    artist: str
    album: str
    duration: Optional[int] = None
    thumbnail: str
    genre: Optional[str] = None
    year: Optional[int] = None

class Playlist(BaseModel):
    id: str
    name: str
    description: str
    tracks: List[Track]
    created_at: datetime
    updated_at: datetime

class User(BaseModel):
    user_id: str
    username: str
    created_at: datetime
    favorites: List[str] = []
    playlists: List[str] = []
    history: List[str] = []

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
    
    def clear(self):
        self.cache.clear()
    
    def invalidate(self, pattern: str):
        keys_to_delete = [k for k in self.cache.keys() if pattern in k]
        for k in keys_to_delete:
            del self.cache[k]

# ==================== DATABASE SIMULATION ====================
class Database:
    def __init__(self):
        self.users = {}
        self.playlists = {}
        self.favorites = defaultdict(list)
        self.history = defaultdict(list)
        self.queue = defaultdict(list)
    
    def create_user(self, user_id: str, username: str) -> User:
        user = User(user_id=user_id, username=username, created_at=datetime.now())
        self.users[user_id] = user
        return user
    
    def get_user(self, user_id: str) -> Optional[User]:
        return self.users.get(user_id)
    
    def add_to_favorites(self, user_id: str, track_id: str):
        if track_id not in self.favorites[user_id]:
            self.favorites[user_id].append(track_id)
    
    def remove_from_favorites(self, user_id: str, track_id: str):
        if track_id in self.favorites[user_id]:
            self.favorites[user_id].remove(track_id)
    
    def add_to_history(self, user_id: str, track_id: str):
        self.history[user_id].append((track_id, datetime.now()))
        # Keep only last 100 tracks
        if len(self.history[user_id]) > 100:
            self.history[user_id] = self.history[user_id][-100:]
    
    def create_playlist(self, user_id: str, playlist_id: str, name: str, description: str = ""):
        playlist = Playlist(
            id=playlist_id,
            name=name,
            description=description,
            tracks=[],
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self.playlists[playlist_id] = playlist
        if user_id in self.users:
            self.users[user_id].playlists.append(playlist_id)
        return playlist
    
    def add_to_playlist(self, playlist_id: str, track: Track):
        if playlist_id in self.playlists:
            self.playlists[playlist_id].tracks.append(track)
            self.playlists[playlist_id].updated_at = datetime.now()

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
            
            # Use cookies if available
            cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
            if os.path.exists(cookies_path):
                ydl_opts['cookiefile'] = cookies_path
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                
                # Try multiple audio formats
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
        """Check if stream is cached and still valid (15 min TTL)"""
        if video_id in self.stream_cache:
            url, timestamp = self.stream_cache[video_id]
            if datetime.now() - timestamp < timedelta(minutes=15):
                return url
            else:
                del self.stream_cache[video_id]
        return None

# ==================== FASTAPI APP ====================
app = FastAPI(
    title="Spotify-Alternative Music API",
    description="Full-featured Spotify alternative with search, trending, recommendations, playlists, lyrics, and streaming.",
    version="4.0.0"
)

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
ytmusic = YTMusic()
cache_manager = CacheManager(ttl_minutes=60)
db = Database()
streaming_handler = StreamingHandler()

# ==================== SEARCH ENDPOINTS ====================
@app.get("/")
def home():
    return {
        "status": "online",
        "version": "4.0.0",
        "message": "Spotify-Alternative API - Full-Featured Music Streaming",
        "features": [
            "Advanced Search",
            "Trending Charts",
            "Recommendations",
            "Lyrics",
            "Streaming",
            "Playlists",
            "Favorites",
            "History",
            "User Profiles"
        ],
        "endpoints": {
            "search": "/api/search?q=artist_or_song&limit=20",
            "search_advanced": "/api/search/advanced?q=query&type=songs&artist=artist&album=album",
            "trending": "/api/trending",
            "recommendations": "/api/recommendations/{video_id}",
            "lyrics": "/api/lyrics/{video_id}",
            "stream": "/api/stream/{video_id}",
            "artist": "/api/artist/{artist_name}",
            "album": "/api/album/{album_name}",
            "playlist": "/api/playlist/{playlist_id}",
            "user": "/api/user/{user_id}",
            "favorites": "/api/favorites/{user_id}",
            "history": "/api/history/{user_id}",
        }
    }

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
                    "play_url": f"/api/stream/{video_id}",
                    "lyrics_url": f"/api/lyrics/{video_id}",
                    "recommendations_url": f"/api/recommendations/{video_id}"
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

@app.get("/api/search/advanced")
def advanced_search(
    q: str = Query(...),
    type_filter: str = Query("songs", regex="^(songs|artists|albums|playlists)$"),
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

# ==================== TRENDING & CHARTS ====================
@app.get("/api/trending")
def get_trending(limit: int = Query(20, le=50)):
    """Get trending songs globally with caching"""
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
                    "play_url": f"/api/stream/{video_id}",
                    "recommendations_url": f"/api/recommendations/{video_id}"
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
                    "play_url": f"/api/stream/{vid}"
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
        return {"video_id": video_id, "lyrics": f"Could not fetch lyrics: {str(e)}"}

# ==================== STREAMING ====================
@app.get("/api/stream/{video_id}")
async def stream_track(video_id: str):
    """Get streaming URL with robust error handling and multiple fallbacks"""
    try:
        # Check cache first
        cached_url = streaming_handler.get_cached_stream(video_id)
        if cached_url:
            return RedirectResponse(url=cached_url, status_code=307)
        
        # Get fresh stream
        url = streaming_handler.get_best_stream(video_id)
        
        if not url:
            raise HTTPException(status_code=404, detail="Could not extract stream URL")
        
        return RedirectResponse(url=url, status_code=307)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Streaming error: {str(e)}. Try again in a few moments."
        )

# ==================== ARTIST & ALBUM ====================
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
        
        # Get top tracks
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
                    "play_url": f"/api/stream/{t.get('videoId')}"
                } for t in top_tracks if t.get("videoId")
            ]
        }
        
        cache_manager.set(cache_key, result)
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/album/{album_name}")
def get_album(album_name: str):
    """Get album information and tracks"""
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

# ==================== PLAYLISTS ====================
@app.post("/api/playlist")
def create_playlist(user_id: str, name: str, description: str = ""):
    """Create a new playlist"""
    try:
        playlist_id = f"pl_{user_id}_{int(datetime.now().timestamp())}"
        playlist = db.create_playlist(user_id, playlist_id, name, description)
        cache_manager.invalidate("playlist")
        return {
            "status": "created",
            "playlist": {
                "id": playlist.id,
                "name": playlist.name,
                "description": playlist.description,
                "tracks_count": 0,
                "created_at": playlist.created_at.isoformat()
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/playlist/{playlist_id}")
def get_playlist(playlist_id: str):
    """Get playlist details"""
    cache_key = f"playlist:{playlist_id}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached
    
    if playlist_id not in db.playlists:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    playlist = db.playlists[playlist_id]
    result = {
        "id": playlist.id,
        "name": playlist.name,
        "description": playlist.description,
        "tracks_count": len(playlist.tracks),
        "tracks": playlist.tracks,
        "created_at": playlist.created_at.isoformat(),
        "updated_at": playlist.updated_at.isoformat()
    }
    
    cache_manager.set(cache_key, result)
    return result

# ==================== USER FAVORITES & HISTORY ====================
@app.post("/api/favorites/{user_id}/{track_id}")
def add_favorite(user_id: str, track_id: str):
    """Add track to favorites"""
    try:
        db.add_to_favorites(user_id, track_id)
        cache_manager.invalidate(f"favorites:{user_id}")
        return {"status": "added", "user_id": user_id, "track_id": track_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/favorites/{user_id}/{track_id}")
def remove_favorite(user_id: str, track_id: str):
    """Remove track from favorites"""
    try:
        db.remove_from_favorites(user_id, track_id)
        cache_manager.invalidate(f"favorites:{user_id}")
        return {"status": "removed", "user_id": user_id, "track_id": track_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/favorites/{user_id}")
def get_favorites(user_id: str):
    """Get user's favorite tracks"""
    cache_key = f"favorites:{user_id}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached
    
    favorites = db.favorites.get(user_id, [])
    result = {
        "user_id": user_id,
        "count": len(favorites),
        "favorites": favorites
    }
    
    cache_manager.set(cache_key, result)
    return result

@app.post("/api/history/{user_id}/{track_id}")
def add_to_history(user_id: str, track_id: str):
    """Add track to listening history"""
    try:
        db.add_to_history(user_id, track_id)
        return {"status": "added_to_history", "user_id": user_id, "track_id": track_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history/{user_id}")
def get_history(user_id: str, limit: int = Query(50, le=100)):
    """Get user's listening history"""
    history = db.history.get(user_id, [])
    recent = [(tid, ts.isoformat()) for tid, ts in history[-limit:]]
    
    return {
        "user_id": user_id,
        "count": len(recent),
        "history": recent[::-1]  # Most recent first
    }

# ==================== QUEUE ====================
@app.post("/api/queue/{user_id}")
def add_to_queue(user_id: str, track_id: str):
    """Add track to queue"""
    db.queue[user_id].append(track_id)
    return {"status": "added_to_queue", "queue_length": len(db.queue[user_id])}

@app.get("/api/queue/{user_id}")
def get_queue(user_id: str):
    """Get user's queue"""
    queue = db.queue.get(user_id, [])
    return {"user_id": user_id, "queue_length": len(queue), "queue": queue}

@app.delete("/api/queue/{user_id}")
def clear_queue(user_id: str):
    """Clear queue"""
    db.queue[user_id] = []
    return {"status": "cleared", "user_id": user_id}

# ==================== HEALTH CHECK ====================
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "cache_size": len(cache_manager.cache),
        "users": len(db.users),
        "playlists": len(db.playlists)
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("music_api_enhanced:app", host="0.0.0.0", port=port, reload=True)
