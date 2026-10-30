import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor

from .plex import parse_guids
from .tmdb import IMAGE_BASE

WORKERS = 8


def poster(path):
    return IMAGE_BASE + path if path else None


def year_of(date):
    return date[:4] if date else ""


class Scanner:
    def __init__(self, plex, tmdb, tv_libraries=(), movie_libraries=(),
                 include_specials=False, include_unaired=False):
        self.plex = plex
        self.tmdb = tmdb
        self.tv_libraries = {n.lower() for n in tv_libraries}
        self.movie_libraries = {n.lower() for n in movie_libraries}
        self.include_specials = include_specials
        self.include_unaired = include_unaired
        self.lock = threading.Lock()
        self.progress = {"running": False, "done": 0, "total": 0, "message": "Idle"}

    def _set(self, **kw):
        with self.lock:
            self.progress.update(kw)

    def _tick(self):
        with self.lock:
            self.progress["done"] += 1

    def _released(self, date, today):
        if self.include_unaired:
            return True
        return bool(date) and date <= today

    def _libraries(self, kind, wanted):
        out = []
        for s in self.plex.sections():
            if s.get("type") != kind:
                continue
            if wanted and s.get("title", "").lower() not in wanted:
                continue
            out.append(s)
        return out

    def run(self):
        self._set(running=True, done=0, total=0, message="Reading Plex libraries")
        try:
            self.tmdb.clear_cache()
            today = dt.date.today().isoformat()
            shows = [i for s in self._libraries("show", self.tv_libraries)
                     for i in self.plex.items(s["key"])]
            movies = [i for s in self._libraries("movie", self.movie_libraries)
                      for i in self.plex.items(s["key"])]
            self._set(total=len(shows) + len(movies), message="Checking TV series")
            tv, tv_unmatched = self._scan_tv(shows, today)
            self._set(message="Checking movie collections")
            cols, movie_unmatched = self._scan_movies(movies, today)
            result = {
                "scanned_at": dt.datetime.now().isoformat(timespec="seconds"),
                "tv": sorted(tv, key=lambda x: x["title"].lower()),
                "movies": sorted(cols, key=lambda x: x["title"].lower()),
                "unmatched": {"tv": sorted(tv_unmatched), "movies": sorted(movie_unmatched)},
                "stats": {"shows": len(shows), "movies": len(movies)},
            }
            self._set(message="Done")
            return result
        finally:
            self._set(running=False)

    def _scan_tv(self, shows, today):
        def one(show):
            try:
                tid = self.tmdb.resolve(parse_guids(show), "tv")
                if not tid:
                    return ("unmatched", show["title"])
                return ("ok", self._check_show(show, tid, today))
            finally:
                self._tick()

        with ThreadPoolExecutor(WORKERS) as pool:
            results = list(pool.map(one, shows))
        found = [r for k, r in results if k == "ok" and r]
        return found, [r for k, r in results if k == "unmatched"]

    def _check_show(self, show, tid, today):
        details = self.tmdb.tv(tid)
        if not details:
            return None
        owned = {(e["parentIndex"], e["index"])
                 for e in self.plex.episodes(show["ratingKey"])
                 if "parentIndex" in e and "index" in e}
        missing, total = [], 0
        for s in details.get("seasons", []):
            n = s["season_number"]
            if n == 0 and not self.include_specials:
                continue
            season = self.tmdb.season(tid, n)
            for ep in (season or {}).get("episodes", []):
                if not self._released(ep.get("air_date"), today):
                    continue
                total += 1
                if (n, ep["episode_number"]) not in owned:
                    missing.append({
                        "season": n,
                        "episode": ep["episode_number"],
                        "title": ep.get("name") or "",
                        "air_date": ep.get("air_date") or "",
                    })
        if not missing:
            return None
        return {
            "key": f"tv:{tid}",
            "title": show["title"],
            "year": year_of(details.get("first_air_date")),
            "poster": poster(details.get("poster_path")),
            "tmdb_id": tid,
            "total": total,
            "have": total - len(missing),
            "missing": missing,
        }

    def _scan_movies(self, movies, today):
        def one(movie):
            try:
                mid = self.tmdb.resolve(parse_guids(movie), "movie")
                if not mid:
                    return ("unmatched", movie["title"], None)
                details = self.tmdb.movie(mid)
                col = (details or {}).get("belongs_to_collection")
                return ("ok", mid, col["id"] if col else None)
            finally:
                self._tick()

        with ThreadPoolExecutor(WORKERS) as pool:
            results = list(pool.map(one, movies))
        owned = {mid for k, mid, _ in results if k == "ok"}
        unmatched = [t for k, t, _ in results if k == "unmatched"]
        col_ids = {cid for k, _, cid in results if k == "ok" and cid}

        def collection(cid):
            data = self.tmdb.collection(cid)
            if not data:
                return None
            parts = data.get("parts", [])
            have = [p for p in parts if p["id"] in owned]
            miss = [p for p in parts
                    if p["id"] not in owned and self._released(p.get("release_date"), today)]
            if not miss:
                return None
            miss.sort(key=lambda p: p.get("release_date") or "9999")
            have.sort(key=lambda p: p.get("release_date") or "9999")
            return {
                "key": f"col:{cid}",
                "title": data.get("name", f"Collection {cid}"),
                "poster": poster(data.get("poster_path")),
                "collection_id": cid,
                "have": [{"title": p["title"], "year": year_of(p.get("release_date"))} for p in have],
                "missing": [{
                    "title": p["title"],
                    "year": year_of(p.get("release_date")),
                    "tmdb_id": p["id"],
                    "release_date": p.get("release_date") or "",
                    "poster": poster(p.get("poster_path")),
                } for p in miss],
            }

        with ThreadPoolExecutor(WORKERS) as pool:
            cols = [c for c in pool.map(collection, col_ids) if c]
        return cols, unmatched
