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
import re
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
                direcao     TEXT NOT NULL
                            CHECK (direcao IN ('in','out','out_falhou')),
                tipo        TEXT,
                preview     TEXT,
                ts          TEXT NOT NULL
            );
CREATE INDEX IF NOT EXISTS idx_msglog_ts  ON msg_log(ts);
CREATE INDEX IF NOT EXISTS idx_msglog_tel ON msg_log(telefone, id);
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

            """ + _MSGLOG_DDL
        )
        # Migração leve: adiciona colunas novas em bancos criados antes delas
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        for col, ddl in [("idade", "INTEGER"), ("profissao", "TEXT"),
                         ("interesses", "TEXT"),
                         ("status", "TEXT DEFAULT 'trial'"),
                         ("onboarding_step", "TEXT"),
                         ("trial_nudges_sent", "TEXT DEFAULT ''"),
                         # v16: sem isto, um banco criado antes da coluna
                         # derruba TODO o resumo semanal com OperationalError
                         # dentro do cron — e o cron engole a exceção.
                         ("dia_resumo", "TEXT DEFAULT 'Segunda-feira'")]:
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
        # msg_log: o CHECK antigo só conhecia 'in'/'out'. Quando passamos a
        # registrar 'out_falhou' (envio recusado pela Wasender), o INSERT
        # violava o CHECK e o try/except do log_message engolia — a
        # instrumentação criada pra achar falha silenciosa falhava em
        # silêncio. Rebuild pra aceitar o novo valor.
        sql_log = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='msg_log'").fetchone()
        if sql_log and "out_falhou" not in (sql_log["sql"] or ""):
            conn.execute("ALTER TABLE msg_log RENAME TO msg_log_old")
            conn.executescript(_MSGLOG_DDL)
            conn.execute(
                "INSERT INTO msg_log (id,user_id,telefone,direcao,tipo,preview,ts) "
                "SELECT id,user_id,telefone,direcao,tipo,preview,ts FROM msg_log_old")
            conn.execute("DROP TABLE msg_log_old")

    # memória de longo prazo (fatos aprendidos sobre cada usuário)
    init_memoria()


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


def trial_days_left_raw(user: dict, trial_days: int = 14) -> int:
    """Dias restantes do trial SEM clamp (negativo = expirado há N dias)."""
    created = datetime.strptime(user["data_criacao"], "%Y-%m-%d %H:%M:%S")
    elapsed = (tempo.agora() - created).days
    return trial_days - elapsed


def trial_days_left(user: dict, trial_days: int = 14) -> int:
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


def active_trial_users(trial_days: int = 14) -> list[dict]:
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


def trial_ending_users(days_left: int = 1, trial_days: int = 14) -> list[dict]:
    """Usuários em trial cujo teste termina em exatamente N dias."""
    return [u for u in list_users()
            if (u.get("status") or "trial") == "trial"
            and (u.get("onboarding_step") or "done") == "done"
            and trial_days_left(u, trial_days) == days_left]


def get_user(user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_phone(phone: str) -> Optional[dict]:
    """Busca usuário pelo telefone (dígitos), usado pelo relatório do admin."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
        for r in rows:
            if "".join(c for c in (r["telefone"] or "") if c.isdigit()) == digits:
                return dict(r)
        return None


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
    ref = ref or tempo.hoje()
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


def spend_by_category_period(user_id: int, ini: str, fim: str) -> dict[str, float]:
    """Despesas por categoria REGISTRADAS entre ini e fim (ISO, inclusive).

    Filtra por data_criacao e não por data_vencimento: quando o usuário diz
    "gastei 80 no mercado", o que ele quer ver de volta é o dia em que contou.
    Categorias zeradas ficam de fora — linha com R$ 0,00 é ruído.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT categoria, COALESCE(SUM(valor_reais),0) AS total
               FROM items
               WHERE user_id=? AND tipo='despesa'
                 AND valor_reais IS NOT NULL
                 AND substr(data_criacao,1,10) BETWEEN ? AND ?
               GROUP BY categoria
               HAVING total > 0
               ORDER BY total DESC""",
            (user_id, ini, fim),
        ).fetchall()
        return {r["categoria"]: float(r["total"]) for r in rows}


