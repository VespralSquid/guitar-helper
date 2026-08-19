# ISSUE-006 — Preset map diverges between fresh and migrated databases

**Status:** RESOLVED (2026-08-18). Fixed in commit `afa150d`.
**Date:** 2026-08-04
**Component:** `guitar_helper/db/schema.py` — `_seed()`, `_MIGRATIONS`, `_DEFAULT_PRESETS`
**Found by:** MVP readiness review (`docs/Report/mvp-readiness-review.md` §B2)

---

## Summary

`_DEFAULT_PRESETS` is applied **only when a database is created**. It is a
creation-time fixture, not a converging target. Three of the five historical
changes to that list shipped without a migration, so a database's tone→PC map
depends on *when it was created*, not on the current code.

The result is three incompatible populations of database in the wild. One of
them cannot store `edge` segments at all; another silently sends the wrong
Program Change for every tone except `clean`.

---

## Symptom

No user-visible error in the common case — which is what makes this dangerous.

| Population | Symptom |
|---|---|
| Created before Phase 2 | `IntegrityError: FOREIGN KEY constraint failed` when analysing any song containing an `edge` section. At runtime the dispatcher logs `UNMAPPED` and holds the current preset. |
| Created at Phase 3 (v6/v7/v8) | **No error at all.** The amp switches to the wrong preset for every tone except `clean`. Detectable only by ear. |
| Created fresh at v9 | Correct. |

The Phase 3 case is the realistic one and the more serious, because nothing in
the app reports it. The dispatch log shows a clean `send` — it is sending the PC
the `presets` table asks for, and the table is wrong.

---

## Root cause

`init_db()` seeds presets only when `schema_version` has no row:

```python
row = conn.execute("SELECT version FROM schema_version").fetchone()
if row is None:
    _seed(conn)              # ← only path that reads _DEFAULT_PRESETS
    ...
else:
    _apply_migrations(conn, row[0])
```

So an existing database never picks up a change to `_DEFAULT_PRESETS` unless a
migration explicitly moves its rows. Tracing every change to that list:

| Commit | `_DEFAULT_PRESETS` | Version bump? | Migration written? |
|---|---|---|---|
| `82542df` initial | clean 0, crunch 1, metal 3, ambient 4 | v1 | — |
| `d4a732e` | clean 0, crunch 1, **metal 2, ambient 3** | **no** | **no** |
| `3ee7d86` Phase 2 | + **edge 4** | **no** | **no** |
| `0922b7c` Phase 3 | clean 0, crunch 1, metal 2, **edge 3, overdrive 4** | v6 (+ migrations 2–6) | yes |
| `de249c7` current | **clean 0, edge 1, overdrive 2, crunch 3, metal 4** | v9 | **no** |

Two specific failures follow:

**1. `_migrate_v4_to_v5` is a silent no-op on early databases.**

```python
conn.execute("UPDATE presets SET pc_number = 3 WHERE tone_label = 'edge'")
```

The `edge` row was added to `_DEFAULT_PRESETS` at `3ee7d86` with no migration,
so a database created at `82542df`/`d4a732e` has no such row. The `UPDATE`
matches zero rows and reports no error. `edge` is never created, and
`segments.tone_label REFERENCES presets(tone_label)` then rejects every `edge`
segment.

**2. The gain-ramp reorder at `de249c7` shipped with no migration.**

Migrations 7, 8 and 9 only create tables (`segments_calibration`, `playlists`,
`settings`). Nothing touches `presets`. A database created at `0922b7c` keeps
the pre-reorder ordering forever.

---

## Reproduction

Both cases reproduce deterministically against throwaway databases. Run from the
repo root with the venv active.

