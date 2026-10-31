"""Small, local-only AtlasFlow Superset config override."""
import os
from urllib.parse import quote_plus

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
_db_password = quote_plus(os.environ["SUPERSET_DB_PASSWORD"])
SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://superset:{_db_password}@superset-db:5432/superset"
)

# The UI is bound to loopback in Compose. TLS is not configured in this local stack.
# Do not copy this local HTTP-only setting into a public/production deployment.
TALISMAN_ENABLED = False
WTF_CSRF_ENABLED = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
ROW_LIMIT = 5000
SQL_MAX_ROW = 10000
SUPERSET_WEBSERVER_TIMEOUT = 120
LOAD_EXAMPLES = False
FEATURE_FLAGS = {}
