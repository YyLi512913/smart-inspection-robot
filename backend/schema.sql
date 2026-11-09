-- M2 建库建表（设计文档第 6 节，M2 范围：任务三表 + 系统配置表）
CREATE DATABASE IF NOT EXISTS robot_inspection CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE robot_inspection;

-- 任务主表
CREATE TABLE IF NOT EXISTS task (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  task_no       VARCHAR(32)  NOT NULL UNIQUE COMMENT '对外任务号 T20261107-001，MQTT 报文用',
  robot_id      VARCHAR(32)  NOT NULL,
  source        ENUM('chat','scheduled','manual') NOT NULL DEFAULT 'manual',
  status        ENUM('CREATED','DISPATCHED','ACCEPTED','NAVIGATING','INSPECTING',
                     'REPORTING','BLOCKED','DEFERRED','DONE','CANCELED','TIMEOUT','FAILED')
                NOT NULL DEFAULT 'CREATED',
  priority      TINYINT      NOT NULL DEFAULT 5,
  created_by    VARCHAR(32)  NOT NULL DEFAULT '',
  created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  dispatched_at DATETIME(3)  NULL,
  started_at    DATETIME(3)  NULL,
  finished_at   DATETIME(3)  NULL,
  KEY idx_robot_status (robot_id, status),
  KEY idx_status (status)
) ENGINE=InnoDB COMMENT='巡检任务';

