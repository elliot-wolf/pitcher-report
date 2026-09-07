#!/usr/bin/env python3
"""bake.py — inline cards.json into card_template.html -> pitcher-card.html"""
import json, os, sys

tpl  = open("card_template.html").read()
data = open("cards.json").read()
json.loads(data)                                    # fail loudly on bad JSON
if "__PAYLOAD__" not in tpl:
    sys.exit("template is missing the __PAYLOAD__ placeholder")
# </script> inside a <script> block would close it early
data = data.replace("</", "<\\/")
out = tpl.replace("__PAYLOAD__", data)
open("pitcher-card.html", "w").write(out)
print(f"pitcher-card.html — {os.path.getsize('pitcher-card.html')/1e6:.2f} MB")
