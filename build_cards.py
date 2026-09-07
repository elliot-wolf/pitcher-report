#!/usr/bin/env python3
"""
build_cards.py — fetch real MLB pitch-level data and bake it into the scouting card.

Sources (both free, unauthenticated):
  * MLB Stats API   statsapi.mlb.com      — roster, bio, season line, running game
  * Baseball Savant baseballsavant.mlb.com — pitch-by-pitch Statcast

A published Artifact cannot make cross-origin requests (CSP), so this script
resolves everything at BUILD time and inlines the payload into the HTML.
Re-run it to refresh the card.

    python3 build_cards.py --season 2026 --limit 60
"""
import argparse, csv, io, json, math, ssl, sys, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

CTX = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
STATS = "https://statsapi.mlb.com/api/v1"
SAVANT = "https://baseballsavant.mlb.com"

# ── plate geometry ──────────────────────────────────────────────────────────
PLATE_Y = 17.0 / 12.0          # front of home plate, feet
RELEASE_Y = 50.0               # Statcast trajectory coefficients are stated at y=50
ZONE = dict(x0=-0.83, x1=0.83, z0=1.59, z1=3.41)

# ── compact wire encoding for the pitch table ───────────────────────────────
B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
def e1(v): return B64[max(0, min(63, int(v)))]
def e2(v):
    v = max(0, min(4095, int(v)))
    return B64[v >> 6] + B64[v & 63]

COUNT_STATES = ["first", "ahead", "even", "behind", "twok"]
def count_state(balls, strikes):
    if balls == 0 and strikes == 0: return 0          # 0-0
    if strikes == 2:                return 4          # 2 strikes
    if strikes > balls:             return 1          # ahead
    if balls > strikes:             return 3          # behind
    return 2                                          # even

WHIFF_DESC = {"swinging_strike", "swinging_strike_blocked", "foul_tip", "swinging_pitchout"}
SWING_DESC = WHIFF_DESC | {"foul", "foul_bunt", "hit_into_play", "missed_bunt"}

# canonical pitch grouping for the three location panels
FASTBALLS  = {"FF", "SI", "FC", "FA"}
BREAKING   = {"SL", "ST", "CU", "KC", "SV", "SC", "CS", "KN"}
OFFSPEED   = {"CH", "FS", "FO", "SC"}

SHORT = {"4-Seam Fastball":"4-Seam", "Split-Finger":"Splitter", "Knuckle Curve":"Knuckle CB"}

