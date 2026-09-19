"""Build a self-contained offline page from vendored JS and recorded JSON."""
import base64
import gzip
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as temp:
    bundle = Path(temp) / 'viewer.js'
    subprocess.run([str(ROOT / 'node_modules/.bin/esbuild'), str(ROOT / 'viewer.js'),
                    '--bundle', '--minify', '--format=iife', '--legal-comments=inline',
                    f'--alias:three={ROOT / "vendor/three.module.js"}', f'--outfile={bundle}'], check=True)
    data = gzip.compress((ROOT / 'replay.json').read_bytes(), compresslevel=9, mtime=0)
    html = (ROOT / 'template.html').read_text().replace('__REPLAY_GZIP__', base64.b64encode(data).decode())
    html = html.replace('__VIEWER_BUNDLE__', bundle.read_text().replace('</script', '<\\/script'))
    html = '\n'.join(line.rstrip() for line in html.split('\n'))
    if len(html.encode()) >= 5 * 1024 * 1024:
        raise SystemExit('Page exceeds the 5 MiB swarm Page limit')
    (ROOT / 'index.html').write_text(html)
    print(f'Built index.html: {len(html.encode()):,} bytes; replay JSON: {(ROOT / "replay.json").stat().st_size:,} bytes; gzip: {len(data):,} bytes')
