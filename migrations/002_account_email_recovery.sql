-- Goodwill Users V3: transactional password setup and one-time email links.
-- Run only AFTER 001_user_management_access.sql and on the correct database.
-- Adds tables only; does not change USER_ACCOUNT or existing passwords.

CREATE TABLE IF NOT EXISTS USER_PASSWORD_STATE (
    User_Id INT NOT NULL,
    Must_Change_Password TINYINT(1) NOT NULL DEFAULT 0,
    Temp_Password_Expires_At DATETIME NULL,
    Session_Version INT UNSIGNED NOT NULL DEFAULT 0,
    Updated_At DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (User_Id),
    CONSTRAINT chk_pwstate_change CHECK (Must_Change_Password IN (0, 1)),
    CONSTRAINT fk_pwstate_account FOREIGN KEY (User_Id)
        REFERENCES USER_ACCOUNT (User_Id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS USER_PASSWORD_TOKEN (
    Token_Id BIGINT NOT NULL AUTO_INCREMENT,
    User_Id INT NOT NULL,
    Token_Hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    Purpose ENUM('invite', 'reset') NOT NULL,
    Created_At DATETIME NOT NULL,
    Expires_At DATETIME NOT NULL,
    Sent_At DATETIME NULL,
    Consumed_At DATETIME NULL,
    PRIMARY KEY (Token_Id),
    UNIQUE KEY uq_pwtoken_hash (Token_Hash),
    KEY ix_pwtoken_user_sent (User_Id, Sent_At),
    CONSTRAINT fk_pwtoken_account FOREIGN KEY (User_Id)
        REFERENCES USER_ACCOUNT (User_Id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
