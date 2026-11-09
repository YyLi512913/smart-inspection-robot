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
