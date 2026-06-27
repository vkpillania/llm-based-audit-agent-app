psql -U postgres -d postgres -c "
  DROP DATABASE audit_agents ;
  CREATE DATABASE audit_agents OWNER auditadmin;
"