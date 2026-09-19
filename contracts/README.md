# Robot interaction contracts

Taras selected high-level robot actions for the learning boundary.
The policy chooses a demonstrated destination.
The contract calls `pick_at` and `place_in`.
Jaume's controller owns motion, serial commands, and the electromagnet.

This folder remains outside `sim/magnet_sorter/`, which `sync.sh` replaces during synchronization.

## Completed experiments

| Policy | Correct | Wrong | Deferred |
| --- | ---: | ---: | ---: |
| Local image features, 21 demonstrations | 19 | 2 | 9 |
| OpenRouter vision and Jev, 3 demonstrations | 28 | 0 | 2 |
| OpenRouter vision and Jev, 21 demonstrations | 30 | 0 | 0 |

The test contains 30 poses of three M4 models, with simulated demonstrations from three M3 models.
These are controlled simulator results.
Both perception and decision models changed between the first and second experiments.
The comparison cannot isolate Jev's contribution.

With changed demonstration labels, all 30 Jev predictions followed the new destination mapping.
The Jev policy also drove three separate simulated placements to their expected bin footprints.

Read the [experiment instructions](learning/README.md), [Jev architecture](learning/JEV.md), and [latest results](learning/results/v2/report.md).

After synchronization to `91f6200`, all 29 tests passed, including rendering.
Three simulated sorts also passed with cached provider responses and no new API calls.
The [compatibility record](learning/results/compatibility-91f6200.json) preserves that check.

## Contract and evidence

The policy sends a `SortDecision` with an object ID, pickup coordinates, and a target.
Invalid decisions stop before robot calls.
A failed or malformed pick result prevents placement.

`reported_ok` records the adapter's result.
`task_verified` remains `null` because the contract lacks an independent task verifier.
The simulation evaluates final positions separately from the learning policy.

## Remaining scope

For this experiment, clusters mean groups defined by demonstrated destinations.
Separating touching objects, extracting human demonstrations from video, and physical hardware evaluation remain unimplemented.

Read the [existing interface review](current-interface.md).
