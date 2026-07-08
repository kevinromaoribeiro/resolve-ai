"""
db.py — Camada de dados do RESOLVE AI (SQLite local).

Responsabilidades:
- Criar/garantir o schema no arquivo resolve_ai.db na inicialização.
- CRUD de usuários e itens (lembretes, despesas, documentos).
- Funções de consulta usadas pelo Dashboard e pelo Scheduler.

Zero dependências externas: apenas stdlib (sqlite3, datetime).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent / "resolve_ai.db"

VALID_ITEM_TYPES = ("lembrete", "despesa", "documento")
VALID_STATUSES = ("pendente", "concluido", "aglutinado")
VALID_CATEGORIES = ("Alimentação", "Pet", "Veículo", "Contas", "Lazer", "Outros")


# ---------------------------------------------------------------------------
# Conexão e schema
# ---------------------------------------------------------------------------

def get_conn() -> sqlite3.Connection:
    """Abre conexão com row_factory de dicionário."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    """Cria as tabelas se não existirem. Idempotente."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                nome              TEXT NOT NULL,
                telefone          TEXT NOT NULL UNIQUE,
                idade             INTEGER,
                profissao         TEXT,
                interesses        TEXT,
                carro_modelo      TEXT,
                carro_km          INTEGER,
                pet_info          TEXT,
                dia_resumo        TEXT DEFAULT 'Segunda-feira',
                data_criacao      TEXT NOT NULL,
                ultima_interacao  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                tipo            TEXT NOT NULL CHECK (tipo IN ('lembrete','despesa','documento')),
                categoria       TEXT NOT NULL,
                descricao       TEXT NOT NULL,
                valor_reais     REAL,
                data_vencimento TEXT,
                status          TEXT NOT NULL DEFAULT 'pendente'
                                CHECK (status IN ('pendente','concluido','aglutinado')),
                link_afiliado   TEXT,
                data_criacao    TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_items_user   ON items(user_id);
            CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
            CREATE INDEX IF NOT EXISTS idx_items_venc   ON items(data_vencimento);
            """
        )
        # Migração leve: adiciona colunas novas em bancos criados antes delas
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        for col, ddl in [("idade", "INTEGER"), ("profissao", "TEXT"),
                         ("interesses", "TEXT"),
                         ("status", "TEXT DEFAULT 'trial'"),
                         ("onboarding_step", "TEXT"),
                         ("trial_nudges_sent", "TEXT DEFAULT ''")]:
            if col not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Usuários
# ---------------------------------------------------------------------------

def create_user(
    nome: str,
    telefone: str,
    idade: Optional[int] = None,
    profissao: Optional[str] = None,
    interesses: Optional[str] = None,   # CSV: "contas,mercado,carro"
    carro_modelo: Optional[str] = None,
    carro_km: Optional[int] = None,
    pet_info: Optional[str] = None,
    dia_resumo: str = "Segunda-feira",
) -> int:
    """Cria (ou atualiza, se telefone já existir) um usuário. Retorna o id."""
    now = _now_iso()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE telefone = ?", (telefone,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE users SET nome=?, idade=?, profissao=?, interesses=?,
                   carro_modelo=?, carro_km=?, pet_info=?, dia_resumo=?,
                   ultima_interacao=? WHERE id=?""",
                (nome, idade, profissao, interesses, carro_modelo, carro_km,
                 pet_info, dia_resumo, now, existing["id"]),
            )
            return int(existing["id"])
        cur = conn.execute(
            """INSERT INTO users
               (nome, telefone, idade, profissao, interesses, carro_modelo,
                carro_km, pet_info, dia_resumo, data_criacao, ultima_interacao)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (nome, telefone, idade, profissao, interesses, carro_modelo,
             carro_km, pet_info, dia_resumo, now, now),
        )
        return int(cur.lastrowid)


def trial_days_left(user: dict, trial_days: int = 7) -> int:
    """Dias restantes do teste grátis a partir de data_criacao."""
    created = datetime.strptime(user["data_criacao"], "%Y-%m-%d %H:%M:%S")
    elapsed = (datetime.now() - created).days
    return max(0, trial_days - elapsed)


def update_user_fields(user_id: int, **fields) -> None:
    """Atualiza campos arbitrários do usuário (whitelist de colunas)."""
    allowed = {"nome", "idade", "profissao", "interesses", "carro_modelo",
               "carro_km", "pet_info", "dia_resumo", "status",
               "onboarding_step", "trial_nudges_sent"}
    cols = {k: v for k, v in fields.items() if k in allowed}
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {sets} WHERE id=?",
                     (*cols.values(), user_id))


def trial_day_number(user: dict) -> int:
    """Em que dia do trial o usuário está (0 = dia da entrada, 1 = dia seguinte...)."""
    created = datetime.strptime(user["data_criacao"], "%Y-%m-%d %H:%M:%S")
    return (datetime.now() - created).days


def nudge_already_sent(user: dict, nudge_id: str) -> bool:
    sent = (user.get("trial_nudges_sent") or "").split(",")
    return nudge_id in sent


def mark_nudge_sent(user_id: int, nudge_id: str) -> None:
    user = get_user(user_id)
    sent = [s for s in (user.get("trial_nudges_sent") or "").split(",") if s]
    if nudge_id not in sent:
        sent.append(nudge_id)
    update_user_fields(user_id, trial_nudges_sent=",".join(sent))


def active_trial_users(trial_days: int = 7) -> list[dict]:
    """Usuários em trial, já com onboarding concluído, dentro do prazo."""
    return [u for u in list_users()
            if (u.get("status") or "trial") == "trial"
            and not u.get("onboarding_step")
            and trial_days_left(u, trial_days) > 0]


def set_status(user_id: int, status: str) -> None:
    """trial | ativo | cancelado"""
    update_user_fields(user_id, status=status)


def delete_user(user_id: int) -> None:
    """Exclusão LGPD: apaga o usuário e todos os seus itens (CASCADE)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def set_created_days_ago(user_id: int, days: int) -> None:
    """Utilitário de teste: retrocede data_criacao (simula fim de trial)."""
    when = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("UPDATE users SET data_criacao=? WHERE id=?",
                     (when, user_id))


def trial_ending_users(days_left: int = 1, trial_days: int = 7) -> list[dict]:
    """Usuários em trial cujo teste termina em exatamente N dias."""
    return [u for u in list_users()
            if (u.get("status") or "trial") == "trial"
            and not u.get("onboarding_step")
            and trial_days_left(u, trial_days) == days_left]


def get_user(user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def touch_user(user_id: int, when: Optional[str] = None) -> None:
    """Atualiza ultima_interacao (default: agora). `when` permite simular datas."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET ultima_interacao=? WHERE id=?",
            (when or _now_iso(), user_id),
        )


def set_last_interaction_days_ago(user_id: int, days: int) -> None:
    """Utilitário de teste: força ultima_interacao para N dias atrás."""
    when = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    touch_user(user_id, when)


# ---------------------------------------------------------------------------
# Itens
# ---------------------------------------------------------------------------

def add_item(
    user_id: int,
    tipo: str,
    categoria: str,
    descricao: str,
    valor_reais: Optional[float] = None,
    data_vencimento: Optional[str] = None,  # ISO 'YYYY-MM-DD'
    status: str = "pendente",
    link_afiliado: Optional[str] = None,
) -> int:
    if tipo not in VALID_ITEM_TYPES:
        raise ValueError(f"tipo inválido: {tipo!r}")
    if status not in VALID_STATUSES:
        raise ValueError(f"status inválido: {status!r}")
    if categoria not in VALID_CATEGORIES:
        categoria = "Outros"
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO items
               (user_id, tipo, categoria, descricao, valor_reais,
                data_vencimento, status, link_afiliado, data_criacao)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (user_id, tipo, categoria, descricao, valor_reais,
             data_vencimento, status, link_afiliado, _now_iso()),
        )
    touch_user(user_id)
    return int(cur.lastrowid)


def list_items(
    user_id: int,
    status: Optional[str] = None,
    tipo: Optional[str] = None,
) -> list[dict]:
    query = "SELECT * FROM items WHERE user_id=?"
    params: list[Any] = [user_id]
    if status:
        query += " AND status=?"
        params.append(status)
    if tipo:
        query += " AND tipo=?"
        params.append(tipo)
    query += " ORDER BY COALESCE(data_vencimento, data_criacao)"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_item_status(item_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"status inválido: {status!r}")
    with get_conn() as conn:
        conn.execute("UPDATE items SET status=? WHERE id=?", (status, item_id))


# ---------------------------------------------------------------------------
# Consultas para Dashboard e Scheduler
# ---------------------------------------------------------------------------

def month_spend(user_id: int, ref: Optional[date] = None) -> float:
    """Soma de despesas do mês corrente (por data_criacao)."""
    ref = ref or date.today()
    prefix = ref.strftime("%Y-%m")
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(valor_reais),0) AS total FROM items
               WHERE user_id=? AND tipo='despesa'
               AND substr(data_criacao,1,7)=?""",
            (user_id, prefix),
        ).fetchone()
        return float(row["total"])


def active_reminders_count(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM items
               WHERE user_id=? AND tipo='lembrete' AND status='pendente'""",
            (user_id,),
        ).fetchone()
        return int(row["n"])


def spend_by_category(user_id: int) -> dict[str, float]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT categoria, COALESCE(SUM(valor_reais),0) AS total
               FROM items WHERE user_id=? AND tipo='despesa'
               GROUP BY categoria ORDER BY total DESC""",
            (user_id,),
        ).fetchall()
        return {r["categoria"]: float(r["total"]) for r in rows}


def items_due_within(user_id: int, days: int = 3, ref: Optional[date] = None) -> list[dict]:
    """Itens pendentes com vencimento entre hoje e hoje+N dias (inclusive)."""
    ref = ref or date.today()
    start = ref.isoformat()
    end = (ref + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM items
               WHERE user_id=? AND status='pendente'
               AND data_vencimento IS NOT NULL
               AND data_vencimento BETWEEN ? AND ?
               ORDER BY data_vencimento""",
            (user_id, start, end),
        ).fetchall()
        return [dict(r) for r in rows]


def inactive_users(days: int = 10, ref: Optional[datetime] = None) -> list[dict]:
    """Usuários com ultima_interacao há mais de N dias."""
    ref = ref or datetime.now()
    cutoff = (ref - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE ultima_interacao < ?", (cutoff,)
        ).fetchall()
        return [dict(r) for r in rows]
