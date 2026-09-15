import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from ytmusicapi import YTMusic
import yt_dlp

app = FastAPI(
    title="Spotify-Alternative Music API",
    description="Full-featured music streaming, search, trending charts, lyrics, recommendations, and cookie auth.",
    version="3.2.0"
)

ytmusic = YTMusic()

@app.get("/")
def home():
    return {
        "status": "online",
        "message": "Music API is online and cookie-ready!",
        "endpoints": {
            "search": "/api/search?q=artist_or_song",
            "trending": "/api/trending",
            "recommendations": "/api/recommendations/{video_id}",
            "lyrics": "/api/lyrics/{video_id}",
            "stream": "/api/stream/{video_id}"
        }
    }

@app.get("/api/search")
def search_tracks(q: str = Query(..., description="Search song, artist, or album")):
    try:
        search_results = ytmusic.search(q, filter="songs", limit=20)
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
                "lyrics_url": f"/api/lyrics/{video_id}",
                "recommendations_url": f"/api/recommendations/{video_id}"
            })
        return {"query": q, "total": len(tracks), "tracks": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trending")
def get_trending():
    try:
        search_results = ytmusic.search("Global top hits music", filter="songs", limit=20)
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
                "stream_url": f"/api/stream/{video_id}",
                "lyrics_url": f"/api/lyrics/{video_id}"
            })
        return {"count": len(tracks), "tracks": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/recommendations/{video_id}")
def get_recommendations(video_id: str):
    try:
        watch_playlist = ytmusic.get_watch_playlist(videoId=video_id)
        tracks = []
        for item in watch_playlist.get("tracks", []):
            vid = item.get("videoId")
            if not vid:
                continue
            thumbnails = item.get("thumbnails", [])
            thumb_url = thumbnails[-1]["url"] if thumbnails else ""
            artists = item.get("artists", [])
            artist_name = artists[0]["name"] if artists else "Unknown Artist"
            tracks.append({
                "id": vid,
                "title": item.get("title"),
                "artist": artist_name,
                "thumbnail": thumb_url,
                "stream_url": f"/api/stream/{vid}",
                "lyrics_url": f"/api/lyrics/{vid}"
            })
        return {"seed_video_id": video_id, "recommendations": tracks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not fetch recommendations: {str(e)}")

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
    """Extracts stream URL with client spoofing and automatic cookies.txt fallback"""
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}}
        }
        
        # Automatically detect cookies.txt in the app folder if uploaded
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
