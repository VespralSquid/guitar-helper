# Lane A design decisions — ISSUE-006 (+ readiness review §B1)

**Stage 1 output.** Scope: master-plan Gate 1 in full — §3.1 atomic writes, §3.2 the v10
preset reconcile, §3.3 the five migration tests. `delete_track` is Wave 2 and is not here.

Everything below was checked against the code at `fix/issue-006-008-wave` and against
throwaway SQLite databases on Python 3.14.3. Where a claim was verified, the probe output is
quoted. **`library.db` was never opened for writing** — the live-DB result below comes from a
copy in the scratchpad.

---

## Decisions

### D1. Reconcile strategy — A, B or C (master-plan D1)

**Options:** A = `presets.user_modified` flag + reconcile + Output-mode divergence banner ·
B = hard-coded legacy-layout fingerprints · C = insert missing rows only, banner does the rest.

**Decision: A, unchanged.** No deviation from the standing recommendation.

**Why:** the missing distinction between "migration drift" and "deliberate user mapping" is
the actual defect (ISSUE-006 §"What makes the fix non-obvious"); A is the only option that
adds it to the schema, so every *future* reconcile is correct by construction. B re-creates
the exact discipline whose absence caused the issue. C leaves a silently wrong gain ramp on
any database whose owner ignores the banner — and the wrong-ramp population is the realistic
one. The known cost of A stands as written: every pre-v10 row carries `user_modified = 0`, so
a genuine pre-v10 customisation *is* overwritten; the banner (A2) plus the O4 preset table is
how the user gets it back.

**Consequence for the implementer:** implement A1 (column + reconcile in `_migrate_v9_to_v10`)
and A2 (banner + reset button in `ui/modes/output.py`). Both are in Wave 1; neither is
optional.

---

### D2. Where `user_modified` is set to 1

**Options:** (a) `save_preset` sets it unconditionally · (b) add a parameter to `save_preset` ·
(c) add a `user_modified` field to the `Preset` dataclass · (d) a separate marking method.

**Decision: (a) — `save_preset` sets `user_modified = 1` unconditionally, in both the INSERT
and the ON CONFLICT branch.**

**Why:** `save_preset` has exactly one production caller and it is the Output panel —
verified by grep: `ui/modes/output.py:193` (`_on_pc_edit`) and `:205` (`_on_name_edit`); the
only other callers are `tests/test_application.py:159` and `tests/test_midi_dispatcher.py:75`.
Every other write to `presets` in the codebase is raw SQL inside `db/schema.py` (`_seed`,
migrations), which correctly leaves the flag at 0. So "originates in the Output panel" and
"went through `save_preset`" are the same set, and no signature change is needed. (b) and (c)
are both blocked by orchestration §4 — `Preset`'s fields are frozen and existing method
signatures may not change.

**Consequence for the implementer:**
- Do **not** add `user_modified` to the `Preset` dataclass. Nothing outside `schema.py` needs
  to read it; the banner compares PC numbers, not flags.
- A name-only edit also sets the flag. That is intended: renaming "Metal" to "Rectifier Lead"
  is user intent about that row, and the reconcile rewrites `preset_name` as well as
  `pc_number`, so an unflagged rename would be silently reverted at the next reconcile.
- The "reset to defaults" button must **not** loop over `save_preset` — that would flag every
  row and freeze the table against all future reconciles. It calls
  `reset_presets_to_defaults()` (D6).

---

### D3. `_relocate_unmapped_presets` policy — surviving `ambient`, user-added presets

**Options:** (a) lowest free PC ≥ 0 · (b) quarantine at −1 (no dispatch) · (c) delete the row ·
(d) keep the original PC when it is free, quarantine at −1 when it is not.

**Decision: (d), with an explicit precedence order for who gets to keep a contested PC:
`user_modified = 1` > tone in `_DEFAULT_PRESETS` > everything else.** A displaced *default*
tone moves to the lowest free PC; a displaced *unmapped* tone goes to −1.

**Why:** (c) is out — `_migrate_v3_to_v4` already refuses to delete `ambient` while a segment
references it, and deleting it would break the FK. (a) sends a real Program Change for a tone
the app no longer knows, so the amp jumps to an unconfigured patch mid-song; (b) alone
gratuitously destroys a harmless pre-v10 custom row such as `lead` at PC 10, which collides
with nothing. (d) loses nothing that is not genuinely contested, and −1 is not an invented
state: `midi_dispatcher.tick()` treats `pc == -1` as `HOLD` and logs it
(`playback/midi_dispatcher.py:99-102`) — exactly the documented `other` behaviour. The
precedence order is what makes the function safe for a *future* reconcile, where
`user_modified = 1` rows exist and can contest a canonical PC; during v10 itself that set is
necessarily empty, because the column is created with `DEFAULT 0`.

