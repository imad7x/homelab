"""A small, anonymous client for the Docker Registry HTTP API v2.

It answers two questions about an image reference such as
"lscr.io/linuxserver/jellyfin:latest":

  remote_digest()  which manifest does the tag point to on the registry now?
  image_config()   what labels and build date does that image carry?

The same API is spoken by Docker Hub, ghcr.io, lscr.io, gcr.io and quay.io,
so nothing here is specific to one registry. Requests are anonymous on
purpose: every image in use is public, and Docker Hub's anonymous pull limit
is only charged for manifest GETs, which happen only when a tag has moved.
"""

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

TIMEOUT = 20  # seconds, per HTTP request

# Everything a tag can point at: a multi-platform index or a single image,
# in either the OCI or the older Docker format. Listing all four stops the
# registry from converting (and re-digesting) the manifest for us.
INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
IMAGE_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
ACCEPT = ", ".join(INDEX_TYPES + IMAGE_TYPES)


class RegistryError(Exception):
    """A lookup failed; the message is short enough to show on the page."""


@dataclass(frozen=True)
class Ref:
    registry: str        # as people write it: docker.io, ghcr.io, lscr.io ...
    api_host: str        # where the API lives (Docker Hub's is different)
    repository: str      # e.g. library/postgres, linuxserver/jellyfin
    tag: str             # "latest" when the reference names no tag
    digest: str | None   # set when the reference is pinned with @sha256:...

    @property
    def key(self):
        """Identity used to look each distinct image up only once per check."""
        return (self.api_host, self.repository, self.tag)


def parse_ref(ref):
    """Split an image reference the way the docker CLI does."""
    name, _, digest = ref.partition("@")

    # A colon in the last path component is a tag; one earlier is a port
    # (registry.local:5000/app).
    tag = "latest"
    colon, slash = name.rfind(":"), name.rfind("/")
    if colon > slash:
        name, tag = name[:colon], name[colon + 1:]

    # The first component is a registry only if it looks like a host name.
    # Otherwise the image lives on Docker Hub ("grafana/grafana", "alpine").
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = "docker.io", name
    if registry in ("index.docker.io", "registry-1.docker.io"):
        registry = "docker.io"

    # Official images ("postgres") are "library/postgres" in the API.
    if registry == "docker.io" and "/" not in repository:
        repository = "library/" + repository

    api_host = "registry-1.docker.io" if registry == "docker.io" else registry
    return Ref(registry, api_host, repository, tag, digest or None)