def http(url, timeout=90, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last

def jget(url):
    return json.loads(http(url))

def f(row, key):
    v = row.get(key, "")
    if v in ("", "null", None): return None
    try: return float(v)
    except ValueError: return None

def vaa_deg(vy0, vz0, ay, az):
    """Vertical approach angle at the front of the plate. Not a Savant column."""
    if None in (vy0, vz0, ay, az): return None
    disc = vy0 * vy0 - 2 * ay * (RELEASE_Y - PLATE_Y)
    if disc < 0: return None
    t = (-vy0 - math.sqrt(disc)) / ay
    vy_f, vz_f = vy0 + ay * t, vz0 + az * t
    if vy_f == 0: return None
    return math.degrees(math.atan(vz_f / abs(vy_f)))

# ── roster ──────────────────────────────────────────────────────────────────
def _leaders(season):
    """Every pitcher with a season line, keyed by MLBAM id."""
    url = (f"{STATS}/stats?stats=season&group=pitching&season={season}"
           f"&sportId=1&limit=1500&sortStat=inningsPitched&playerPool=ALL")
    d = jget(url)
    by_id = {}
    for s_ in d.get("stats", []):
        for sp in s_.get("splits", []):
            st, pl = sp.get("stat", {}), sp.get("player", {})
            if not pl.get("id"): continue
            by_id[pl["id"]] = {
                "id": pl["id"], "name": pl["fullName"],
                "team": sp.get("team", {}).get("abbreviation") or sp.get("team", {}).get("name", ""),
                "gs": int(st.get("gamesStarted") or 0), "stat": st,
            }
    return by_id

def _probables(season, days):
    """Announced starters for today .. today+days-1."""
    import datetime as _dt
    found = {}
    today = _dt.date.today()
    for k in range(max(0, days)):
        day = (today + _dt.timedelta(days=k)).isoformat()
        try:
            d = jget(f"{STATS}/schedule?sportId=1&date={day}&hydrate=probablePitcher")
        except Exception as e:
            print(f"  probables {day}: {type(e).__name__}", file=sys.stderr)
            continue
        for date in d.get("dates", []):
            for gm in date.get("games", []):
                for side in ("away", "home"):
                    pp = gm.get("teams", {}).get(side, {}).get("probablePitcher")
                    if pp and pp.get("id"):
                        found[pp["id"]] = pp.get("fullName", "")
    return found

def _season_stat(pid, season):
    """Fallback season line for a starter outside the leaderboard."""
    try:
        d = jget(f"{STATS}/people/{pid}/stats?stats=season&group=pitching&season={season}")
        for s_ in d.get("stats", []):
            for sp in s_.get("splits", []):
                return sp.get("stat", {}), (sp.get("team", {}) or {}).get("abbreviation") \
                       or (sp.get("team", {}) or {}).get("name", "")
    except Exception:
        pass
    return {}, ""

def _ip(stat):
    """innings pitched, '137.2' meaning 137 and 2/3."""
    v = str(stat.get("inningsPitched", "0") or "0")
    w, _, f = v.partition(".")
    try: return int(w or 0) + (int(f or 0) / 3.0)
    except ValueError: return 0.0

def get_roster(season, min_ip, probable_days=0):
    """Every pitcher with enough work to support a card — starters and relievers
    alike — plus anyone announced to start in the next few days."""
    by_id = _leaders(season)
    base = [r for r in by_id.values() if _ip(r["stat"]) >= min_ip]
    base.sort(key=lambda r: -_ip(r["stat"]))
    have = {r["id"] for r in base}
    n_sp = sum(1 for r in base if r["gs"] >= 5)
    print(f"  {len(base)} with >= {min_ip} IP  ({n_sp} starters, {len(base)-n_sp} relievers)",
          file=sys.stderr)

    if probable_days > 0:
        added = 0
        for pid, name in _probables(season, probable_days).items():
            if pid in have: continue
            if pid in by_id:
                r = dict(by_id[pid])
            else:
                st, team = _season_stat(pid, season)
                if not st: continue
                r = {"id": pid, "name": name, "team": team,
                     "gs": int(st.get("gamesStarted") or 0), "stat": st}
            base.append(r); have.add(pid); added += 1
        print(f"  + {added} announced starter(s) below the innings cut", file=sys.stderr)
    return base

def get_bio(pid):
    p = jget(f"{STATS}/people/{pid}")["people"][0]
    return {
        "throws": p.get("pitchHand", {}).get("code", ""),
        "height": p.get("height", ""),
        "weight": p.get("weight", ""),
        "age": p.get("currentAge", ""),
    }

# ── Statcast pull ───────────────────────────────────────────────────────────
def get_statcast(pid, season):
    q = ("all=true&type=details&player_type=pitcher"
         f"&pitchers_lookup%5B%5D={pid}"
         f"&hfSea={season}%7C&game_date_gt={season}-02-01&game_date_lt={season}-12-31")
    txt = http(f"{SAVANT}/statcast_search/csv?{q}", timeout=180).decode("utf-8-sig", "replace")
    return list(csv.DictReader(io.StringIO(txt)))

# ── per-pitcher build ───────────────────────────────────────────────────────
def build_pitcher(meta, season):
    pid = meta["id"]
    rows = get_statcast(pid, season)
    rows = [r for r in rows if r.get("pitch_type") and r.get("plate_x") and r.get("plate_z")]
    if len(rows) < 300:
        return None
    bio = get_bio(pid)
    throws = bio["throws"] or (rows[0].get("p_throws") or "R")
    armsign = -1.0 if throws == "R" else 1.0      # + HB = arm side

    # ---- arsenal aggregates ------------------------------------------------
    by_pt = defaultdict(list)
    for r in rows:
        by_pt[r["pitch_type"]].append(r)
    total = len(rows)

    arsenal = []
    for pt, rs in sorted(by_pt.items(), key=lambda kv: -len(kv[1])):
        if len(rs) / total < 0.015: continue
        velo = [f(r, "release_speed") for r in rs]; velo = [v for v in velo if v]
        ivb  = [f(r, "pfx_z") * 12 for r in rs if f(r, "pfx_z") is not None]
        hb   = [f(r, "pfx_x") * 12 * armsign for r in rs if f(r, "pfx_x") is not None]
        spin = [f(r, "release_spin_rate") for r in rs if f(r, "release_spin_rate")]
        ext  = [f(r, "release_extension") for r in rs if f(r, "release_extension")]
        vaas = [vaa_deg(f(r,"vy0"), f(r,"vz0"), f(r,"ay"), f(r,"az")) for r in rs]
        vaas = [v for v in vaas if v is not None]
        xw   = [f(r, "estimated_woba_using_speedangle") for r in rs]
        xw   = [v for v in xw if v is not None]

        def zone_pct(hand):
            sub = [r for r in rs if r["stand"] == hand]
            if len(sub) < 15: return None          # too thin to quote a rate
            iz = sum(1 for r in sub
                     if abs(f(r,"plate_x") or 9) <= 0.83
                     and (f(r,"sz_bot") or 1.59) <= (f(r,"plate_z") or -9) <= (f(r,"sz_top") or 3.41))
            return iz / len(sub)

        swings = [r for r in rs if r["description"] in SWING_DESC]
        whiffs = [r for r in rs if r["description"] in WHIFF_DESC]
        ooz = [r for r in rs if abs(f(r,"plate_x") or 9) > 0.83
               or not ((f(r,"sz_bot") or 1.59) <= (f(r,"plate_z") or -9) <= (f(r,"sz_top") or 3.41))]
        ooz_sw = [r for r in ooz if r["description"] in SWING_DESC]

        mean = lambda a: (sum(a) / len(a)) if a else None
        arsenal.append({
            "id": pt,
            "name": SHORT.get(rs[0].get("pitch_name") or pt, rs[0].get("pitch_name") or pt),
            "usage": len(rs) / total,
            "n": len(rs),
            "velo": mean(velo), "max": max(velo) if velo else None,
            "ivb": mean(ivb), "hb": mean(hb),
            "spin": mean(spin), "ext": mean(ext), "vaa": mean(vaas),
            "zR": zone_pct("R"), "zL": zone_pct("L"),
            "whiff": (len(whiffs) / len(swings)) if len(swings) >= 15 else None,
            "chase": (len(ooz_sw) / len(ooz)) if len(ooz) >= 15 else None,
            "xwoba": mean(xw),
        })

    # A representative *real* pitch per type: the one whose shape sits closest
    # to that type's median. Using medians of each parameter independently can
    # describe a trajectory nobody actually threw, so pick an actual pitch.
    TRAJ = ["release_pos_x", "release_pos_z", "vx0", "vy0", "vz0",
            "ax", "ay", "az", "release_extension", "plate_x", "plate_z"]
    flight = {}
    for a in arsenal:
        rs = [r for r in by_pt[a["id"]] if all(f(r, k) is not None for k in TRAJ)]
        if len(rs) < 5: continue
        import statistics as _st
        mv = _st.median([f(r, "release_speed") for r in rs if f(r, "release_speed")])
        mx = _st.median([f(r, "pfx_x") for r in rs])
        mz = _st.median([f(r, "pfx_z") for r in rs])
        def score(r):
            return (((f(r, "release_speed") or mv) - mv) / 2.0) ** 2 \
                 + ((f(r, "pfx_x") - mx) / 0.25) ** 2 \
                 + ((f(r, "pfx_z") - mz) / 0.25) ** 2
        best = min(rs, key=score)
        flight[a["id"]] = [round(f(best, k), 3) for k in TRAJ]

    order = [a["id"] for a in arsenal]
    pt_idx = {pt: i for i, pt in enumerate(order)}

    # ---- usage by count state x hand --------------------------------------
    usage = {h: {cs: {} for cs in COUNT_STATES} for h in ("R", "L")}
    denom = defaultdict(int)
    tally = defaultdict(int)
    for r in rows:
        if r["pitch_type"] not in pt_idx: continue
        h = r["stand"]
        cs = COUNT_STATES[count_state(int(r["balls"] or 0), int(r["strikes"] or 0))]
        denom[(h, cs)] += 1
        tally[(h, cs, r["pitch_type"])] += 1
    for (h, cs, pt), n in tally.items():
        usage[h][cs][pt] = round(n / denom[(h, cs)], 4)

    # ---- encoded pitch table ----------------------------------------------
    buf = []
    pool = []
    for r in rows:
        pt = r["pitch_type"]
        if pt not in pt_idx: continue
        x, z = f(r, "plate_x"), f(r, "plate_z")
        if x is None or z is None or abs(x) > 3 or not (0 <= z <= 6): continue
        stand = 0 if r["stand"] == "R" else 1
        cs = count_state(int(r["balls"] or 0), int(r["strikes"] or 0))
        whiff = r["description"] in WHIFF_DESC
        ls, la = f(r, "launch_speed"), f(r, "launch_angle")
        barrel = bool(ls and la and ls >= 98 and 8 <= la <= 50 and (ls * 1.5 - la) >= 117)
        flags = (1 if whiff else 0) | (2 if barrel else 0)
        buf.append(e1(pt_idx[pt]) + e1(stand * 20 + cs * 4 + flags)
                   + e2((x + 3) * 100) + e2(z * 100))
        pool.append((x, z, stand, whiff, barrel))

    rel_x = [f(r, "release_pos_x") for r in rows if f(r, "release_pos_x") is not None]
    rel_z = [f(r, "release_pos_z") for r in rows if f(r, "release_pos_z") is not None]
    ext_a = [f(r, "release_extension") for r in rows if f(r, "release_extension")]
    mean0 = lambda a: round(sum(a) / len(a), 2) if a else None

    st = meta["stat"]
    g = lambda k, d="—": st.get(k, d)
    ip = st.get("inningsPitched", "0")
    bf = int(st.get("battersFaced") or 0) or 1

    card = {
        "id": pid, "name": meta["name"], "team": meta["team"],
        "throws": throws, "height": bio["height"], "weight": bio["weight"], "age": bio["age"],
        "rel": {"x": mean0(rel_x), "z": mean0(rel_z), "ext": mean0(ext_a)},
        "season": {
            "IP": ip, "ERA": g("era"), "WHIP": g("whip"),
            "K%": round(100 * int(st.get("strikeOuts") or 0) / bf, 1),
            "BB%": round(100 * int(st.get("baseOnBalls") or 0) / bf, 1),
            "HR": g("homeRuns"), "GS": meta["gs"],
            "G": int(st.get("gamesPlayed") or 0),
            "SV": int(st.get("saves") or 0),
            "AVG": g("avg"),
            "Pit/App": round(len(rows) / max(1, int(st.get("gamesPlayed") or 1))),
        },
        "run": {
            "SB": int(st.get("stolenBases") or 0),
            "CS": int(st.get("caughtStealing") or 0),
            "PK": int(st.get("pickoffs") or 0),
            "WP": int(st.get("wildPitches") or 0),
            "runnersOn": round(100 * sum(1 for r in rows if r.get("on_1b") or r.get("on_2b") or r.get("on_3b")) / len(rows)),
        },
        "role": "SP" if meta["gs"] >= 5 else "RP",
        "arsenal": arsenal,
        "usage": usage,
        "flight": flight,
        "pitches": "".join(buf),
        "n": len(buf),
    }
    return card, pool

# ── league baseline from the pooled sample ──────────────────────────────────
NX, NZ, BW = 24, 26, 0.42
XD, ZD = (-1.95, 1.95), (0.35, 4.55)

BASE_SAMPLE = 130_000        # per batter hand; well past the point of diminishing returns

def baseline_grid(pool):
    """Location-matched whiff & barrel rates from the pooled sample.

    The grid is O(cells x pitches). With ~500 pitchers the pool runs to a
    million-plus pitches, which is minutes of pure Python for no statistical
    gain, so each hand is subsampled."""
    import random
    cw = (XD[1] - XD[0]) / (NX - 1)
    ch = (ZD[1] - ZD[0]) / (NZ - 1)
    out = {}
    for stand, key in ((0, "R"), (1, "L")):
        pts = [p for p in pool if p[2] == stand]
        if len(pts) > BASE_SAMPLE:
            pts = random.Random(20260907).sample(pts, BASE_SAMPLE)
        wg, bg = [], []
        for j in range(NZ):
            gz = ZD[1] - ch * j
            for i in range(NX):
                gx = XD[0] + cw * i
                ws = vw = vb = 0.0
                for (x, z, _s, whf, bar) in pts:
                    d2 = ((x - gx) ** 2 + (z - gz) ** 2) / (BW * BW)
                    if d2 > 9: continue
                    w = math.exp(-0.5 * d2)
                    ws += w; vw += w * whf; vb += w * bar
                wg.append(round(vw / ws, 4) if ws > 25 else -1)
                bg.append(round(vb / ws, 4) if ws > 25 else -1)
        out[key] = {"whiff": wg, "barrel": bg, "n": len(pts)}
    return out

def default_season():
    """The season whose data is worth pulling right now.

    MLB plays late March through October. Before April we fall back to the
    previous season, because the current one has no meaningful sample yet
    (and the build's sanity gate would reject it)."""
    import datetime as _dt
    t = _dt.date.today()
    return t.year if t.month >= 4 else t.year - 1

# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=default_season(),
                    help="defaults to the season currently in progress")
    ap.add_argument("--min-ip", type=float, default=20.0,
                    help="minimum innings pitched to earn a card")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="cards.json")
    ap.add_argument("--probable-days", type=int, default=2,
                    help="also include announced starters for the next N days (0 to disable)")
    a = ap.parse_args()

    print(f"roster: >= {a.min_ip} IP + probables for the next {a.probable_days}d, {a.season}", file=sys.stderr)
    roster = get_roster(a.season, a.min_ip, a.probable_days)
    print(f"  got {len(roster)}", file=sys.stderr)

    cards, pool = [], []
    done = [0]
    def work(m):
        try:
            r = build_pitcher(m, a.season)
            done[0] += 1
            print(f"  [{done[0]:>3}/{len(roster)}] {m['name']:<26} "
                  f"{'ok ' + str(r[0]['n']) + ' pitches' if r else 'SKIP (thin)'}", file=sys.stderr)
            return r
        except Exception as e:
            done[0] += 1
            print(f"  [{done[0]:>3}/{len(roster)}] {m['name']:<26} FAIL {type(e).__name__}: {e}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(work, roster):
            if r:
                cards.append(r[0]); pool.extend(r[1])

    print(f"building league baseline from {len(pool):,} pooled pitches…", file=sys.stderr)
    base = baseline_grid(pool)

    cards.sort(key=lambda c: c["name"].split()[-1])
    print(f"  {sum(1 for c in cards if c['role']=='SP')} SP / {sum(1 for c in cards if c['role']=='RP')} RP", file=sys.stderr)
    payload = {
        "season": a.season,
        "built": time.strftime("%Y-%m-%d"),
        "grid": {"nx": NX, "nz": NZ, "bw": BW, "xd": XD, "zd": ZD},
        "baseline": base,
        "pitchers": cards,
    }
    with open(a.out, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    import os
    print(f"\nwrote {a.out}  —  {len(cards)} pitchers, "
          f"{os.path.getsize(a.out)/1e6:.2f} MB", file=sys.stderr)

if __name__ == "__main__":
    main()
