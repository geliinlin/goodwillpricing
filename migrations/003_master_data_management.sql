-- Goodwill Configuration V1 (MySQL 8+).
-- Select the intended database in MySQL Workbench first.
-- Prerequisites: 001_user_management_access.sql and 002_account_email_recovery.sql.
-- Re-runnable: adds missing active-status columns without dropping existing records.
-- Uses the current selected database; no hardcoded schema name.

SELECT DATABASE() AS Selected_Database;

-- Keep all existing master data active initially, matching behavior before V1.
SET @gw_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ITEM_TYPE'
      AND COLUMN_NAME = 'Item_Type_Is_Active') = 0,
  'ALTER TABLE ITEM_TYPE ADD COLUMN Item_Type_Is_Active TINYINT(1) NOT NULL DEFAULT 1',
  'SELECT ''ITEM_TYPE active flag already exists'' AS note'
);
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

SET @gw_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'CATEGORY'
      AND COLUMN_NAME = 'Category_Is_Active') = 0,
  'ALTER TABLE CATEGORY ADD COLUMN Category_Is_Active TINYINT(1) NOT NULL DEFAULT 1',
  'SELECT ''CATEGORY active flag already exists'' AS note'
);
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

SET @gw_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'BRAND'
      AND COLUMN_NAME = 'Brand_Is_Active') = 0,
  'ALTER TABLE BRAND ADD COLUMN Brand_Is_Active TINYINT(1) NOT NULL DEFAULT 1',
  'SELECT ''BRAND active flag already exists'' AS note'
);
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

SET @gw_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ITEM_CONDITION'
      AND COLUMN_NAME = 'Condition_Is_Active') = 0,
  'ALTER TABLE ITEM_CONDITION ADD COLUMN Condition_Is_Active TINYINT(1) NOT NULL DEFAULT 1',
  'SELECT ''ITEM_CONDITION active flag already exists'' AS note'
);
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

SET @gw_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'BRANCH'
      AND COLUMN_NAME = 'Branch_Is_Active') = 0,
  'ALTER TABLE BRANCH ADD COLUMN Branch_Is_Active TINYINT(1) NOT NULL DEFAULT 1',
  'SELECT ''BRANCH active flag already exists'' AS note'
);
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

CREATE TABLE IF NOT EXISTS MASTER_DATA_AUDIT (
  Audit_Id BIGINT NOT NULL AUTO_INCREMENT,
  Actor_User_Id INT NOT NULL,
  Entity_Type VARCHAR(30) NOT NULL,
  Entity_Id INT NOT NULL,
  Action_Type VARCHAR(20) NOT NULL,
  Before_Json JSON NULL,
  After_Json JSON NULL,
  Changed_At DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (Audit_Id),
  INDEX ix_master_audit_entity (Entity_Type, Entity_Id, Audit_Id),
  INDEX ix_master_audit_actor (Actor_User_Id),
  CONSTRAINT fk_master_audit_admin FOREIGN KEY (Actor_User_Id)
    REFERENCES ADMINISTRATOR (Admin_Id),
  CONSTRAINT chk_master_audit_action CHECK (Action_Type IN ('CREATE', 'UPDATE', 'ACTIVATE', 'DEACTIVATE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Confirm all flags exist and no existing rows were deactivated:
SELECT 'ITEM_TYPE' AS Master_Table, COUNT(*) AS Total,
       SUM(Item_Type_Is_Active=1) AS Active FROM ITEM_TYPE
UNION ALL SELECT 'CATEGORY', COUNT(*), SUM(Category_Is_Active=1) FROM CATEGORY
UNION ALL SELECT 'BRAND', COUNT(*), SUM(Brand_Is_Active=1) FROM BRAND
UNION ALL SELECT 'ITEM_CONDITION', COUNT(*), SUM(Condition_Is_Active=1) FROM ITEM_CONDITION
UNION ALL SELECT 'BRANCH', COUNT(*), SUM(Branch_Is_Active=1) FROM BRANCH;
