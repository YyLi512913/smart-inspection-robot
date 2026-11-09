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