def items_overdue_for_user(user_id: int,
                           ref: Optional[date] = None) -> list[dict]:
    """Itens pendentes DESTE usuário cuja data já passou.

    Existe `overdue_items`, mas ela é global (varre todo mundo, pra cobrança).
    Aqui o recorte é por usuário, para o resumo.
    """
    ref = ref or tempo.hoje()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM items
               WHERE user_id=? AND status='pendente'
                 AND data_vencimento IS NOT NULL
                 AND data_vencimento < ?
               ORDER BY data_vencimento""",
            (user_id, ref.isoformat()),
        ).fetchall()
        return [dict(r) for r in rows]


def items_due_within(user_id: int, days: int = 3, ref: Optional[date] = None) -> list[dict]:
    """Itens pendentes com vencimento entre hoje e hoje+N dias (inclusive)."""
    ref = ref or tempo.hoje()
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
    today = tempo.hoje().isoformat()
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
    """Itens pendentes de HOJE com hora_alvo <= agora (alarme intraday).
    Também casa itens sem data_vencimento (NULL) — reminders criados por
    frase relativa ("daqui 1 min") não têm data, só hora_alvo."""
    now = now or tempo.agora()
    today = now.date().isoformat()
    hhmm = now.strftime("%H:%M")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.*, u.nome AS user_nome, u.telefone
               FROM items i JOIN users u ON u.id = i.user_id
               WHERE i.status='pendente'
                 AND (i.data_vencimento = ? OR i.data_vencimento IS NULL)
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
            "AND sent_at >= ?", (item_id, tempo.hoje().isoformat()))


def ultimo_alarme_disparado(user_id: int,
                            horas: int = 12) -> Optional[dict]:
    """Item cujo alarme REALMENTE tocou por último. Alvo natural de "feito".

    `last_alarmed_item` devolve o item de maior id com hora marcada — que
    quase nunca é o que acabou de tocar. Em 03/08, o alarme das 16:47 era
    "definir próxima pós graduação" e a função devolvia "fruta" (id maior).
    Resultado: o usuário respondeu "Feito" e nada saiu da lista.

    Aqui a fonte é a tabela `dispatches`: quem tocou por último, tocou.
    """
    corte = (tempo.agora() - timedelta(hours=horas)).strftime(
        "%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        row = conn.execute(
            """SELECT i.* FROM dispatches d
               JOIN items i ON i.id = d.item_id
               WHERE d.user_id = ? AND d.kind IN ('hora','vencimento','vencido')
                 AND d.sent_at >= ? AND i.status = 'pendente'
               ORDER BY d.sent_at DESC, d.id DESC LIMIT 1""",
            (user_id, corte)).fetchone()
    return dict(row) if row else None


def last_alarmed_item(user_id: int) -> Optional[dict]:
    """Ultimo item que o bot ALARMOU e ainda esta pendente - o alvo natural
    de um 'feito' ou 'adiar' logo apos o alarme tocar. Prioriza o item cujo
    alarme de hora foi disparado mais recentemente (via log de dispatches);
    cai para o item de hoje com hora_alvo caso nao ache nenhum disparo."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT i.* FROM items i
               JOIN dispatches d ON d.item_id = i.id
               WHERE i.user_id=? AND i.status='pendente' AND d.kind='hora'
               ORDER BY d.sent_at DESC LIMIT 1""", (user_id,)).fetchone()
        if row:
            return dict(row)
        today = tempo.hoje().isoformat()
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
    ref = ref or tempo.hoje()
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
    ref = ref or tempo.hoje()
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


def recurring_items_past(ref_iso: str) -> list[dict]:
    """Itens recorrentes cuja data já passou (para rolar à próxima ocorrência)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM items WHERE recorrencia IS NOT NULL
               AND data_vencimento IS NOT NULL AND data_vencimento < ?""",
            (ref_iso,)).fetchall()
    return [dict(r) for r in rows]


def overdue_items_on(dia_iso: str) -> list[dict]:
    """Itens NÃO-recorrentes pendentes que venceram exatamente em dia_iso."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT i.*, u.nome AS user_nome, u.telefone, u.status u_status
               FROM items i JOIN users u ON u.id=i.user_id
               WHERE i.status='pendente' AND i.recorrencia IS NULL
                 AND i.data_vencimento = ?""", (dia_iso,)).fetchall()
    return [dict(r) for r in rows]


def user_can_receive(user: dict, trial_days: int = 14) -> bool:
    """Usuário elegível a disparos proativos: ativo, ou trial ainda válido."""
    st = (user.get("status") or "trial")
    if st == "ativo":
        return True
    if st == "trial":
        return trial_days_left_raw(user, trial_days) >= 0
    return False


def winback_candidates(trial_days: int = 14, days_after: int = 3) -> list[dict]:
    """Trials expirados há exatamente N dias (para 1 única mensagem winback)."""
    return [u for u in list_users()
            if (u.get("status") or "trial") == "trial"
            and trial_days_left_raw(u, trial_days) == -days_after]


def items_due_all(days: int = 3, ref: Optional[date] = None) -> list[dict]:
    """TODOS os itens pendentes vencendo em até N dias, com dados do dono.
    Uma query só — substitui o loop por usuário (performance em escala)."""
    ref = ref or tempo.hoje()
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
    ref = ref or tempo.hoje()
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
                 (preview or "")[:600], tempo.agora().isoformat(timespec="seconds")))
    except Exception:
        pass


def pulso_envio(horas: int = 24) -> dict:
    """Termômetro anti-bloqueio: o quanto o número está "falando" sozinho.

    Existe porque em 04/08 a Meta restringiu o número e não havia como saber
    que a gente estava perto do limite — só dava pra descobrir batendo nele.
    O que a Meta lê é RITMO: mensagens por minuto e proporção do que o bot
    inicia contra o que ele responde. Então é isso que se mede.
    """
    corte = (tempo.agora() - timedelta(hours=horas)).strftime(
        "%Y-%m-%d %H:%M:%S")
    hoje = tempo.hoje().isoformat()
    with get_conn() as conn:
        def _um(q, *a):
            r = conn.execute(q, a).fetchone()
            return (r[0] if r and r[0] is not None else 0)

        # pico de saídas num mesmo MINUTO (a assinatura que derruba)
        pico = _um("""SELECT MAX(c) FROM (
                        SELECT COUNT(*) c FROM msg_log
                        WHERE direcao='out' AND ts >= ?
                        GROUP BY substr(ts,1,16))""", corte)
        saidas = _um("SELECT COUNT(*) FROM msg_log WHERE direcao='out' "
                     "AND ts >= ?", corte)
        entradas = _um("SELECT COUNT(*) FROM msg_log WHERE direcao='in' "
                       "AND ts >= ?", corte)
        proativas = _um("SELECT COUNT(*) FROM dispatches WHERE sent_at >= ? "
                        "AND kind NOT IN ('admin-report')", corte)
        pior_user = conn.execute(
            """SELECT user_id, COUNT(*) c FROM dispatches
               WHERE substr(sent_at,1,10)=? AND kind NOT IN ('admin-report')
               GROUP BY user_id ORDER BY c DESC LIMIT 1""", (hoje,)).fetchone()
    # Bot que só responde é seguro; bot que só inicia é broadcaster.
    razao = round(proativas / entradas, 2) if entradas else (
        999 if proativas else 0)
    if pico >= 10 or razao >= 3:
        risco = "🔴 alto"
    elif pico >= 6 or razao >= 1.5:
        risco = "🟡 atenção"
    else:
        risco = "🟢 ok"
    return {
        "janela_horas": horas,
        "pico_por_minuto": pico,
        "saidas": saidas,
        "entradas": entradas,
        "proativas": proativas,
        "razao_proativa_por_recebida": razao,
        "usuario_mais_notificado": (dict(zip(("user_id", "n"), pior_user))
                                    if pior_user else None),
        "risco": risco,
    }


def serie_diaria(dias: int = 7) -> list[dict]:
    """Últimos N dias: usuários novos, demandas recebidas, itens e falhas.

    O painel antigo só mostrava HOJE. Um número solto não diz se está subindo
    ou caindo — e a única pergunta que importa no beta é justamente essa:
    as pessoas estão usando MAIS ou MENOS a cada dia?
    """
    hoje = tempo.hoje()
    saida = []
    with get_conn() as conn:
        def _um(q, *a):
            r = conn.execute(q, a).fetchone()
            return int(r[0]) if r and r[0] is not None else 0
        for i in range(dias - 1, -1, -1):
            d = (hoje - timedelta(days=i)).isoformat()
            saida.append({
                "dia": d,
                "rotulo": d[8:10] + "/" + d[5:7],
                "novos": _um("SELECT COUNT(*) FROM users WHERE "
                             "substr(data_criacao,1,10)=?", d),
                "recebidas": _um("SELECT COUNT(*) FROM msg_log WHERE "
                                 "direcao='in' AND substr(ts,1,10)=?", d),
                "enviadas": _um("SELECT COUNT(*) FROM msg_log WHERE "
                                "direcao='out' AND substr(ts,1,10)=?", d),
                "falhas": _um("SELECT COUNT(*) FROM msg_log WHERE "
                              "direcao='out_falhou' AND substr(ts,1,10)=?", d),
                "itens": _um("SELECT COUNT(*) FROM items WHERE "
                             "substr(data_criacao,1,10)=?", d),
                "disparos": _um("SELECT COUNT(*) FROM dispatches WHERE "
                                "substr(sent_at,1,10)=?", d),
                "ativos": _um("SELECT COUNT(DISTINCT user_id) FROM msg_log "
                              "WHERE direcao='in' AND substr(ts,1,10)=? "
                              "AND user_id IS NOT NULL", d),
            })
    return saida


def engajamento(excluir_telefones: Optional[list] = None) -> dict:
    """A métrica que decide se isto é um negócio: DESPEJOS POR PESSOA POR DIA.

    Não é quantos lembretes o bot disparou — é quantas vezes a pessoa
    espontaneamente jogou algo na cabeça dele. Abaixo de 1x/dia não virou
    hábito, e produto de hábito que não vira hábito não retém.

    O DONO NÃO ENTRA NA CONTA. Ele testa o dia inteiro, sabe onde apertar e
    nunca vai cancelar — contar as mensagens dele infla a métrica exatamente
    na direção em que a gente quer acreditar. É a armadilha clássica de
    fundador: medir o próprio uso e chamar de tração.

    (Para RISCO DE BLOQUEIO é o contrário: lá as mensagens do dono contam,
    porque a Meta não sabe quem é o dono. Ver `pulso_envio`.)
    """
    hoje = tempo.hoje()
    ini = (hoje - timedelta(days=6)).isoformat()
    fora = {re.sub(r"\D", "", t) for t in (excluir_telefones or []) if t}
    with get_conn() as conn:
        ids_fora = set()
        if fora:
            for row in conn.execute("SELECT id, telefone FROM users"):
                if re.sub(r"\D", "", row["telefone"] or "") in fora:
                    ids_fora.add(row["id"])
        filtro = ""
        args: list = [ini]
        if ids_fora:
            filtro = " AND m.user_id NOT IN (%s)" % ",".join("?" * len(ids_fora))
            args += list(ids_fora)
        r = conn.execute(
            f"""SELECT COUNT(*) n, COUNT(DISTINCT m.user_id) u
                FROM msg_log m WHERE m.direcao='in'
                  AND substr(m.ts,1,10) >= ? AND m.user_id IS NOT NULL{filtro}
            """, args).fetchone()
        n, u = (int(r[0]), int(r[1])) if r else (0, 0)
        top = conn.execute(
            f"""SELECT u.nome, COUNT(*) c FROM msg_log m
                JOIN users u ON u.id = m.user_id
                WHERE m.direcao='in' AND substr(m.ts,1,10) >= ?{filtro}
                GROUP BY m.user_id ORDER BY c DESC LIMIT 5""",
            args).fetchall()
        # quanto do tráfego era do próprio dono — pro tamanho do viés ficar visível
        rt = conn.execute(
            """SELECT COUNT(*) FROM msg_log WHERE direcao='in'
               AND substr(ts,1,10) >= ? AND user_id IS NOT NULL""",
            (ini,)).fetchone()
        total = int(rt[0]) if rt else 0
    por_dia = round(n / (u * 7), 2) if u else 0.0
    return {"despejos_7d": n, "pessoas": u, "por_pessoa_dia": por_dia,
            "dono_excluido": bool(ids_fora),
            "mensagens_do_dono_7d": max(0, total - n),
            "veredito": ("🟢 virou hábito" if por_dia >= 2 else
                         "🟡 no limite" if por_dia >= 1 else
                         "🔴 não virou hábito" if u else
                         "⚪ sem usuário real ainda"),
            "top": [{"nome": t[0], "n": t[1]} for t in top]}


PRECO_MENSAL = float(os.environ.get("PRECO_MENSAL", "19.90"))

# ── CUSTOS (edite no EasyPanel, não no código) ───────────────────────────
# Deixados prontos mesmo antes de existir pagamento, porque no dia em que o
# primeiro pagante entrar você vai querer saber o que sobra — e é justamente
# aí que ninguém para pra montar planilha.
#
# FIXOS: não mudam com o número de usuários.
CUSTO_VPS_MES = float(os.environ.get("CUSTO_VPS_MES", "0"))
CUSTO_WHATSAPP_MES = float(os.environ.get("CUSTO_WHATSAPP_MES", "0"))
CUSTO_DOMINIO_MES = float(os.environ.get("CUSTO_DOMINIO_MES", "0"))
CUSTO_OUTROS_MES = float(os.environ.get("CUSTO_OUTROS_MES", "0"))
# VARIÁVEIS: crescem com o uso. O de LLM é o que assusta em escala — o
# usuário mais engajado é o mais caro, que é a pior curva de custo possível.
CUSTO_LLM_POR_MSG = float(os.environ.get("CUSTO_LLM_POR_MSG", "0.02"))
CUSTO_MSG_ENVIADA = float(os.environ.get("CUSTO_MSG_ENVIADA", "0"))
# % que a plataforma de pagamento retém (Kirvano/Stripe/Mercado Pago)
TAXA_PAGAMENTO_PCT = float(os.environ.get("TAXA_PAGAMENTO_PCT", "0"))
# imposto sobre o faturamento (MEI/Simples). 0 até formalizar.
IMPOSTO_PCT = float(os.environ.get("IMPOSTO_PCT", "0"))


def financeiro(trial_days: int = 14) -> dict:
    """Dinheiro — com um aviso grande colado.

    NÃO EXISTE INTEGRAÇÃO DE PAGAMENTO. O status "ativo" é posto na mão pelo
    comando de admin, e o Kirvano nunca foi ligado. Então:

      • MRR aqui é ESTIMATIVA (assinantes × preço), não caixa recebido.
      • "Inadimplente" no sentido de assinou-e-não-pagou NÃO EXISTE ainda:
        sem cobrança recorrente, ninguém pode ficar devendo.
      • O que dá pra medir de verdade é o FUNIL: quem está no teste, quem
        está prestes a decidir, e quem saiu sem assinar.

    Chamar estimativa de faturamento num painel é o jeito mais rápido de
    tomar decisão com número que não existe. Por isso cada campo aqui diz o
    que é.
    """
    hoje = tempo.hoje()
    users = list_users()
    ativos, trial, cancelados, bloqueados = [], [], [], []
    for u in users:
        st = (u.get("status") or "trial")
        (ativos if st == "ativo" else
         cancelados if st == "cancelado" else
         bloqueados if st == "bloqueado" else trial).append(u)

    # quem decide nos próximos dias = pipeline real
    vencendo, expirados = [], []
    for u in trial:
        if (u.get("onboarding_step") or "done") != "done":
            continue
        faltam = trial_days_left_raw(u, trial_days)
        alvo = {"id": u["id"], "nome": u.get("nome"), "dias": faltam}
        if 0 <= faltam <= 3:
            vencendo.append(alvo)
        elif faltam < 0:
            expirados.append(alvo)

    decidiram = len(ativos) + len(expirados) + len(cancelados)
    conversao = round(len(ativos) / decidiram * 100, 1) if decidiram else None

    # ── BRUTO → LÍQUIDO ─────────────────────────────────────────────────
    bruto = round(len(ativos) * PRECO_MENSAL, 2)
    # tráfego dos últimos 30 dias projeta o custo variável do mês
    ini30 = (hoje - timedelta(days=29)).isoformat()
    with get_conn() as conn:
        def _um(q, *a):
            r = conn.execute(q, a).fetchone()
            return int(r[0]) if r and r[0] is not None else 0
        msgs_in = _um("SELECT COUNT(*) FROM msg_log WHERE direcao='in' "
                      "AND substr(ts,1,10) >= ?", ini30)
        msgs_out = _um("SELECT COUNT(*) FROM msg_log WHERE direcao='out' "
                       "AND substr(ts,1,10) >= ?", ini30)

    fixos = round(CUSTO_VPS_MES + CUSTO_WHATSAPP_MES + CUSTO_DOMINIO_MES
                  + CUSTO_OUTROS_MES, 2)
    # cada mensagem recebida vira ~1 chamada de LLM
    custo_llm = round(msgs_in * CUSTO_LLM_POR_MSG, 2)
    custo_envio = round(msgs_out * CUSTO_MSG_ENVIADA, 2)
    variaveis = round(custo_llm + custo_envio, 2)
    taxa = round(bruto * TAXA_PAGAMENTO_PCT / 100, 2)
    imposto = round(bruto * IMPOSTO_PCT / 100, 2)
    custo_total = round(fixos + variaveis + taxa + imposto, 2)
    liquido = round(bruto - custo_total, 2)
    # quantos assinantes só pra empatar
    margem_unit = PRECO_MENSAL * (1 - (TAXA_PAGAMENTO_PCT + IMPOSTO_PCT) / 100)
    custo_por_user = (variaveis / len(ativos)) if ativos else 0
    contrib = margem_unit - custo_por_user
    breakeven = (int(-(-fixos // contrib)) if contrib > 0 and fixos else
                 (0 if not fixos else None))

    return {
        "aviso": "MRR é ESTIMATIVA — não há integração de pagamento ligada",
        "assinantes": len(ativos),
        "mrr_estimado": bruto,
        "bruto": bruto,
        "liquido": liquido,
        "custo_total": custo_total,
        "custos": {"fixos": fixos, "llm": custo_llm, "envio": custo_envio,
                   "taxa_pagamento": taxa, "imposto": imposto},
        "msgs_30d": {"recebidas": msgs_in, "enviadas": msgs_out},
        "custo_por_assinante": round(custo_por_user, 2),
        "breakeven_assinantes": breakeven,
        "preco": PRECO_MENSAL,
        "em_teste": len(trial),
        "decidem_ate_3_dias": sorted(vencendo, key=lambda x: x["dias"]),
        "saiu_sem_assinar": len(expirados),
        "cancelados": len(cancelados),
        "bloqueados": len(bloqueados),
        "conversao_pct": conversao,
        "ja_decidiram": decidiram,
    }


def painel_metricas() -> dict:
    """Snapshot de métricas para o dashboard em tempo real."""
    with get_conn() as conn:
        def one(q, *a):
            r = conn.execute(q, a).fetchone()
            return r[0] if r else 0
        hoje = tempo.hoje().isoformat()
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


# ── Ações de admin para o painel ─────────────────────────────────────────
def admin_list_users() -> list[dict]:
    """Lista todos os usuários com dados úteis pro painel de admin."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT u.id, u.nome, u.telefone, u.status, u.data_criacao,
                      u.ultima_interacao, u.interesses,
                      (SELECT COUNT(*) FROM items i WHERE i.user_id=u.id) AS n_itens,
                      (SELECT COUNT(*) FROM items i WHERE i.user_id=u.id
                       AND i.status='pendente') AS n_pendentes
               FROM users u ORDER BY u.data_criacao DESC""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["dias_trial_restantes"] = trial_days_left(d)
        out.append(d)
    return out


