from app.plex import parse_guids
from app.scanner import Scanner


class FakePlex:
    def sections(self):
        return [{"key": "1", "title": "TV", "type": "show"},
                {"key": "2", "title": "Movies", "type": "movie"}]

    def items(self, key):
        if key == "1":
            return [
                {"ratingKey": "10", "title": "Show A", "Guid": [{"id": "tmdb://100"}]},
                {"ratingKey": "11", "title": "Complete", "Guid": [{"id": "tmdb://101"}]},
                {"ratingKey": "12", "title": "Mystery", "Guid": []},
            ]
        return [
            {"ratingKey": "20", "title": "Movie 1", "Guid": [{"id": "tmdb://1"}]},
            {"ratingKey": "21", "title": "Movie 3", "Guid": [{"id": "imdb://tt3"}]},
            {"ratingKey": "22", "title": "Solo", "Guid": [{"id": "tmdb://9"}]},
        ]

    def episodes(self, rk):
        if rk == "10":
            return [{"parentIndex": 1, "index": 1}, {"parentIndex": 1, "index": 3}]
        return [{"parentIndex": 1, "index": 1}]


class FakeTmdb:
    def clear_cache(self):
        pass

    def resolve(self, ids, kind):
        if ids.get("tmdb"):
            return int(ids["tmdb"])
        return {"tt3": 3}.get(ids.get("imdb"))

    def tv(self, tid):
        return {"first_air_date": "2020-01-01", "seasons": [{"season_number": 0}, {"season_number": 1}]}

    def season(self, tid, n):
        if n == 0:
            return {"episodes": [{"episode_number": 1, "air_date": "2020-01-01", "name": "Special"}]}
        eps = [{"episode_number": i, "air_date": "2020-01-01", "name": f"E{i}"} for i in (1, 2, 3)]
        if tid == 100:
            eps.append({"episode_number": 4, "air_date": "2999-01-01", "name": "Future"})
        return {"episodes": eps[:1] if tid == 101 else eps}

    def movie(self, mid):
        return {"belongs_to_collection": {"id": 500} if mid in (1, 3) else None}

    def collection(self, cid):
        return {"name": "Trilogy", "parts": [
            {"id": 1, "title": "Movie 1", "release_date": "2001-01-01"},
            {"id": 2, "title": "Movie 2", "release_date": "2003-01-01"},
            {"id": 3, "title": "Movie 3", "release_date": "2005-01-01"},
            {"id": 4, "title": "Movie 4", "release_date": "2999-01-01"},
        ]}


def test_parse_guids_modern_and_legacy():
    assert parse_guids({"Guid": [{"id": "tvdb://5"}, {"id": "tmdb://7"}]}) == {"tvdb": "5", "tmdb": "7"}
    assert parse_guids({"guid": "com.plexapp.agents.thetvdb://78901?lang=en"}) == {"tvdb": "78901"}


def test_scan_finds_gaps():
    res = Scanner(FakePlex(), FakeTmdb()).run()
    assert [s["title"] for s in res["tv"]] == ["Show A"]
    show = res["tv"][0]
    assert [(e["season"], e["episode"]) for e in show["missing"]] == [(1, 2)]
    assert (show["have"], show["total"]) == (2, 3)
    assert res["unmatched"]["tv"] == ["Mystery"]
    (col,) = res["movies"]
    assert [m["title"] for m in col["missing"]] == ["Movie 2"]
    assert len(col["have"]) == 2


def test_options_include_specials_and_unaired():
    res = Scanner(FakePlex(), FakeTmdb(), include_specials=True, include_unaired=True).run()
    show = next(s for s in res["tv"] if s["title"] == "Show A")
    assert (0, 1) in {(e["season"], e["episode"]) for e in show["missing"]}
    assert (1, 4) in {(e["season"], e["episode"]) for e in show["missing"]}
    assert "Movie 4" in [m["title"] for m in res["movies"][0]["missing"]]
