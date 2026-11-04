# Architecture

How data moves through the integration, and where to look when changing it.

```
Pi-hole /api/queries ──► api.PiHoleClient
                              │
                              ▼
                  coordinator.PiHoleQueryCoordinator   (polls every scan_interval)
                    │            │             │
                    │            │             └─► Logbook ("Activity"), detail mode only
                    │            ▼
                    │      store._IdentityStore  (cursor + per-client counters, HA Store)
                    ▼
        sensor.py / binary_sensor.py  ──► Recorder statistics
                                              │
                    activity_data.py ◄────────┘  (one read, shared)
                      │            │
        websocket.py ◄┘            └─► activity_monitor.py (every 5 min) ─► binary sensors
              │
              ▼
   frontend/phe-activity-card.js
```

## Modules

| Module | Responsibility |
|---|---|
| `api.py` | Pi-hole v6 client: session ID, one retry after a 401, logout on unload. |
| `coordinator.py` | Polling, cursor walking, counters, rolling one-minute windows, rollback when the Store cannot confirm progress. |
| `store.py` | Source-scoped persistent Store; validates and sanitizes what it loads. |
| `validation.py` | Validators shared by the Store and the coordinator. |
| `classification.py` | Versioned mapping from Pi-hole query status to allowed / blocked / provisional. |
| `entity_ids.py` | Unique IDs, device identifiers and the `phe_` entity IDs. |
| `activity.py` | Pure functions: bucketing and robust unusual-activity scoring. |
| `activity_data.py` | Reads Recorder statistics and applies `activity.py`. |
| `activity_monitor.py` | Periodic evaluation for the binary sensors. |
| `websocket.py` | `pihole_queries/client_activity` command used by the card. |
| `diagnostics.py` | Downloadable diagnostics with host, password and clients redacted. |

## Retention modes

- **Activity enabled** (`detail`): each new query is counted and, per the
  activity policy, written to the Logbook. Pages are walked back to the stored
  cursor.
- **Totals only** (`aggregate`): rows are streamed one at a time and kept only
  as anonymous per-client deltas (`aggregate_pending`) until the cursor is
  reached, then merged into the counters. Nothing is sent to Activity.

## Cursor, coverage and recovery

Pi-hole returns newest queries first. The coordinator stores the highest query
ID it has fully processed (`cursor`) and each poll walks pages back until it
reaches it. The result is exposed as `coverage`:

| Coverage | Meaning |
|---|---|
| `complete` | The walk reached the stored cursor; nothing was missed. |
| `partial` | Counters are lower bounds (retention truncation, invalid rows, stalled pagination). |
| `unknown` | No trustworthy baseline yet, or the cursor was not reached. |

`coverage_reason` (`CoverageReason` in `const.py`) says why. When the walk
cannot reach the cursor a `recovery_cursor` is kept so the next poll resumes
instead of losing the gap. If Pi-hole's query IDs go backwards (database
replaced), the coordinator sets `awaiting_generation` and stops counting until
the user confirms a new source in the reconfigure flow.

## Source identity

Each entry has a `source_id` and a `source_generation`. Store keys, unique IDs
and device identifiers include both, so a new Pi-hole never merges its counters
with a retired one. Entity IDs add the generation only after the first one.

## Conventions

- Entity IDs are English and do not depend on the Home Assistant language:
  `phe_<client>_<metric>` (`dns_queries`, `unusual_activity`) for clients and
  `<entry>_diagnostics_<metric>` for diagnostics.
- Every translatable string lives in `translations/<language>.json`, the file Home
  Assistant loads: adding that one file adds a language. The cards read their strings
  from its `selector.card.options` section. Logs, errors and Logbook text are English.
- Entity names come from translations only; never set `name=` on descriptions
  that have a `translation_key`.
- Persisted coverage reasons are `CoverageReason` members; add new ones there
  and in the Store sanitizer.
- Tests build the coordinator with `object.__new__` in places, which is why
  some attributes are read with `getattr(self, …)`.
