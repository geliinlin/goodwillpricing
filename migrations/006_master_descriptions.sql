-- Goodwill Master Data Descriptions V1 (MariaDB/MySQL compatible)
-- Requires 003_master_data_management.sql; Categories should already be linked
-- to Item Types by 004_category_item_type.sql.
-- Select the intended Goodwill schema in Workbench BEFORE executing.
-- Re-runnable: only adds missing columns; existing descriptions are preserved.
-- DDL auto-commits: back up your database before running.

SELECT DATABASE() AS Selected_Database;

SET @gw_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ITEM_TYPE'
     AND COLUMN_NAME = 'Item_Type_Description') = 0,
  'ALTER TABLE ITEM_TYPE ADD COLUMN Item_Type_Description VARCHAR(255) NULL',
  'SELECT ''ITEM_TYPE description already exists'' AS note'
);
PREPARE gw_sql FROM @gw_ddl;
EXECUTE gw_sql;
DEALLOCATE PREPARE gw_sql;

SET @gw_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'BRANCH'
     AND COLUMN_NAME = 'Branch_Description') = 0,
  'ALTER TABLE BRANCH ADD COLUMN Branch_Description VARCHAR(255) NULL',
  'SELECT ''BRANCH description already exists'' AS note'
);
PREPARE gw_sql FROM @gw_ddl;
EXECUTE gw_sql;
DEALLOCATE PREPARE gw_sql;

-- Verify all five attribute families expose their description fields.
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA=DATABASE() AND
      ((TABLE_NAME='ITEM_TYPE' AND COLUMN_NAME='Item_Type_Description') OR
       (TABLE_NAME='CATEGORY' AND COLUMN_NAME='Category_Description') OR
       (TABLE_NAME='BRAND' AND COLUMN_NAME='Brand_Description') OR
       (TABLE_NAME='ITEM_CONDITION' AND COLUMN_NAME='Condition_Description') OR
       (TABLE_NAME='BRANCH' AND COLUMN_NAME='Branch_Description'))
ORDER BY TABLE_NAME;