**Verified** (candidate implementation, real `init_db` with `_CURRENT_VERSION = 10`):

```
v1 82542df + ambient segment   v=10 idx=True dup=[] CONVERGED
  [('ambient','Ambient',-1,0), ('other','Other',-1,0), ('clean','Clean',0,0),
   ('edge','Edge of Breakup',1,0), ('overdrive','Overdrive',2,0),
   ('crunch','Crunch',3,0), ('metal','Metal',4,0)]
  PRAGMA foreign_key_check -> []

v9 + pre-v10 custom 'lead'@10  v=10 idx=True dup=[] CONVERGED
  ... ('metal','Metal',4,0), ('lead','Lead Boost',10,0)      <- PC 10 kept

v9 + user pinned metal@2       v=10 idx=True dup=[]
  [('other',-1,0),('clean',0,0),('edge',1,0),('metal','Rectifier',2,1),
   ('crunch',3,0),('overdrive','Overdrive',4,0)]             <- overdrive displaced to 4,
                                                                user's metal@2 untouched
```

**Consequence for the implementer:** write `_relocate_unmapped_presets` as the single greedy
pass given in §Signatures, not as two ad-hoc passes. Two rows may not both be silently
dropped to −1 when only one is contested. A `sqlite3.IntegrityError` guard runs after it as a
last-line invariant check (see `reconcile_presets`).

---

### D4. B1's transaction boundary

**Options:** (a) `with self._conn:` inside each `SQLiteSegmentStore` method, callers unchanged ·
(b) expose the connection / a `transaction()` context manager to `EditorState` ·
(c) `with self._conn:` inside the store **plus** one new store method `apply_edits`.

**Decision: (c), the master plan's shape — with one refinement: `apply_edits` takes
`file_hash` as its first argument.**

Signature: `apply_edits(self, file_hash: str, updated: list[Segment], deleted_ids: list[int]) -> None`

**Why:** (b) leaks SQLite into Tier 3 and violates the "Qt main thread is the sole SQLite
owner, and it owns it through the store role" arrangement. (a) alone cannot fix
`EditorState.save()` (`ui/state/editor_state.py:220`), which is N `update_segment` calls plus
M `delete_segment` calls, each committing. The master plan §3.1 sketches
`apply_edits(updated, deleted_ids)` without `file_hash`; that shape cannot call
`ensure_calibration_copy` when `updated` is empty, and deriving the hash from
`updated[0].file_hash` makes correctness depend on an incidental property of `merge_run`
(it happens to always queue the surviving segment alongside the deletions). The method does
not exist yet, so no frozen contract is broken by adding the parameter — orchestration §4
freezes *existing* signatures. **This is the one place I depart from a written sketch;
flagging it rather than doing it silently.**

**Consequence for the implementer:** `EditorState.save()` becomes a single store call.
`save_segments` separately gets its own `with self._conn:` and loses its explicit `commit()`.

---

### D5. Does `ensure_calibration_copy` belong inside the `apply_edits` transaction?

**Decision: yes, inside.**

**Why:** it is the same logical operation — "commit an edit session, having first preserved
what the classifier originally said". Either placement is *semantically* survivable (the
snapshot is idempotent and its content is the pre-edit state whether or not the edits land),
but one atomic unit is one thing to test and one thing to reason about, it lets the Gate 4 H3
fix reuse `apply_edits` from the correction CLI and get the snapshot for free, and it makes
`EditorState.save()` literally one call as the plan intends.

**Verified** — the snapshot rolls back with the edits:

```
apply_edits failure -> FOREIGN KEY constraint failed
unchanged after rollback: True [('metal',True),('clean',True),('crunch',True)]
calibration rows after rollback: 0
```

**Consequence for the implementer:** `apply_edits` runs the calibration-copy INSERT…SELECT,
then the updates, then the deletes, all inside one `with self._conn:`.

---

### D6. How "reset to defaults" writes

**Options:** (a) `OutputMode` loops `save_preset` over the defaults · (b) a new store method ·
(c) hand `OutputMode` a connection.

**Decision: (b) — add `reset_presets_to_defaults(self) -> None` to `IPresetStore`,
implemented as `UPDATE presets SET user_modified = 0` followed by `reconcile_presets(conn)`,
in one transaction.**

**Why:** (a) is actively wrong — it would set `user_modified = 1` on every row and
permanently disarm every future reconcile, i.e. it would reintroduce ISSUE-006 the first time
a user clicked the button meant to fix ISSUE-006. (c) violates the connection-ownership rule.
Clearing the flags and re-running the same reconcile primitive is exactly what "reset" means,
and it reuses code the migration tests already cover.

