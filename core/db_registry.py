import os
import pyodbc

_DEF_NAME = 'mssql_default'

def default_conn_name():
    return _DEF_NAME

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

def get_conn(name=_DEF_NAME, override: dict | None = None):
    if name != _DEF_NAME:
        raise ValueError('Only default MSSQL connection configured in MVP')

    if override:
        driver   = override.get('driver')   or os.getenv('MSSQL_DRIVER', 'ODBC Driver 17 for SQL Server')
        server   = override.get('server')   or os.getenv('MSSQL_SERVER', 'localhost')
        database = override.get('database') or os.getenv('MSSQL_DATABASE', 'master')
        username = override.get('username') or os.getenv('MSSQL_USERNAME', '')
        password = override.get('password') or os.getenv('MSSQL_PASSWORD', '')
        port     = override.get('port')     or os.getenv('MSSQL_PORT', '1433')
        cnx_str  = _compose_cnx_str(driver, server, database, username, password, port)
    else:
        cnx_str  = _build_mssql_cnx_from_env()

    return pyodbc.connect(cnx_str, autocommit=True)

def close_conn(conn):
    try:
        conn.close()
    except Exception:
        pass
