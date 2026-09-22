---
name: archive
description: "Read verbatim history back from czip-auto session packs."
version: 1.0.0
author: hoy, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [memory, archive, sessions, recall]
    related_skills: []
---

# Czip-Auto Archive Skill

Long sessions are archived verbatim in the background. This skill tells you how to read them back without reloading full history.

It does not replace semantic memory (`memory` tool, `MEMORY.md`); use memory for facts and packs for exact wording.

## When to Use

- The user asks about an older session, past company, or exact wording.
- The current session was compressed and detail was lost.
- `/czip-auto list` shows packs for this profile.

## Prerequisites

- The `czip` toolset must be available (`czip_map`, `czip_search`, `czip_range`).
- Packs live per profile under `session-packs`; never look in another profile's home.

## How to Run

1. Find the pack: `/czip-auto list` (slash, user-facing) or ask the user which session.
2. Load the small map first: `czip_map` with the pack id.
3. Search for the topic: `czip_search` with a query.
4. Read only the needed slice: `czip_range` with the exact `a-b` indices.

## Quick Reference

- `czip_map(pack)` — counts, user requests, recent messages, next-step hint.
- `czip_search(pack, query)` — up to 20 hits as index + role + snippet.
- `czip_range(pack, start, end)` — exact messages, 0-based, inclusive.
- `/czip-auto status` — pack count and thresholds for this profile.
- `/czip-auto prune` — apply the retention caps now (`max_packs`, `max_age_days`, `max_total_bytes`).
- `/czip-auto pack <session_id>` — manual pack (long-only gate still applies).

## Procedure

Follow map, then search, then range. Never dump a whole pack into context; pull only the slice that answers the question. If `czip_search` returns nothing, try a shorter query or a different term before widening the range.

## Pitfalls

- Pack ids are per profile; a pack from another profile is invisible by design.
- Short sessions are never packed (below `min_messages` / `min_bytes`).
- A fresh pack may lag the live session by up to `cooldown_seconds`; recent turns may not be archived yet.
- Retention is enforced after every pack; old packs disappear without warning once a cap is hit.
- `[SAME-#N]` markers in raw files resolve automatically in `czip_range`; do not interpret them literally.

## Verification

After `czip_range`, quote the indices used (e.g. `pack a37tvc 1040-1060`) so the user can re-check the source slice.
