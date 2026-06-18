import sqlite3

conn = sqlite3.connect("library.db")
conn.row_factory = sqlite3.Row

def fmt(ms):
    m, rem = divmod(ms, 60000)
    s, milli = divmod(rem, 1000)
    return f"{m}:{s:02d}.{milli:03d}"

print("=== TRACKS ===")
tracks = conn.execute("SELECT * FROM tracks").fetchall()
if tracks:
    for r in tracks:
        mins, secs = divmod(r["duration_ms"] // 1000, 60)
        print(f"  {r['file_hash'][:12]}...  {r['filename']}  ({r['title']} / {r['artist']})  {mins}:{secs:02d}  analysed={r['analysed_at'][:19]}")
else:
    print("  (empty)")

print()
print("=== SEGMENTS ===")
if tracks:
    for t in tracks:
        segs = conn.execute(
            "SELECT start_ms, end_ms, tone_label, confidence, manually_corrected FROM segments WHERE file_hash=? ORDER BY start_ms",
            (t["file_hash"],)
        ).fetchall()
        print(f"  {t['filename']} ({len(segs)} segments):")
        for i, s in enumerate(segs):
            corr = " [corrected]" if s["manually_corrected"] else ""
            print(f"    [{i}] {fmt(s['start_ms'])} -> {fmt(s['end_ms'])}  |  {s['tone_label']:<8}  |  conf={s['confidence']:.2f}{corr}")
else:
    print("  (empty)")

print()
print("=== PRESETS ===")
for r in conn.execute("SELECT tone_label, preset_name, pc_number FROM presets ORDER BY pc_number").fetchall():
    pc = str(r["pc_number"]) if r["pc_number"] >= 0 else "no dispatch"
    print(f"  {r['tone_label']:<8}  {r['preset_name']:<10}  PC={pc}")

conn.close()
