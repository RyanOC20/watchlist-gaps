#!/usr/bin/env python3
"""List Letterboxd watchlist movies that aren't included on your streaming services.

Usage:
    cp .env.example .env               # then fill in your details
    ./watchlist_gaps.py                # LETTERBOXD_USERNAME's public watchlist
    ./watchlist_gaps.py some_user      # another public watchlist
    ./watchlist_gaps.py watchlist.csv  # a Letterboxd export (Settings > Data)

TV shows are skipped. Availability comes from TMDB's watch provider data
(sourced from JustWatch).
"Included" means a flat rate subscription or free/ad supported viewing.
Rentals and purchases don't count.

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

INCLUDED_TYPES = ("flatrate", "free", "ads")

LETTERBOXD = "https://letterboxd.com"
TMDB_API = "https://api.themoviedb.org/3"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
CACHE_FILE = Path.home() / ".cache" / "watchlist-gaps" / "tmdb_ids.json"


def ssl_context() -> ssl.SSLContext:
    """Default TLS context, falling back to the macOS system CA bundle.

    python.org builds of Python on macOS ship without CA certificates until
    "Install Certificates.command" is run, which makes every HTTPS call fail.
    """
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats()["x509_ca"] and Path("/etc/ssl/cert.pem").exists():
        ctx.load_verify_locations("/etc/ssl/cert.pem")
    return ctx


SSL_CONTEXT = ssl_context()


# Configuration

def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file. Real environment variables win."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.removeprefix("export ").split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)


@dataclass
class Config:
    tmdb_key: str
    username: str
    region: str
    services: list[str]  # TMDB provider names; each also covers its variants

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv(Path(__file__).resolve().parent / ".env")
        env = lambda name, default="": os.environ.get(name, default).strip()
        return cls(
            tmdb_key=env("TMDB_API_KEY"),
            username=env("LETTERBOXD_USERNAME"),
            region=env("WATCH_REGION", "US").upper(),
            services=[s.strip() for s in env("STREAMING_SERVICES").split(",") if s.strip()],
        )

    def service_for(self, provider: str) -> str | None:
        """The configured service a TMDB provider name belongs to, if any.

        "Netflix" covers "Netflix Standard with Ads", "Peacock Premium" covers
        "Peacock Premium Plus", and so on: a service matches any provider name
        that starts with it as whole words.
        """
        p = provider.lower()
        for service in self.services:
            s = service.lower()
            if p == s or p.startswith(s + " "):
                return service
        return None


@dataclass
class Film:
    name: str  # "Title (Year)"
    link: str  # Letterboxd film URL
    tmdb_type: str | None = None  # "movie" or "tv"
    tmdb_id: str | None = None
    mine: list[str] = field(default_factory=list)  # my services carrying it
    others: list[str] = field(default_factory=list)  # other services carrying it
    error: str | None = None


def fetch(url: str, headers: dict | None = None, retries: int = 4) -> tuple[str, str]:
    """GET a URL, returning (final_url, body). Retries on 429 and 5xx."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
                return resp.geturl(), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if attempt < retries and (e.code == 429 or e.code >= 500):
                wait = float(e.headers.get("Retry-After") or 2 ** attempt)
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise
    raise AssertionError("unreachable")


# Letterboxd

def watchlist_from_username(username: str) -> list[Film]:
    films: list[Film] = []
    page = 1
    while True:
        url = f"{LETTERBOXD}/{urllib.parse.quote(username)}/watchlist/page/{page}/"
        try:
            _, body = fetch(url)
        except urllib.error.HTTPError as e:
            if e.code == 404 and page == 1:
                sys.exit(f"No public Letterboxd watchlist found for '{username}'.")
            raise
        entries = re.findall(r"<div[^>]*\bdata-item-slug=\"[^\"]*\"[^>]*>", body)
        if not entries:
            break
        for tag in entries:
            name = re.search(r'data-item-name="([^"]*)"', tag)
            link = re.search(r'data-item-link="([^"]*)"', tag)
            if name and link:
                films.append(Film(html.unescape(name.group(1)), LETTERBOXD + link.group(1)))
        if f"/watchlist/page/{page + 1}/" not in body:
            break
        page += 1
    return films


def watchlist_from_csv(path: Path) -> list[Film]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if rows and "Letterboxd URI" not in rows[0]:
        sys.exit(f"{path} doesn't look like a Letterboxd watchlist export.")
    return [
        Film(f"{r['Name']} ({r['Year']})" if r.get("Year") else r["Name"], r["Letterboxd URI"])
        for r in rows
    ]


def resolve_tmdb_id(film: Film, cache: dict) -> None:
    """Read the TMDB id from the Letterboxd film page (cached, since it never changes)."""
    if film.link in cache:
        film.tmdb_type, film.tmdb_id = cache[film.link]
        return
    try:
        final_url, body = fetch(film.link)
    except urllib.error.URLError as e:
        film.error = f"couldn't load Letterboxd page ({e})"
        return
    # The TMDB link is the reliable source: for TV, data-tmdb-id is empty and
    # data-tmdb-type wrongly says "movie", but the link points at /tv/<id>.
    m = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", body) or re.search(
        r'data-tmdb-type="(movie|tv)"[^>]*?data-tmdb-id="(\d+)"', body
    )
    if not m:
        film.error = "Letterboxd has no TMDB link for this title"
        return
    film.tmdb_type, film.tmdb_id = m.group(1), m.group(2)
    cache[film.link] = cache[final_url] = [film.tmdb_type, film.tmdb_id]


