"""Generate the browser catalogue from a backend checkout; no dependencies or model calls.
Usage: python3 web/scripts/sync-error-catalogue.py /path/to/Basis_Info/backEnd
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from basis.errors.catalogue import ENTRIES, check_consistent
problems = check_consistent()
if problems:
    raise SystemExit('\n'.join(problems))
rows = {e.code: {'what': e.what, 'fix': e.fix, 'retry': e.retry, 'signal': e.signal} for e in ENTRIES}
out = Path(__file__).resolve().parents[1] / 'live' / 'error-catalogue.js'
out.write_text('/* Generated from basis.errors.catalogue; run web/scripts/sync-error-catalogue.py. */\n(function(s){const catalogue=' + json.dumps(rows, ensure_ascii=False, separators=(',', ':')) + ';if(typeof module==="object"&&module.exports)module.exports=catalogue;else s.BasisErrorCatalogue=catalogue;})(globalThis);\n')
print(f'Generated {len(rows)} error codes')
