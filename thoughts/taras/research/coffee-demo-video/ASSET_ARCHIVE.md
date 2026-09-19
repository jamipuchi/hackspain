# Coffee demo asset archive

Date: 2026-09-19.
Branch: `codex/coffee-demo-video`.
Renderer source commit: `14257e3`.

Taras requested an agent-fs backup using the repository's `.env` credentials.
The upload used only `AGENT_FS_API_URL` and `AGENT_FS_API_KEY` from that file.
No credential values or `.env` files entered Git or the archives.
The initial upload used the personal organization `t`.
Taras requested the `swarm` organization instead.
The transfer selected the organization and drive explicitly, without changing the CLI's saved configuration.

## Destination

- Organization: `swarm` (`9d0f4b46-6113-49f7-8e8c-d315a64bd59d`)
- Drive: `default` (`ad84339c-9d70-462a-84cf-b58aba031ac5`)
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

All eleven binary uploads created version 1 in the `swarm` organization.
Each returned content hash and byte count matched its local file.
Both archives and all nine individual clips were downloaded through agent-fs and hashed again.
Each downloaded file matched its local SHA-256 hash and byte count exactly.
Both local ZIP integrity checks passed.
The archive index was also uploaded and verified against its local bytes.
After destination verification, all twelve original files were removed from the personal organization.
The destination inventory contains twelve files. The original prefix contains no files.
The assets remain recoverable from the verified Swarm copies and unchanged local files.

## Review status and 1080p

The original archive and `clips/` files are 640 by 360 previews at 30 fps.
Taras authorized native 1920 by 1080 renders of all nine clips after reviewing this archive.
The Full HD batch now renders on the HackSpain box, with unchanged framing and timing.
Completed Full HD clips upload separately under `full-hd/`, with `-1080p.mp4` filename suffixes.
The first two Full HD clips passed verification during the remote handoff.
Swarm Lead task `8d7515a6-f830-4140-bcf5-2a1d09cea153` owns the remaining rendering and delivery checks.
The task requires exact agent-fs paths and signed links in Slack `#x-hackspain`.
The final `coffee-demo-clips-1080p.zip` remains pending.
See [Full HD render status](FULL_HD_RENDER.md) for the render settings and delivery checks.

Taras reported that the slow-motion discard remains difficult to see.
The recorded physical event is verified, but the shot needs clearer framing and more time showing the reject path.
Higher resolution alone does not resolve that visual problem.
The current Full HD pass does not revise that composition or imply visual acceptance.
