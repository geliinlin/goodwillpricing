-- Goodwill Users/Configuration access (MySQL 8+).
-- Select the intended database/schema in MySQL Workbench BEFORE running.
-- Non-destructive; existing accounts default to no management privileges.
-- Only Administrator subtype rows are permitted by the FOREIGN KEY.
-- This migration is for fresh installations of Users V2, not V1 upgrades.
CREATE TABLE IF NOT EXISTS USER_MODULE_ACCESS (
    User_Id INT NOT NULL,
    Can_Manage_Users_Config TINYINT(1) NOT NULL DEFAULT 0,
    Granted_By_User_Id INT NULL,
    Updated_At DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (User_Id),
    CONSTRAINT chk_module_access_flag CHECK (Can_Manage_Users_Config IN (0, 1)),
    CONSTRAINT fk_module_access_admin FOREIGN KEY (User_Id)
        REFERENCES ADMINISTRATOR (Admin_Id),
    CONSTRAINT fk_module_access_grantor_admin FOREIGN KEY (Granted_By_User_Id)
        REFERENCES ADMINISTRATOR (Admin_Id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ONE-TIME BOOTSTRAP: change the username first, run only for your designated
-- trusted active administrator. Staff and ordinary admins remain unprivileged.
-- Never choose a broad condition such as User_Type='Administrator'.
--
-- SET @bootstrap_admin_username = 'ACTUAL_ADMIN_USERNAME';
-- INSERT INTO USER_MODULE_ACCESS
--     (User_Id, Can_Manage_Users_Config, Granted_By_User_Id)
-- SELECT u.User_Id, 1, NULL
-- FROM USER_ACCOUNT u
-- INNER JOIN ADMINISTRATOR a ON a.Admin_Id = u.User_Id
-- WHERE u.User_Username = @bootstrap_admin_username
--   AND u.User_Type = 'Administrator' AND u.User_Is_Active = 1
-- ON DUPLICATE KEY UPDATE Can_Manage_Users_Config = 1;
--
-- SELECT u.User_Id, u.User_Username, p.Can_Manage_Users_Config
-- FROM USER_ACCOUNT u JOIN USER_MODULE_ACCESS p ON p.User_Id = u.User_Id
-- WHERE u.User_Username = @bootstrap_admin_username;
