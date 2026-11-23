-- Goodwill master data starter catalog. Requires migration 004.
-- Idempotent: does not insert existing type/category combinations, never deletes.
-- Choose the correct MySQL schema in Workbench, then run.
SELECT DATABASE() AS Selected_Database;

DROP PROCEDURE IF EXISTS gw_seed_type_categories;
DELIMITER $$
CREATE PROCEDURE gw_seed_type_categories()
BEGIN
    DECLARE got_lock INT DEFAULT 0;
    DECLARE i INT DEFAULT 1;
    DECLARE j INT DEFAULT 1;
    DECLARE total_types INT;
    DECLARE total_cats INT;
    DECLARE t_name VARCHAR(50);
    DECLARE c_name VARCHAR(50);
    DECLARE type_id INT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        IF got_lock = 1 THEN DO RELEASE_LOCK('goodwill.master_data.v1'); END IF;
        RESIGNAL;
    END;
    IF DATABASE() IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Select Goodwill database first.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='CATEGORY'
                     AND COLUMN_NAME='Item_Type_Id') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Run migration 004 first.';
    END IF;
    SELECT GET_LOCK('goodwill.master_data.v1', 10) INTO got_lock;
    IF got_lock <> 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Master data is busy.';
    END IF;
    START TRANSACTION;
    SELECT COUNT(*) INTO total_types FROM gw_seed_item_types;
    WHILE i <= total_types DO
        SELECT Item_Type_Name INTO t_name FROM gw_seed_item_types WHERE Seq=i;
        SET type_id=NULL;
        SELECT Item_Type_Id INTO type_id FROM ITEM_TYPE WHERE Item_Type_Name=t_name LIMIT 1;
        IF type_id IS NULL THEN
           SELECT COALESCE(MAX(Item_Type_Id),0)+1 INTO type_id FROM ITEM_TYPE;
           INSERT INTO ITEM_TYPE (Item_Type_Id,Item_Type_Name,Item_Type_Is_Active)
           VALUES (type_id,t_name,1);
        END IF;
        SET i=i+1;
    END WHILE;
    SELECT COUNT(*) INTO total_cats FROM gw_seed_categories;
    WHILE j <= total_cats DO
        SELECT Item_Type_Name,Category_Name INTO t_name,c_name
        FROM gw_seed_categories WHERE Seq=j;
        SET type_id=NULL;
        SELECT Item_Type_Id INTO type_id FROM ITEM_TYPE WHERE Item_Type_Name=t_name LIMIT 1;
        IF NOT EXISTS (SELECT 1 FROM CATEGORY
                       WHERE Item_Type_Id=type_id AND Category_Name=c_name) THEN
            INSERT INTO CATEGORY (Category_Id,Category_Name,Category_Description,
                                  Category_Is_Active,Item_Type_Id)
            SELECT COALESCE(MAX(Category_Id),0)+1,c_name,NULL,1,type_id
            FROM CATEGORY;
        END IF;
        SET j=j+1;
    END WHILE;
    COMMIT;
    DO RELEASE_LOCK('goodwill.master_data.v1');
END$$
DELIMITER ;

DROP TEMPORARY TABLE IF EXISTS gw_seed_categories;
DROP TEMPORARY TABLE IF EXISTS gw_seed_item_types;
CREATE TEMPORARY TABLE gw_seed_item_types
 (Seq INT PRIMARY KEY, Item_Type_Name VARCHAR(50) NOT NULL);
CREATE TEMPORARY TABLE gw_seed_categories
 (Seq INT PRIMARY KEY,Item_Type_Name VARCHAR(50) NOT NULL,
  Category_Name VARCHAR(50) NOT NULL);
INSERT INTO gw_seed_item_types (Seq,Item_Type_Name) VALUES
(1,'Furniture'),
(2,'Home & Kitchen'),
(3,'Electronics'),
(4,'Books & Media'),
(5,'Toys & Games'),
(6,'Sporting Goods'),
(7,'Jewelry & Accessories');

INSERT INTO gw_seed_categories (Seq,Item_Type_Name,Category_Name) VALUES
(1,'Furniture','Sofas & Couches'),
(2,'Furniture','Chairs'),
(3,'Furniture','Dining Tables'),
(4,'Furniture','Coffee & Side Tables'),
(5,'Furniture','Dressers'),
(6,'Furniture','Bookshelves'),
(7,'Furniture','Desks'),
(8,'Furniture','Bed Frames'),
(9,'Home & Kitchen','Cookware'),
(10,'Home & Kitchen','Dinnerware'),
(11,'Home & Kitchen','Small Appliances'),
(12,'Home & Kitchen','Home Decor'),
(13,'Home & Kitchen','Bedding'),
(14,'Home & Kitchen','Lighting'),
(15,'Home & Kitchen','Storage & Organization'),
(16,'Electronics','TVs & Monitors'),
(17,'Electronics','Computers & Tablets'),
(18,'Electronics','Audio Equipment'),
(19,'Electronics','Gaming Consoles'),
(20,'Electronics','Cameras'),
(21,'Electronics','Chargers & Cables'),
(22,'Electronics','Home Electronics'),
(23,'Books & Media','Books'),
(24,'Books & Media','DVDs & Blu-rays'),
(25,'Books & Media','Vinyl Records'),
(26,'Books & Media','Music CDs'),
(27,'Books & Media','Video Games'),
(28,'Books & Media','Board Games'),
(29,'Toys & Games','Dolls & Action Figures'),
(30,'Toys & Games','Educational Toys'),
(31,'Toys & Games','Puzzles'),
(32,'Toys & Games','Outdoor Toys'),
(33,'Toys & Games','Plush Toys'),
(34,'Toys & Games','Building Sets'),
(35,'Sporting Goods','Exercise Equipment'),
(36,'Sporting Goods','Bicycles'),
(37,'Sporting Goods','Outdoor Gear'),
(38,'Sporting Goods','Sports Equipment'),
(39,'Sporting Goods','Camping Gear'),
(40,'Jewelry & Accessories','Necklaces'),
(41,'Jewelry & Accessories','Bracelets'),
(42,'Jewelry & Accessories','Earrings'),
(43,'Jewelry & Accessories','Watches'),
(44,'Jewelry & Accessories','Handbags'),
(45,'Jewelry & Accessories','Belts'),
(46,'Jewelry & Accessories','Scarves');

CALL gw_seed_type_categories();
DROP PROCEDURE gw_seed_type_categories;
DROP TEMPORARY TABLE gw_seed_categories;
DROP TEMPORARY TABLE gw_seed_item_types;

SELECT t.Item_Type_Name, c.Category_Id, c.Category_Name, c.Category_Is_Active
FROM ITEM_TYPE t LEFT JOIN CATEGORY c ON c.Item_Type_Id=t.Item_Type_Id
ORDER BY t.Item_Type_Name,c.Category_Name;
