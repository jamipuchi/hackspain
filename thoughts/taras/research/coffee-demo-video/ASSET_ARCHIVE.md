# Coffee demo asset archive

Date: 2026-09-19.
Branch: `codex/coffee-demo-video`.
Renderer source commit: `14257e3`.

Taras requested an agent-fs backup using the repository's `.env` credentials.
The upload used only `AGENT_FS_API_URL` and `AGENT_FS_API_KEY` from that file.
No credential values or `.env` files entered Git or the archives.
The CLI's saved organization differed from the authenticated account's default organization.
The upload selected the authenticated organization and drive explicitly, without changing the CLI's saved configuration.

## Destination

- Organization: `e5223ba8-d883-4887-99fe-91910b6260ed`
- Drive: `40258bdf-7154-48d0-ad06-1a81f0dafe01`
- Prefix: `qa/hackspain/2026-09-19-coffee-demo/`

| File under the prefix | Bytes | SHA-256 |
|---|---:|---|
| `coffee-demo-clips.zip` | 2,996,332 | `61c98461c88344486a3be47e93440d0eeaa78a2e7911a1e9833b79e3ba329d3a` |
| `coffee-demo-editable-assets.zip` | 49,327,235 | `549fe733466e8c9fde652bf0c924ee8b078941645f977ef39e7b135245b99d5c` |

The clip archive contains nine descriptively named MP4s.
The editable archive contains 75 files:

- Twelve scene PNGs, twelve Blender scenes, and their manifests.
- Nine current MP4 previews and their manifests.
- Two concept images, the review sheet, and both local galleries.
- Seven representative slow-motion frames and their visibility manifest.
- The fresh capture, reproduction, manifests, validation, independent audit, and inference-equivalence evidence.

The archive excludes rejected render attempts, the unused UI reference capture, and redundant full PNG frame sequences.
All local source files remain unchanged and available.
The render commands remain in Git and can regenerate the omitted frame sequences.

## Individual clips

These files are also available directly under the `clips/` subdirectory:

- `01-bean-macro.mp4`
- `02-conveyor-reveal.mp4`
- `03-overhead-inspection.mp4`
- `04-blueprint-discharge.mp4`
- `04b-normal-discharge.mp4`
- `05-ultra-slowmo.mp4`
- `07-warm-closing.mp4`
- `07b-blueprint-closing.mp4`
- `07c-normal-closing.mp4`

## Verification

All eleven binary uploads created version 1.
Each returned content hash and byte count matched its local file.
Remote metadata confirmed both archive sizes and all nine individual clip sizes.
Both archives were downloaded through agent-fs and hashed again.
Their downloaded bytes matched the local SHA-256 hashes exactly.
Both local ZIP integrity checks passed.

## Review status and 1080p

These are 640 by 360 preview clips at 30 fps, not final production renders.
Taras reported that the slow-motion discard remains difficult to see.
The recorded physical event is verified, but the shot needs clearer framing and more time showing the reject path.
Higher resolution alone does not resolve that visual problem.

The recommended next step is to fix that shot, then render the accepted scenes natively at 1920 by 1080 and 30 fps.
Benchmark representative frames before the full batch. Preserve identical timing and camera paths across matched mode passes.
No 1080p render started during this archive task.
