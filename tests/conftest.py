import duckdb
import pytest

DB_PATH = "data/warehouse.duckdb"


@pytest.fixture(scope="session")
def con():
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
    except Exception:
        pytest.skip(f"{DB_PATH} not found - run the pipeline (download_data.py -> "
                     f"generate_ops_notes.py -> pipeline/load_raw.py -> staging.py -> marts.py) first.")
    yield conn
    conn.close()