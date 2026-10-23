# watchlist-gaps

Find the movies on your Letterboxd watchlist that you can't stream on the
services you already have.

```
$ ./watchlist_gaps.py
70 of 203 watchlist movies aren't on your services (US):

  12 Years a Slave (2013)
  After Hours (1985)  [on: Howdy Amazon Channel]
  Boogie Nights (1997)  [on: Criterion Channel, Plex, Plex Channel]
  Harakiri (1962)  [on: Criterion Channel]
  The Truman Show (1998)  [on: Hoopla, Kanopy]
  ...
```

Movies that are on other subscription or free services list them in brackets,
so you can see where else to look. Movies with no brackets are rent or buy
only, or not streaming anywhere.

## Requirements

- Python 3.9 or newer. There's nothing to install beyond that.
- A free [TMDB](https://www.themoviedb.org) API key.
- A public Letterboxd watchlist, or a CSV export of it.

## Setup

1. Clone the repo:
   ```
   git clone https://github.com/RyanOC20/watchlist-gaps.git
   cd watchlist-gaps
   ```
2. Get a TMDB API key at <https://www.themoviedb.org/settings/api>. For a
   personal project, choose "Personal" and enter any application URL, such as
   `http://localhost`. Either the API Key or the API Read Access Token works.
3. Create your config from the template and fill it in:
   ```
   cp .env.example .env
   ```
   | Setting | Meaning |
   |---|---|
   | `TMDB_API_KEY` | Your TMDB key or read access token |
   | `LETTERBOXD_USERNAME` | Whose watchlist to read by default |
   | `WATCH_REGION` | Two-letter country code, such as `US` or `GB` |
   | `STREAMING_SERVICES` | Comma-separated TMDB provider names for your services |

   `.env` is git-ignored, so your key stays on your machine.
4. Check your service names against TMDB:
   ```
   ./watchlist_gaps.py --list-providers
   ```
   Every provider that counts as one of your services is marked with `*`. If
   one of yours isn't marked, copy its exact name from the list into
   `STREAMING_SERVICES`. The script also warns you about any name that matches
   nothing.

### Service names

Each name in `STREAMING_SERVICES` also covers its variants, so you only need
the base name:

| You write | Also matches |
|---|---|
| `Netflix` | Netflix Standard with Ads, Netflix Kids |
| `Amazon Prime Video` | Amazon Prime Video with Ads |
| `Peacock Premium` | Peacock Premium Plus |
| `HBO Max` | HBO Max Amazon Channel |
| `Tubi` | Tubi TV |

Free services count too, such as `Tubi`, `Fandango at Home Free` or
`YouTube Free`.

## Usage

```
./watchlist_gaps.py                     # your watchlist (LETTERBOXD_USERNAME)
./watchlist_gaps.py some_user           # any public watchlist
./watchlist_gaps.py ~/Downloads/watchlist.csv
./watchlist_gaps.py --csv > gaps.csv    # spreadsheet output
./watchlist_gaps.py --list-providers    # every provider in your region
```

If your watchlist is private, export it from Letterboxd under Settings > Data >
Export Your Data and pass the `watchlist.csv` from the zip.

## How it works

1. Reads your watchlist from your public Letterboxd pages or a CSV export.
2. Gets each film's exact TMDB ID from its Letterboxd page, so there's no
   guessing by title. IDs are cached in `~/.cache/watchlist-gaps/`, which
   makes later runs faster.
3. Asks TMDB where each movie streams in your region. A movie counts as
   available when it's included with a subscription or free with ads. Rentals
   and purchases don't count.

TV shows on the watchlist are skipped.

## Data freshness

TMDB's streaming data comes from [JustWatch](https://www.justwatch.com) and
refreshes about daily. A movie that just moved services can show its old
service for a day or two.

## Attribution

This product uses the TMDB API but is not endorsed or certified by TMDB.
Streaming availability data is provided by JustWatch.
