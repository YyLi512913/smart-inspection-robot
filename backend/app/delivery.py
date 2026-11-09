"""Transactional command outbox. MQTT PUBACK is not robot acceptance."""
import json
import logging
from uuid import uuid4

from . import db

logger = logging.getLogger(__name__)
RETRY_SECONDS = 5
_publisher = None


def set_publisher(fn):
    global _publisher
    _publisher = fn


def enqueue(conn, robot_id, task_no, action, points=None):
    command_id = uuid4().hex
    payload = {"protocol_version": 2, "command_id": command_id,
               "task_id": task_no, "action": action}
    if points is not None:
        payload["points"] = points
    conn.execute(db.text(
        "INSERT INTO mqtt_command (command_id, task_no, robot_id, action, payload) "
        "VALUES (:c, :t, :r, :a, :p)"),
        {"c": command_id, "t": task_no, "r": robot_id, "a": action,
         "p": json.dumps(payload, ensure_ascii=False)})
    return command_id


def supersede(conn, task_no):
    conn.execute(db.text(
        "UPDATE mqtt_command SET status='superseded' "
        "WHERE task_no=:n AND status<>'superseded'"), {"n": task_no})


def flush_pending():
    """Send only committed rows. A crash after publish causes same-ID redelivery.

    Lock each command until publish returns; ACK processing may wait for this lock.
    No publish wait_for_publish here: robot acceptance is an application event.
    """
    if _publisher is None:
        return
    rows = db.query(
        "SELECT command_id FROM mqtt_command WHERE status='pending' "
        "AND (last_sent_at IS NULL OR last_sent_at < NOW(3) - INTERVAL :s SECOND) "
        "ORDER BY created_at, command_id LIMIT 50", {"s": RETRY_SECONDS})
    for row in rows:
        with db.transaction() as conn:
            cmd = conn.execute(db.text(
                "SELECT * FROM mqtt_command WHERE command_id=:c FOR UPDATE"),
                {"c": row["command_id"]}).mappings().first()
            if cmd is None or cmd["status"] != "pending":
                continue
            payload = json.loads(cmd["payload"]) if isinstance(cmd["payload"], str) else cmd["payload"]
            # Failure leaves the row pending. Retry on next worker tick.
            try:
                _publisher(f"robot/{cmd['robot_id']}/task", payload)
            except Exception:
                logger.exception("Command transport unavailable: %s", cmd["command_id"])
                continue
            conn.execute(db.text(
                "UPDATE mqtt_command SET attempts=attempts+1, last_sent_at=NOW(3) "
                "WHERE command_id=:c"), {"c": cmd["command_id"]})


def acknowledge(conn, command_id):
    conn.execute(db.text(
        "UPDATE mqtt_command SET status='acked', acked_at=NOW(3) "
        "WHERE command_id=:c AND status='pending'"), {"c": command_id})


def command_for(conn, task_no, robot_id, command_id):
    if not command_id:
        return None
    return conn.execute(db.text(
        "SELECT * FROM mqtt_command WHERE command_id=:c AND task_no=:t AND robot_id=:r"),
        {"c": command_id, "t": task_no, "r": robot_id}).mappings().first()
