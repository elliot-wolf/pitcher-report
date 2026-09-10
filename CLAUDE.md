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

**Cache safety:** per-pitcher files are fetched as `p/<id>.json?b=<buildId>`,
where `buildId` is stamped by `bake.py` on every bake. Without it a returning
viewer decodes last build's bytes with this build's decoder — which is exactly
what happened when the packed encoding changed, producing a plausible-looking
but wrong count distribution. Never drop the stamp.

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
  quadratic root, no special case.
- **Two reference frames, do not mix them.** `vx0/vy0/vz0` and `ax/ay/az` are
  stated at y = 50 ft; `release_pos_x` / `release_pos_z` are measured at the
  release point (~53.7 ft). Feeding the release position in as the y=50 origin
  slides the entire path — it put the release 4 inches off and the plate
  crossing 4.4 inches off. `originAt50()` solves back for the true origin;
  after it, release error is 0.05 inches worst case across every pitcher.
- Savant's `plate_x` / `plate_z` empirically correspond to about y = 0.5–0.7 ft
  (mid-plate), not the front edge at y = 1.417. The drawn path ends at the
  front edge by convention, leaving a sub-inch difference. Ball/strike calls in
  the simulator read the stored values directly, so they are unaffected. `60.5 − extension` matches Savant's own
  `release_pos_y` column exactly (median difference 0.0000 ft over 1,134
  pitches), so extension is honoured per pitcher.
- **The pitcher is an arm, not a figure.** At 55 ft a stick body renders about
  40px tall, so the limbs read as clutter and the arm — the only part carrying
  information — is lost in them. `armVector()` draws just the throwing arm into
  the measured release point at his real arm angle. Stature lives on the
  Movement Profile card, drawn large enough to mean something.
- **Frames differ, do not reuse `armSideSign()` in the 3D scene.** The scene
  uses the plate_x frame, where third base is NEGATIVE x, so a righty's arm
  side is -1. `armSideSign()` is for the movement chart's pitcher's-view frame,
  where it is +1. Verified: the drawn arm angle matches `armAngle` exactly
  (Skenes 25.7 to 3B, Harrison 31.6 to 1B).
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
- The tab is **Showdown**: one mode, real time, behind the plate. The replay
  button walks the *previous* pitch back at quarter speed (0.39 s becomes
  1.55 s) — `FL.lastT`/`FL.lastType` survive the stage clearing for the next
  guess, so it still works once the board is empty. Cleared on a new batter. There is no
  Explore mode and no pitch picker — the stage draws the field, mound, pitcher,
  plate and zone, and a pitch only flies once the batter has guessed.
- **Batter handedness is chosen, not random.** It has to be: Skenes at 0-0
  throws 30% sweepers and 27% sinkers to righties against 18% splitters and
  14% changeups to lefties. Switching hands starts a new batter.
- The guess bar lives under the stage, not beside it. The scene is tall and
  narrow, so `.flight-wrap` is capped at 900px and centred; a full-width stage
  wasted its width and pushed the guess bar off screen.
- The faint field (foul lines, diamond, infield arc, fence) is scenery and is
  deliberately **not** in the camera fit — its far edges run off frame the way
  they would from the box. Home plate *is* in the fit; leaving it out clipped
  it off the bottom.
- Both cameras were true to proportion. The side view used to stretch
  vertically ×4 so the arc read as a curve, but once the mound and pitcher were
  drawn that made a 6'6" arm look 26 ft tall. `fitView`'s `maxStretch` argument
  still exists if you need it — the label states any stretch above 1.05.
- **The mound and the figure are real geometry, not decoration.** The mound is
  regulation (18 ft across, centred 59 ft from the plate, 10 in at the table),
  modelled radially so it reads from any camera and so the pitcher's feet stand
  on the same `moundZ()`. The figure's hand sits on the *measured release point
  of the pitch being shown*, so it shifts with the arsenal; everything else is
  proportioned off his listed height. A 6'6" pitcher draws 6.5 ft tall. Both
  are included in the shared-view fit or they clip in the side view.

### Showdown (the at-bat simulator)

Every step is driven by this pitcher's real data rather than a recorded at-bat,
so the user's swing/take decision is genuinely theirs:

