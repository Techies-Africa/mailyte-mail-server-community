-- Create SOGo database (if it doesn't exist)
-- SOGo will initialize its own tables on first run
CREATE DATABASE IF NOT EXISTS sogo;
GRANT ALL PRIVILEGES ON sogo.* TO 'mailuser'@'%';
FLUSH PRIVILEGES;
