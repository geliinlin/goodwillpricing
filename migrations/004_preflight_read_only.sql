-- Read-only checks before running 004_category_item_type.sql
SELECT DATABASE() AS Selected_Database;
SELECT Item_Type_Id, Item_Type_Name FROM ITEM_TYPE ORDER BY Item_Type_Id;
-- If this query returns rows, STOP. A Category was used with multiple Item Types.
SELECT c.Category_Id,c.Category_Name,
       COUNT(DISTINCT usage_rows.Item_Type_Id) AS Distinct_Item_Types,
       GROUP_CONCAT(DISTINCT t.Item_Type_Name ORDER BY t.Item_Type_Name SEPARATOR ' | ') AS Item_Types_Used
FROM CATEGORY c
JOIN (
    SELECT Category_Id,Item_Type_Id FROM PRICING_GUIDELINE
    UNION ALL
    SELECT Category_Id,Item_Type_Id FROM ITEM
) usage_rows ON usage_rows.Category_Id=c.Category_Id
JOIN ITEM_TYPE t ON t.Item_Type_Id=usage_rows.Item_Type_Id
GROUP BY c.Category_Id,c.Category_Name
HAVING COUNT(DISTINCT usage_rows.Item_Type_Id)>1;

-- Current category coverage; this is informational and makes no changes.
SELECT c.Category_Id,c.Category_Name,
       COALESCE(COUNT(DISTINCT g.Guideline_Id),0) AS Guideline_Count,
       COALESCE(COUNT(DISTINCT i.Item_Id),0) AS Item_Count
FROM CATEGORY c
LEFT JOIN PRICING_GUIDELINE g ON g.Category_Id=c.Category_Id
LEFT JOIN ITEM i ON i.Category_Id=c.Category_Id
GROUP BY c.Category_Id,c.Category_Name
ORDER BY c.Category_Id;