**Consequence for the implementer:** `reconcile_presets` must be importable from
`db/schema.py` by `db/repository.py` (which already imports `utcnow` from there) — so it is
public (no leading underscore) and it does **not** open its own transaction; the caller owns
the transaction.

---

### D7. Both new ABC methods must be **concrete**, not `@abstractmethod`

**Decision: `ISegmentEditor.apply_edits` and `IPresetStore.reset_presets_to_defaults` are
concrete methods with default bodies. Neither is decorated `@abstractmethod`.**

**Why — this is a hard cross-lane constraint, not a style preference.**
`tests/test_pipeline.py:14` defines `class MockStore(ISegmentStore)`, a real subclass of the
ABC. Adding an abstract method makes it uninstantiable:

```
ABSTRACT ADDITION BREAKS EXISTING IMPLEMENTERS:
  Can't instantiate abstract class Impl2 without an implementation for abstract method 'b'
MockStore instantiates today: <tests.test_pipeline.MockStore object at ...>
```

`tests/test_pipeline.py` is **Lane C's file** (orchestration §3), and §7 forbids Lane A from
editing it. A concrete default keeps every existing implementer working with no cross-lane
edit. Verified that a concrete default on an ABC resolves through the subclass's overrides.

**Consequence for the implementer:**
- `ISegmentEditor.apply_edits` gets a *working* default that delegates to
  `ensure_calibration_copy` → `update_segment` → `delete_segment`, with a docstring stating
  that the default is **not** atomic and that any implementer owning a connection must
  override it. `SQLiteSegmentStore` overrides it.
- `IPresetStore.reset_presets_to_defaults` default body is `raise NotImplementedError` — a
  silent `pass` would make a broken reset button look like it worked.
- Do not touch `tests/test_pipeline.py`. If anything in this design appears to require it,
  stop and report per §7.

---

### D8. Where the UNIQUE partial index is created

**Options:** (a) in `_DDL` · (b) in the migration only · (c) in `_seed()` for fresh databases
and in `_migrate_v9_to_v10` for existing ones.

**Decision: (c). The index must NOT go in `_DDL`.**

**Why — a third trap, not recorded in ISSUE-006.** `init_db` runs
`conn.executescript(_DDL)` **before** it reads `schema_version`, i.e. before any migration.
An index in `_DDL` therefore executes against un-reconciled legacy data and aborts
`init_db` outright on precisely the population the migration exists to repair:

```
TRAP3 CONFIRMED: _DDL would abort init_db on a legacy dup DB
  -> UNIQUE constraint failed: presets.pc_number
```

(b) alone leaves a freshly created database without the index, because `_seed()` runs instead
of the migrations. Hence both paths, sharing one `_PRESET_PC_INDEX_DDL` constant.

Also verified: the partial predicate does what it must — three rows at −1 coexist; two rows at
0 are rejected.

**Consequence for the implementer:** add the index in `_seed()` and in
`_migrate_v9_to_v10`, from the same module constant. `user_modified` *does* go in `_DDL`
(`CREATE TABLE IF NOT EXISTS` makes it a no-op on legacy tables, which get the column from
`_add_column_if_missing`).

---

### D9. Making the v10 migration itself atomic

**Decision: `_migrate_v9_to_v10` opens an explicit `conn.execute("BEGIN")` and wraps its body
in `with conn:`. Do NOT restructure `_apply_migrations` to wrap every step.**

**Why:** `with conn:` does not begin a transaction — it only commits or rolls back at exit —
and Python's legacy transaction control starts an implicit transaction before DML but **not**
before DDL. So a bare `with conn:` leaves `ALTER TABLE ADD COLUMN` and `CREATE UNIQUE INDEX`
committed even when the block raises:

```
DDL ROLLBACK: failure -> FOREIGN KEY constraint failed
DDL ROLLBACK: presets cols = [... 'user_modified']   index present = True   <- survived
```

With an explicit `BEGIN` first, SQLite's transactional DDL applies and the rollback is total:

```
EXPLICIT BEGIN after rollback: cols=['tone_label','pc_number'] index=False
                               pcs=[('clean',0),('metal',4)]
```

A blanket wrapper over `_apply_migrations` would be *false* atomicity: migrations 7, 8 and 9
use `conn.executescript`, which silently commits any pending transaction —

```
in_transaction before executescript: True
executescript inside BEGIN: no error; in_transaction now: False
rows after rollback: [(1,), (2,)]      <- the pre-script INSERT was committed
```

— so the guarantee would hold for v10 and quietly not hold for the steps that use
`executescript`. Claiming atomicity where it does not exist is worse than not claiming it.

**Verified end-to-end** — injecting a failure late in v10 leaves the database exactly at v9:

