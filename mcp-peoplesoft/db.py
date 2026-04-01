"""
Database connection management for mcp-peoplesoft.
Async Oracle DB com suporte a pool de conexões e query com limite de linhas.
Compatível com oracledb thin mode (sem Oracle Client instalado).
"""
import os
import oracledb
from typing import Any
from dotenv import load_dotenv

load_dotenv()


def get_connection_params() -> dict[str, str]:
    """Lê credenciais das variáveis de ambiente (dotenv ou sistema)."""
    # Suporta tanto o padrão rgrz (ORACLE_*) quanto o nosso (PS_DB_*)
    dsn      = os.getenv("ORACLE_DSN")      or os.getenv("PS_DB_DSN")
    user     = os.getenv("ORACLE_USER")     or os.getenv("PS_DB_USER")
    password = os.getenv("ORACLE_PASSWORD") or os.getenv("PS_DB_PASSWORD")

    if not all([dsn, user, password]):
        raise ValueError(
            "Credenciais Oracle não configuradas.\n"
            "Defina ORACLE_DSN, ORACLE_USER e ORACLE_PASSWORD "
            "(ou PS_DB_DSN, PS_DB_USER, PS_DB_PASSWORD) no arquivo .env"
        )
    return {"dsn": dsn, "user": user, "password": password}


async def execute_query(
    sql: str,
    params: list[Any] | None = None,
    fetch_one: bool = False,
) -> dict:
    """
    Executa uma query SELECT e retorna os resultados como lista de dicts.

    Args:
        sql:       SQL a executar (deve ser SELECT)
        params:    Parâmetros posicionais (:1, :2, ...)
        fetch_one: Se True, retorna apenas a primeira linha

    Returns:
        {"results": [...]} ou {"error": "mensagem"}
    """
    if params is None:
        params = []

    try:
        conn_params = get_connection_params()
        async with oracledb.connect_async(
            user=conn_params["user"],
            password=conn_params["password"],
            dsn=conn_params["dsn"],
        ) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)

                if cursor.description is None:
                    return {"results": [], "message": "Query executada (sem resultados)"}

                columns = [col[0] for col in cursor.description]

                if fetch_one:
                    row = await cursor.fetchone()
                    return {"results": [dict(zip(columns, row))] if row else []}

                rows = await cursor.fetchall()
                return {"results": [dict(zip(columns, row)) for row in rows]}

    except oracledb.Error as e:
        return {"error": f"Erro Oracle: {str(e)}"}
    except ValueError as e:
        return {"error": str(e)}


async def execute_query_with_limit(
    sql: str,
    params: list[Any] | None = None,
    limit: int = 100,
) -> dict:
    """
    Executa query com limite de linhas para evitar leituras acidentais de grandes volumes.

    Returns:
        {"results": [...], "truncated": bool, "row_count": int} ou {"error": "..."}
    """
    if params is None:
        params = []

    try:
        conn_params = get_connection_params()
        async with oracledb.connect_async(
            user=conn_params["user"],
            password=conn_params["password"],
            dsn=conn_params["dsn"],
        ) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)

                if cursor.description is None:
                    return {"results": [], "truncated": False, "row_count": 0}

                columns = [col[0] for col in cursor.description]
                rows    = await cursor.fetchmany(limit + 1)

                truncated = len(rows) > limit
                if truncated:
                    rows = rows[:limit]

                return {
                    "results":   [dict(zip(columns, row)) for row in rows],
                    "truncated": truncated,
                    "row_count": len(rows),
                }

    except oracledb.Error as e:
        return {"error": f"Erro Oracle: {str(e)}"}
    except ValueError as e:
        return {"error": str(e)}


def execute_query_sync(
    sql: str,
    params: dict | None = None,
    max_rows: int = 500,
) -> list[dict]:
    """
    Versão síncrona para uso em contextos não-async (ex: ferramentas de trace).

    Returns:
        Lista de dicts com os resultados.
    Raises:
        ConnectionError, RuntimeError
    """
    conn_params = get_connection_params()
    try:
        conn = oracledb.connect(
            user=conn_params["user"],
            password=conn_params["password"],
            dsn=conn_params["dsn"],
        )
        try:
            cur = conn.cursor()
            cur.execute(sql, params or {})
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(max_rows)
            return [dict(zip(cols, row)) for row in rows]
        finally:
            conn.close()
    except oracledb.Error as e:
        raise RuntimeError(f"Erro Oracle: {e}") from e
