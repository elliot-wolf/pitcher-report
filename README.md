# Opposing Starter Card

A live-game advance scouting card for MLB starting pitchers — the kind of
one-pager a college program builds for an opposing arm, rebuilt from public
Statcast data and refreshed every morning.

**[View the card →](https://elliot-wolf.github.io/pitcher-report/)**

Search any of ~85 starters. For each one:

- **Arsenal & shape** — usage, velocity, induced vertical break, horizontal
  break (arm-side positive), spin, and vertical approach angle, plus in-zone
  rate split by batter handedness and whiff / chase / xwOBA.
- **Usage by count state** — what he throws 0-0, ahead, even, behind, and with
  two strikes, separately vs RHH and LHH.
- **Running game** — stolen bases and caught stealing against, pickoffs, wild
  pitches, and how much of his season comes from the stretch.
- **Plan of attack** — location density for fastballs, breaking balls and
  offspeed, plus Whiff+ and Damage+ heat maps, all filterable by count state.
- **Flight** — a second tab that replays the real tracked trajectory of each
  pitch from behind the plate, in slow motion, with the strike zone for scale.
  It uses the nine-parameter trajectory model Statcast publishes per pitch, so
  the path on screen is the path the ball actually took.

The roster is the 60 starters with the most starts, plus every announced
probable starter for the next two days — so whoever is going tonight is in
there, even a spot starter or an opener.

## How it works

```
build_cards.py  →  cards.json  →  bake.py  →  pitcher-card.html
```

`build_cards.py` pulls pitch-by-pitch data from Baseball Savant and season
lines from the MLB Stats API, computes the arsenal aggregates, and packs each
pitcher's location table into a compact string (6 characters per pitch, so ~85
pitchers fit in about 1.5 MB). `bake.py` inlines that payload into the page.

A GitHub Actions workflow runs the whole thing daily at 12:43 UTC and deploys
to Pages, so the published card is current without anyone's laptop being on —
the build runs on GitHub's servers and the site is served from their CDN.

The header shows how old the data is. If a build fails, Pages keeps serving the
last good deploy, so the card says so plainly rather than passing stale numbers
off as current.

## Running it locally

```bash
git clone https://github.com/elliot-wolf/pitcher-report.git
cd pitcher-report
./refresh.sh                 # ~5 minutes; writes cards.json + pitcher-card.html
python3 -m http.server 8777  # then open http://localhost:8777/pitcher-card.html
```

No API keys, no dependencies beyond the Python standard library.

## Data sources

| Need | Source |
|---|---|
| Pitch-by-pitch Statcast | `baseballsavant.mlb.com/statcast_search/csv` |
| Roster, bio, season line, running game | `statsapi.mlb.com/api/v1` |
| Tonight's probable starters | `/api/v1/schedule?hydrate=probablePitcher` |

Both are free and unauthenticated. Vertical approach angle is not published by
Savant — it's computed here from the trajectory coefficients at the front of
the plate.

Whiff+ and Damage+ are indexed against a location-matched baseline pooled from
every pitch in the build (~200,000), so "red" means hitters did better there
than the starter population does in the same spot.

## Notes and limits

- Pitcher time-to-plate isn't published anywhere public. That tile reads `n/a`
  rather than guessing.
- Stuff+ / Location+ are FanGraphs proprietary and aren't reproduced here.
- The "Read" column is generated from measured thresholds, not scouted by a
  human, and is labeled that way on the card.
- Pitchers with fewer than 300 located pitches are skipped.

See [CLAUDE.md](CLAUDE.md) for architecture notes and the gotchas worth knowing
before changing the pipeline.

## Disclaimer

This is an unofficial, independent project. It is not affiliated with, endorsed
by, or sponsored by Major League Baseball, MLB Advanced Media, or any MLB club.
All trademarks are the property of their respective owners.

Data is retrieved from publicly accessible Baseball Savant and MLB Stats API
endpoints and is displayed here in derived form for analysis and educational
purposes.