```
T-atomic raised: simulated failure late in v10
T-atomic version: 9
T-atomic cols: ['tone_label','preset_name','pc_number']     <- no user_modified
T-atomic index: None
T-atomic presets: [('other',-1),('clean',0),('crunch',1),('metal',2),('edge',3),('overdrive',4)]
```

**Consequence for the implementer:** the `BEGIN` lives inside `_migrate_v9_to_v10` only.
`_apply_migrations`' loop body is unchanged. Because `_apply_migrations` commits after every
step, no transaction is open when v10 issues its `BEGIN`.

---

### D10. Reconcile once at v10, or on every `init_db`?

**Options:** (a) v10 migration only · (b) call `reconcile_presets` unconditionally from
`init_db` on every startup.

**Decision: (a), plus a pinning canary test on `_DEFAULT_PRESETS`.**

**Why:** (b) is genuinely tempting — it would make the constant a converging target rather
than an initial condition, which is the issue's own stated lesson, and the `user_modified`
flag makes it *safe*. It was rejected because it writes to the live 123-correction database on
every launch to fix a problem that occurs a handful of times per project lifetime, and because
the integration gate (§6 step 7) verifies a one-time migration, not a startup behaviour.
The discipline gap is closed instead by a test that pins `_DEFAULT_PRESETS` to a literal, so
the next person to change the list gets a red test pointing at the migration requirement.
Note that `_migrate_v9_to_v10` reads `_DEFAULT_PRESETS` at call time, so it converges any
database at ≤ v9 onto whatever the constant currently says; the residual exposure is only
databases already at v10. Recorded as a live option for a future gate.

**Consequence for the implementer:** ship the canary test (test 6 below) with the failure
message naming `reconcile_presets` and `_CURRENT_VERSION`.

---

### D11. What the divergence banner compares, and where the comparison lives

**Decision:** compare `pc_number` only, per tone in `_DEFAULT_PRESETS`, in a module-level
Qt-free function in `ui/modes/output.py`, using a new public `default_preset_map()` in
`db/schema.py`.

**Why:** comparing `preset_name` too would leave the banner permanently on for any user who
labelled a row with their real amp patch — which is the panel's advertised purpose — and a
banner that is always on is a banner nobody reads. A tone *missing* from the table cannot
happen after v10, and an extra tone quarantined at −1 is a legitimate outcome of D3, so
neither is flagged. `default_preset_map()` exists so `ui/` never reaches into
`_DEFAULT_PRESETS` and never hardcodes a PC. The function is module-level (like the existing
`format_event` / `format_position`, which `tests/test_output_mode.py` already imports
directly) so the comparison is testable without constructing the widget.

**Consequence for the implementer:** banner text names the diverging tones and both PCs.
`reset_presets_to_defaults` → `refresh_presets()` → `presetsChanged.emit()` so the running
dispatcher picks up the new map on the main thread.

---

## Signatures / schema

### `db/schema.py`

```python
_CURRENT_VERSION = 10

# _DEFAULT_PRESETS is UNCHANGED — do not edit it in this lane.

_PARK_OFFSET = 1000
_NO_DISPATCH_PC = -1

_PRESET_PC_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_presets_pc "
    "ON presets(pc_number) WHERE pc_number >= 0"
)
```

`_DDL`, presets table only — add the column, and **nothing else**:

```sql
CREATE TABLE IF NOT EXISTS presets (
    tone_label    TEXT PRIMARY KEY,
    preset_name   TEXT NOT NULL,
    pc_number     INTEGER NOT NULL,
    user_modified INTEGER NOT NULL DEFAULT 0
);
```

