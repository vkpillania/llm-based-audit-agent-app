-- Add useful Postgres extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Confirm setup
SELECT 'Library database initialized successfully' AS status;



-- -- create user
-- CREATE USER auditadmin WITH PASSWORD 'auditadmin';
