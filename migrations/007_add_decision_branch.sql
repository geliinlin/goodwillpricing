-- Run in the intended schema after 001–006, before releasing the snapshot writer.
-- DDL may commit implicitly. No UPDATE/backfill: existing decisions retain NULL.
DROP PROCEDURE IF EXISTS gw_add_decision_branch;
DELIMITER $$
CREATE PROCEDURE gw_add_decision_branch()
BEGIN
    DECLARE parent_type VARCHAR(128);
    SELECT COLUMN_TYPE INTO parent_type FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='BRANCH' AND COLUMN_NAME='Branch_Id';
    IF parent_type IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Missing BRANCH.Branch_Id in selected schema';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
                   AND TABLE_NAME='PRICING_DECISION' AND COLUMN_NAME='Decision_Branch_Id') THEN
        SET @gw_branch_ddl=CONCAT('ALTER TABLE PRICING_DECISION ADD COLUMN Decision_Branch_Id ',
            parent_type, ' NULL DEFAULT NULL, ADD INDEX ix_decision_branch (Decision_Branch_Id), ',
            'ADD CONSTRAINT fk_decision_branch FOREIGN KEY (Decision_Branch_Id) REFERENCES BRANCH(Branch_Id)');
        PREPARE gw_stmt FROM @gw_branch_ddl;
        EXECUTE gw_stmt;
        DEALLOCATE PREPARE gw_stmt;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
                   AND TABLE_NAME='PRICING_DECISION' AND COLUMN_NAME='Decision_Branch_Id'
                   AND COLUMN_TYPE=parent_type AND IS_NULLABLE='YES')
       OR NOT EXISTS (SELECT 1 FROM information_schema.KEY_COLUMN_USAGE k
                      JOIN information_schema.REFERENTIAL_CONSTRAINTS r
                        USING(CONSTRAINT_SCHEMA, TABLE_NAME, CONSTRAINT_NAME)
                      WHERE k.TABLE_SCHEMA=DATABASE() AND k.TABLE_NAME='PRICING_DECISION'
                        AND k.COLUMN_NAME='Decision_Branch_Id' AND k.REFERENCED_TABLE_SCHEMA=DATABASE()
                        AND k.REFERENCED_TABLE_NAME='BRANCH' AND k.REFERENCED_COLUMN_NAME='Branch_Id'
                        AND r.DELETE_RULE IN ('RESTRICT','NO ACTION'))
       OR NOT EXISTS (SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE()
                      AND TABLE_NAME='PRICING_DECISION' AND COLUMN_NAME='Decision_Branch_Id' AND SEQ_IN_INDEX=1)
    THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Existing decision branch definition is incompatible; inspect before proceeding';
    END IF;
END$$
DELIMITER ;
CALL gw_add_decision_branch();
DROP PROCEDURE gw_add_decision_branch;