```python
def default_preset_map() -> dict[str, int]:
    """Canonical tone -> PC. The UI reads this instead of hardcoding numbers."""
    return {tone: pc for tone, _name, pc in _DEFAULT_PRESETS}


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO presets(tone_label, preset_name, pc_number) VALUES (?,?,?)",
        _DEFAULT_PRESETS,
    )
    conn.execute(_PRESET_PC_INDEX_DDL)


def reconcile_presets(conn: sqlite3.Connection) -> None:
    """Move every non-user-modified preset row onto _DEFAULT_PRESETS.

    The caller owns the transaction. Rows with user_modified = 1 are never
    touched; rows for tones the app no longer knows keep their PC if it is free
    and are parked at no-dispatch if it is not.
    """
    conn.executemany(
        "INSERT OR IGNORE INTO presets(tone_label, preset_name, pc_number) VALUES (?,?,?)",
        _DEFAULT_PRESETS,
    )
    # SQLite enforces UNIQUE row-by-row inside an UPDATE, so a reorder collides
    # mid-statement even when the end state is valid. Park out of range first.
    conn.execute(
        "UPDATE presets SET pc_number = pc_number + ? "
        "WHERE user_modified = 0 AND pc_number >= 0",
        (_PARK_OFFSET,),
    )
    for tone, name, pc in _DEFAULT_PRESETS:
        conn.execute(
            "UPDATE presets SET preset_name = ?, pc_number = ? "
            "WHERE tone_label = ? AND user_modified = 0",
            (name, pc, tone),
        )
    _relocate_unmapped_presets(conn)

    dupes = [
        r[0]
        for r in conn.execute(
            "SELECT pc_number FROM presets WHERE pc_number >= 0 "
            "GROUP BY pc_number HAVING COUNT(*) > 1"
        )
    ]
    if dupes:
        raise sqlite3.IntegrityError(
            f"preset reconcile left duplicate pc_number(s): {dupes}"
        )


def _relocate_unmapped_presets(conn: sqlite3.Connection) -> None:
    """Unpark leftovers and break every remaining pc_number tie.

    Precedence: a user-modified row keeps its PC; then a tone in
    _DEFAULT_PRESETS; anything else yields. A displaced default takes the
    lowest free PC, a displaced leftover goes to no-dispatch.
    """
    default_tones = {tone for tone, _name, _pc in _DEFAULT_PRESETS}
    rows = conn.execute(
        "SELECT tone_label, pc_number, user_modified FROM presets"
    ).fetchall()

    def rank(row: tuple) -> tuple:
        tone, _pc, user_modified = row
        return (0 if user_modified else 1, 0 if tone in default_tones else 1, tone)

    taken: set[int] = set()
    moves: list[tuple[int, str]] = []
    for tone, pc, _user_modified in sorted(rows, key=rank):
        if pc < 0:
            continue
        parked = pc >= _PARK_OFFSET
        wanted = pc - _PARK_OFFSET if parked else pc
        if wanted in taken:
            wanted = _NO_DISPATCH_PC if parked else _lowest_free_pc(taken)
        if wanted >= 0:
            taken.add(wanted)
        if wanted != pc:
            moves.append((wanted, tone))
    if moves:
        conn.executemany(
            "UPDATE presets SET pc_number = ? WHERE tone_label = ?", moves
        )


def _lowest_free_pc(taken: set[int]) -> int:
    pc = 0
    while pc in taken:
        pc += 1
    return pc


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    # Explicit BEGIN, not just `with conn:` — sqlite3 opens an implicit
    # transaction before DML but not before DDL, so ALTER TABLE and
    # CREATE INDEX would otherwise survive a rollback.
    conn.execute("BEGIN")
    with conn:
        _add_column_if_missing(
            conn, "presets", "user_modified", "INTEGER NOT NULL DEFAULT 0"
        )
        reconcile_presets(conn)
        conn.execute(_PRESET_PC_INDEX_DDL)


_MIGRATIONS = { ..., 10: _migrate_v9_to_v10 }
```

`_apply_migrations` and `init_db` are otherwise unchanged.

### `db/interfaces.py` — two additive **concrete** methods

```python
class ISegmentEditor(ABC):
    ...
    def apply_edits(
        self, file_hash: str, updated: list[Segment], deleted_ids: list[int]
    ) -> None:
        """Persist one edit session — the calibration snapshot, the updates and
        the deletions — as a single unit.

        This default is a convenience for in-memory implementers and is NOT
        atomic. Any implementer that owns a transaction must override it.
        """
        self.ensure_calibration_copy(file_hash)
        for segment in updated:
            self.update_segment(segment)
        for segment_id in deleted_ids:
            self.delete_segment(segment_id)


class IPresetStore(ABC):
    ...
    def reset_presets_to_defaults(self) -> None:
        """Clear every user_modified flag and re-derive the table from the
        seeded defaults. Backs the Output-mode divergence banner."""
        raise NotImplementedError
```

`Segment` / `Preset` / `Track` dataclasses: **unchanged**. No `user_modified` field.

### `db/repository.py`

Hoist the three statements so the single-row methods and `apply_edits` share them:

```python
_UPDATE_SEGMENT_SQL = """
    UPDATE segments
    SET start_ms = ?, end_ms = ?, tone_label = ?,
        confidence = ?, manually_corrected = ?
    WHERE id = ?
"""

_DELETE_SEGMENT_SQL = "DELETE FROM segments WHERE id = ?"

_CALIBRATION_COPY_SQL = """
    INSERT INTO segments_calibration
        (file_hash, start_ms, end_ms, tone_label, confidence, manually_corrected)
    SELECT file_hash, start_ms, end_ms, tone_label, confidence, manually_corrected
    FROM segments
    WHERE file_hash = ?
      AND NOT EXISTS (SELECT 1 FROM segments_calibration WHERE file_hash = ?)
"""
```

