"""
db.py — Camada de dados do RESOLVE AI (SQLite local).

Responsabilidades:
- Criar/garantir o schema no arquivo resolve_ai.db na inicialização.
- CRUD de usuários e itens (lembretes, despesas, documentos).
- Funções de consulta usadas pelo Dashboard e pelo Scheduler.

Zero dependências externas: apenas stdlib (sqlite3, datetime).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, date, timedelta
import tempo
from pathlib import Path
from typing import Any, Optional

# Configurável por env (deploy em volume/VPS); default = pasta do projeto.
DB_PATH = Path(os.environ.get("DB_PATH",
                              Path(__file__).parent / "resolve_ai.db"))

VALID_ITEM_TYPES = ("lembrete", "despesa", "documento")
VALID_STATUSES = ("pendente", "concluido", "aglutinado", "vencido")
VALID_CATEGORIES = ("Alimentação", "Pet", "Veículo", "Contas", "Saúde",
                    "Casa", "Lazer", "Outros")


# ---------------------------------------------------------------------------
# Conexão e schema
# ---------------------------------------------------------------------------

def get_conn() -> sqlite3.Connection:
    """Abre conexão com row_factory de dicionário."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


_MSGLOG_DDL = """
CREATE TABLE IF NOT EXISTS msg_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                telefone    TEXT,
                direcao     TEXT NOT NULL CHECK (direcao IN ('in','out')),
                tipo        TEXT,
                preview     TEXT,
                ts          TEXT NOT NULL
            );
"""


