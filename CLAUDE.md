# Opposing Starter Card

A live-game advance scouting card for MLB starting pitchers, modeled on a
college advance report. Arsenal shape, count-state usage, running game, and
location / Whiff+ / Damage+ heat maps, all from public Statcast data.

## Ground rules

**Never commit anything from `original-reports/`, and never commit a PDF.**
That folder holds source scouting reports belonging to another organization.
They are reference material only. This repo is public. Both are in
`.gitignore`; do not remove those lines or use `git add -f` on them.

## Architecture — read this before changing how data flows

Data is resolved at build time — the page never queries Savant or the MLB API
at runtime — and is then **split** into a light index and per-pitcher location
files:

```
build_cards.py  →  cards.json  →  bake.py  →  dist/index.html      (index inlined)
   (fetch)          (payload)      (split)     dist/p/<id>.json    (locations, fetched)
```

`card_template.html` holds a `__PAYLOAD__` placeholder that `bake.py` fills
with everything except the packed pitch tables. Edit the template, never
`dist/index.html` — it is generated and gitignored.

**Why split.** At ~500 pitchers the packed location tables are several MB.
Inlining them all would make the page unusable on a phone. One pitcher's
locations ship in the page (`DATA.defaultId`) so the first paint is complete
without a fetch; every other pitcher is fetched from `p/<id>.json` on select
and cached in `PITCH_CACHE`. `select()` is async and guards against a stale
response overwriting a newer selection via `loadSeq`.

**Consequence:** the page no longer works as a single self-contained file for
the heat maps — it needs `p/` served alongside it. That is why the local dev
server points at `dist/`, not the project root. Everything except the heat
maps still works without the fetch.

## Running it

```bash
./refresh.sh                                     # full rebuild, ~12 min
python3 build_cards.py --min-ip 20 --out cards.json && python3 bake.py
python3 -m http.server 8777 --directory dist     # serve dist/, not the repo root
```

**Roster:** every pitcher with at least 20 IP — starters *and* relievers,
around 500 of them — plus any announced starter below that cut. It used to be
"top 60 by games started", which silently dropped real starters: Payton Tolle
had 137 IP and 24 starts and ranked 71st. Don't reintroduce a rank cap; use an
innings floor so the criterion is about workload, not about list length.

`refresh.sh` builds to a temp file and only swaps it in after a sanity gate, so
a failed fetch keeps the last good card. It resolves its own directory, so it
works from any clone. Logs to `refresh.log`.

CI (`.github/workflows/refresh.yml`) runs the same build daily at 12:43 UTC and
deploys to Pages, so the public site updates with no device involved. Use the
Actions tab → "Run workflow" to rebuild on demand.

Three things exist purely to keep that true unattended:

- **Season is derived, never hardcoded.** `default_season()` returns the
  current year from April onward and the previous year before that, so nothing
  breaks at the calendar rollover. Don't reintroduce a literal year.
- **A keepalive commit** writes `.github/last-build` when the stamp is over 14
  days old. GitHub disables scheduled workflows after 60 days of repo
  inactivity, which would silently stop the refresh. It is
  `continue-on-error` — it must never block a deploy.
- **A freshness chip** in the header reads the payload's build date and turns
  amber past 2 days, red past 6. Pages keeps serving the last good deploy when
  a build fails, so without this a stale card looks identical to a fresh one.

## The Flight tab

Replays the real tracked trajectory of a representative pitch. Statcast
publishes a 9-parameter constant-acceleration model per pitch, stated at
y = 50 ft, so position is closed-form — `p(t) = p0 + v0·t + ½a·t²`. This is the
actual flight of an actual pitch, not a reconstruction. 99.9% of pitches carry
all nine parameters.

`build_cards.py` stores one representative pitch per type in
`pitcher.flight[type]` as `[relX, relZ, vx0, vy0, vz0, ax, ay, az, ext,
plateX, plateZ]` (~41 KB for all 86 pitchers). It picks the *actual pitch*
whose shape sits closest to the type's median, because taking the median of
each parameter independently can describe a trajectory nobody threw.

Things worth knowing before you touch the camera code:

- **True catcher eye height does not work.** At ~3 ft behind the plate the
  release point projects *below* the top of the strike zone — 53 ft of flight
  collapses into a couple of degrees. That is honestly what a hitter sees and
  is exactly why hitting is hard, but it is a useless diagram. Both cameras sit
  behind the plate and elevated.
- **Focal length is fitted, not hand-tuned**, and the principal point is
  shifted so the bounding box centres. Hand-picked values put the oblique
  view's strike zone 600 px off a 1280 px canvas. `fitView()` handles any
  pitcher and any pitch type.
- The fit deliberately **excludes home plate**. It sits ~3 ft below the camera
  at close range, so including it dominates the fit and shrinks the zone;
  clipping it at the bottom edge reads naturally.
- Release time is negative. Parameters are stated at y = 50, but release is at
  y = 60.5 − extension ≈ 54 ft, which is *behind* the reference point. Same
  quadratic root, no special case. `60.5 − extension` matches Savant's own
  `release_pos_y` column exactly (median difference 0.0000 ft over 1,134
  pitches), so extension is honoured per pitcher.