```python
def save_segments(self, file_hash: str, segments: list[Segment]) -> None:
    with self._conn:                       # rolls back the DELETE if any row fails
        self._conn.execute("DELETE FROM segments WHERE file_hash = ?", (file_hash,))
        self._conn.executemany(<existing INSERT>, [<existing tuple build>])
    # explicit commit() removed

def apply_edits(
    self, file_hash: str, updated: list[Segment], deleted_ids: list[int]
) -> None:
    with self._conn:
        self._conn.execute(_CALIBRATION_COPY_SQL, (file_hash, file_hash))
        self._conn.executemany(
            _UPDATE_SEGMENT_SQL,
            [
                (s.start_ms, s.end_ms, s.tone_label, s.confidence,
                 int(s.manually_corrected), s.id)
                for s in updated
            ],
        )
        self._conn.executemany(_DELETE_SEGMENT_SQL, [(i,) for i in deleted_ids])

def save_preset(self, preset: Preset) -> None:
    self._conn.execute(
        """
        INSERT INTO presets(tone_label, preset_name, pc_number, user_modified)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(tone_label) DO UPDATE
            SET preset_name   = excluded.preset_name,
                pc_number     = excluded.pc_number,
                user_modified = 1
        """,
        (preset.tone_label, preset.preset_name, preset.pc_number),
    )
    self._conn.commit()

def reset_presets_to_defaults(self) -> None:
    with self._conn:
        self._conn.execute("UPDATE presets SET user_modified = 0")
        reconcile_presets(self._conn)
```

`update_segment`, `delete_segment` and `ensure_calibration_copy` keep their existing
per-call `commit()` — they remain the single-row API and the correction CLI still uses
`update_segment`.

### `ui/state/editor_state.py`

```python
def save(self) -> None:
    if not self.dirty:
        return
    self._store.apply_edits(
        self._file_hash, list(self._pending.values()), list(self._deleted_ids)
    )
    self._pending.clear()
    self._deleted_ids.clear()
    self._emit(StateEvent("dirty"))
    self._emit(StateEvent("saved"))
```

If `apply_edits` raises, the exception propagates untouched and `_pending` / `_deleted_ids`
are **not** cleared, so the session stays dirty and the user can retry or `discard()`. Do not
add a `try/except` here — dialog handling is Gate 4 H2.

### `ui/modes/output.py`

```python
def preset_divergences(
    presets: Sequence[Preset], defaults: Mapping[str, int]
) -> list[tuple[str, int, int]]:
    """(tone, live_pc, default_pc) for every default tone whose PC has drifted."""


def format_divergence_banner(divergences: Sequence[tuple[str, int, int]]) -> str:
    """One line naming the diverging tones and both PC values."""
```

Widget additions: `self.divergence_banner: QLabel` (hidden when empty) and
`self.reset_presets_button: QPushButton("Reset to defaults")` inside `preset_box`,
beside `test_send_button`.

`refresh_presets()` gains, after `set_presets`:

```python
diverged = preset_divergences(self.preset_model.presets, default_preset_map())
self.divergence_banner.setText(format_divergence_banner(diverged))
self.divergence_banner.setVisible(bool(diverged))
self.reset_presets_button.setVisible(bool(diverged))
```

`_on_reset_presets()` → `self._store.reset_presets_to_defaults()`, then
`self.refresh_presets()`, then `self.presetsChanged.emit()`, then a `statusMessage`.

---

## Sequencing

1. **B1 first, in one commit-sized unit:** `save_segments` transaction, `apply_edits` on
   `ISegmentEditor` (concrete default) and on `SQLiteSegmentStore`, `EditorState.save()`.
   Tests in `test_repository.py` and `test_editor_state.py`. ISSUE-006's own ordering note is
   explicit about this: v10 makes previously-rejected `edge` analyses succeed, which is
   exactly when a partial-write failure would destroy existing segments.
2. **Schema v10 second:** `_DDL` column, `_PRESET_PC_INDEX_DDL`, `default_preset_map`,
   `reconcile_presets`, `_relocate_unmapped_presets`, `_lowest_free_pc`,
   `_migrate_v9_to_v10`, `_CURRENT_VERSION = 10`, `_seed` index. Then `save_preset`'s flag.
   Tests in `test_schema.py`.
3. **Output mode last:** it consumes `default_preset_map()` and `reset_presets_to_defaults()`,
   so both must exist. Tests in `test_output_mode.py`.

Run `python -m ruff check` on Lane A's files only (§7). Do not commit (§7).

---

## What this does not cover