_ITEMS_DDL = """
CREATE TABLE IF NOT EXISTS items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                tipo            TEXT NOT NULL CHECK (tipo IN ('lembrete','despesa','documento')),
                categoria       TEXT NOT NULL,
                descricao       TEXT NOT NULL,
                valor_reais     REAL,
                data_vencimento TEXT,
                hora_alvo       TEXT,
                recorrencia     TEXT,
                status          TEXT NOT NULL DEFAULT 'pendente'
                                CHECK (status IN ('pendente','concluido','aglutinado','vencido')),
                link_afiliado   TEXT,
                data_criacao    TEXT NOT NULL
            );
"""


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
                hora_alvo       TEXT,
                recorrencia     TEXT,
                status          TEXT NOT NULL DEFAULT 'pendente'
                                CHECK (status IN ('pendente','concluido','aglutinado','vencido')),
                link_afiliado   TEXT,
                data_criacao    TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_items_user   ON items(user_id);
            CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
            CREATE INDEX IF NOT EXISTS idx_items_venc   ON items(data_vencimento);

            CREATE TABLE IF NOT EXISTS msg_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                telefone    TEXT,
                direcao     TEXT NOT NULL CHECK (direcao IN ('in','out')),
                tipo        TEXT,
                preview     TEXT,
                ts          TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msglog_ts ON msg_log(ts);
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
        # items: coluna de horário-alvo (v6.1)
        item_cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
        if "hora_alvo" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN hora_alvo TEXT")
        if "recorrencia" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN recorrencia TEXT")
        if "recorrencia" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN recorrencia TEXT")
        # v6.5: CHECK antigo de status não conhece 'vencido' -> rebuild
        sql_items = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='items'").fetchone()
        if sql_items and "vencido" not in (sql_items["sql"] or ""):
            conn.executescript("""
                ALTER TABLE items RENAME TO items_old;
            """)
            conn.executescript(_ITEMS_DDL)
            cols = ("id,user_id,tipo,categoria,descricao,valor_reais,"
                    "data_vencimento,hora_alvo,recorrencia,status,"
                    "link_afiliado,data_criacao")
            conn.execute(f"INSERT INTO items ({cols}) "
                         f"SELECT {cols} FROM items_old")
            conn.execute("DROP TABLE items_old")
        # v6.3: log de disparos proativos (dedup do motor)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dispatches (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                item_id   INTEGER,
                kind      TEXT NOT NULL,
                sent_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_disp_user ON dispatches(user_id, kind);
            CREATE INDEX IF NOT EXISTS idx_disp_item ON dispatches(item_id, kind);
            """
        )


def _now_iso() -> str:
    return tempo.agora().strftime("%Y-%m-%d %H:%M:%S")


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


def trial_days_left_raw(user: dict, trial_days: int = 7) -> int:
    """Dias restantes do trial SEM clamp (negativo = expirado há N dias)."""
    created = datetime.strptime(user["data_criacao"], "%Y-%m-%d %H:%M:%S")
    elapsed = (tempo.agora() - created).days
    return trial_days - elapsed


def trial_days_left(user: dict, trial_days: int = 7) -> int:
    """Dias restantes do teste grátis (>= 0, para exibição)."""
    return max(0, trial_days_left_raw(user, trial_days))


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
    return (tempo.agora() - created).days


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
            and u.get("onboarding_step") == "done"
            and trial_days_left_raw(u, trial_days) >= 0]


def set_status(user_id: int, status: str) -> None:
    """trial | ativo | cancelado"""
    update_user_fields(user_id, status=status)


def delete_user(user_id: int) -> None:
    """Exclusão LGPD: apaga o usuário e todos os seus itens (CASCADE)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def set_created_days_ago(user_id: int, days: int) -> None:
    """Utilitário de teste: retrocede data_criacao (simula fim de trial)."""
    when = (tempo.agora() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("UPDATE users SET data_criacao=? WHERE id=?",
                     (when, user_id))


def trial_ending_users(days_left: int = 1, trial_days: int = 7) -> list[dict]:
    """Usuários em trial cujo teste termina em exatamente N dias."""
    return [u for u in list_users()
            if (u.get("status") or "trial") == "trial"
            and (u.get("onboarding_step") or "done") == "done"
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
    when = (tempo.agora() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
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
    hora_alvo: Optional[str] = None,        # 'HH:MM' ou None
    recorrencia: Optional[str] = None,      # 'diaria'|'mensal:20'|'semanal:2'|'horas:8'
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
                data_vencimento, hora_alvo, recorrencia, status,
                link_afiliado, data_criacao)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, tipo, categoria, descricao, valor_reais,
             data_vencimento, hora_alvo, recorrencia, status,
             link_afiliado, _now_iso()),
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
    ref = ref or tempo.agora()
    cutoff = (ref - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE ultima_interacao < ?", (cutoff,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Disparos proativos — log e dedup (v6.3)
# ---------------------------------------------------------------------------

def log_dispatch(user_id: int, kind: str, item_id: Optional[int] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO dispatches (user_id, item_id, kind, sent_at) "
            "VALUES (?,?,?,?)",
            (user_id, item_id, kind, _now_iso()))


def dispatched_today(kind: str, user_id: int,
                     item_id: Optional[int] = None) -> bool:
    """Já houve disparo deste tipo hoje (para este item, se informado)?"""
    today = date.today().isoformat()
    q = ("SELECT 1 FROM dispatches WHERE user_id=? AND kind=? "
         "AND sent_at >= ? ")
    args: list = [user_id, kind, today]
    if item_id is not None:
        q += "AND item_id=? "
        args.append(item_id)
    with get_conn() as conn:
        return conn.execute(q + "LIMIT 1", args).fetchone() is not None


def dispatched_within(kind: str, user_id: int, days: int) -> bool:
    """Já houve disparo deste tipo nos últimos N dias (por usuário)?"""
    cutoff = (tempo.agora() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM dispatches WHERE user_id=? AND kind=? "
            "AND sent_at >= ? LIMIT 1", (user_id, kind, cutoff)
        ).fetchone() is not None


def dispatched_ever(kind: str, user_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM dispatches WHERE user_id=? AND kind=? LIMIT 1",
            (user_id, kind)).fetchone() is not None


def items_due_at_time(now: Optional[datetime] = None) -> list[dict]:
    """Itens pendentes de HOJE com hora_alvo <= agora (alarme intraday)."""
    now = now or tempo.agora()
    today = now.date().isoformat()
    hhmm = now.strftime("%H:%M")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.*, u.nome AS user_nome, u.telefone
               FROM items i JOIN users u ON u.id = i.user_id
               WHERE i.status='pendente'
                 AND i.data_vencimento = ?
                 AND i.hora_alvo IS NOT NULL
                 AND i.hora_alvo <= ?""", (today, hhmm)).fetchall()
    return [dict(r) for r in rows]


def postpone_item(item_id: int, new_date: Optional[str] = None,
                  new_time: Optional[str] = None) -> None:
    """Adia um item: atualiza data e/ou hora e o reabre para novo disparo."""
    with get_conn() as conn:
        if new_date:
            conn.execute("UPDATE items SET data_vencimento=? WHERE id=?",
                         (new_date, item_id))
        if new_time:
            conn.execute("UPDATE items SET hora_alvo=? WHERE id=?",
                         (new_time, item_id))
        # limpa o log de 'hora' de hoje para permitir novo alarme
        conn.execute(
            "DELETE FROM dispatches WHERE item_id=? AND kind='hora' "
            "AND sent_at >= ?", (item_id, date.today().isoformat()))


def last_alarmed_item(user_id: int) -> Optional[dict]:
    """Item pendente de hoje com hora_alvo (o alvo natural de um 'adiar')."""
    today = date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM items WHERE user_id=? AND status='pendente'
               AND data_vencimento=? AND hora_alvo IS NOT NULL
               ORDER BY id DESC LIMIT 1""", (user_id, today)).fetchone()
    return dict(row) if row else None


def dispatch_count(kind: str, user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM dispatches WHERE user_id=? AND kind=?",
            (user_id, kind)).fetchone()["c"]


def dispatched_ever_item(kind: str, item_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM dispatches WHERE item_id=? AND kind=? LIMIT 1",
            (item_id, kind)).fetchone() is not None


def overdue_items(days_ago: int, ref: Optional[date] = None) -> list[dict]:
    """Itens pendentes vencidos há exatamente/mais que N dias (sem recorrência)."""
    ref = ref or date.today()
    alvo = (ref - timedelta(days=days_ago)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.*, u.nome AS user_nome, u.telefone
               FROM items i JOIN users u ON u.id=i.user_id
               WHERE i.status='pendente' AND i.recorrencia IS NULL
                 AND i.data_vencimento IS NOT NULL
                 AND i.data_vencimento <= ?""", (alvo,)).fetchall()
    return [dict(r) for r in rows]


def recurring_to_roll(ref: Optional[date] = None) -> list[dict]:
    """Itens recorrentes cuja ocorrência já passou (concluída ou vencida)."""
    ref = ref or date.today()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM items WHERE recorrencia IS NOT NULL
               AND (status='concluido'
                    OR (status='pendente' AND data_vencimento < ?))""",
            (ref.isoformat(),)).fetchall()
    return [dict(r) for r in rows]


def roll_item(item_id: int, new_date: str, new_time: Optional[str]) -> None:
    """Rola um recorrente para a próxima ocorrência e reabre os disparos."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE items SET data_vencimento=?, hora_alvo=?, "
            "status='pendente' WHERE id=?", (new_date, new_time, item_id))
        conn.execute(
            "DELETE FROM dispatches WHERE item_id=? AND kind IN "
            "('vencido','arquivado')", (item_id,))


def archive_item(item_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE items SET status='aglutinado' WHERE id=?",
                     (item_id,))


def roll_items_batch(rolls: list[tuple]) -> None:
    """Rola vários recorrentes numa única conexão: [(id, data, hora), ...]."""
    if not rolls:
        return
    with get_conn() as conn:
        conn.executemany(
            "UPDATE items SET data_vencimento=?, hora_alvo=?, "
            "status='pendente' WHERE id=?",
            [(d, h, i) for (i, d, h) in rolls])
        conn.executemany(
            "DELETE FROM dispatches WHERE item_id=? AND kind IN "
            "('hora','vencimento','vencido')", [(i,) for (i, _, _) in rolls])


def dispatched_ever_item(kind: str, item_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM dispatches WHERE item_id=? AND kind=? LIMIT 1",
            (item_id, kind)).fetchone() is not None


def recurring_items_past(ref_iso: str) -> list[dict]:
    """Itens recorrentes cuja data já passou (para rolar à próxima ocorrência)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM items WHERE recorrencia IS NOT NULL
               AND data_vencimento IS NOT NULL AND data_vencimento < ?""",
            (ref_iso,)).fetchall()
    return [dict(r) for r in rows]


def roll_item(item_id: int, nova_data: str) -> None:
    """Rola item recorrente: nova data, reabre, limpa dedup de disparos."""
    with get_conn() as conn:
        conn.execute("UPDATE items SET data_vencimento=?, status='pendente' "
                     "WHERE id=?", (nova_data, item_id))
        conn.execute("DELETE FROM dispatches WHERE item_id=? AND "
                     "kind IN ('hora','vencimento','1-click-buy','vencido')",
                     (item_id,))


def overdue_items_on(dia_iso: str) -> list[dict]:
    """Itens NÃO-recorrentes pendentes que venceram exatamente em dia_iso."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.*, u.nome AS user_nome, u.telefone, u.status u_status
               FROM items i JOIN users u ON u.id=i.user_id
               WHERE i.status='pendente' AND i.recorrencia IS NULL
                 AND i.data_vencimento = ?""", (dia_iso,)).fetchall()
    return [dict(r) for r in rows]


def user_can_receive(user: dict, trial_days: int = 7) -> bool:
    """Usuário elegível a disparos proativos: ativo, ou trial ainda válido."""
    st = (user.get("status") or "trial")
    if st == "ativo":
        return True
    if st == "trial":
        return trial_days_left_raw(user, trial_days) >= 0
    return False


def winback_candidates(trial_days: int = 7, days_after: int = 3) -> list[dict]:
    """Trials expirados há exatamente N dias (para 1 única mensagem winback)."""
    return [u for u in list_users()
            if (u.get("status") or "trial") == "trial"
            and trial_days_left_raw(u, trial_days) == -days_after]


def items_due_all(days: int = 3, ref: Optional[date] = None) -> list[dict]:
    """TODOS os itens pendentes vencendo em até N dias, com dados do dono.
    Uma query só — substitui o loop por usuário (performance em escala)."""
    ref = ref or date.today()
    fim = (ref + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.*, u.nome AS user_nome, u.telefone
               FROM items i JOIN users u ON u.id = i.user_id
               WHERE i.status='pendente' AND i.data_vencimento IS NOT NULL
                 AND i.data_vencimento BETWEEN ? AND ?
               ORDER BY i.data_vencimento""", (ref.isoformat(), fim)).fetchall()
    return [dict(r) for r in rows]


