"""
Start the full DloWatch backend: the original api.py plus the DloWatch additions.

  uvicorn server:app --reload          (run from the port_louis_epanet folder)

  /docs        every endpoint, try them in the browser
  /api/...     endpoints used by the Angular app
  /health      quick check that the server is up

If you build the Angular app (npm run build) and copy dist/frontend/browser into a
folder called "web" next to this file, the whole system runs from this one server on port 8000.
"""

import os
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import app
import dlowatch

# The original api.py test routes (/predict, /simulate-leak, /repairs, the "/" test dashboard) have no login.
# They are not used by the app, so they are switched off here. api.py itself is left unchanged.
# Set DLOWATCH_LEGACY_ROUTES=on to keep them for local testing.
if os.environ.get("DLOWATCH_LEGACY_ROUTES", "off").lower() != "on":
    KEEP = ("/health", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc")
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") in KEEP]

app.include_router(dlowatch.router, prefix="/api")


@app.middleware("http")
async def save_changes(request, call_next):
    """Anything that changes data (POST, PUT, DELETE) is written to the database straight after."""
    response = await call_next(request)
    if request.method != "GET":
        dlowatch.flush()
    return response


dlowatch.start()     # open the database (backend/data/dlowatch.db), load it, and run the network live

WEB = Path(__file__).with_name("web")
if WEB.exists():
    # The built Angular app (frontend/dist/frontend/browser copied to backend/web) is served at /.
    # Anything that is not a file and not /api/... gets index.html, so deep links like /dashboard/alerts work.
    from fastapi import HTTPException

    ROOT = WEB.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def angular(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        f = (ROOT / path).resolve()
        if path and f.is_file() and ROOT in f.parents:
            return FileResponse(f)
        return FileResponse(ROOT / "index.html")