- **A pre-v10 customisation is overwritten.** Stated in ISSUE-006 §A and accepted as D1's
  price. The banner cannot recover it — after v10 the table simply *is* the defaults, so no
  banner appears and the user gets no notification that anything changed. The banner protects
  future drift, not this one-time reconcile. The live `library.db` is unaffected (below).
- **Databases already at v10 will diverge again** if `_DEFAULT_PRESETS` changes without a v11
  that calls `reconcile_presets`. Mitigated by a canary test, not by the code (D10).
- **`_relocate_unmapped_presets` is not a validator.** A leftover row at PC 500 keeps PC 500;
  the MIDI 0–127 range is enforced by `validate_pc` at edit time only.
- **`ambient` quarantined at −1 cannot be moved back to −1** through the UI once given a real
  PC, because `validate_pc` reserves −1 for `other`. Acceptable; −1 is a quarantine, not a
  user-selectable state.
- **`correction/cli.py` still bypasses `ensure_calibration_copy`** (review H3) and still
  commits per row. It is not Lane A's file and the fix is Gate 4. Reported here, not fixed.
- **`delete_track`** — Wave 2, per orchestration §3.
- **The banner does not flag extra or missing tones**, only PC drift on known tones (D11).
- **Concurrency.** Everything here assumes the Qt main thread is the sole SQLite owner. None
  of it is safe under a second connection, and none of it tries to be.

### Live-database expectation (for gate §6 step 7)

Dry-run against a **copy** of `library.db` in the scratchpad. The live table already matches
the defaults, so v10 changes no content — it only adds the column and the index:

```
version: 10
    ('other',     'Other',           -1, 0)
    ('clean',     'Clean',            0, 0)
    ('edge',      'Edge of Breakup',  1, 0)
    ('overdrive', 'Overdrive',        2, 0)
    ('crunch',    'Crunch',           3, 0)
    ('metal',     'Metal',            4, 0)
tracks/segments/corrections: 9 123 123
calibration rows: 42
dupes: []      index: ('idx_presets_pc',)      fk_check: []
```

If the orchestrator's live run reports anything other than 9 / 123 / 123 and this exact
preset table, **stop and restore from `library.db.bak-2026-08-18`.**

---

## Verification checklist

### ISSUE-006 §Test plan — the five required tests (`tests/test_schema.py`)

Build legacy fixtures locally in `test_schema.py` (nobody edits `conftest.py`, §4). Extend the
existing `_make_legacy_db` / `_make_v3_db` helpers rather than inventing a third shape.

1. **Convergence.** Parametrise over the four historical layouts and assert the migrated
   `presets` rows restricted to tones in `_DEFAULT_PRESETS` equal a freshly created
   database's, as `(tone_label, preset_name, pc_number, user_modified)` tuples ordered by
   `pc_number`:
   - `82542df` v1: `other -1, clean 0, crunch 1, metal 3, ambient 4`
   - `d4a732e` v1: `other -1, clean 0, crunch 1, metal 2, ambient 3`
   - `3ee7d86` v1: the above `+ edge 4`
   - `0922b7c` v6: `other -1, clean 0, crunch 1, metal 2, edge 3, overdrive 4`

   Expected for all four: `[('other','Other',-1,0), ('clean','Clean',0,0),
   ('edge','Edge of Breakup',1,0), ('overdrive','Overdrive',2,0), ('crunch','Crunch',3,0),
   ('metal','Metal',4,0)]`. Also assert `schema_version == 10`.

2. **`edge` storability.** Migrate an `82542df` v1 database, then
   `INSERT INTO segments(...) VALUES (...,'edge',...)` and assert it commits and reads back —
   no `IntegrityError`.

3. **Uniqueness with a surviving `ambient`.** Migrate a v1 database that holds an `ambient`
   segment. Assert: `SELECT pc_number, COUNT(*) ... WHERE pc_number >= 0 GROUP BY pc_number
   HAVING COUNT(*) > 1` returns `[]`; `idx_presets_pc` exists in `sqlite_master`;
   the `ambient` row still exists with `pc_number == -1`; `PRAGMA foreign_key_check` returns
   `[]`. Then assert inserting a second row at an occupied PC ≥ 0 raises `IntegrityError`
   while a second row at −1 does not.

4. **Intent preserved.** Build a v9 database that already carries the `user_modified` column
   with `crunch` flagged 1 at `pc_number = 9`. After migration assert `crunch` is still
   `(9, user_modified=1)` and its `preset_name` is unchanged, while every unflagged default
   tone sits at its canonical PC. Add the clash variant: `metal` flagged 1 at PC 2 with name
   `'Rectifier'` survives untouched, `overdrive` is displaced to PC 4, and there are no
   duplicates.

5. **Trap-1 regression.** Migrating the exact `0922b7c` ordering completes without raising.
   Assert on the *absence* of `sqlite3.IntegrityError` explicitly (call `init_db` outside a
   `pytest.raises`), and on the final ordering.