def admin_extend_trial(user_id: int, dias_extra: int) -> bool:
    """Estende o trial adiantando a data_criacao (dá mais dias grátis)."""
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT data_criacao FROM users WHERE id=?",
                               (user_id,)).fetchone()
            if not row:
                return False
            base = datetime.strptime(row["data_criacao"], "%Y-%m-%d %H:%M:%S")
            nova = base + timedelta(days=dias_extra)
            conn.execute("UPDATE users SET data_criacao=?, status='trial' WHERE id=?",
                         (nova.strftime("%Y-%m-%d %H:%M:%S"), user_id))
        return True
    except Exception:
        return False


def admin_set_status(user_id: int, status: str) -> bool:
    """ativo | trial | cancelado | bloqueado."""
    if status not in ("ativo", "trial", "cancelado", "bloqueado"):
        return False
    try:
        with get_conn() as conn:
            conn.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))
        return True
    except Exception:
        return False


# ── Heartbeat do cron (monitorar se o motor está sendo chamado) ──────────
def registrar_cron_ping() -> None:
    """Marca que o /cron/proactive foi chamado agora (heartbeat)."""
    try:
        with get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS settings "
                         "(k TEXT PRIMARY KEY, v TEXT)")
            conn.execute("INSERT OR REPLACE INTO settings (k, v) VALUES "
                         "('last_cron', ?)",
                         (tempo.agora().isoformat(timespec="seconds"),))
    except Exception:
        pass


