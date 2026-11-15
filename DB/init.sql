-- La imagen selecciona la base indicada en MARIADB_DATABASE.
CREATE TABLE IF NOT EXISTS hosts (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    ip VARCHAR(15) NOT NULL,
    port SMALLINT UNSIGNED NOT NULL DEFAULT 161,
    community VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    -- Evita guardar consultas iniciadas antes de editar un host.
    config_version INT UNSIGNED NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_target (ip, port)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS samples (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    host_id INT UNSIGNED NOT NULL,
    config_version INT UNSIGNED NOT NULL,
    checked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status ENUM('up', 'down') NOT NULL,
    response_ms FLOAT NULL,
    sys_name VARCHAR(255) NULL,
    sys_descr TEXT NULL,
    uptime_seconds BIGINT UNSIGNED NULL,
    cpu_percent FLOAT NULL,
    memory_percent FLOAT NULL,
    memory_total_mb FLOAT NULL,
    error VARCHAR(255) NULL,
    FOREIGN KEY (host_id) REFERENCES hosts(id) ON DELETE CASCADE,
    INDEX latest_sample (host_id, config_version, id),
    INDEX retention (checked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