# TMDB

def tmdb_get(path: str, key: str, **params) -> dict:
    headers = {"Accept": "application/json"}
    if key.startswith("eyJ"):  # v4 read access token
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    url = f"{TMDB_API}{path}?{urllib.parse.urlencode(params)}"
    try:
        return json.loads(fetch(url, headers)[1])
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("TMDB rejected the API key. Check TMDB_API_KEY.")
        raise


def check_availability(film: Film, cfg: Config) -> None:
    if not film.tmdb_id:
        return
    try:
        data = tmdb_get(f"/{film.tmdb_type}/{film.tmdb_id}/watch/providers", cfg.tmdb_key)
    except urllib.error.URLError as e:
        film.error = f"TMDB lookup failed ({e})"
        return
    region = data.get("results", {}).get(cfg.region, {})
    providers = {p["provider_name"] for t in INCLUDED_TYPES for p in region.get(t, [])}
    mine, others = set(), set()
    for provider in providers:
        service = cfg.service_for(provider)
        (mine if service else others).add(service or provider)
    film.mine, film.others = sorted(mine), sorted(others)


def region_providers(cfg: Config) -> list[str]:
    """Every movie provider name TMDB knows in the configured region."""
    data = tmdb_get("/watch/providers/movie", cfg.tmdb_key, watch_region=cfg.region)
    return sorted({p["provider_name"] for p in data.get("results", [])}, key=str.lower)


def list_providers(cfg: Config) -> None:
    for name in region_providers(cfg):
        service = cfg.service_for(name)
        print(f"{'*' if service else ' '} {name}" + (f"  -> {service}" if service else ""))


def warn_unmatched_services(cfg: Config) -> None:
    """Catch typos in STREAMING_SERVICES, which would silently hide matches."""
    names = region_providers(cfg)
    for service in cfg.services:
        if not any(cfg.service_for(n) == service for n in names):
            print(f"Warning: no TMDB provider in {cfg.region} matches '{service}' from "
                  "STREAMING_SERVICES. Run with --list-providers to see valid names.",
                  file=sys.stderr)


# Main

def load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=0, sort_keys=True))


def run_parallel(films: list[Film], workers: int, label: str, task) -> None:
    """Run task on every film, showing progress on stderr when interactive."""
    with ThreadPoolExecutor(workers) as pool:
        for done, _ in enumerate(pool.map(task, films), 1):
            if sys.stderr.isatty():
                end = "" if done < len(films) else "\n"
                print(f"\r{label}: {done}/{len(films)}", end=end, file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", nargs="?",
                    help="Letterboxd username or path to watchlist.csv "
                         "(default: LETTERBOXD_USERNAME from .env)")
    ap.add_argument("--csv", action="store_true", help="output CSV instead of text")
    ap.add_argument("--list-providers", action="store_true",
                    help="print every TMDB provider name in your region, marking yours")
    args = ap.parse_args()

    cfg = Config.from_env()
    if not cfg.tmdb_key:
        sys.exit("Set TMDB_API_KEY in .env (free at https://www.themoviedb.org/settings/api).")
    if args.list_providers:
        list_providers(cfg)
        return
    if not cfg.services:
        sys.exit("Set STREAMING_SERVICES in .env. Run with --list-providers to see names.")
    source = args.source or cfg.username
    if not source:
        ap.error("give a Letterboxd username or watchlist.csv path, or set LETTERBOXD_USERNAME")

    warn_unmatched_services(cfg)
    path = Path(source).expanduser()
    films = watchlist_from_csv(path) if path.suffix.lower() == ".csv" else watchlist_from_username(source)
    if not films:
        sys.exit("The watchlist is empty.")

    cache = load_cache()
    # Letterboxd gets few workers to stay polite; TMDB allows plenty.
    run_parallel(films, 4, "Reading Letterboxd", lambda f: resolve_tmdb_id(f, cache))
    save_cache(cache)
    films = [f for f in films if f.tmdb_type != "tv"]  # movies only
    run_parallel(films, 10, "Checking TMDB", lambda f: check_availability(f, cfg))

    missing = sorted((f for f in films if f.tmdb_id and not f.mine and not f.error), key=lambda f: f.name.lower())
    unknown = [f for f in films if f.error]

    if args.csv:
        w = csv.writer(sys.stdout)
        w.writerow(["Title", "Letterboxd", "Other services", "Note"])
        for f in missing + unknown:
            w.writerow([f.name, f.link, "; ".join(f.others), f.error or ""])
        return

    print(f"{len(missing)} of {len(films)} watchlist movies aren't on your services ({cfg.region}):\n")
    for f in missing:
        print(f"  {f.name}" + (f"  [on: {', '.join(f.others)}]" if f.others else ""))
    if unknown:
        print(f"\nCouldn't check {len(unknown)}:")
        for f in unknown:
            print(f"  {f.name}: {f.error}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