```python
import importlib.util, os, subprocess, sys, tempfile
sys.path.insert(0, os.getcwd())
from guitar_helper.db.schema import init_db as init_current

def schema_at(commit, tmp):
    """Load db/schema.py as it existed at `commit`."""
    src = os.path.join(tmp, f"schema_{commit}.py")
    with open(src, "w") as f:
        f.write(subprocess.run(
            ["git", "show", f"{commit}:guitar_helper/db/schema.py"],
            capture_output=True, text=True).stdout)
    spec = importlib.util.spec_from_file_location(f"s_{commit}", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def presets(conn):
    return conn.execute(
        "SELECT tone_label, pc_number FROM presets ORDER BY pc_number").fetchall()

tmp = tempfile.mkdtemp()
for commit, label in [("82542df", "pre-Phase-2 (v1)"), ("0922b7c", "Phase 3 (v6)")]:
    db = os.path.join(tmp, f"{commit}.db")
    old = schema_at(commit, tmp)
    c = old.init_db(db); print(f"{label} at creation: {presets(c)}"); c.close()
    c = init_current(db);  print(f"{label} after migrate to v9: {presets(c)}\n"); c.close()
```

Observed output:

```
pre-Phase-2 (v1) at creation:      [('other',-1),('clean',0),('crunch',1),('metal',3),('ambient',4)]
pre-Phase-2 (v1) after migrate:    [('other',-1),('clean',0),('crunch',1),('metal',3),('overdrive',4)]
                                                                    ^^^^ no 'edge' row

Phase 3 (v6) at creation:          [('other',-1),('clean',0),('crunch',1),('metal',2),('edge',3),('overdrive',4)]
Phase 3 (v6) after migrate to v9:  [('other',-1),('clean',0),('crunch',1),('metal',2),('edge',3),('overdrive',4)]
                                                                    ^^^^ unchanged — wrong ramp, no error

Fresh v9 install:                  [('other',-1),('clean',0),('edge',1),('overdrive',2),('crunch',3),('metal',4)]
```

Storing an `edge` segment in the migrated v1 database:

```
sqlite3.IntegrityError: FOREIGN KEY constraint failed
```

---

## Secondary defect — `presets.pc_number` has no UNIQUE constraint

Uniqueness is enforced **only** by `ui/editor/preset_validation.validate_pc`,
which migrations bypass entirely.

`_migrate_v3_to_v4` correctly refuses to delete `ambient` while a segment still
references it (the FK would break). `_migrate_v5_to_v6` then inserts `overdrive`
at PC 4 unconditionally. A v1 database with even one `ambient` segment therefore
ends with **two tones on PC 4**:

```
migrated v1 (with an ambient segment):
    [('other',-1),('clean',0),('crunch',1),('metal',3),('ambient',4),('overdrive',4)]
    DUPLICATE PC numbers: [(4, 2)]
```

Which of the two wins at dispatch is whichever `get_presets()` happens to return
last into `{p.tone_label: p.pc_number for p in ...}` — order-dependent and
undefined.

---

## Collateral damage: this already corrupted the project documentation

`SAVE_STATE.md:83` carried this note:

> CORRECTION (2026-08-03): this line previously read "clean=0, crunch=1,
> metal=2, edge=3, overdrive=4" — wrong.

That mapping is exactly the Phase 3-era migrated layout. **The original line was
correct.** It was checked against a freshly-created database, found to disagree,
and "corrected" — generalising from one population to all of them. Both
statements were true, of different databases.

This is the bug's signature: two honest observers reading two databases reach
contradictory conclusions and neither is wrong. Retracted in `SAVE_STATE.md`
2026-08-04.

The live `library.db` was migrated (v6→v8→v9 per the save state) yet now shows
the *fresh* ordering, so those rows were changed after migration — almost
certainly through the O4 preset table, the only caller of `save_preset`. The
symptom was fixed by hand without the cause being visible.

---

## What makes the fix non-obvious

Before Phase 4 O4, `_DEFAULT_PRESETS` was unambiguously canonical and a blanket
overwrite would obviously have been correct.

O4 made presets **user-editable** — the whole point of the Output panel's preset
table is to let a user map tones onto their amp's actual patch slots. So a
divergent `pc_number` now has two possible meanings:

