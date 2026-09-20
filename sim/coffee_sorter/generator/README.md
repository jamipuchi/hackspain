# Object generator (source copy)

The live service runs this copy. It is the ONE generator: `item_job_generate.py`,
`item_job_render.py`, and `object_definitions._load_generator_probe()` all load it.

Model output is data. The recipe is validated, hashed, copied, and rendered. No
part of this package imports, executes, or evaluates model output.

## Origin

Copied byte for byte at commit `249e2de238c0af33af4aa9d74bf98b9d02f96c13` from:

| File | Original path | Original sha256 |
| --- | --- | --- |
| `probe.py` | `thoughts/taras/research/coffee-quality/object-generation/probe.py` | `4c9de2baeb932d85f300f35a8f4d6dbb25827859b4a1be7658fd7dea875409d3` |
| `render_suite.py` | `thoughts/taras/research/coffee-quality/object-generation/render_suite.py` | `1380830bfb7a00325e7a6f9c315bc129c9fc5718cd3fd45cd633682acfe2f571` |
| `render_recipe.py` | `thoughts/taras/research/coffee-quality/object-generation/render_recipe.py` | `42924fc046d5fd500c55e4517089bc138ca9c880f12cb18d8389a4290320499b` |
| `providers.py` | `contracts/learning/providers.py` | `4f3d8f9d008b671a17269927e197db716b8ca3b37329a173c8d32e527a384ff2` |

`render_recipe.py` and `providers.py` are unchanged. The research copies stay
untouched: they are recorded evidence, and their runner hashes must stay valid.

`probe.py` locates `providers.py` beside itself. The search path never leaves
this directory.

## Additive changes

`probe.py`:

1. Typed outcomes `CacheMiss`, `CredentialMissing`, `SubmissionUncertain`,
   `CachedProviderFailure`, and `CacheEntryInvalid`. A caller maps classes, never
   free provider text.
2. `verify_cached_entry()` checks the endpoint, the canonical request digest, and
   the response metadata before a cached entry is reused.
3. The credential is required only inside `call()`, immediately before an
   authorized live request. A pure cache replay needs no credential and no env file.
4. `--source-revision`, falling back to `CINTA_SOURCE_REVISION`, validated as 40
   lowercase hex characters. `git rev-parse HEAD` runs only when neither is given.

`render_suite.py`:

5. `--lock PATH`, default `/private/tmp/hackspain-coffee-runtime.lock`. The
   deployment passes a writable path. Locking stays in this one layer.

The system prompt, the schema, payload construction, the model defaults, and the
renderer behaviour are unchanged. `test_item_jobs.GeneratorInterfaceTest` proves
that the cached `star` request digest is still
`c5d88ad700875432803b4c9016746ff64e03740d65a373d73e2812af48ed27f8`.

## Provider cache layout

```
<provider-cache>/cache/<request digest>.json
```

Local development reads the research results directory, which already holds that
layout. The service never writes into it in cached mode.
