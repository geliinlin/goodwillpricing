-- Upgrade only the original G3 two-value CHECK. Existing application schemas
-- already using Store Staff need no changes. Quiesce account writers for rollout.
DROP PROCEDURE IF EXISTS gw_align_g3_staff_role;
DELIMITER $$
CREATE PROCEDURE gw_align_g3_staff_role()
BEGIN
    DECLARE legacy_rule TEXT;
    DECLARE transition_rule TEXT;
    DECLARE original_prefix TEXT DEFAULT '^[[:space:] (]*`?user_type`?[[:space:]]+in[[:space:]]*[(][[:space:]]*(_[a-z0-9]+)?''administrator''[[:space:]]*,[[:space:]]*(_[a-z0-9]+)?''employee''';
    DECLARE closing_pattern TEXT DEFAULT '[[:space:]]*[)]+[[:space:]]*$';
    DECLARE transitional_suffix TEXT DEFAULT '[[:space:]]*,[[:space:]]*(_[a-z0-9]+)?''store staff''';
    DECLARE safe_updates_before INT DEFAULT @@SESSION.sql_safe_updates;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        SET SESSION sql_safe_updates=safe_updates_before;
        RESIGNAL;
    END;
    -- MySQL renders CHECK string literals with character-set introducers.
    SELECT MAX(CASE WHEN t.CONSTRAINT_NAME='chk_user_type' THEN LOWER(REPLACE(c.CHECK_CLAUSE,CHAR(92),'')) END),
           MAX(CASE WHEN t.CONSTRAINT_NAME='chk_gw_role_transition' THEN LOWER(REPLACE(c.CHECK_CLAUSE,CHAR(92),'')) END)
      INTO legacy_rule, transition_rule
      FROM information_schema.TABLE_CONSTRAINTS t
      JOIN information_schema.CHECK_CONSTRAINTS c USING (CONSTRAINT_SCHEMA, CONSTRAINT_NAME)
     WHERE t.TABLE_SCHEMA=DATABASE() AND t.TABLE_NAME='USER_ACCOUNT' AND t.CONSTRAINT_TYPE='CHECK';
    IF legacy_rule REGEXP CONCAT(original_prefix,closing_pattern) THEN
        ALTER TABLE USER_ACCOUNT DROP CONSTRAINT chk_user_type,
            ADD CONSTRAINT chk_gw_role_transition CHECK (User_Type IN ('Administrator','Employee','Store Staff'));
        SET transition_rule='user_type in (''administrator'',''employee'',''store staff'')';
    ELSEIF legacy_rule LIKE '%employee%' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Unexpected legacy employee role constraint; inspect before conversion';
    END IF;
    -- An interruption after the first ALTER can resume from this CHECK.
    IF transition_rule IS NOT NULL THEN
        IF transition_rule NOT REGEXP CONCAT(original_prefix,transitional_suffix,closing_pattern) THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Unexpected role transition constraint';
        END IF;
        SET SESSION sql_safe_updates=0;
        UPDATE USER_ACCOUNT SET User_Type='Store Staff' WHERE User_Type='Employee';
        SET SESSION sql_safe_updates=safe_updates_before;
        ALTER TABLE USER_ACCOUNT DROP CONSTRAINT chk_gw_role_transition,
            ADD CONSTRAINT chk_user_type CHECK (User_Type IN ('Administrator','Store Staff'));
    END IF;
END$$
DELIMITER ;
CALL gw_align_g3_staff_role();
DROP PROCEDURE gw_align_g3_staff_role;