- migration drift (should be corrected), or
- a deliberate user mapping (must not be touched).

**The schema records nothing that distinguishes them.** That missing distinction
is the real defect; the wrong PC numbers are its symptom.

---

## Proposed fixes

### A. (Recommended) Record user intent, then reconcile — plus a divergence banner

Two parts, addressing future and existing databases respectively.

**A1 — schema.** Add `presets.user_modified INTEGER NOT NULL DEFAULT 0`, set to
1 by `save_preset` when the write originates in the Output panel. Reconcile then
becomes unambiguous permanently: correct anything not user-modified, never touch
anything that is. Retires the entire bug class rather than this instance of it.

Note that every pre-v10 row necessarily carries `user_modified = 0`, because the
flag did not exist when they were written — so the v10 reconcile *will* overwrite
a genuine user customisation made before v10. That is the deliberate trade: a
silently wrong gain ramp is worse than a customisation the user can re-enter in
Output mode, and A2 tells them it happened.

**A2 — UI.** Output mode compares the live `presets` table against
`_DEFAULT_PRESETS` and shows a banner on divergence, with a one-click "reset to
defaults". This is what covers databases that already exist and have no edit
history to consult. It also puts the check somewhere the user already goes when
the amp misbehaves, next to the test-send button.

**Cost:** one column, one migration, one comparison in `OutputMode.refresh_presets`.

### B. Legacy-fingerprint reconcile

Recognise the two known-bad layouts explicitly and rewrite only those; leave
anything unrecognised alone. No schema change, and it never touches a
customisation.

**Cost:** a hard-coded list of historical layouts that must be extended every
time `_DEFAULT_PRESETS` moves — i.e. it re-creates the discipline whose absence
caused this issue.

### C. Detect and report only

Migration inserts genuinely missing tone rows (fixing the `edge` FK crash) and
nothing else; the A2 banner handles everything else. Safest, zero risk of
overwriting intent, but leaves wrong ramps in place for any user who ignores the
banner.

### Recommendation

**A (A1 + A2).** A1 makes every future reconcile correct by construction; A2 is
what actually reaches the databases that exist today. B is a maintenance
liability. C is a reasonable fallback if overwriting a pre-v10 customisation is
judged unacceptable — it is A minus the reconcile.

**The UNIQUE index should land under any of the three.** Nothing legitimately
wants two tones on one Program Change.

---

## Migration sketch (option A), and two traps in it

```python
def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(
        conn, "presets", "user_modified", "INTEGER NOT NULL DEFAULT 0"
    )

    # Tones added to _DEFAULT_PRESETS without a migration (notably 'edge').
    conn.executemany(
        "INSERT OR IGNORE INTO presets(tone_label, preset_name, pc_number) "
        "VALUES (?,?,?)",
        _DEFAULT_PRESETS,
    )

    # TRAP 1 — park every reconcilable row out of range first, so the
    # intermediate states cannot collide with each other (edge -> 1 while
    # crunch still holds 1).
    conn.execute(
        "UPDATE presets SET pc_number = pc_number + 1000 "
        "WHERE user_modified = 0 AND pc_number >= 0"
    )
    for tone, _name, pc in _DEFAULT_PRESETS:
        conn.execute(
            "UPDATE presets SET pc_number = ? "
            "WHERE tone_label = ? AND user_modified = 0",
            (pc, tone),
        )

    # TRAP 2 — a tone not in _DEFAULT_PRESETS (a surviving 'ambient', or a
    # user-added preset) may still be parked at +1000 or clashing. Relocate
    # any leftover to the lowest free PC before the index is created, or
    # CREATE UNIQUE INDEX below fails on existing data and the whole
    # migration aborts.
    _relocate_unmapped_presets(conn)

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_presets_pc "
        "ON presets(pc_number) WHERE pc_number >= 0"
    )
```

