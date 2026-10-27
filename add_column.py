from sqlalchemy import text, inspect
from models import engine

def add_column_if_missing(table: str, column: str, ddl_type: str) -> None:
    """Add a column only if it doesn't already exist. ddl_type e.g. 'INTEGER' or 'BOOLEAN NOT NULL DEFAULT 0'."""
    insp = inspect(engine)
    existing = [c["name"] for c in insp.get_columns(table)]
    if column in existing:
        print(f"{table}.{column} already exists, skipping.")
        return

    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl_type}'))
    print(f"Added {table}.{column}.")

name_of_table=input('Enter the name of the table: ')
name_of_column=input('Enter the name of the column: ')
data_type=input('Enter the name of the data type eg;INTERGER, VARCHAR(20), BOOLEAN NOT NULL DEFAULT 0:')

# Usage
add_column_if_missing(name_of_table, name_of_column, data_type)