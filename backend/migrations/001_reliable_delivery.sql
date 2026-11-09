-- Additive, idempotent reliability migration. Existing business rows are preserved.
CREATE TABLE IF NOT EXISTS robot_execution (
 robot_id VARCHAR(32) PRIMARY KEY,
 task_no VARCHAR(32) NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS mqtt_command (
 command_id CHAR(32) PRIMARY KEY,
 task_no VARCHAR(32) NOT NULL,
 robot_id VARCHAR(32) NOT NULL,
 action VARCHAR(16) NOT NULL,
 payload JSON NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'pending',
 attempts INT NOT NULL DEFAULT 0,
 last_sent_at DATETIME(3) NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 acked_at DATETIME(3) NULL,
 KEY idx_delivery (status, last_sent_at),
 KEY idx_task_command (task_no, created_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS message_inbox (
 event_id VARCHAR(64) PRIMARY KEY,
 robot_id VARCHAR(32) NOT NULL,
 task_no VARCHAR(32) NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS task_request (
 request_key VARCHAR(128) PRIMARY KEY,
 request_hash CHAR(64) NOT NULL,
 task_no VARCHAR(32) NULL
) ENGINE=InnoDB;
