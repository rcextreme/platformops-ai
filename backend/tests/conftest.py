import atexit
import os
import shutil
import tempfile

# app.database builds its engine at import time from DATABASE_URL. Point it at
# a throwaway SQLite file before anything imports it, overriding any DATABASE_URL
# exported in the shell, so tests can never reach PostgreSQL.
_TEST_DIR = tempfile.mkdtemp(prefix="platformops-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR}/import-time.db"
atexit.register(shutil.rmtree, _TEST_DIR, ignore_errors=True)
