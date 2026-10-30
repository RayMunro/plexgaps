import time

import httpx

IMAGE_BASE = "https://image.tmdb.org/t/p/w185"


class Tmdb:
    def __init__(self, key, transport=None):
        headers, params = {}, {}
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        else:
            params["api_key"] = key
        self.http = httpx.Client(
            base_url="https://api.themoviedb.org/3",
            headers=headers,
            params=params,
            timeout=20,
            transport=transport,
        )
        self.cache = {}

    def clear_cache(self):
        self.cache.clear()

    def get(self, path, **params):
        key = (path, tuple(sorted(params.items())))
        if key in self.cache:
            return self.cache[key]
        for _ in range(6):
            r = self.http.get(path, params=params)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", "2")))
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                # httpx's own message would include the api_key query parameter
                hint = " (check the API key)" if r.status_code == 401 else ""
                raise RuntimeError(f"TMDB returned {r.status_code} for {path}{hint}")
            data = r.json()
            self.cache[key] = data
            return data
        raise RuntimeError(f"TMDB rate limit not clearing for {path}")

    def tv(self, tmdb_id):
        return self.get(f"/tv/{tmdb_id}")

    def season(self, tmdb_id, number):
        return self.get(f"/tv/{tmdb_id}/season/{number}")

    def movie(self, tmdb_id):
        return self.get(f"/movie/{tmdb_id}")

    def collection(self, collection_id):
        return self.get(f"/collection/{collection_id}")

    def resolve(self, ids, kind):
        """Map Plex guids to a TMDB id for kind 'tv' or 'movie'."""
        if ids.get("tmdb"):
            return int(ids["tmdb"])
        lookups = [("imdb", "imdb_id")]
        if kind == "tv":
            lookups.insert(0, ("tvdb", "tvdb_id"))
        for src, ext in lookups:
            if ids.get(src):
                data = self.get(f"/find/{ids[src]}", external_source=ext)
                results = (data or {}).get(f"{kind}_results") or []
                if results:
                    return results[0]["id"]
        return None