| step | source |
|---|---|
| which pitch + where | **one** real pitch sampled from the packed table at the **exact** count — type and location come together, so the joint distribution is his |
| ball / strike | that pitch's actual `plate_x`/`plate_z` vs the rulebook zone |
| whiff | `outcomes[type][bin][0]` — his whiff-per-swing in that attack zone |
| foul | `outcomes[type][bin][1]` — foul-per-contact |
| 1B / 2B / 3B / HR | `outcomes[type][bin][2..5]` — each per ball in play |

Contact resolves with a **single cumulative roll** over HR → 3B → 2B → 1B →
out, so the branches cannot drift out of sync. Each hit type is shrunk toward
its own league rate; triples are ~0.4% of balls in play, so a pitcher's own
triple rate is essentially noise and shrinkage correctly pulls it to league.

Four attack zones (`ZONE_BINS`: heart / edge / shadow / chase) keep per-cell
samples usable. Per-pitcher rates are **shrunk toward the pooled league rate**
for the same zone (`shrink()`, k=45), so a six-swing cell cannot produce a 100%
whiff rate. League rates ship as `DATA.leagueOutcomes` and are the fallback
when a pitcher has no cell.

Sanity checks if you change the model. The league rates should stay monotonic
in the right places and flat in the others:

| zone | whiff/swing | 1B | 2B | 3B | HR |
|---|---|---|---|---|---|
| heart | 14% | 20.6% | 6.9% | 0.62% | 5.8% |
| chase | 57% | 21.9% | 4.0% | 0.43% | 0.9% |

Note what moves and what doesn't. Extra-base hits collapse away from the heart
(HR 6.4x, doubles ~2x) while **singles stay flat** — the drop in total hit rate
is entirely power, not contact quality. Triples are flat and rare everywhere;
they are a function of the park and the runner, not the pitch. If singles start
tracking zone strongly, or triples do, the binning is wrong.

Deliberately NOT replaying recorded at-bats: that would pin the outcome to what
the real hitter did, so the user's decision would not matter.

**The animation must fly the sampled pitch.** `aimedTrajectory()` keeps his real
release point and real acceleration and solves the velocity so the path ends
exactly on the sampled location (0 inches of error). Before it, the sim animated
the one representative trajectory while judging a different, varying location —
you could watch a pitch down the middle and be told it was ball four.

**Exact count, not count state.** The packed table carries balls and strikes,
not just the coarse state, because 3-0 and 1-0 are both "behind" and that is
exactly where it matters. Skenes in the zone by count: 0-0 49%, 2-0 59%,
3-0 60%, 1-2 36%, 0-2 26%. Grooving when behind and expanding when ahead is
what makes the walk rate come out right.

**Walks are emergent, never dialled in.** They come from taking pitches he
genuinely missed the zone with. Validated by simulating a hitter with each
pitcher's own chase rate — simulated BB% lands within a point of real:
Valdez 8.8 vs 8.4, Gore 10.1 vs 9.2, Skenes 8.8 vs 7.0, Wheeler 6.9 vs 7.9.
A flat league chase rate instead of the pitcher's own put Skenes at 13.1%,
which is a hitter-model artefact, not a location-model error. If you ever want
to "fix" the walk rate with a knob, don't — check the chase assumption first.


## Movement profile

Induced break, drawn from the **pitcher's** point of view: +x moves toward
third base, +y is induced rise. `pfx_x` is catcher's-view so it flips
(`chartX = hb * (throws === "R" ? 1 : -1)`); `pfx_z` is already gravity-removed,
so a "rising" fastball is one that falls less than a spinless ball.

- Individual pitches are a **stratified sample** (~115 per pitcher, min 4 per
  type so a 3%-usage pitch still appears), stored in `p/<id>.json` alongside
  the locations. Per-type means and the league ellipses use **all** pitches, so
  nothing quantitative rests on the sample — only the cloud shape.
- Per-type means render straight from the index, so the chart is never empty
  while the sample fetches.
- League ellipses are ±1 SD per pitch type **and throwing hand**. Sanity check:
  R|FF mean (+7.8, +15.6) and L|FF (-7.9, +15.6) — exact mirrors.
