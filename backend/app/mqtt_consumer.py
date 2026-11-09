"""MQTT transport. Event/result ACKs follow the database transaction commit."""
import json
import logging

import paho.mqtt.client as mqtt
import redis

from . import config, state_machine

logger = logging.getLogger("mqtt_consumer")


class MqttConsumer:
    def __init__(self):
        self.r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=config.MQTT_CLIENT_ID, clean_session=False)
        self.c.on_connect = self._on_connect
        self.c.on_message = self._on_message
        self.c.reconnect_delay_set(min_delay=1, max_delay=10)

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            logger.error("MQTT connection rejected: %s", rc)
            return
        client.subscribe("robot/+/status", qos=0)
        client.subscribe("robot/+/event", qos=1)
        client.subscribe("robot/+/result", qos=1)

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) != 3 or parts[0] != "robot":
            return
        robot_id, kind = parts[1], parts[2]
        try:
            payload = json.loads(msg.payload)
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            if kind == "status":
                self.r.set(f"robot:{robot_id}:live", json.dumps(payload), ex=config.LIVE_TTL)
                battery, pos = payload.get("battery"), payload.get("pos")
                if isinstance(battery, (float, int)) and isinstance(pos, dict):
                    state_machine.try_resume_deferred(robot_id, float(battery), pos)
            elif kind in {"event", "result"}:
                if kind == "event":
                    state_machine.handle_event(robot_id, payload)
                else:
                    state_machine.handle_result(robot_id, payload)
                # Return even for duplicate/stale messages: commit succeeded or message was rejected.
                # No ACK on DB errors: robot's disk spool retries independently of MQTT PUBACK.
                if isinstance(payload.get("event_id"), str):
                    self.publish(f"robot/{robot_id}/ack", {"event_id": payload["event_id"]})
        except (ValueError, UnicodeDecodeError):
            logger.warning("Invalid MQTT payload on %s", msg.topic)
        except Exception:
            logger.exception("MQTT processing failed; application ACK withheld: %s", msg.topic)

    def publish(self, topic, payload):
        if not self.c.is_connected():
            raise ConnectionError("MQTT 尚未连接")
        info = self.c.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT publish failed: {info.rc}")

    def start(self):
        # API can start while the broker is down; outbox holds commands until connected.
        self.c.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
        self.c.loop_start()

    def stop(self):
        self.c.disconnect()
        self.c.loop_stop()
