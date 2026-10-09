# syntax=docker/dockerfile:1
# Two images for the discover stack (compose picks one with `target:`).
#
# jellylook needs TMDb for ids, posters, genres, trailers and streaming info, and
# only talks to it with an API key we don't have. Seerr already has TMDb access and
# returns the same data through its own API, so:
#   - tmdb-via-seerr answers the handful of TMDb v3 calls jellylook makes, from Seerr;
#   - jellylook is the upstream image with its TMDb address pointed at that service.
# The shim lives inline here (not as a .py next to it) so the nightly sync backs it up
# with the Dockerfile.

FROM python:3.12-alpine AS tmdb-via-seerr
COPY <<'EOF' /app/shim.py
"""TMDb v3 look-alike answered from Seerr's API - only what jellylook's metadata.py calls:

  /3/search/movie?query=&year=          /3/search/tv?query=&first_air_date_year=
  /3/movie/<id>   /3/tv/<id>            (as if append_to_response=external_ids,videos,watch/providers)
  /3/tv/<id>/watch/providers
"""
import json, os, re, sys, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEERR = os.environ["SEERR_URL"].rstrip("/")
KEY = [[secret:JELLYFIN_KEY_2]]
KINDS = ("flatrate", "free", "ads", "rent", "buy")


def seerr(path, **params):
    # quote, not quote_plus: Seerr rejects '+' for spaces in a search query
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    req = urllib.request.Request(f"{SEERR}/api/v1{path}" + (f"?{qs}" if qs else ""),
                                 headers={"X-Api-Key": KEY, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def providers(d):
    out = {}
    for w in d.get("watchProviders") or []:
        entry = {"link": w.get("link")}
        for kind in KINDS:
            if w.get(kind):
                entry[kind] = [{"provider_id": p.get("id"), "provider_name": p.get("name"),
                                "logo_path": p.get("logoPath")} for p in w[kind]]
        out[w.get("iso_3166_1")] = entry
    return {"id": d.get("id"), "results": out}


def common(r):
    return {"id": r.get("id"), "poster_path": r.get("posterPath"), "backdrop_path": r.get("backdropPath"),
            "overview": r.get("overview"), "original_language": r.get("originalLanguage"),
            "popularity": r.get("popularity"), "vote_average": r.get("voteAverage"),
            "vote_count": r.get("voteCount")}


def search(kind, query, year):
    found = []
    for page in (1, 2):
        data = seerr("/search", query=query, page=page)
        for r in data.get("results") or []:
            date = r.get("releaseDate") or r.get("firstAirDate") or ""
            if r.get("mediaType") != kind or (year and not date.startswith(str(year))):
                continue
            item = common(r)
            if kind == "movie":
                item.update(title=r.get("title"), original_title=r.get("originalTitle"), release_date=date)
            else:
                item.update(name=r.get("name"), original_name=r.get("originalName"), first_air_date=date)
            found.append(item)
        if found or page >= (data.get("totalPages") or 1):
            break
    return {"page": 1, "results": found, "total_pages": 1, "total_results": len(found)}


def details(kind, tmdb_id):
    d = seerr(f"/{kind}/{tmdb_id}")
    imdb = d.get("imdbId") or (d.get("externalIds") or {}).get("imdbId")
    out = common(d)
    out.update(
        genres=d.get("genres") or [],
        imdb_id=imdb,
        external_ids={"imdb_id": imdb},
        videos={"results": [{"site": v.get("site"), "key": v.get("key"), "type": v.get("type"),
                             "name": v.get("name"), "official": False} for v in d.get("relatedVideos") or []]},
        **{"watch/providers": providers(d)})
    if kind == "movie":
        out.update(title=d.get("title"), release_date=d.get("releaseDate"), runtime=d.get("runtime"))
    else:
        out.update(name=d.get("name"), first_air_date=d.get("firstAirDate"),
                   episode_run_time=d.get("episodeRunTime") or [])
    return out


ROUTES = [
    (re.compile(r"^/3/search/(movie|tv)$"),
     lambda m, q: search(m[1], q.get("query", ""), q.get("year") or q.get("first_air_date_year"))),
    (re.compile(r"^/3/(movie|tv)/(\d+)$"), lambda m, q: details(m[1], m[2])),
    (re.compile(r"^/3/tv/(\d+)/watch/providers$"), lambda m, q: providers(seerr(f"/tv/{m[1]}"))),
    (re.compile(r"^/health$"), lambda m, q: {"ok": True}),
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        code, body = 404, {"status_code": 34, "status_message": "not found"}
        for pattern, handler in ROUTES:
            m = pattern.match(url.path)
            if m:
                try:
                    code, body = 200, handler(m, query)
                except urllib.error.HTTPError as e:
                    code, body = (404 if e.code == 404 else 502), {"status_message": f"seerr: HTTP {e.code}"}
                except Exception as e:  # noqa: BLE001
                    print(f"{url.path}: {e}", file=sys.stderr, flush=True)
                    code, body = 502, {"status_message": str(e)[:200]}
                break
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
EOF
HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1
CMD ["python", "-u", "/app/shim.py"]


# Pinned to an exact upstream build: the patch below edits one line of its source,
# so bump this tag deliberately (and rebuild) rather than following :latest.
FROM ghcr.io/dean1850/jellylook:sha-b08a4d1 AS jellylook
RUN grep -q '^TMDB_BASE = "https://api.themoviedb.org/3"$' app/metadata.py \
 && sed -i 's#^TMDB_BASE = .*#TMDB_BASE = "http://tmdb-via-seerr:8000/3"#' app/metadata.py
