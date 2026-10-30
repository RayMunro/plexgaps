import hashlib
import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .plex import Plex
from .scanner import Scanner
from .tmdb import Tmdb


def env_list(name):
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def env_bool(name):
    return os.environ.get(name, "false").lower() in ("1", "true", "yes")


DATA_DIR = Path(os.environ.get("DATA_DIR", "/config"))
RESULTS_FILE = DATA_DIR / "results.json"
IGNORE_FILE = DATA_DIR / "ignore.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="PlexGaps")
state = {"error": None}
scan_lock = threading.Lock()
scanner = None


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path, data, private=False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    if private:
        tmp.chmod(0o600)
    tmp.replace(path)


def get_settings():
    """Environment variables give the defaults, the settings page overrides them."""
    s = {
        "plex_url": os.environ.get("PLEX_URL", ""),
        "plex_token": os.environ.get("PLEX_TOKEN", ""),
        "tmdb_api_key": os.environ.get("TMDB_API_KEY", ""),
        "tv_libraries": env_list("PLEX_TV_LIBRARIES"),
        "movie_libraries": env_list("PLEX_MOVIE_LIBRARIES"),
        "scan_interval_hours": float(os.environ.get("SCAN_INTERVAL_HOURS", "24")),
        "include_specials": env_bool("INCLUDE_SPECIALS"),
        "include_unaired": env_bool("INCLUDE_UNAIRED"),
    }
    s.update(load_json(SETTINGS_FILE, {}))
    return s


def configured(s):
    return bool(s["plex_url"] and s["plex_token"] and s["tmdb_api_key"])


def normalize_url(url):
    url = url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def build_scanner(s):
    return Scanner(
        Plex(s["plex_url"], s["plex_token"]),
        Tmdb(s["tmdb_api_key"]),
        s["tv_libraries"],
        s["movie_libraries"],
        s["include_specials"],
        s["include_unaired"],
    )


def do_scan():
    global scanner
    if not scan_lock.acquire(blocking=False):
        return False
    try:
        state["error"] = None
        s = get_settings()
        if not configured(s):
            raise RuntimeError("Not configured. Open Settings and enter the Plex and TMDB details.")
        scanner = scanner or build_scanner(s)
        save_json(RESULTS_FILE, scanner.run())
    except Exception as e:  # surfaced in the UI
        state["error"] = f"{type(e).__name__}: {e}"
    finally:
        scan_lock.release()
    return True


def settings_signature(s):
    return hashlib.sha1(json.dumps(s, sort_keys=True).encode()).hexdigest()


def scheduler():
    last_run = RESULTS_FILE.stat().st_mtime if RESULTS_FILE.exists() else 0
    attempted = None
    while True:
        s = get_settings()
        if configured(s):
            sig = settings_signature(s)
            interval = s["scan_interval_hours"] * 3600
            first = not RESULTS_FILE.exists() and sig != attempted
            due = interval > 0 and time.time() - last_run >= interval
            if first or due:
                attempted = sig
                do_scan()
                last_run = time.time()
        time.sleep(30)


@app.on_event("startup")
def startup():
    threading.Thread(target=scheduler, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    p = dict(scanner.progress) if scanner else {"running": False, "done": 0, "total": 0, "message": "Idle"}
    p["running"] = p["running"] or scan_lock.locked()
    p["error"] = state["error"]
    p["configured"] = configured(get_settings())
    return p


@app.post("/api/scan")
def scan():
    if scan_lock.locked():
        return {"started": False}
    threading.Thread(target=do_scan, daemon=True).start()
    return {"started": True}


@app.get("/api/results")
def results():
    data = load_json(RESULTS_FILE, None)
    if data is None:
        return {"scanned_at": None, "tv": [], "movies": [], "unmatched": {"tv": [], "movies": []}, "stats": {}}
    ignored = set(load_json(IGNORE_FILE, []))
    for item in data["tv"] + data["movies"]:
        item["ignored"] = item["key"] in ignored
    return data


class Ignore(BaseModel):
    key: str
    ignored: bool


@app.post("/api/ignore")
def ignore(body: Ignore):
    if not body.key.startswith(("tv:", "col:")):
        raise HTTPException(400, "bad key")
    keys = set(load_json(IGNORE_FILE, []))
    (keys.add if body.ignored else keys.discard)(body.key)
    save_json(IGNORE_FILE, sorted(keys))
    return {"ok": True}


class SettingsIn(BaseModel):
    plex_url: str = ""
    plex_token: str = ""  # blank keeps the saved value
    tmdb_api_key: str = ""  # blank keeps the saved value
    tv_libraries: list[str] = []
    movie_libraries: list[str] = []
    scan_interval_hours: float = 24
    include_specials: bool = False
    include_unaired: bool = False


def merged(body):
    saved = get_settings()
    s = body.model_dump()
    s["plex_url"] = normalize_url(s["plex_url"])
    s["plex_token"] = s["plex_token"].strip() or saved["plex_token"]
    s["tmdb_api_key"] = s["tmdb_api_key"].strip() or saved["tmdb_api_key"]
    s["scan_interval_hours"] = max(0.0, s["scan_interval_hours"])
    return s


@app.get("/api/settings")
def read_settings():
    s = get_settings()
    out = {k: v for k, v in s.items() if k not in ("plex_token", "tmdb_api_key")}
    out["has_plex_token"] = bool(s["plex_token"])
    out["has_tmdb_key"] = bool(s["tmdb_api_key"])
    out["configured"] = configured(s)
    return out


@app.put("/api/settings")
def write_settings(body: SettingsIn):
    global scanner
    s = merged(body)
    save_json(SETTINGS_FILE, s, private=True)
    scanner = None  # rebuilt with the new settings on the next scan
    return {"ok": True}


@app.post("/api/test")
def test_connection(body: SettingsIn):
    s = merged(body)
    out = {"plex": {"ok": False, "error": None, "libraries": []}, "tmdb": {"ok": False, "error": None}}
    if not s["plex_url"] or not s["plex_token"]:
        out["plex"]["error"] = "Enter the Plex URL and token."
    else:
        try:
            sections = Plex(s["plex_url"], s["plex_token"]).sections()
            out["plex"]["libraries"] = [
                {"title": d["title"], "type": d["type"]} for d in sections if d.get("type") in ("show", "movie")
            ]
            out["plex"]["ok"] = True
        except Exception as e:
            out["plex"]["error"] = f"{type(e).__name__}: {e}"
    if not s["tmdb_api_key"]:
        out["tmdb"]["error"] = "Enter the TMDB API key."
    else:
        try:
            out["tmdb"]["ok"] = Tmdb(s["tmdb_api_key"]).get("/configuration") is not None
        except Exception as e:
            out["tmdb"]["error"] = f"{type(e).__name__}: {e}"
    return out