- `arm_angle` is a real Savant column (82-91% populated; the median is stored).
  Validation: the only negative values in the whole build are Tyler Rogers
  (-60.8, MLB's submariner), Tim Hill, Ryan Thompson and Hoby Milner. If more
  than a handful go negative, something is wrong.
- **Mean dots are direct-labelled on purpose.** The palette's slot order is
  validated for *adjacent* pairs (stacked bars); a scatter shows all pairs at
  once, and a six-pitch arsenal lands on both greens (slots 3 and 6). Identity
  must not be colour alone here.
- An `<svg>` flex item ignores `width:100%` and falls back to its 300px
  intrinsic size — `.move-plot` is block with auto margins for that reason.


## Game log

Two charts at the foot of the Report page, both from `pitcher.games` in the
per-pitcher file: the primary fastball's average velocity per outing, and every
pitch's share of each outing.

- **Primary fastball** = most-thrown of FF/SI. A cutter qualifies only when a
  pitcher has neither: it is classified as a fastball but behaves like a
  breaking ball, and an 88 mph cutter beside a 96 mph four-seam reads as a
  velocity collapse. The heading names the pitch it chose.
- A game needs 5+ pitches to count as an outing, and the velocity point is null
  unless the fastball went 3+ times that game — the line breaks rather than
  plotting a one-pitch average.
- Every line is direct-labelled at its right end. A multi-series line chart puts
  all colour pairs on screen at once, same reason as the movement scatter.

## Regular season only

`build_pitcher` filters `game_type == "R"`. Savant returns **spring training
under the same season parameter** (`game_type "S"`), and it was silently folded
into every aggregate — about 7% of a starter's pitches, thrown while arms are
building up. It also put the pitch count out of step with the season line, which
comes from StatsAPI and is regular season by definition. Postseason (F/D/L/W) is
excluded for the same consistency reason; including it would need the season
line handled to match.


## Deploying: one at a time

**Never trigger two deploys close together.** The workflow's
`concurrency: group: pages` serializes the *jobs*, but the GitHub Pages API
keeps its own deployment lock that outlives the job. Firing a second run while
the first is deploying gets:

    Deployment request failed for <sha> due to in progress deployment.
    Please cancel <other sha> first or wait for it to complete.

The build succeeds and only the deploy step fails, so the site silently keeps
serving the previous tree. It also sends a workflow-failure email, which looks
identical to a genuinely broken daily build.

That lock can wedge: on 2026-09-10 it rejected three consecutive runs while the
blocking deployment simultaneously reported `success` in the environment API and
"as it's finished" from the cancel endpoint. Waiting it out or letting the daily
cron retry is the fix — do not hammer it, each attempt costs a ~6 minute build
and another failure email.

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
- **Use the measured strike zone, not the rulebook approximation.** Statcast's
  own `sz_top`/`sz_bot`, pooled over 35,758 pitches, median **3.214 / 1.622**.
  The widely quoted 1.59-3.41 zone is 2.4 inches too tall at the top; it
  mis-draws the zone and shifts the in-zone rate by about 4 points (47.0% vs
  42.8% against each batter's real zone), which every ball/strike call in the
  simulator inherits. One `SZ` constant in the page, `SZ_TOP`/`SZ_BOT` in the
  build. The zone is deliberately NOT per-batter: the simulator's batter is
  abstract, and a drawn zone that disagrees with the call reads as a bug.
- **The representative pitch must be typical in location, not just shape.**
  Scoring only on velocity and break picked whatever spot that pitch happened
  to go — Bryan Woo's came out at 4.12 ft, the 94th percentile of his own
  four-seams, against a median of 2.95. `score()` now includes plate_x/plate_z.
- Pitchers with fewer than 300 located pitches are skipped — too thin for
  honest heat maps.
- **The baseline pool is subsampled** (`BASE_SAMPLE`, 130k per batter hand).
  The grid is O(cells x pitches) in pure Python; with ~500 pitchers the pool
  passes a million pitches and the build would take minutes for no statistical
  gain.
- **Watch for name shadowing in `build_cards.py`.** It bit twice while adding
  the movement profile. `build_pitcher` has a local `st = meta["stat"]`, so
  `import statistics as st` is shadowed inside it (the import is aliased
  `_stats` for this reason). `main` binds the argparse namespace to `a`, so a
  loop variable named `a` there breaks `a.season` several lines later. Both
  failed deep into a 12-minute build. Prefer distinctive names in those two
  functions.
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
