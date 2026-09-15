import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from ytmusicapi import YTMusic
import yt_dlp

app = FastAPI(
    title="Spotify-Style YouTube Music API",
    description="Full music streaming backend featuring search, live audio streaming redirect, charts, and song lyrics.",
    version="2.1.0"
)

# Initialize YouTube Music client (public unauthenticated instance)
ytmusic = YTMusic()

@app.get("/")
def home():
    return {
        "status": "online",
        "message": "Welcome to your Spotify-like API with Lyrics & Trending support!",
        "endpoints": {
            "search": "/api/search?q=the weeknd",
            "trending": "/api/trending",
            "lyrics": "/api/lyrics/{video_id}",
            "stream": "/api/stream/{video_id}"
        }
    }

@app.get("/api/search")
def search_tracks(q: str = Query(..., description="Search term for songs, artists, or albums")):
    """Search tracks with metadata (Thumbnails, Artists, Albums, IDs)"""
    try:
        search_results = ytmusic.search(q, filter="songs", limit=15)
        tracks = []
        for item in search_results:
            thumbnails = item.get("thumbnails", [])
            thumb_url = thumbnails[-1]["url"] if thumbnails else ""
            
            artists = item.get("artists", [])
            artist_name = artists[0]["name"] if artists else "Unknown Artist"
            
            album = item.get("album")
            album_name = album["name"] if album else "Single"
            
            video_id = item.get("videoId")
            if not video_id:
                continue
                
            tracks.append({
                "id": video_id,
                "title": item.get("title"),
                "artist": artist_name,
                "album": album_name,
                "duration": item.get("duration"),
                "thumbnail": thumb_url,
                "stream_url": f"/api/stream/{video_id}",
                "lyrics_url": f"/api/lyrics/{video_id}"
            })
        return {"query": q, "total": len(tracks), "tracks": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trending")
def get_trending():
    """Get global trending music charts and hits right now"""
    try:
        charts = ytmusic.get_charts()
        # Extract trending songs from chart payload if available, fallback to query search
        chart_tracks = []
        
        # Safely parse trending items from chart response structure
        sections = charts.get("videos", {}).get("items", []) or charts.get("songs", {}).get("items", [])
        if not sections:
            # Fallback search if specific chart format shifts
            search_results = ytmusic.search("Billboard top hits", filter="songs", limit=15)
            sections = search_results

        for item in sections:
            video_id = item.get("videoId")
            if not video_id:
                continue
            
            thumbnails = item.get("thumbnails", [])
            thumb_url = thumbnails[-1]["url"] if thumbnails else ""
            
            artists = item.get("artists", [])
            artist_name = artists[0]["name"] if artists else "Unknown Artist"
            
            chart_tracks.append({
                "id": video_id,
                "title": item.get("title"),
                "artist": artist_name,
                "thumbnail": thumb_url,
                "stream_url": f"/api/stream/{video_id}",
                "lyrics_url": f"/api/lyrics/{video_id}"
            })
            
        return {"trending_count": len(chart_tracks), "tracks": chart_tracks}
    except Exception as e:
        # Fallback safeguard in case region/charts block
        try:
            fallback_search = ytmusic.search("Top global hits", filter="songs", limit=10)
            return {"trending_count": len(fallback_search), "tracks": fallback_search}
        except Exception as inner_e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch trending: {str(inner_e)}")

@app.get("/api/lyrics/{video_id}")
def get_lyrics(video_id: str):
    """Fetch lyrics for a given track video ID"""
    try:
        # Get watch playlist to find the unique lyrics browse ID
        watch_playlist = ytmusic.get_watch_playlist(videoId=video_id)
        lyrics_browse_id = watch_playlist.get("lyrics")
        
        if not lyrics_browse_id:
            raise HTTPException(status_code=404, detail="Lyrics not found or unavailable for this track.")
        
        lyrics_data = ytmusic.get_lyrics(lyrics_browse_id)
        return {
            "video_id": video_id,
            "lyrics": lyrics_data.get("lyrics", "No lyrics text available."),
            "source": lyrics_data.get("source", "Unknown")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not retrieve lyrics: {str(e)}")

@app.get("/api/stream/{video_id}")
def stream_track(video_id: str):
    """Extracts direct live audio streaming URL and redirects client for playback"""
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            audio_url = info.get('url')
            if not audio_url:
                raise HTTPException(status_code=404, detail="Could not extract audio stream link.")
            
            return RedirectResponse(url=audio_url, status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Streaming error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
