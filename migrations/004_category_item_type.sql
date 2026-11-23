-- Goodwill Configuration V2: each Category belongs to exactly one Item Type.
-- MariaDB / MySQL: BACK UP FIRST, run after 003_master_data_management.sql.
-- This is an ALTER TABLE migration. DDL changes auto-commit and cannot be rolled back.
-- Run the preflight file first; do not continue if historical category conflicts appear.
-- DOES NOT DROP TABLES OR DELETE RECORDS. Run once in the correct selected schema;
-- safe to re-run after successful completion. DDL performs implicit commits.
SELECT DATABASE() AS Selected_Database;

-- 1. Abort BEFORE any DDL if historical records assigned a Category to multiple Item Types.
-- The old database allowed those combinations; they require a human decision.
DROP PROCEDURE IF EXISTS gw_category_preflight;
DELIMITER $$
CREATE PROCEDURE gw_category_preflight()
BEGIN
  IF DATABASE() IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Select your Goodwill schema first.';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM ITEM_TYPE WHERE Item_Type_Name='Clothing') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Clothing Item Type not found; cannot assign unused legacy categories.';
  END IF;
  IF EXISTS (
    SELECT 1 FROM (
       SELECT Category_Id FROM (
         SELECT Category_Id, Item_Type_Id FROM PRICING_GUIDELINE
         UNION ALL
         SELECT Category_Id, Item_Type_Id FROM ITEM
       ) usage_rows
       GROUP BY Category_Id HAVING COUNT(DISTINCT Item_Type_Id) > 1
    ) conflicts
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT =
      'Legacy Category used with multiple Item Types; review conflicts before migration.';
  END IF;
END$$
DELIMITER ;
CALL gw_category_preflight();
DROP PROCEDURE gw_category_preflight;

-- 2. Add the parent reference. Existing data will be backfilled below.
SET @gw_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='CATEGORY'
     AND COLUMN_NAME='Item_Type_Id')=0,
  'ALTER TABLE CATEGORY ADD COLUMN Item_Type_Id INT NULL',
  'SELECT ''CATEGORY.Item_Type_Id already exists'' AS info'
);
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

-- First derive parent ID from actual prior Items and Guidelines.
SET @previous_safe_updates=@@SQL_SAFE_UPDATES;
SET SQL_SAFE_UPDATES=0;
UPDATE CATEGORY c
JOIN (
  SELECT Category_Id, MIN(Item_Type_Id) AS derived_type
  FROM (
      SELECT Category_Id, Item_Type_Id FROM PRICING_GUIDELINE
      UNION ALL SELECT Category_Id, Item_Type_Id FROM ITEM
  ) usage_rows
  GROUP BY Category_Id
) used_types ON used_types.Category_Id=c.Category_Id
SET c.Item_Type_Id=used_types.derived_type
WHERE c.Item_Type_Id IS NULL;

-- The screenshots show old unused categories are Clothing categories.
-- Assign ONLY those not referenced in ITEM or PRICING_GUIDELINE.
SET @gw_clothing_type=(SELECT MIN(Item_Type_Id) FROM ITEM_TYPE WHERE Item_Type_Name='Clothing');
UPDATE CATEGORY SET Item_Type_Id=@gw_clothing_type WHERE Item_Type_Id IS NULL;
SET SQL_SAFE_UPDATES=@previous_safe_updates;

-- Prevent existing mismatches from being hidden by the schema change.
DROP PROCEDURE IF EXISTS gw_category_assert_backfill;
DELIMITER $$
CREATE PROCEDURE gw_category_assert_backfill()
BEGIN
  IF EXISTS(SELECT 1 FROM CATEGORY WHERE Item_Type_Id IS NULL) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Some Category parent types are still NULL.';
  END IF;
  IF EXISTS (SELECT 1 FROM PRICING_GUIDELINE g JOIN CATEGORY c
             ON c.Category_Id=g.Category_Id
             WHERE c.Item_Type_Id <> g.Item_Type_Id)
    OR EXISTS (SELECT 1 FROM ITEM i JOIN CATEGORY c
             ON c.Category_Id=i.Category_Id
             WHERE c.Item_Type_Id <> i.Item_Type_Id) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Existing Item or Guideline category/type mismatch.';
  END IF;
