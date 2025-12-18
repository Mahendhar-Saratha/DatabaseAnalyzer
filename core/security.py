import os
import re

_BLOCK = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|CREATE|EXEC|EXECUTE|GRANT|REVOKE)\b", re.I)


def is_safe_sql(sql: str) -> bool:
    if os.getenv('NLQ_ALLOW_DML', '0') == '1' and os.getenv('NLQ_ALLOW_DDL', '0') == '1':
        return True
    return _BLOCK.search(sql or '') is None