### Additional tests this design requires

6. **`_DEFAULT_PRESETS` canary.** Assert the literal list equals the current six tuples, with
   an assertion message telling the reader to bump `_CURRENT_VERSION` and add a migration
   calling `reconcile_presets` if they are changing it deliberately.
7. **Fresh database has the index and the column.** `idx_presets_pc` in `sqlite_master`;
   `user_modified` in `PRAGMA table_info(presets)`; all six rows at `user_modified = 0`.
8. **v10 is idempotent.** Run `_migrate_v9_to_v10` a second time against an already-migrated
   database and assert the preset rows are identical (this is what makes a re-run after a
   crashed `schema_version` update safe).
9. **v10 is atomic.** Monkeypatch `reconcile_presets` (or `_relocate_unmapped_presets`) to
   raise, run `init_db` against a v9 fixture, assert it propagates, then reopen with a plain
   `sqlite3.connect` and assert `schema_version == 9`, `user_modified` absent from
   `PRAGMA table_info(presets)`, `idx_presets_pc` absent, and the preset rows unchanged.

### B1 — `tests/test_repository.py`

10. **`save_segments` rolls back.** Seed one `manually_corrected = 1` `metal` segment, call
    `save_segments` with `[valid, Segment(tone_label='bogus')]`, assert `IntegrityError`, then
    assert `get_segments` still returns exactly the original segment with
    `manually_corrected is True`. Then perform an unrelated write (`save_track` or
    `set_setting`) and assert again — the old bug only became visible at the *next* commit.
11. **`apply_edits` rolls back the whole session.** Three segments; call `apply_edits` with
    one good update, one update carrying a bogus `tone_label`, and one deletion. Assert
    `IntegrityError`, all three segments unchanged, and
    `SELECT COUNT(*) FROM segments_calibration == 0` — the snapshot must roll back too.
12. **`apply_edits` happy path** writes updates and deletions and takes the calibration
    snapshot in one call; a second `apply_edits` does **not** overwrite the first snapshot.
13. **`save_preset` sets the flag** (`SELECT user_modified` == 1 for the written tone, and
    still 0 for every other tone). Assert against raw SQL, since `Preset` does not expose it.
14. **`reset_presets_to_defaults`** restores PCs after a `save_preset` remap, clears
    `user_modified` on every row, and leaves no duplicate PC.

### B1 — `tests/test_editor_state.py`

15. **`save()` makes exactly one store call.** A fake store records calls; after two relabels
    and a merge, assert `apply_edits` was called once with the expected `file_hash`, the
    pending segments, and the deleted ids — and that `update_segment` / `delete_segment` /
    `ensure_calibration_copy` were **not** called directly.
16. **A failing save leaves the session dirty.** Fake store's `apply_edits` raises; assert the
    exception propagates, `state.dirty is True`, no `"saved"` event was emitted, and a
    subsequent `discard()` restores the store's contents.
17. The existing `test_save_preserves_original_in_calibration_copy_across_merge` and
    `test_save_flushes_pending_updates_and_clears_dirty` must still pass unmodified — they are
    the regression net for the new code path.

### `tests/test_output_mode.py`

18. **No banner on a fresh store.** `output.divergence_banner.isVisible()` is False and
    `preset_divergences(store.get_presets(), default_preset_map()) == []`.
19. **Banner appears on drift.** Remap a tone via `save_preset` directly, `refresh_presets()`,
    assert the banner is visible and its text names the tone and both PC numbers.
20. **Reset clears it.** Click `reset_presets_button`; assert the store's PCs equal
    `default_preset_map()`, the banner is hidden, and `presetsChanged` was emitted.
21. **`preset_divergences` is unit-tested directly**, without a widget, including the case
    where only `preset_name` differs — which must yield `[]`.
22. Existing behaviour that must not regress: `_edit(output, "clean", _PC_COL, "4")` (metal's
    PC, `tests/test_output_mode.py:69`) is still rejected by `validate_pc` *before* reaching
    the store, so the new UNIQUE index is never the thing that raises. Assert the rejection
    still surfaces as a `statusMessage`, not an `IntegrityError`.

### Suite-level

23. `python -m pytest tests/test_schema.py tests/test_repository.py tests/test_editor_state.py
    tests/test_output_mode.py` green, then the whole suite: 370 tests were green at baseline,
    the count must rise and nothing else may fail. In particular `tests/test_pipeline.py`
    (`MockStore(ISegmentStore)`), `tests/test_application.py:159` and
    `tests/test_midi_dispatcher.py:75` must be untouched and still pass — they are the canary
    for D7 and for the UNIQUE index.