def ultimo_cron_ping() -> Optional[str]:
    """Retorna o timestamp do último ping do cron, ou None se nunca rodou."""
    try:
        with get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS settings "
                         "(k TEXT PRIMARY KEY, v TEXT)")
            r = conn.execute("SELECT v FROM settings WHERE k='last_cron'").fetchone()
            return r["v"] if r else None
    except Exception:
        return None


# ── Settings genéricos (k/v) — usados pelo watchdog e heartbeat ──────────
def set_setting(k: str, v: str) -> None:
    try:
        with get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)")
            conn.execute("INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)", (k, v))
    except Exception:
        pass


def get_setting(k: str) -> Optional[str]:
    try:
        with get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)")
            r = conn.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
            return r["v"] if r else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# MEMÓRIA DE CURTO PRAZO (contexto de conversa)
# ---------------------------------------------------------------------------
# O bot tratava cada mensagem isolada: perguntava algo, o usuário respondia, e
# a resposta virava um item novo. Estas funções dão ao motor o que ele precisa
# pra entender continuidade — a conversa recente e o que a pessoa já tem aberto.

def conversa_recente(telefone: str, limite: int = 8) -> list[dict]:
    """Últimas mensagens (in/out) desse telefone, em ordem cronológica.
    Busca por telefone porque o webhook grava msg_log com user_id nulo."""
    tel = "".join(ch for ch in str(telefone or "") if ch.isdigit())
    if not tel:
        return []
    sufixo = tel[-8:]                      # ignora DDI/9º dígito divergente
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT direcao, tipo, preview, ts FROM msg_log "
                "WHERE telefone LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{sufixo}", int(limite))).fetchall()
        return [dict(r) for r in reversed(rows)]
    except Exception:
        return []


