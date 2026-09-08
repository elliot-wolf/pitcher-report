#!/usr/bin/env python3
"""
bake.py — split cards.json into a light index + per-pitcher location files.

At ~500 pitchers the packed pitch tables run to several MB, far too much to
inline. Everything a card needs *except* those tables goes into the page; the
location data for one pitcher is fetched on demand from p/<id>.json.

    dist/index.html   the page, with the index inlined
    dist/p/<id>.json  {"pitches": "<packed>"} per pitcher
"""
import json, os, shutil, sys, time

SRC, TPL, OUTDIR = "cards.json", "card_template.html", "dist"

data = json.load(open(SRC))
tpl = open(TPL).read()
if "__PAYLOAD__" not in tpl:
    sys.exit("template is missing the __PAYLOAD__ placeholder")

shutil.rmtree(OUTDIR, ignore_errors=True)
os.makedirs(f"{OUTDIR}/p", exist_ok=True)

# The pitcher shown on first paint keeps their locations inline, so the page
# is useful before any fetch resolves. Falls back to the heaviest workload if
# the named default is not in this build (traded, hurt, under the innings cut).
PREFERRED_DEFAULT = "Payton Tolle"
default = next((p for p in data["pitchers"] if p["name"] == PREFERRED_DEFAULT), None) \
       or max(data["pitchers"], key=lambda p: p.get("n", 0))
default_id = default["id"]
if default["name"] != PREFERRED_DEFAULT:
    print(f"note: {PREFERRED_DEFAULT} not in this build — defaulting to {default['name']}")

split = 0
for p in data["pitchers"]:
    packed = p.pop("pitches", "")
    move = p.pop("move", [])
    if not packed:
        continue
    with open(f"{OUTDIR}/p/{p['id']}.json", "w") as fh:
        json.dump({"pitches": packed, "move": move}, fh, separators=(",", ":"))
    split += 1
    if p["id"] == default_id:
        p["pitches"] = packed          # inline this one only
        p["move"] = move

data["defaultId"] = default_id
# Per-pitcher files are fetched with this stamp appended, so a rebuild can
# never leave a viewer decoding last build's bytes with this build's decoder.
data["buildId"] = int(time.time())
payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
open(f"{OUTDIR}/index.html", "w").write(tpl.replace("__PAYLOAD__", payload))

# keep a single-file build too, for opening straight off disk
shutil.copy(f"{OUTDIR}/index.html", "pitcher-card.html")

idx = os.path.getsize(f"{OUTDIR}/index.html")
tot = sum(os.path.getsize(f"{OUTDIR}/p/{f}") for f in os.listdir(f"{OUTDIR}/p"))
print(f"index.html : {idx/1e6:.2f} MB  ({len(data['pitchers'])} pitchers, "
      f"locations for {default['name']} inlined)")
print(f"p/*.json   : {split} files, {tot/1e6:.2f} MB total, "
      f"{tot/max(1,split)/1024:.0f} KB each")