def items_overdue(min_days: int, max_days: int,
                  ref: Optional[date] = None) -> list[dict]:
    """Itens pendentes vencidos entre min e max dias atrás (follow-up)."""
    ref = ref or date.today()
    ini = (ref - timedelta(days=max_days)).isoformat()
    fim = (ref - timedelta(days=min_days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.*, u.nome AS user_nome, u.telefone
               FROM items i JOIN users u ON u.id = i.user_id
               WHERE i.status='pendente' AND i.data_vencimento IS NOT NULL
                 AND i.data_vencimento BETWEEN ? AND ?""", (ini, fim)).fetchall()
    return [dict(r) for r in rows]


def log_message(user_id, telefone, direcao, tipo, preview):
    """Registra uma mensagem (in/out) para o painel de acompanhamento."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO msg_log (user_id, telefone, direcao, tipo, preview, ts) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, telefone, direcao, tipo,
                 (preview or "")[:120], tempo.agora().isoformat(timespec="seconds")))
    except Exception:
        pass


def painel_metricas() -> dict:
    """Snapshot de métricas para o dashboard em tempo real."""
    with get_conn() as conn:
        def one(q, *a):
            r = conn.execute(q, a).fetchone()
            return r[0] if r else 0
        hoje = date.today().isoformat()
        m = {
            "total_users": one("SELECT COUNT(*) FROM users"),
            "ativos": one("SELECT COUNT(*) FROM users WHERE status='ativo'"),
            "trial": one("SELECT COUNT(*) FROM users WHERE status='trial'"),
            "users_hoje": one("SELECT COUNT(*) FROM users WHERE substr(data_criacao,1,10)=?", hoje),
            "itens_total": one("SELECT COUNT(*) FROM items"),
            "itens_pendentes": one("SELECT COUNT(*) FROM items WHERE status='pendente'"),
            "itens_hoje": one("SELECT COUNT(*) FROM items WHERE substr(data_criacao,1,10)=?", hoje),
            "msgs_hoje": one("SELECT COUNT(*) FROM msg_log WHERE substr(ts,1,10)=?", hoje),
            "msgs_in_hoje": one("SELECT COUNT(*) FROM msg_log WHERE direcao='in' AND substr(ts,1,10)=?", hoje),
            "msgs_out_hoje": one("SELECT COUNT(*) FROM msg_log WHERE direcao='out' AND substr(ts,1,10)=?", hoje),
            "disparos_hoje": one("SELECT COUNT(*) FROM dispatches WHERE substr(sent_at,1,10)=?", hoje),
        }
        m["mrr"] = round(m["ativos"] * 19.90, 2)
        # últimas 30 mensagens
        rows = conn.execute(
            "SELECT direcao, tipo, preview, ts, telefone FROM msg_log "
            "ORDER BY id DESC LIMIT 30").fetchall()
        m["ultimas"] = [dict(r) for r in rows]
        return m