- **The camera fit must be SHARED across pitchers, not per pitcher.** Fitting
  each arm's own trajectories to fill the frame cancelled out release-distance
  differences: Valdez (5.6 ft extension) and Gilbert (7.6 ft) projected their
  release points within one pixel of each other despite releasing two feet
  apart. `sharedView()` fits once over every trajectory in the build.
- Extension is nearly invisible from behind the plate — two feet out of a 62 ft
  depth is a 3% change in apparent ball size. The side-on camera is what makes
  it readable, and it sits 300 ft out so it is effectively a parallel
  projection: at 32 ft, a pitcher's arm-side release position shifted screen-x
  more than his extension did. Reading release distance back out of pixel
  position now recovers the true value to within 0.21 ft.
- The side view stretches vertically (capped ×4, stated on the stage label)
  because the flight spans ~52 ft across and under 5 ft vertically.

### At-bat simulator ("Face him")

Every step is driven by this pitcher's real data rather than a recorded at-bat,
so the user's swing/take decision is genuinely theirs:

| step | source |
|---|---|
| which pitch | `usage[hand][countState]` — his real mix in that count |
| where | a real pitch of that type he threw in that count, from the packed table |
| ball / strike | that pitch's actual `plate_x`/`plate_z` vs the rulebook zone |
| whiff | `outcomes[type][bin][0]` — his whiff-per-swing in that attack zone |
| foul | `outcomes[type][bin][1]` — foul-per-contact |
| hit / homer | `outcomes[type][bin][2..3]` — per ball in play |

Four attack zones (`ZONE_BINS`: heart / edge / shadow / chase) keep per-cell
samples usable. Per-pitcher rates are **shrunk toward the pooled league rate**
for the same zone (`shrink()`, k=45), so a six-swing cell cannot produce a 100%
whiff rate. League rates ship as `DATA.leagueOutcomes` and are the fallback
when a pitcher has no cell.

Sanity check if you change the model — the league rates should stay monotonic:
heart 14% whiff/swing and 34% hit/BIP, chase 57% whiff and 27% hit. If chasing
stops being punished, something is wrong.

Deliberately NOT replaying recorded at-bats: that would pin the outcome to what
the real hitter did, so the user's decision would not matter.

## Data sources

Both are free and need no auth or API key.

| Need | Source |
|---|---|
| Pitch-by-pitch | `baseballsavant.mlb.com/statcast_search/csv` |
| Roster, bio, season line, SB/CS/pickoffs | `statsapi.mlb.com/api/v1` |
| Tonight's starters | `/api/v1/schedule?...&hydrate=probablePitcher` |

FanGraphs is Cloudflare-protected and **not needed** — Savant plus StatsAPI
covers everything here. The only real gaps are Stuff+/Location+ (proprietary)
and pitcher time-to-plate (not published anywhere; the card says `n/a` rather
than inventing it).

## Things that will bite you

- **`pfx_x` / `pfx_z` are in FEET.** Multiply by 12 for inches.
- **Horizontal break sign.** `pfx_x` is catcher's-view. The card reports HB as
  arm-side-positive, so it is flipped by `p_throws` (`armsign` in
  `build_cards.py`). Don't "fix" the sign without checking a known sinker.
- **VAA is not a Savant column.** It is computed from `vy0/vz0/ay/az` solved to
  the front of the plate (y = 17/12 ft). Sanity check any change: four-seamers
  land around −4.5 to −5.1°, curveballs −8.2 to −9.5°.
- **`plate_x` negative = catcher's left = where a RHH stands.** Heat maps are
  drawn catcher's-view, so a RHH silhouette belongs on the left.
- **d3 `selectAll("div")` is a DESCENDANT selector.** `#run-grid` rows each
  contain a nested `.sub` div, so a naive join matched 12 elements against 6
  data rows and threw `HierarchyRequestError` — but only on the *second*
  render, meaning switching pitchers silently updated the name and nothing
  else. All joins use `:scope > *`. Keep it that way.
- **macOS has no `flock`.** `refresh.sh` uses an atomic `mkdir` lock.
- Pitchers with fewer than 300 located pitches are skipped — too thin for
  honest heat maps.
- **The baseline pool is subsampled** (`BASE_SAMPLE`, 130k per batter hand).
  The grid is O(cells x pitches) in pure Python; with ~500 pitchers the pool
  passes a million pitches and the build would take minutes for no statistical
  gain.
- Relievers have `role: "RP"` and GS of 0. Anything dividing by games started
  needs a guard — the season line uses appearances (`Pit/App`) and swaps GS for
  SV on relievers.

## Design conventions

- Sequential blue ramp for location density (magnitude). Diverging blue↔red
  with a neutral midpoint for Whiff+/Damage+ (polarity). Never a rainbow.
- Pitch-type colors are assigned by usage rank from a fixed 8-slot order that
  is CVD-validated for adjacent pairs. Don't reorder or cycle them.
- Whiff+/Damage+ index against a **location-matched baseline pooled from every
  pitch in the build** (~200k). It is not the pitcher's own rate (circular) and
  is not labeled "league average" — it's a starter population. Keep that
  wording honest.
- The "Read" column is derived from measured thresholds, not scouted. It is
  labeled as such. Don't let it drift into claims the numbers don't support.
