import os
import pyodbc

_DEF_NAME = 'mssql_default'

def default_conn_name():
    return _DEF_NAME

def clean_env_value(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if len(s) >= 2 and (
        (s[0] == s[-1] == "'") or
        (s[0] == s[-1] == '"')
    ):
        s = s[1:-1].strip()
    return s


def clean_driver_value(val) -> str:
    s = clean_env_value(val)
    if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
        s = s[1:-1].strip()
    return s

def _compose_cnx_str(driver: str, server: str, database: str, username: str, password: str, port: str | None):
    """
    Build a valid SQL Server ODBC connection string.
    """
    parts = [f"DRIVER={{{driver}}}"]

    # If user already supplied host\instance or host,port, don't re-append port
    if ("\\" in server) or ("," in server) or not port:
        parts.append(f"SERVER={server}")
    else:
        parts.append(f"SERVER={server},{port}")

    parts.append(f"DATABASE={database}")

    if username and password:
        parts.append(f"UID={username}")
        parts.append(f"PWD={password}")
    else:
        # Windows authentication when no SQL credentials provided
        parts.append("Trusted_Connection=Yes")

    parts.append("Encrypt=Yes")
    parts.append("TrustServerCertificate=Yes")
    parts.append("MARS_Connection=Yes")

    return ";".join(parts) + ";"

def _build_mssql_cnx_from_env():
    driver   = os.getenv('MSSQL_DRIVER', 'ODBC Driver 17 for SQL Server')
    server   = os.getenv('MSSQL_SERVER', 'localhost')
    database = os.getenv('MSSQL_DATABASE', 'master')
    username = os.getenv('MSSQL_USERNAME', '')
    password = os.getenv('MSSQL_PASSWORD', '')
    port     = os.getenv('MSSQL_PORT', '1433')

    return _compose_cnx_str(driver, server, database, username, password, port)

def get_conn(name: str = None, override: dict | None = None):
    if override:
        server   = clean_env_value(override.get("server"))
        database = clean_env_value(override.get("database"))
        username = clean_env_value(override.get("username"))
        password = clean_env_value(override.get("password"))
        port     = clean_env_value(override.get("port")) or "1433"
        driver   = clean_driver_value(
            override.get("driver") or "ODBC Driver 17 for SQL Server"
        )
    else:
        server   = clean_env_value(os.getenv("MSSQL_SERVER", "localhost"))
        database = clean_env_value(os.getenv("MSSQL_DATABASE", "master"))
        username = clean_env_value(os.getenv("MSSQL_USERNAME", "sa"))
        password = clean_env_value(os.getenv("MSSQL_PASSWORD", ""))
        port     = clean_env_value(os.getenv("MSSQL_PORT", "1433"))
        driver   = clean_driver_value(
            os.getenv("MSSQL_DRIVER", "ODBC Driver 17 for SQL Server")
        )

    if driver and not (driver.startswith("{") and driver.endswith("}")):
        driver = "{" + driver + "}"

    cnx_str = (
        f"DRIVER={driver};"
        f"SERVER={server},{port};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
    )

    return pyodbc.connect(cnx_str, autocommit=True)

def close_conn(conn):
    try:
        conn.close()
    except Exception:
        pass
