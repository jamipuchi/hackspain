# Independent collection reviews

## Scorer: Spec

Reviewer: `collection_scorer_spec_review`, GPT-5.6 Sol, high effort.
Scope: production scorer and duration changes through `0f4dc05`.
Final verdict: clean.

The reviewer confirmed positive named-bin contact, one outcome, one retirement, and removal of the early height and retirement rules.
The reviewer confirmed ground and bounded downstream Spill behavior, shared duration use, and the sixteen preserved diagnostic cases.
The review required restoration of the existing lateral guard and a fresh test beyond the old strict cutoff.
Both findings were resolved before the scorer commit.

The review records one integration dependency outside this branch's ownership.
The Engine owner must copy `Bean.last_quat` into retained object history.
The coordinator accepted that dependency and owns its integration.

## Scorer: Standards

Reviewer: `collection_scorer_standards_review`, GPT-5.6 Terra, high effort.
Final verdict: pass.

The first review rejected anonymous selection of the last wall by geometry properties.
The revised implementation names both walls and derives the escape boundary from their exterior coordinates.
The reviewer found no remaining standards issue.

## Exact four-box candidate: static Spec

Reviewer: `collection_scorer_spec_review`, GPT-5.6 Sol, high effort.
Scope: four geometry lines after `0f4dc05`.
Final static verdict: clean.

The reviewer verified every authorized center and half-size.
The reviewer confirmed unchanged splitter, widths, friction, force, model, and lead.
This verdict covers source conformance only.
It does not establish physical retention or runtime acceptance.

## Exact four-box candidate: Standards

Reviewer: `collection_scorer_standards_review`, GPT-5.6 Terra, high effort.
Final verdict: pass.

The reviewer verified that the diff changes only four named colliders.
The reviewer accepted the use of `L.split_z` for the raised Accept surfaces.
The reviewer confirmed unchanged unrelated settings and a clean whitespace check.

## Physical validation remains separate

The first exact geometry candidate emits eight correct Accept and eight correct Reject events.
Its unabsorbed continuation retains all eight Keep objects and only four Reject objects.
The other four Reject objects penetrate the thin floor and reach the ground.
This failed physical check overrides the clean static reviews for geometry acceptance.
The scorer verdict remains accepted independently.