-- 任务点位
CREATE TABLE IF NOT EXISTS task_point (
  id         BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  task_id    BIGINT UNSIGNED NOT NULL,
  seq        INT NOT NULL COMMENT '执行顺序，从 1 开始',
  place      VARCHAR(64) NOT NULL,
  x          DOUBLE NOT NULL,
  y          DOUBLE NOT NULL,
  check_item VARCHAR(128) NOT NULL DEFAULT '',
  status     ENUM('pending','navigating','arrived','inspected','blocked','skipped')
             NOT NULL DEFAULT 'pending',
  UNIQUE KEY uk_task_seq (task_id, seq),
  CONSTRAINT fk_point_task FOREIGN KEY (task_id) REFERENCES task(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='任务点位';

-- 状态机审计日志（只增不改）
CREATE TABLE IF NOT EXISTS task_event (
  id         BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  task_id    BIGINT UNSIGNED NOT NULL,
  robot_id   VARCHAR(32) NOT NULL DEFAULT '',
  event      VARCHAR(32) NOT NULL COMMENT 'created/dispatch/accepted/point_arrived/blocked/.../timeout/cancel',
  point_seq  INT NULL,
  detail     JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_task_time (task_id, created_at),
  CONSTRAINT fk_event_task FOREIGN KEY (task_id) REFERENCES task(id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='状态机流转审计';

-- 系统配置（充电桩坐标、能耗参数等，key-value）
CREATE TABLE IF NOT EXISTS config (
  id        BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  cfg_key   VARCHAR(64) NOT NULL UNIQUE,
  cfg_value VARCHAR(255) NOT NULL,
  remark    VARCHAR(255) NOT NULL DEFAULT ''
) ENGINE=InnoDB COMMENT='系统配置';

-- Agent 执行过程审计（M4，聊天窗口"执行过程"数据源）
CREATE TABLE IF NOT EXISTS agent_trace (
  id         BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(64) NOT NULL,
  seq        INT NOT NULL COMMENT '会话内事件序号',
  event_type ENUM('reasoning','tool_call','tool_result','ask_user','message') NOT NULL,
  tool_name  VARCHAR(64) NOT NULL DEFAULT '',
  payload    JSON NOT NULL,
  ts         DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_session (session_id, seq)
) ENGINE=InnoDB COMMENT='Agent 工具调用轨迹';

-- 巡检结果 + VLM 检测结论（M5，设计文档 5.2/5.4）
CREATE TABLE IF NOT EXISTS inspection_result (
  id         BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  task_no    VARCHAR(32)  NOT NULL,
  point_seq  INT          NOT NULL,
  place      VARCHAR(64)  NOT NULL DEFAULT '',
  check_item VARCHAR(128) NOT NULL DEFAULT '',
  image_url  VARCHAR(255) NULL,
  normal     TINYINT(1)   NULL COMMENT 'VLM 判定正常与否，NULL=未检测/失败',
  reading    VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'VLM 读数/观察描述',
  anomaly    VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'VLM 异常描述',
  raw        JSON NULL COMMENT 'VLM 原始返回',
  vlm_model  VARCHAR(64)  NOT NULL DEFAULT '',
  error      VARCHAR(255) NOT NULL DEFAULT '' COMMENT '检测失败原因',
  created_at DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_task_point (task_no, point_seq),
  KEY idx_place (place)
) ENGINE=InnoDB COMMENT='巡检点位 VLM 检测结果';

INSERT IGNORE INTO config (cfg_key, cfg_value, remark) VALUES
  ('dock_x', '2.0', '充电桩坐标 X（factory 地图）'),
  ('dock_y', '-3.0', '充电桩坐标 Y'),
  ('energy_per_meter_pct', '0.05', '每米耗电百分比'),
  ('energy_per_point_pct', '2.0', '每个点位作业耗电百分比'),
  ('safety_margin_pct', '20', '安全余量百分比'),
  ('resume_threshold_pct', '80', 'DEFERRED 任务自动下发的电量阈值'),
  ('watchdog_timeout_s', '300', '看门狗无事件超时秒数');

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

CREATE TABLE IF NOT EXISTS equipment (
 equipment_id VARCHAR(32) PRIMARY KEY, name VARCHAR(80) NOT NULL,
 equipment_type VARCHAR(32) NOT NULL, area VARCHAR(64) NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE,
 KEY idx_equipment_area (area, enabled)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS observation_point (
 observation_id VARCHAR(64) PRIMARY KEY, equipment_id VARCHAR(32) NOT NULL,
 name VARCHAR(80) NOT NULL, x DOUBLE NOT NULL, y DOUBLE NOT NULL, yaw DOUBLE NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE,
 CONSTRAINT fk_observation_equipment FOREIGN KEY(equipment_id) REFERENCES equipment(equipment_id),
 KEY idx_observation_equipment(equipment_id, enabled)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS observation_check (
 observation_id VARCHAR(64) NOT NULL, seq INT NOT NULL, check_code VARCHAR(64) NOT NULL,
 definition JSON NOT NULL, PRIMARY KEY(observation_id, check_code),
 CONSTRAINT fk_check_observation FOREIGN KEY(observation_id) REFERENCES observation_point(observation_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS task_point_inspection (
 task_id BIGINT UNSIGNED NOT NULL, seq INT NOT NULL, snapshot JSON NOT NULL,
 PRIMARY KEY(task_id, seq),
 CONSTRAINT fk_inspection_point FOREIGN KEY(task_id,seq) REFERENCES task_point(task_id,seq) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS inspection_alert (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 equipment_id VARCHAR(32) NOT NULL, equipment_name VARCHAR(80) NOT NULL,
 observation_id VARCHAR(64) NOT NULL, observation_name VARCHAR(80) NOT NULL,
 status VARCHAR(24) NOT NULL DEFAULT 'open', kind VARCHAR(24) NOT NULL,
 occurrences INT NOT NULL DEFAULT 0, latest_task_no VARCHAR(32) NOT NULL,
 latest_point_seq INT NOT NULL, summary VARCHAR(500) NOT NULL DEFAULT '',
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), closed_at DATETIME(3) NULL,
 KEY idx_alert_status(status,updated_at), KEY idx_alert_equipment(equipment_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS alert_slot (
 observation_id VARCHAR(64) PRIMARY KEY, alert_id BIGINT UNSIGNED NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS alert_result (
 task_no VARCHAR(32) NOT NULL, point_seq INT NOT NULL, alert_id BIGINT UNSIGNED NULL,
 PRIMARY KEY(task_no,point_seq)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS alert_event (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, alert_id BIGINT UNSIGNED NOT NULL,
 action VARCHAR(32) NOT NULL, actor VARCHAR(64) NOT NULL, note VARCHAR(1000) NOT NULL DEFAULT '',
 task_no VARCHAR(32) NULL, created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 KEY idx_alert_event(alert_id,id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS alert_recheck (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, alert_id BIGINT UNSIGNED NOT NULL,
 task_no VARCHAR(32) NOT NULL UNIQUE, baseline INT NOT NULL,
 outcome VARCHAR(24) NOT NULL DEFAULT 'pending', created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 KEY idx_alert_recheck(alert_id,id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS vision_job (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 task_no VARCHAR(32) NOT NULL, point_seq INT NOT NULL,
 place VARCHAR(64) NOT NULL, check_item VARCHAR(128) NOT NULL DEFAULT '',
 image_url VARCHAR(255) NOT NULL, snapshot JSON NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'pending', attempts INT NOT NULL DEFAULT 0,
 lease_token CHAR(32) NULL, lease_until DATETIME(3) NULL,
 available_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 last_error VARCHAR(500) NOT NULL DEFAULT '',
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3), finished_at DATETIME(3) NULL,
 UNIQUE KEY uk_vision_point(task_no,point_seq), KEY idx_vision_due(status,available_at),
 KEY idx_vision_lease(status,lease_until),
 CONSTRAINT fk_vision_task FOREIGN KEY(task_no) REFERENCES task(task_no) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS auth_guard (id INT PRIMARY KEY) ENGINE=InnoDB;
INSERT IGNORE INTO auth_guard(id) VALUES(1);
CREATE TABLE IF NOT EXISTS app_user (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, username VARCHAR(32) NOT NULL UNIQUE,
 display_name VARCHAR(80) NOT NULL, role VARCHAR(16) NOT NULL, password_hash VARCHAR(255) NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE, created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS auth_session (
 token_hash CHAR(64) PRIMARY KEY, user_id BIGINT UNSIGNED NOT NULL, csrf_token CHAR(64) NOT NULL,
 expires_at DATETIME(3) NOT NULL, created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 KEY idx_session_user(user_id), CONSTRAINT fk_session_user FOREIGN KEY(user_id) REFERENCES app_user(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS auth_throttle (
 throttle_key CHAR(64) PRIMARY KEY, attempts INT NOT NULL DEFAULT 0, window_end DATETIME(3) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS auth_event (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, actor VARCHAR(32) NOT NULL,
 action VARCHAR(32) NOT NULL, target VARCHAR(32) NOT NULL DEFAULT '', detail VARCHAR(255) NOT NULL DEFAULT '',
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS agent_session_owner (
 session_id CHAR(32) PRIMARY KEY, user_id BIGINT UNSIGNED NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB;