END$$
DELIMITER ;
CALL gw_category_assert_backfill();
DROP PROCEDURE gw_category_assert_backfill;

-- 3. Require a parent, and replace global category-name uniqueness with scoped uniqueness.
SET @gw_ddl=IF((SELECT IS_NULLABLE FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='CATEGORY'
     AND COLUMN_NAME='Item_Type_Id')='YES',
   'ALTER TABLE CATEGORY MODIFY COLUMN Item_Type_Id INT NOT NULL',
   'SELECT ''Category parent already NOT NULL'' AS info');
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;
SET @gw_ddl=IF((SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='CATEGORY' AND INDEX_NAME='uq_category_type_name')=0,
    'ALTER TABLE CATEGORY ADD UNIQUE KEY uq_category_type_name (Item_Type_Id, Category_Name)',
    'SELECT ''Category name/type key exists'' AS info');
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;
SET @gw_ddl=IF((SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='CATEGORY' AND INDEX_NAME='uq_category_name')>0,
    'ALTER TABLE CATEGORY DROP INDEX uq_category_name',
    'SELECT ''Global category-name key already removed'' AS info');
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

-- Item_Type_Id + Category_Id is a composite parent key for data integrity.
SET @gw_ddl=IF((SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='CATEGORY' AND INDEX_NAME='uq_category_type_id')=0,
    'ALTER TABLE CATEGORY ADD UNIQUE KEY uq_category_type_id (Item_Type_Id, Category_Id)',
    'SELECT ''Category composite key exists'' AS info');
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

SET @gw_ddl=IF((SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='CATEGORY'
      AND CONSTRAINT_NAME='fk_category_item_type')=0,
    'ALTER TABLE CATEGORY ADD CONSTRAINT fk_category_item_type FOREIGN KEY (Item_Type_Id) REFERENCES ITEM_TYPE(Item_Type_Id)',
    'SELECT ''Category parent foreign key exists'' AS info');
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

-- Prevent ALL future historical ITEM and PRICING_GUIDELINE mismatches at the DB layer.
SET @gw_ddl=IF((SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ITEM' AND INDEX_NAME='ix_item_category_type')=0,
    'ALTER TABLE ITEM ADD INDEX ix_item_category_type (Item_Type_Id, Category_Id)',
    'SELECT ''ITEM composite index exists'' AS info');
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;
SET @gw_ddl=IF((SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='PRICING_GUIDELINE' AND INDEX_NAME='ix_guideline_category_type')=0,
    'ALTER TABLE PRICING_GUIDELINE ADD INDEX ix_guideline_category_type (Item_Type_Id, Category_Id)',
    'SELECT ''GUIDELINE composite index exists'' AS info');
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;
SET @gw_ddl=IF((SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ITEM' AND CONSTRAINT_NAME='fk_item_type_category_pair')=0,
    'ALTER TABLE ITEM ADD CONSTRAINT fk_item_type_category_pair FOREIGN KEY (Item_Type_Id,Category_Id) REFERENCES CATEGORY(Item_Type_Id,Category_Id)',
    'SELECT ''ITEM pair FK exists'' AS info');
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;
SET @gw_ddl=IF((SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='PRICING_GUIDELINE' AND CONSTRAINT_NAME='fk_guideline_type_category_pair')=0,
    'ALTER TABLE PRICING_GUIDELINE ADD CONSTRAINT fk_guideline_type_category_pair FOREIGN KEY (Item_Type_Id,Category_Id) REFERENCES CATEGORY(Item_Type_Id,Category_Id)',
    'SELECT ''GUIDELINE pair FK exists'' AS info');
PREPARE gw_sql FROM @gw_ddl; EXECUTE gw_sql; DEALLOCATE PREPARE gw_sql;

SELECT c.Category_Id, c.Category_Name, t.Item_Type_Name,
       c.Category_Is_Active
FROM CATEGORY c JOIN ITEM_TYPE t ON t.Item_Type_Id=c.Item_Type_Id
ORDER BY t.Item_Type_Name,c.Category_Name;
