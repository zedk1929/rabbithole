"""
Rabbithole Web Application Server.
Exposes the recommendation and discovery REST API and serves the interactive UI.
"""
import os
import logging
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from dotenv import load_dotenv, set_key

from rabbithole_app.models import UserPreferences, FeedResponse, CURIOSITY_ARCHETYPES
from rabbithole_app.service import RabbitholeService
from rabbithole_app.youtube_integrator import (
    fetch_browser_youtube_data,
    parse_subscriptions_file,
    synthesize_youtube_taste,
    apply_youtube_sync_to_preferences,
)

load_dotenv(os.path.expanduser("~/.env"))
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rabbithole")

app = FastAPI(
    title="Rabbithole API",
    description="Anti-clickbait, curiosity-driven media recommendation engine.",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize service
service = RabbitholeService()

STATIC_DIR = Path(__file__).parent / "static"


@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_index():
    """Serves the main application UI."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(
        index_file,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


@app.get("/api/health")
async def health_check():
    """Returns application health and API configuration status."""
    has_gemini = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    has_youtube = bool(os.getenv("YOUTUBE_API_KEY"))
    return {
        "status": "healthy",
        "gemini_configured": has_gemini,
        "youtube_api_configured": has_youtube,
        "mode": "full_llm" if has_gemini else "heuristic_fast",
    }


class ConfigRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    youtube_api_key: Optional[str] = None


@app.post("/api/config")
async def update_config(config: ConfigRequest):
    """Dynamically updates API keys in environment and .env file."""
    global service
    env_path = Path.cwd() / ".env"
    
    if config.gemini_api_key:
        os.environ["GEMINI_API_KEY"] = config.gemini_api_key.strip()
        set_key(str(env_path), "GEMINI_API_KEY", config.gemini_api_key.strip())
        logger.info("Updated GEMINI_API_KEY.")

    if config.youtube_api_key:
        os.environ["YOUTUBE_API_KEY"] = config.youtube_api_key.strip()
        set_key(str(env_path), "YOUTUBE_API_KEY", config.youtube_api_key.strip())
        logger.info("Updated YOUTUBE_API_KEY.")

    # Re-initialize service with updated keys
    service = RabbitholeService()
    return {"status": "keys_updated"}


@app.post("/api/feed", response_model=FeedResponse)
async def generate_feed(preferences: UserPreferences):
    """
    Main recommendation endpoint:
    Processes user curiosity preferences, expands lateral queries,
    retrieves candidate media, and applies two-tier anti-clickbait scoring.
    """
    try:
        response = await run_in_threadpool(service.generate_feed, preferences)
        return response
    except Exception as e:
        logger.exception(f"Error in feed generation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BranchRequest(BaseModel):
    branch_topic: str
    base_preferences: UserPreferences


@app.post("/api/branch", response_model=FeedResponse)
async def branch_out(request: BranchRequest):
    """
    Branching endpoint:
    Explores a specific tangent or adjacent concept selected from a recommended item.
    """
    try:
        response = service.branch_out(request.branch_topic, request.base_preferences)
        return response
    except Exception as e:
        logger.exception(f"Error in branch generation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/archetypes")
async def get_archetypes():
    """Returns canonical curiosity archetypes and descriptions for personality-driven discovery."""
    return {"archetypes": list(CURIOSITY_ARCHETYPES.values())}


class BrowserSyncRequest(BaseModel):
    browser: str = "chrome"
    preferences: Optional[UserPreferences] = None


@app.post("/api/youtube/sync-browser")
async def sync_youtube_from_browser(req: BrowserSyncRequest):
    """
    Extracts subscriptions from local browser session cookies and synthesizes
    personality-based curiosity archetypes and followed creators with Gemini.
    """
    try:
        browser_data = await run_in_threadpool(fetch_browser_youtube_data, req.browser, limit=60)
        if not browser_data.get("success"):
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": browser_data.get("error", "Failed to access browser YouTube session."),
                    "detail": browser_data.get("error", "Failed to access browser YouTube session.")
                }
            )

        channels = browser_data.get("channels", [])
        sample_titles = browser_data.get("recommended_titles", [])

        # Synthesize personality archetypes and taste profile
        sync_result = await run_in_threadpool(
            synthesize_youtube_taste,
            channels,
            sample_titles,
            service.gemini_api_key
        )

        base_prefs = req.preferences or UserPreferences()
        # Apply to preferences
        updated_prefs = apply_youtube_sync_to_preferences(
            base_prefs,
            sync_result,
            channel_count=len(channels),
            sync_mode=f"browser_{req.browser}"
        )

        return {
            "success": True,
            "status": "ok",
            "browser": req.browser,
            "channel_count": len(channels),
            "channels_analyzed": len(channels),
            "sync_result": sync_result,
            "synthesized_profile": sync_result,
            "updated_preferences": updated_prefs
        }
    except Exception as e:
        logger.exception(f"Error syncing from browser: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e), "detail": str(e)})


class FileImportRequest(BaseModel):
    content: str
    filename: str = ""
    preferences: Optional[UserPreferences] = None


@app.post("/api/youtube/import-subscriptions")
async def import_youtube_subscriptions(req: FileImportRequest):
    """
    Imports YouTube subscriptions from Takeout CSV, JSON, or channel list text,
    and synthesizes curiosity archetypes and followed creators.
    """
    try:
        channels = parse_subscriptions_file(req.content, req.filename)
        if not channels:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "No valid channel names or subscriptions found in content.",
                    "detail": "No valid channel names or subscriptions found in content."
                }
            )

        sync_result = await run_in_threadpool(
            synthesize_youtube_taste,
            channels,
            [],
            service.gemini_api_key
        )

        base_prefs = req.preferences or UserPreferences()
        updated_prefs = apply_youtube_sync_to_preferences(
            base_prefs,
            sync_result,
            channel_count=len(channels),
            sync_mode="file_import"
        )

        return {
            "success": True,
            "status": "ok",
            "channel_count": len(channels),
            "channels_analyzed": len(channels),
            "sync_result": sync_result,
            "synthesized_profile": sync_result,
            "updated_preferences": updated_prefs
        }
    except Exception as e:
        logger.exception(f"Error importing subscriptions: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e), "detail": str(e)})
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


def run():
    """CLI entrypoint to run the server."""
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "false").lower() in ("true", "1")
    uvicorn.run("rabbithole_app.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    run()