# --- HTTP ------------------------------------------------------------------


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Hand 3xx responses back instead of following them.

    Blob downloads redirect to a CDN (Cloudflare for Docker Hub, a storage
    bucket for ghcr) that rejects the registry's bearer token, so redirects
    are followed by hand, without the Authorization header.
    """

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def _http(method, url, headers):
    """One request, with one retry for network errors and 5xx answers.

    Returns (status, headers, body) and never raises for an HTTP status, so
    callers can react to 401/404/429 themselves.
    """
    for attempt in (1, 2):
        request = urllib.request.Request(url, method=method, headers=headers)
        try:
            with _opener.open(request, timeout=TIMEOUT) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            status, response_headers, body = error.code, error.headers, error.read()
            if status >= 500 and attempt == 1:
                time.sleep(2)
                continue
            return status, response_headers, body
        except OSError as error:  # DNS failure, refused, reset, timeout
            if attempt == 2:
                host = urllib.parse.urlsplit(url).hostname
                reason = getattr(error, "reason", None) or error
                raise RegistryError(f"cannot reach {host}: {reason}") from None
            time.sleep(2)
    raise AssertionError("unreachable")


# --- Anonymous bearer tokens ----------------------------------------------
#
# A registry answers an anonymous request with 401 and a challenge such as
#   Bearer realm="https://auth.docker.io/token",service="registry.docker.io",
#          scope="repository:library/postgres:pull"
# Asking the realm for a token with that service and scope returns a
# short-lived pull token. Tokens are cached per repository until they expire.

_tokens = {}  # (api_host, repository) -> (token, expires_at)
_tokens_lock = threading.Lock()


def _cached_token(ref):
    with _tokens_lock:
        token, expires_at = _tokens.get((ref.api_host, ref.repository), (None, 0))
    return token if time.time() < expires_at else None


def _new_token(ref, challenge):
    scheme, _, params_text = challenge.partition(" ")
    params = dict(re.findall(r'(\w+)="([^"]*)"', params_text))
    if scheme.lower() != "bearer" or "realm" not in params:
        raise RegistryError("registry needs a login (anonymous access refused)")

    query = {"scope": params.get("scope") or f"repository:{ref.repository}:pull"}
    if "service" in params:
        query["service"] = params["service"]
    realm = params["realm"]
    url = realm + ("&" if "?" in realm else "?") + urllib.parse.urlencode(query)

    status, _, body = _http("GET", url, {})
    if status != 200:
        raise RegistryError(f"token request refused (HTTP {status})")
    data = json.loads(body)
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RegistryError("token response had no token")

    # Renew a little early so a token never expires between two requests.
    lifetime = int(data.get("expires_in") or 60)
    with _tokens_lock:
        _tokens[(ref.api_host, ref.repository)] = (token, time.time() + max(lifetime - 15, 5))
    return token


def _api(ref, method, path, accept=None):
    """Call /v2/<repository>/<path>, fetching a token if the registry asks."""
    url = f"https://{ref.api_host}/v2/{ref.repository}/{path}"
    headers = {"Accept": accept} if accept else {}
    token = _cached_token(ref)
    if token:
        headers["Authorization"] = "Bearer " + token

    status, response_headers, body = _http(method, url, headers)
    if status == 401:
        challenge = response_headers.get("WWW-Authenticate")
        if not challenge:
            raise RegistryError("HTTP 401 without an auth challenge")
        headers["Authorization"] = "Bearer " + _new_token(ref, challenge)
        status, response_headers, body = _http(method, url, headers)

    # Follow redirects (blob downloads) without the token; see _NoRedirects.
    hops = 0
    while status in (301, 302, 303, 307, 308):
        hops += 1
        location = response_headers.get("Location")
        if not location or hops > 5:
            raise RegistryError(f"bad redirect from {ref.api_host}")
        url = urllib.parse.urljoin(url, location)
        status, response_headers, body = _http(method, url, {})

    _raise_for_status(status, ref)
    return response_headers, body


def _raise_for_status(status, ref):
    if status == 200:
        return
    if status == 404:
        raise RegistryError(f"tag '{ref.tag}' not found on {ref.registry}")
    if status == 429:
        raise RegistryError(f"{ref.registry} rate limit reached, try again later")
    if status in (401, 403):
        raise RegistryError(f"{ref.registry} refused anonymous access (HTTP {status})")
    raise RegistryError(f"{ref.registry} answered HTTP {status}")


# --- Public functions -------------------------------------------------------


def remote_digest(ref):
    """Digest of the manifest the tag points to on the registry right now.

    HEAD is enough: the registry puts the digest in Docker-Content-Digest,
    and Docker Hub does not count HEAD requests against its pull limit.
    """
    headers, _ = _api(ref, "HEAD", f"manifests/{ref.tag}", ACCEPT)
    digest = headers.get("Docker-Content-Digest")
    if digest:
        return digest
    # A few registries leave the header off HEAD answers. The digest is the
    # sha256 of the manifest body, so fetch it and compute the same value.
    headers, body = _api(ref, "GET", f"manifests/{ref.tag}", ACCEPT)
    return headers.get("Docker-Content-Digest") or "sha256:" + hashlib.sha256(body).hexdigest()


def image_config(ref, digest, arch):
    """Labels and build date of image `digest` for linux/<arch>.

    Walks index -> this host's platform -> image manifest -> config blob.
    Returns {"labels": {...}, "created": "2026-09-08T02:30:49Z" or None}.
    """
    manifest = _manifest(ref, digest)
    if "manifests" in manifest:  # a multi-platform index
        manifest = _manifest(ref, _platform_digest(manifest, arch))
    config_digest = (manifest.get("config") or {}).get("digest")
    if not config_digest:
        raise RegistryError("manifest has no config blob")
    _, body = _api(ref, "GET", f"blobs/{config_digest}")
    config = json.loads(body)
    return {
        "labels": (config.get("config") or {}).get("Labels") or {},
        "created": config.get("created"),
    }


def _manifest(ref, digest):
    _, body = _api(ref, "GET", f"manifests/{digest}", ACCEPT)
    return json.loads(body)


def _platform_digest(index, arch):
    for entry in index.get("manifests", []):
        platform = entry.get("platform") or {}
        # Build attestations sit in the index as "unknown/unknown"; skip them.
        if platform.get("os") == "linux" and platform.get("architecture") == arch:
            return entry["digest"]
    raise RegistryError(f"no linux/{arch} image for this tag")
