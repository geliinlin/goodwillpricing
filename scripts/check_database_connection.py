"""Read-only connectivity and core-table check for the configured MySQL schema.

Run from project root: python -m scripts.check_database_connection
This script never modifies database records.
"""

import mysql.connector

from database import get_connection


CORE_TABLES = (
    "USER_ACCOUNT", "ADMINISTRATOR", "STORE_STAFF", "BRANCH",
    "CATEGORY", "BRAND", "ITEM_CONDITION", "ITEM_TYPE",
    "PRICING_GUIDELINE", "ITEM", "PRICING_DECISION",
)


def main() -> None:
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE()")
        current_db = cursor.fetchone()
        database_name = current_db[0] if current_db else None
        if not database_name:
            raise RuntimeError("No MySQL schema is selected. Check the database setting.")

        print(f"Connected to database: {database_name}")
        for table in CORE_TABLES:
            # These identifiers are constants, never taken from user input.
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            result = cursor.fetchone()
            print(f"{table}: {int(result[0]) if result else 0} row(s)")
        print("Database connection and all 11 core tables: OK (read-only)")
    except (mysql.connector.Error, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Database check failed: {exc}") from None
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    main()
