# Coffee demo collaboration

These rules apply to coffee sorter work in this repository.

- Start with a working default. Natural-language changes follow.
- Taras owns functional testing, QA, and acceptance. Provide small runnable increments and request feedback after visible changes.
- Add unit tests only when a specific risk makes them strictly necessary. Explain that need first.
- Do not add a QA framework. Keep existing tests available and run the minimum build or syntax checks needed for a reviewable increment.
- Keep measurements honest. Separate classifier accuracy, physical sorting outcomes, engine speed, and browser frame rate.
- Keep simulator truth outside model inputs. Preserve independent evaluation objects.
- Commit and push review revisions to the active coffee branch. Keep its plan, evidence, and PR description consistent.
- Preserve the current policy and model when a language request is unsupported or ambiguous.
- The live demonstration must use a 3D view. Keep the current 2D projection temporary until the dedicated 3D increment.
- The deployed engine must run continuously without browser activity. Use explicitly labeled rolling score windows instead of requiring visitor restarts.

<important if="you are running the approved Full HD coffee render batch">

Run `sim/coffee_sorter/demo_video/bulk_full_hd.py` for sequential CPU renders and verified uploads to Swarm.
Read `sim/coffee_sorter/demo_video/README.md` for the container paths and commands.
The script requires a private JSON file with only the two agent-fs connection variables.
For approved local assistance, select clips with repeatable `--only`, `--device METAL`, and `--source-bundles`.
The source index appears only after all bounded native-frame archives pass upload verification.
Generated and maintained with the script-builder skill.

</important>
