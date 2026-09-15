import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from ytmusicapi import YTMusic
import yt_dlp

app = FastAPI(
    title="Spotify-Style YouTube Music API",
    description="A real working music streaming and search API powered by Python, YouTube Music, and yt-dlp.",
    version="2.0.0"
)

# Initialize YouTube Music client (unauthenticated public search)
ytmusic = YTMusic()

@app.get("/")
def home():
    return {
        "status": "online",
        "message": "Welcome to your self-hosted Spotify-like API!",
        "endpoints": {
            "search_tracks": "/api/search?q=the weeknd",
            "stream_track": "/api/stream/{video_id}",
            "trending_charts": "/api/charts"
        }
    }

@app.get("/api/search")
def search_tracks(q: str = Query(..., description="Search song, artist, or album")):
    """Search tracks with Spotify-like metadata (Thumbnails, Artists, Albums)"""
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
                "stream_url": f"/api/stream/{video_id}"
            })
        return {"query": q, "total": len(tracks), "tracks": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream/{video_id}")
def stream_track(video_id: str):
    """Extracts the direct live audio stream URL and redirects players for instant playback"""
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
            
            # Redirect browser/client straight to the active audio source
            return RedirectResponse(url=audio_url, status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Streaming error: {str(e)}")

@app.get("/api/charts")
def get_charts():
    """Get trending music hits"""
    try:
        search_results = ytmusic.search("Global top hits", filter="songs", limit=10)
        tracks = []
        for item in search_results:
            thumbnails = item.get("thumbnails", [])
            thumb_url = thumbnails[-1]["url"] if thumbnails else ""
            artists = item.get("artists", [])
            artist_name = artists[0]["name"] if artists else "Unknown Artist"
            video_id = item.get("videoId")
            if not video_id:
                continue
            tracks.append({
                "id": video_id,
                "title": item.get("title"),
                "artist": artist_name,
                "thumbnail": thumb_url,
                "stream_url": f"/api/stream/{video_id}"
            })
        return {"charts": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
