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

A published Claude Artifact's CSP **blocks all cross-origin fetch/XHR**. The
page can never query Savant or the MLB API at runtime. Data is therefore
resolved at build time and inlined into the HTML:

```
build_cards.py  →  cards.json  →  bake.py  →  pitcher-card.html
   (fetch)          (payload)      (inline)      (deliverable)
```

`card_template.html` holds a `__PAYLOAD__` placeholder that `bake.py` replaces
with the JSON. Edit the template, never `pitcher-card.html` — it is generated
and gitignored.

On GitHub Pages the CSP restriction does not apply, so a runtime `fetch()` of
`cards.json` would work there. It is deliberately still baked so the same file
works in both places. Don't split them without a reason.

## Running it

```bash
./refresh.sh                                    # full rebuild, ~5 min
python3 build_cards.py --limit 60 --out cards.json && python3 bake.py
python3 -m http.server 8777                     # then open pitcher-card.html
```

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
