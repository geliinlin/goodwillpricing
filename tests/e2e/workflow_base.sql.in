-- Minimal pre-migration G3 workflow tables reconstructed from application SQL.
-- The original G3 DDL is not distributed. Only load in a new test container.
CREATE TABLE USER_ACCOUNT (
    User_Id INT PRIMARY KEY, User_First_Name VARCHAR(100), User_Last_Name VARCHAR(100),
    User_Username VARCHAR(100) NOT NULL UNIQUE, User_Email VARCHAR(254) NOT NULL UNIQUE,
    User_Password_Hash VARCHAR(255) NOT NULL, User_Is_Active BOOLEAN NOT NULL,
    User_Type ENUM('Administrator','Store Staff') NOT NULL
) ENGINE=InnoDB;
CREATE TABLE ADMINISTRATOR (
    Admin_Id INT PRIMARY KEY, FOREIGN KEY (Admin_Id) REFERENCES USER_ACCOUNT(User_Id)
) ENGINE=InnoDB;
CREATE TABLE BRANCH (
    Branch_Id INT PRIMARY KEY, Branch_Name VARCHAR(100) NOT NULL, Branch_City VARCHAR(100),
    Branch_Street VARCHAR(100), Branch_State CHAR(2), Branch_Zip VARCHAR(10)
) ENGINE=InnoDB;
CREATE TABLE STORE_STAFF (
    Staff_Id INT PRIMARY KEY, Branch_Id INT NOT NULL,
    FOREIGN KEY (Staff_Id) REFERENCES USER_ACCOUNT(User_Id),
    FOREIGN KEY (Branch_Id) REFERENCES BRANCH(Branch_Id)
) ENGINE=InnoDB;
CREATE TABLE ITEM_TYPE (Item_Type_Id INT PRIMARY KEY, Item_Type_Name VARCHAR(50) UNIQUE NOT NULL) ENGINE=InnoDB;
CREATE TABLE CATEGORY (
    Category_Id INT PRIMARY KEY, Category_Name VARCHAR(50) NOT NULL,
    Category_Description VARCHAR(255), UNIQUE KEY uq_category_name(Category_Name)
) ENGINE=InnoDB;
CREATE TABLE BRAND (Brand_Id INT PRIMARY KEY, Brand_Name VARCHAR(50), Brand_Description VARCHAR(255)) ENGINE=InnoDB;
CREATE TABLE ITEM_CONDITION (
    Condition_Id INT PRIMARY KEY, Condition_Name VARCHAR(50), Condition_Description VARCHAR(255)
) ENGINE=InnoDB;
CREATE TABLE PRICING_GUIDELINE (
    Guideline_Id INT PRIMARY KEY, Item_Type_Id INT NOT NULL, Category_Id INT NOT NULL,
    Brand_Id INT NOT NULL, Condition_Id INT NOT NULL, Guideline_Suggested_Price DECIMAL(8,2) NOT NULL,
    Guideline_Is_Active BOOLEAN NOT NULL, Guideline_Last_Updated DATETIME NOT NULL,
    Admin_Id INT NOT NULL, FOREIGN KEY (Admin_Id) REFERENCES ADMINISTRATOR(Admin_Id),
    FOREIGN KEY (Brand_Id) REFERENCES BRAND(Brand_Id),
    FOREIGN KEY (Condition_Id) REFERENCES ITEM_CONDITION(Condition_Id)
) ENGINE=InnoDB;
CREATE TABLE ITEM (
    Item_Id INT PRIMARY KEY, Item_Description VARCHAR(255), Item_Received_Date DATE NOT NULL,
    Category_Id INT NOT NULL, Brand_Id INT NOT NULL, Condition_Id INT NOT NULL, Item_Type_Id INT NOT NULL,
    FOREIGN KEY (Brand_Id) REFERENCES BRAND(Brand_Id),
    FOREIGN KEY (Condition_Id) REFERENCES ITEM_CONDITION(Condition_Id)
) ENGINE=InnoDB;
CREATE TABLE PRICING_DECISION (
    Decision_Id INT PRIMARY KEY, Decision_Datetime DATETIME NOT NULL, Decision_Reason VARCHAR(255),
    Decision_Final_Price DECIMAL(8,2) NOT NULL, Decision_Tag_Number VARCHAR(20) UNIQUE NOT NULL,
    Guideline_Id INT NOT NULL, Staff_Id INT NOT NULL, Item_Id INT NOT NULL,
    FOREIGN KEY (Guideline_Id) REFERENCES PRICING_GUIDELINE(Guideline_Id),
    FOREIGN KEY (Staff_Id) REFERENCES STORE_STAFF(Staff_Id), FOREIGN KEY (Item_Id) REFERENCES ITEM(Item_Id)
) ENGINE=InnoDB;
-- Legacy Clothing and Shirts exercise the 004 backfill before 005 adds other category families.
INSERT INTO ITEM_TYPE VALUES (1,'Clothing');
INSERT INTO CATEGORY VALUES (1,'Shirts','Tops worn on the upper body');
