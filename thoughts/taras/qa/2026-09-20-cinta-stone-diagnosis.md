---
status: pass
date: 2026-09-20
feature: CINTA manual Stone injection
---

# CINTA Stone diagnosis

## Scope

Taras reported that manual Stone objects reached Reject while Stone was configured as Keep.

This check separated object identity, classifier output, anomaly detection, valve contact, and physical outcome.

The check preserved the trusted model, controller rule, physics, and manual score exclusion.

## Results

### The injected object is a real Stone

The injection command requested `stone`. `Engine.inject` resolved the Stone profile before spawning the object.

The retained objects used the Stone `box` shape. Their sampled axes stayed inside the profile ranges.

### The class policy mapping is correct

Three isolated Stone-only-Keep trials predicted Stone with confidence from 0.9888368940 to 0.9999761447.

Their summed rejected-class probability ranged from 0.0000238553 to 0.0111631060. Each value stayed below the 0.8 threshold.

The anomaly score ranged from 392.8325 to 426.6733. The trusted model anomaly threshold was 15.6529.

The controller therefore rejected each object through its existing anomaly OR rule. The class probability rule did not cause these decisions.

Three Keep-all trials had zero rejected-class probability. Their anomaly scores ranged from 405.8619 to 428.3801.

The anomaly rule still scheduled each valve pulse. Keep all controls class policy and does not disable open-set anomaly protection.

This behavior is not a per-class policy mapping bug. This increment does not change classifier or anomaly semantics.

### A separate passive Reject path exists

Five live manual Stone records expected Accept under policy version `77ffd68b1915bca56b4bb65e081d0fbd093253ac00f5f8594ed191f0a0a5d2d7`.

Objects 36525, 36543, 36583, 36713, and 36783 reached Reject with zero jet hits and no own pulse contact.

The simulator resolves outcome when an object reaches `x >= 0.34 m`. It accepts only when the object centre remains above `z = 0.475 m`.

An ideal unforced flight from about `z = 0.603 m` needs about `2.10 m/s` forward speed at the belt edge.

Object contacts, rotation, and splitter contact can reduce clearance. The retained evidence lacks splitter-plane trajectories for these five objects.

The evidence proves a passive Reject path. It does not prove why each object lost the required clearance.

## Material limitations

The UI expectation records the selected class policy at injection time. It does not guarantee the controller decision or physical chute.

The anomaly rule can reject a class configured as Keep. A passive trajectory can also reach Reject without any valve contact.

`Spilled` remains distinct from both expected destinations.

## Evidence

- [Isolated Stone trace](evidence/2026-09-20-cinta-stone-diagnosis/isolated-stone-trace.json) records one complete probability and anomaly decision.
- [Controller comparison](evidence/2026-09-20-cinta-stone-diagnosis/controller-comparison.json) records three Stone-only-Keep and three Keep-all trials.
- [Passive live Stones](evidence/2026-09-20-cinta-stone-diagnosis/passive-live-stones.json) records five zero-contact Reject outcomes and their acknowledgements.

Evidence SHA-256 values:

- `isolated-stone-trace.json`: `499b7344a62b44e3c8c97a61b06c7409ac847b8607226612f5a1adc42e459e86`
- `controller-comparison.json`: `d3d6c3d24e2f298720ba9111095da0f06b11e63746b8fffbc56181f5381b9e37`
- `passive-live-stones.json`: `04a88b1249e799550ff291cce6a89ac7b9d56e368a7b9e33c9dd2143fe6bb5d7`

## Final state

Canonical service `http://127.0.0.1:8899/` remained the only live CINTA engine.

The normal demo policy was restored. Good is Keep, and the other nine classes are Reject.

The restored policy version is `5acba1d1a7547e544a6283cb870003120799cc2aa37cb0f273f2af5545c1afff`.

## Verdict

The diagnosis passes. The controller follows its documented probability and anomaly rules.

The anomaly override and passive Stone trajectory remain visible product limitations. Publication must describe both limits.