def ultimo_item(user_id: int) -> Optional[dict]:
    """Item criado mais recentemente — alvo natural de um complemento
    ('são 185 reais' logo depois de registrar a conta de luz)."""
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT * FROM items WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (user_id,)).fetchone()
        return dict(r) if r else None
    except Exception:
        return None


def itens_abertos(user_id: int, limite: int = 20) -> list[dict]:
    """Pendências em aberto, das mais próximas de vencer para as demais.
    É o que o mordomo precisa saber pra responder 'o que tenho pra pagar?'."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                # recorrencia entra no SELECT: sem ela o mordomo não sabe que
                # o remédio é diário e trata como lembrete de uma vez só.
                "SELECT id, tipo, categoria, descricao, valor_reais, "
                "       data_vencimento, hora_alvo, recorrencia, status "
                "FROM items WHERE user_id=? AND status IN ('pendente','vencido') "
                "ORDER BY COALESCE(data_vencimento, data_criacao) LIMIT ?",
                (user_id, int(limite))).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def atualizar_item(item_id: int, **campos) -> bool:
    """Completa/corrige um item existente (valor, data, hora, descrição...).
    Usado quando a pessoa manda a informação em partes."""
    permitidos = {"descricao", "valor_reais", "data_vencimento", "hora_alvo",
                  "recorrencia", "categoria", "status", "tipo"}
    limpos = {k: v for k, v in campos.items()
              if k in permitidos and v is not None}
    if "status" in limpos and limpos["status"] not in VALID_STATUSES:
        limpos.pop("status")
    if "tipo" in limpos and limpos["tipo"] not in VALID_ITEM_TYPES:
        limpos.pop("tipo")
    if "categoria" in limpos and limpos["categoria"] not in VALID_CATEGORIES:
        limpos["categoria"] = "Outros"
    if not limpos:
        return False
    sets = ", ".join(f"{k}=?" for k in limpos)
    try:
        with get_conn() as conn:
            conn.execute(f"UPDATE items SET {sets} WHERE id=?",
                         (*limpos.values(), int(item_id)))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# MEMÓRIA DE LONGO PRAZO (fatos que o mordomo aprende sobre a pessoa)
# ---------------------------------------------------------------------------
# Um mordomo de verdade pergunta UMA vez e nunca mais esquece: quanto dura
# 3kg de ração, de quantos em quantos km troca o óleo, que dia do mês vence o
# aluguel. Sem isso ele repete a mesma pergunta e nunca consegue antecipar a
# recompra/manutenção — que é a promessa do produto.

def init_memoria() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memoria (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                chave    TEXT NOT NULL,
                valor    TEXT NOT NULL,
                ts       TEXT NOT NULL,
                UNIQUE(user_id, chave)
            );
            CREATE INDEX IF NOT EXISTS idx_memoria_user ON memoria(user_id);
            """
        )


