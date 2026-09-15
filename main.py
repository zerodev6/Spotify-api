import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from ytmusicapi import YTMusic
import yt_dlp

app = FastAPI(
    title="Spotify-Style YouTube Music API",
    description="Full music streaming backend with cookie-auth fallback.",
    version="2.1.1"
)

ytmusic = YTMusic()

@app.get("/")
def home():
    return {"status": "online", "message": "API is running with cookie support!"}

@app.get("/api/search")
def search_tracks(q: str = Query(..., description="Search term")):
    try:
        search_results = ytmusic.search(q, filter="songs", limit=15)
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
                "stream_url": f"/api/stream/{video_id}",
                "lyrics_url": f"/api/lyrics/{video_id}"
            })
        return {"query": q, "total": len(tracks), "tracks": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trending")
def get_trending():
    try:
        search_results = ytmusic.search("Top global hits", filter="songs", limit=15)
        tracks = []
        for item in search_results:
            video_id = item.get("videoId")
            if not video_id:
                continue
            thumbnails = item.get("thumbnails", [])
            thumb_url = thumbnails[-1]["url"] if thumbnails else ""
            artists = item.get("artists", [])
            artist_name = artists[0]["name"] if artists else "Unknown Artist"
            tracks.append({
                "id": video_id,
                "title": item.get("title"),
                "artist": artist_name,
                "thumbnail": thumb_url,
                "stream_url": f"/api/stream/{video_id}"
            })
        return {"tracks": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/lyrics/{video_id}")
def get_lyrics(video_id: str):
    try:
        watch_playlist = ytmusic.get_watch_playlist(videoId=video_id)
        lyrics_browse_id = watch_playlist.get("lyrics")
        if not lyrics_browse_id:
            raise HTTPException(status_code=404, detail="Lyrics not found.")
        lyrics_data = ytmusic.get_lyrics(lyrics_browse_id)
        return {"video_id": video_id, "lyrics": lyrics_data.get("lyrics", "No lyrics available.")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream/{video_id}")
def stream_track(video_id: str):
    """Extracts direct audio stream URL using cookies if available"""
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        
        # Automatically use cookies.txt if it exists in the app folder
        cookies_path = os.path.join(os.path.dirname(__file__), "cookies.txt")
        if os.path.exists(cookies_path):
            ydl_opts['cookiefile'] = cookies_path

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
