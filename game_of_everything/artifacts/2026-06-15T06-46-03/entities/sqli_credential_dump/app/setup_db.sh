#!/bin/bash
set -e
service mysql start
sleep 5
mysql -u root <<'SQLEOF'
CREATE DATABASE IF NOT EXISTS shopdb;
CREATE USER IF NOT EXISTS 'appuser'@'127.0.0.1' IDENTIFIED BY 'AppPass123!';
GRANT SELECT, INSERT, UPDATE ON shopdb.* TO 'appuser'@'127.0.0.1';
GRANT SELECT ON information_schema.* TO 'appuser'@'127.0.0.1';
GRANT SELECT ON mysql.user TO 'appuser'@'127.0.0.1';
FLUSH PRIVILEGES;
USE shopdb;
CREATE TABLE IF NOT EXISTS products (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255), description VARCHAR(255));
CREATE TABLE IF NOT EXISTS users (id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(100), password VARCHAR(255), email VARCHAR(255), role VARCHAR(50));
INSERT INTO products (name, description) VALUES ('Laptop Pro 15', 'High-performance laptop with 16GB RAM'), ('Wireless Mouse', 'Ergonomic wireless mouse with long battery life'), ('USB-C Hub', '7-in-1 USB-C hub with HDMI and power delivery'), ('Mechanical Keyboard', 'Tactile mechanical keyboard with RGB lighting'), ('4K Monitor', '27-inch 4K IPS display with USB-C connectivity');
INSERT INTO users (username, password, email, role) VALUES ('admin', 'Summer2024!', 'admin@shoplocal.internal', 'administrator'), ('dbuser', 'Str0ngDBPass#99', 'dbuser@shoplocal.internal', 'db_operator'), ('alice', 'alice_pass_123', 'alice@shoplocal.internal', 'user'), ('bob', 'hunter2', 'bob@shoplocal.internal', 'user');
SQLEOF
echo 'Database setup complete'
