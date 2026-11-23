"""Read-only guideline schema, InnoDB, and active-duplicate consistency check.

Run from project root: python -m scripts.check_guideline_schema
Exits with an error if duplicate active guidelines are found.
"""

import mysql.connector

from database import get_connection
from guidelines import LOOKUPS, VERSION_FIELDS


REQUIRED_TABLES = {
    "USER_ACCOUNT": (
        "User_Id", "User_Is_Active", "User_Type", "User_First_Name", "User_Last_Name"
    ),
    "ADMINISTRATOR": ("Admin_Id",),
    "PRICING_GUIDELINE": VERSION_FIELDS,
}
REQUIRED_TABLES.update(
    {table: (id_field, name_field) for table, id_field, name_field, _ in LOOKUPS}
)


def main() -> None:
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT DATABASE() AS db_name, VERSION() AS version")
        server = cursor.fetchone() or {}
        if not server.get("db_name"):
            raise RuntimeError("No MySQL schema is selected.")
        print(f"Database: {server['db_name']} | Server: {server['version']}")

        for table, columns in REQUIRED_TABLES.items():
            # All table and field names come from fixed application constants.
            quoted = ", ".join(f"`{col}`" for col in columns)
            cursor.execute(f"SELECT {quoted} FROM `{table}` LIMIT 0")
            cursor.fetchall()
            cursor.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
            row = cursor.fetchone() or {}
            print(f"{table}: required columns OK; {row.get('n', 0)} row(s)")

        cursor.execute("""
            SELECT TABLE_NAME, ENGINE
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN (
                'USER_ACCOUNT', 'ADMINISTRATOR', 'PRICING_GUIDELINE',
                'CATEGORY', 'BRAND', 'ITEM_CONDITION', 'ITEM_TYPE'
              )
        """)
        engines = {row["TABLE_NAME"]: row["ENGINE"] for row in cursor.fetchall()}
        invalid = [table for table in REQUIRED_TABLES if engines.get(table) != "InnoDB"]
        if invalid:
            raise RuntimeError("Expected InnoDB tables: " + ", ".join(invalid))

        cursor.execute("""
            SELECT Category_Id, Brand_Id, Condition_Id, Item_Type_Id,
                   COUNT(*) AS n
            FROM PRICING_GUIDELINE
            WHERE Guideline_Is_Active = 1
            GROUP BY Category_Id, Brand_Id, Condition_Id, Item_Type_Id
            HAVING COUNT(*) > 1
        """)
        duplicates = cursor.fetchall()
        print(f"Duplicate active guideline combinations: {len(duplicates)}")
        for row in duplicates:
            print("  Conflict:", row)
        if duplicates:
            raise RuntimeError(
                "Active guideline conflicts must be resolved before item pricing."
            )
        print("Guideline schema and active-guideline uniqueness: OK (read-only)")
    except (mysql.connector.Error, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Guideline schema check failed: {exc}") from None
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    main()