**Trap 1 — transient collisions.** SQLite enforces UNIQUE per row as an
`UPDATE` proceeds, so reordering PCs in place collides mid-statement even when
the final state is valid. Park the values out of range first (the `+1000`
offset), then assign finals.

Verified against the exact `0922b7c` → current reorder:

```
reorder in place, one row at a time, index present:
    IntegrityError: UNIQUE constraint failed: presets.pc_number
with the +1000 park-first workaround:
    OK -> [('other',-1),('clean',0),('edge',1),('overdrive',2),('crunch',3),('metal',4)]
```

**Trap 2 — `CREATE UNIQUE INDEX` fails on pre-existing duplicates.** The
`ambient`+`overdrive`-both-on-4 case must be resolved *before* the index is
created, or the migration raises and leaves the database at v9. `other` (−1)
must stay exempt, hence the partial index on `pc_number >= 0`.

Verified:

```
CREATE UNIQUE INDEX over ('ambient',4),('overdrive',4):
    IntegrityError: UNIQUE constraint failed: presets.pc_number
```

Both traps were reproduced before being written down; the sketch above is the
form that passes.

**Ordering note.** `_migrate_v9_to_v10` must run *after* B1 (the non-atomic
`save_segments` fix in `mvp-readiness-review.md` §B1) is in place, and the live
`library.db` must be backed up first. Inserting the missing `edge` row makes
previously-rejected analyses succeed, which is exactly when a partial-write
failure would otherwise destroy existing segments.

---

## Test plan

The gap that let this ship is that `test_migration_upgrades_to_current` asserts
the version number, two column names, and one surviving track row — and nothing
about `presets`. Minimum coverage to add:

1. **Convergence.** For each legacy start version, a migrated database's
   `presets` contents equal a fresh database's. This single test would have
   caught the entire issue.
2. **`edge` storability.** After migrating a pre-Phase-2 database, inserting an
   `edge` segment succeeds.
3. **Uniqueness.** After migrating a v1 database that has an `ambient` segment,
   no two tones share a `pc_number`, and the UNIQUE index exists.
4. **Intent preserved.** A row with `user_modified = 1` and a non-default
   `pc_number` survives the migration unchanged.
5. **Transient-collision regression.** Migrating a database in the exact
   `0922b7c` ordering (the reorder that provokes Trap 1) completes without an
   IntegrityError.

---

## Current status

**Option A implemented** — `presets.user_modified` column added to schema v10, migrations reconcile non-user-modified rows against `_DEFAULT_PRESETS`, partial UNIQUE index on `pc_number >= 0` created. Output mode displays a divergence banner when the live database does not match `_DEFAULT_PRESETS`, with a one-click reset for users who did not intentionally customise their presets.

Five migration tests added: (1) convergence — migrated presets equal fresh presets for all legacy start versions; (2) `edge` storability after a pre-Phase-2 migration; (3) no duplicate `pc_number` after migrating a v1 database with an `ambient` segment; (4) user-modified rows with non-default PCs survive untouched; (5) the `0922b7c` reorder completes without an `IntegrityError`.

Live `library.db` migrated v9→v10: 9 tracks, 123 segments, 123 manual corrections intact, no duplicate `pc_number`, unique index present.

---

## Lessons

- **A seed that runs only at creation is not a source of truth — it is an
  initial condition.** `_DEFAULT_PRESETS` was treated as canonical in
  `CLAUDE.md`, in the report, and in review, while the code only ever applied it
  once per database.
- **A migration that updates zero rows should not be silent.** Both failures
  here are no-ops that reported success. A migration asserting its expected
  row-count would have surfaced this at the moment it was introduced.
- **When a defect can make two correct observations contradict each other, it
  will eventually corrupt the documentation too** — as it did on 2026-08-03.
  Recording *which database* an observation came from is part of recording the
  observation.
- **Making data user-editable changes what "correct" means for that data.** O4
  was a clear improvement, but it silently converted an unambiguous reconcile
  into an ambiguous one, and no schema change accompanied that shift in meaning.