def lembrar_fato(user_id: int, chave: str, valor: str) -> bool:
    """Guarda (ou atualiza) um fato duradouro. Ex.: chave='racao gatos:dura_dias'."""
    chave = (chave or "").strip().lower()[:80]
    valor = str(valor or "").strip()[:300]
    if not chave or not valor:
        return False
    try:
        init_memoria()
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO memoria (user_id, chave, valor, ts) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id, chave) DO UPDATE SET valor=excluded.valor, "
                "ts=excluded.ts",
                (user_id, chave, valor, _now_iso()))
        return True
    except Exception:
        return False


def fatos(user_id: int, limite: int = 40) -> list[dict]:
    """Tudo que já foi aprendido sobre a pessoa, mais recente primeiro."""
    try:
        init_memoria()
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT chave, valor, ts FROM memoria WHERE user_id=? "
                "ORDER BY id DESC LIMIT ?", (user_id, int(limite))).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def buscar_conversa(telefone: str, termo: str, limite: int = 6) -> list[dict]:
    """Procura no histórico inteiro (não só nas últimas mensagens).
    É o que permite responder 'o que eu te falei sobre a ração?'."""
    tel = "".join(ch for ch in str(telefone or "") if ch.isdigit())
    termo = (termo or "").strip()
    if not tel or len(termo) < 3:
        return []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT direcao, tipo, preview, ts FROM msg_log "
                "WHERE telefone LIKE ? AND preview LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (f"%{tel[-8:]}", f"%{termo}%", int(limite))).fetchall()
        return [dict(r) for r in reversed(rows)]
    except Exception:
        return []


def apagar_item(item_id: int, user_id: int) -> bool:
    """Apaga UM item, exigindo o user_id como trava.

    O user_id não é redundante: sem ele, um id errado apagaria item de outro
    usuário. Com ele, o pior caso é não apagar nada.
    """
    try:
        with get_conn() as conn:
            cur = conn.execute("DELETE FROM items WHERE id = ? AND user_id = ?",
                               (int(item_id), int(user_id)))
            return cur.rowcount > 0
    except Exception:
        return False
