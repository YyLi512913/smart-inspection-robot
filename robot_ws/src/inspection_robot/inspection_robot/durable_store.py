"""SQLite journal shared by ROS producers and MQTT bridge, one file per robot.

The spool is acknowledged by the backend application, not by the MQTT broker.
Disk errors intentionally propagate: continuing without the journal is unsafe.
"""
from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path
from uuid import uuid4


def default_path(robot_id="robot1"):
    return str(Path.home() / ".local" / "state" / "inspection_robot" / f"{robot_id}.sqlite3")


class DurableStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS commands(
                    command_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL,
                    runtime TEXT, last_event TEXT);
                CREATE TABLE IF NOT EXISTS canceled_tasks(task_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS snapshot_requests(
                    request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS spool(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            columns = [row[1] for row in conn.execute("PRAGMA table_info(snapshot_requests)")]
            if "result" not in columns:
                conn.execute("ALTER TABLE snapshot_requests ADD COLUMN result TEXT")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def command(self, command_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
            return dict(row) if row else None

    def active(self):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM commands WHERE state='active' LIMIT 1").fetchone()
            return dict(row) if row else None

    def begin(self, payload):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM commands WHERE command_id=?", (payload["command_id"],)).fetchone()
            if old:
                stored = json.loads(old["payload"])
                if stored != payload:
                    return "conflict"
                return "duplicate"
            if conn.execute("SELECT 1 FROM canceled_tasks WHERE task_id=?", (payload["task_id"],)).fetchone():
                conn.execute("INSERT INTO commands(command_id,task_id,payload,state) VALUES(?,?,?,'rejected')",
                             (payload["command_id"], payload["task_id"], json.dumps(payload, sort_keys=True)))
                return "canceled"
            if conn.execute("SELECT 1 FROM commands WHERE state='active'").fetchone():
                conn.execute("INSERT INTO commands(command_id,task_id,payload,state) VALUES(?,?,?,'rejected')",
                             (payload["command_id"], payload["task_id"], json.dumps(payload, sort_keys=True)))
                return "busy"
            conn.execute("INSERT INTO commands(command_id,task_id,payload,state) VALUES(?,?,?,'active')",
                         (payload["command_id"], payload["task_id"], json.dumps(payload, sort_keys=True)))
            return "new"

    def save_runtime(self, command_id, runtime):
        with self.connect() as conn:
            conn.execute("UPDATE commands SET runtime=? WHERE command_id=?",
                         (json.dumps(runtime), command_id))

    def emit(self, payload, state=None, snapshot=None):
        """Persist state/event/outgoing message in one SQLite transaction."""
        payload = {**payload, "event_id": payload.get("event_id") or uuid4().hex}
        serialized = json.dumps(payload, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO spool(event_id,kind,payload) VALUES(?, 'event', ?)",
                         (payload["event_id"], serialized))
            conn.execute("UPDATE commands SET last_event=? WHERE command_id=?",
                         (serialized, payload.get("command_id")))
            if snapshot:
                conn.execute("INSERT OR IGNORE INTO snapshot_requests(request_id,payload) VALUES(?,?)",
                             (f"{snapshot['command_id']}:{snapshot['point_seq']}", json.dumps(snapshot)))
            if state:
                conn.execute("UPDATE commands SET state=? WHERE command_id=?",
                             (state, payload.get("command_id")))
        return payload

    def replay(self, command_id):
        cmd = self.command(command_id)
        if cmd and cmd["last_event"]:
            self.enqueue("event", json.loads(cmd["last_event"]))

    def enqueue(self, kind, payload):
        if kind not in {"event", "result"}:
            raise ValueError("Invalid spool kind")
        payload = {**payload, "event_id": payload.get("event_id") or uuid4().hex}
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO spool(event_id,kind,payload) VALUES(?,?,?)",
                         (payload["event_id"], kind, json.dumps(payload, ensure_ascii=False)))
        return payload

    def next_message(self):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM spool ORDER BY id LIMIT 1").fetchone()
            return dict(row) if row else None

    def acknowledge(self, event_id):
        with self.connect() as conn:
            conn.execute("DELETE FROM spool WHERE event_id=?", (event_id,))

    def cancel(self, payload):
        """Tombstone prevents delayed patrol commands from reviving a canceled task."""
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO canceled_tasks(task_id) VALUES(?)", (payload["task_id"],))
            conn.execute("UPDATE commands SET state='canceled' WHERE task_id=?", (payload["task_id"],))

    def request_snapshot(self, payload):
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO snapshot_requests(request_id,payload) VALUES(?,?)",
                         (f"{payload['command_id']}:{payload['point_seq']}", json.dumps(payload)))

    def next_snapshot(self):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM snapshot_requests WHERE processed=0 ORDER BY rowid LIMIT 1").fetchone()
            return json.loads(row['payload']) if row else None

    def finish_snapshot(self, request, result):
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO spool(event_id,kind,payload) VALUES(?,'result',?)",
                         (result['event_id'], json.dumps(result, ensure_ascii=False)))
            conn.execute("UPDATE snapshot_requests SET processed=1, result=? WHERE request_id=?",
                         (json.dumps(result, ensure_ascii=False), f"{request['command_id']}:{request['point_seq']}",))

    def snapshot_result(self, command_id, seq):
        with self.connect() as conn:
            row = conn.execute("SELECT result FROM snapshot_requests WHERE request_id=? AND processed=1",
                               (f"{command_id}:{seq}",)).fetchone()
            return json.loads(row["result"]) if row and row["result"] else None

    def acquire_execution_lock(self):
        """One executor process per journal; ROS node names do not provide exclusion."""
        import fcntl
        lock = open(self.path + ".executor.lock", "a+")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock.close()
            raise RuntimeError("该机器人已有执行进程，拒绝重复启动") from exc
        return lock
