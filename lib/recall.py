#!/usr/bin/env python3
"""recall.py — local FTS5 memory for a role (sc recall, sc _recall hook).

    recall.py index [ROLE_DIR]      (re)index memory/, notes/ and the role's Claude
                                 session transcripts into ROLE_DIR/.recall.sqlite
    recall.py query [ROLE_DIR]      read a Claude Code UserPromptSubmit hook payload
                                 on stdin, print the best snippets as context

ROLE_DIR defaults to $CLAUDE_PROJECT_DIR or the cwd. The DB is derived data:
gitignored, rebuilt per machine. Stdlib only.
"""
import json, os, re, sqlite3, sys, time
from pathlib import Path

# ponytail: plain FTS5 keyword match, no stemming/synonyms (Pi-hole vs pihole misses); add a trigram tokenizer if recall feels blind
MAX_HITS, SNIPPET = 3, 400

def role_dir(argv):
    return Path(argv[2] if len(argv) > 2 else os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()

def transcript_dir(role):
    enc = re.sub(r"[/.]", "-", str(role))
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects" / enc

def db(role):
    con = sqlite3.connect(role / ".recall.sqlite")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS doc USING fts5(src, ts UNINDEXED, body, tokenize='unicode61')")
    con.execute("CREATE TABLE IF NOT EXISTS seen(src PRIMARY KEY, mtime REAL)")
    return con

def chunks(text, size=1200):
    paras, buf = re.split(r"\n\s*\n", text), ""
    for p in paras:
        if len(buf) + len(p) > size and buf:
            yield buf.strip(); buf = ""
        buf += p + "\n\n"
    if buf.strip():
        yield buf.strip()

def transcript_text(path):
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message") or {}
        if d.get("type") not in ("user", "assistant") or not isinstance(m.get("content"), (str, list)):
            continue
        parts = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
        text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text").strip()
        if text and not text.startswith("<") and len(text) > 20:
            out.append(("U: " if d["type"] == "user" else "A: ") + text[:2000])
    return "\n\n".join(out)

def index(role):
    con = db(role)
    files = [p for sub in ("memory", "notes") for p in (role / sub).rglob("*.md")]
    tdir = transcript_dir(role)
    files += list(tdir.glob("*.jsonl")) if tdir.is_dir() else []
    n = 0
    # forget files that were deleted since the last run
    for (src,) in con.execute("SELECT src FROM seen").fetchall():
        if not Path(src).exists():
            con.execute("DELETE FROM doc WHERE src=?", (src,))
            con.execute("DELETE FROM seen WHERE src=?", (src,))
            n += 1
    for p in files:
        src, mtime = str(p), p.stat().st_mtime
        row = con.execute("SELECT mtime FROM seen WHERE src=?", (src,)).fetchone()
        if row and row[0] >= mtime:
            continue
        text = transcript_text(p) if p.suffix == ".jsonl" else p.read_text(errors="replace")
        con.execute("DELETE FROM doc WHERE src=?", (src,))
        for c in chunks(text):
            con.execute("INSERT INTO doc(src, ts, body) VALUES (?,?,?)", (src, mtime, c))
        con.execute("INSERT OR REPLACE INTO seen(src, mtime) VALUES (?,?)", (src, mtime))
        n += 1
    con.commit()
    return n

def terms(prompt):
    words = re.findall(r"[\wäöüÄÖÜß][\wäöüÄÖÜß.-]{2,}", prompt)
    stop = {"und", "der", "die", "das", "the", "and", "for", "mit", "von", "ich", "wir", "ist", "nicht", "auch", "mal", "bitte", "kannst", "was", "wie", "ein", "eine", "den", "dem", "auf", "zu", "im", "in", "ob", "oder"}
    return [w.lower().strip(".-") for w in words if w.lower() not in stop][:12]

def query(role):
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    prompt = payload.get("prompt") or ""
    t = terms(prompt)
    if not t:
        return
    con = db(role)
    try:
        index(role)
    except Exception:
        pass
    q = " OR ".join(f'"{w}"' for w in t)
    rows = con.execute("SELECT src, ts, snippet(doc, 2, '', '', '…', 60), bm25(doc) FROM doc WHERE doc MATCH ? ORDER BY bm25(doc) LIMIT ?", (q, MAX_HITS)).fetchall()
    if not rows:
        return
    lines = ["Local memory of this role (FTS hits for the prompt, hints only):"]
    for src, ts, snip, _ in rows:
        when = time.strftime("%Y-%m-%d", time.localtime(ts or 0))
        name = Path(src).name if src.endswith(".jsonl") else str(Path(src).relative_to(role))
        lines.append(f"- [{when} {name}] {snip[:SNIPPET]}")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "\n".join(lines)}}))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "query"
    r = role_dir(sys.argv)
    if cmd == "index":
        print(f"indexed {index(r)} changed file(s) into {r/'.recall.sqlite'}")
    elif cmd == "query":
        query(r)
    else:
        sys.exit(__doc__)
