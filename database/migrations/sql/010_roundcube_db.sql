-- Create Roundcube database (if it doesn't exist)
-- Roundcube will initialize its own tables on first run
CREATE DATABASE IF NOT EXISTS roundcubemail;
GRANT ALL PRIVILEGES ON roundcubemail.* TO 'mailuser'@'%';
FLUSH PRIVILEGES;
