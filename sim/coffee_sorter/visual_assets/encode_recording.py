"""Encode exactly the audited 30 PNG samples into a one-second review MP4."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path, default=Path(__file__).resolve().parent/'recording')
    args = parser.parse_args()
    directory = args.directory.resolve()
    record = json.loads((directory/'manifest.json').read_text())
    assert len(record['frames']) == 30, 'complete 30-frame render required'
    for i, frame in enumerate(record['frames'], 1):
        assert frame['frame'] == i
        assert hashlib.sha256((directory/f'frame_{i:03d}.png').read_bytes()).hexdigest() == frame['png_sha256']
    command = ['ffmpeg', '-y', '-threads', '16', '-framerate', '30', '-start_number', '1',
               '-i', 'frame_%03d.png', '-frames:v', '30', '-an', '-c:v', 'libx264',
               '-threads', '16', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
               '-movflags', '+faststart', 'coffee-one-second.mp4']
    started = datetime.now(timezone.utc).isoformat()
    tick = time.perf_counter()
    subprocess.run(command, cwd=directory, check=True)
    movie = directory/'coffee-one-second.mp4'
    record['encoding'] = dict(command=command, started_utc=started,
        ffmpeg=subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0],
        elapsed_seconds=round(time.perf_counter()-tick, 3), bytes=movie.stat().st_size,
        sha256=hashlib.sha256(movie.read_bytes()).hexdigest(), frames=30, fps=30, duration_seconds=1,
        timing='CFR presentation of native near-30 Hz samples; exact simulation times remain in frames[].t')
    (directory/'manifest.json').write_text(json.dumps(record, indent=2)+'\n')


if __name__ == '__main__':
    main()
