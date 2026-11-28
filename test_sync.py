#!/usr/bin/env python3
"""Test pravidla synchronizace (TDdecide) z nejnovějšího briefing_v*.py. Spusť: python3 test_sync.py (potřebuje node)."""
import ast
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
newest = max(ROOT.glob("briefing_v*.py"), key=lambda p: int(re.search(r"v(\d+)", p.name).group(1)))
js = next(ast.literal_eval(n.value) for n in ast.parse(newest.read_text(encoding="utf-8")).body
          if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "CSYNC_JS")
fn = re.search(r"function TDdecide\(.*?\n  \}", js, re.S).group(0)

# (je tady, otisk tady, záznam na Cloudflare, otisk při minulé synchronizaci) -> co udělat
cases = [
    ([True, "a", {"h": "a"}, "a"], "same"),        # nic se nezměnilo
    ([True, "b", {"h": "a"}, "a"], "up"),          # změna jen tady
    ([True, "a", {"h": "b"}, "a"], "down"),        # změna jen na druhém zařízení
    ([True, "b", {"h": "c"}, "a"], "up"),          # změna na obou: vyhraje tohle zařízení
    ([True, "b", {"h": "a"}, None], "down"),       # první synchronizace: vezmi Cloudflare
    ([True, "a", {"h": "a", "d": 1}, "a"], "dellocal"),  # smazáno jinde, tady beze změny
    ([True, "b", {"h": "a", "d": 1}, "a"], "up"),        # smazáno jinde, ale tady upraveno: nemazat
    ([True, "a", None, None], "up"),               # nový záznam tady
    ([False, None, {"h": "a"}, "a"], "delremote"), # smazáno tady
    ([False, None, {"h": "b"}, "a"], "down"),      # smazáno tady, ale jinde upraveno: obnovit
    ([False, None, {"h": "a"}, None], "down"),     # nový záznam jinde
    ([False, None, {"h": "a", "d": 1}, "a"], "forget"),
]
prog = fn + "\nconst C = " + json.dumps(cases) + ";\n" + \
    "let bad = 0; for (const [a, want] of C) { const s = a[3] === null ? undefined : a[3]; const got = TDdecide(a[0], a[1], a[2], s);" \
    " if (got !== want) { bad++; console.log('CHYBA', JSON.stringify(a), 'čekal', want, 'dostal', got); } }\n" \
    "console.log(bad ? bad + ' chyb' : 'OK: ' + C.length + ' případů (' + " + json.dumps(newest.name) + " + ')'); process.exit(bad ? 1 : 0);"
raise SystemExit(subprocess.run(["node", "-e", prog]).returncode)
