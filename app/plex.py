import re

import httpx

LEGACY_AGENT = re.compile(r"agents\.(themoviedb|thetvdb|imdb)://([^/?]+)")
LEGACY_NAMES = {"themoviedb": "tmdb", "thetvdb": "tvdb", "imdb": "imdb"}


def parse_guids(item):
    """Return {'tmdb': ..., 'tvdb': ..., 'imdb': ...} for a Plex metadata item."""
    ids = {}
    for g in item.get("Guid") or []:
        src, _, val = g.get("id", "").partition("://")
        if src in ("tmdb", "tvdb", "imdb") and val:
            ids[src] = val
    m = LEGACY_AGENT.search(item.get("guid", ""))
    if m:
        ids.setdefault(LEGACY_NAMES[m.group(1)], m.group(2))
    return ids


class Plex:
    def __init__(self, url, token, transport=None):
        self.http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"X-Plex-Token": token, "Accept": "application/json"},
            timeout=60,
            transport=transport,
        )

    def _container(self, path, **params):
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json().get("MediaContainer", {})

    def sections(self):
        return self._container("/library/sections").get("Directory", [])

    def items(self, section_key):
        return self._container(
            f"/library/sections/{section_key}/all", includeGuids=1
        ).get("Metadata", [])

    def episodes(self, rating_key):
        return self._container(f"/library/metadata/{rating_key}/allLeaves").get(
            "Metadata", []
        )
