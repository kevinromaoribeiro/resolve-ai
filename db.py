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
# Viagem e Treino entraram em 09/08/2026. Motivo medido, não achismo: no
# primeiro dia de uso real, 3 dos 18 itens do banco eram voo/check-in/jiu
# jitsu e os três caíram em "Outros" porque não havia caixa pra eles. Item em
# "Outros" não recebe a régua de aviso certa — voo precisa de check-in 48h
# antes, treino não precisa de cobrança nenhuma.
# Categoria fora desta tupla é rejeitada na escrita (add_item coage pra
# "Outros"): acrescentar aqui é obrigatório antes de qualquer classificador
# saber devolvê-la.
VALID_CATEGORIES = ("Alimentação", "Pet", "Veículo", "Contas", "Saúde",
                    "Casa", "Lazer", "Viagem", "Treino", "Outros")

# Ate quantos minutos depois da hora marcada o alarme ainda pode dizer
# "chegou a hora". Passou disso, o scheduler troca o TEXTO — mas nao deixa
# de avisar. Filtrar aqui faria o lembrete SUMIR, que e pior que avisar
# atrasado. (caso da Carol, 11/08)
ALARME_JANELA_MIN = 90

# M1.5 — quantas vezes a pessoa pode empurrar o MESMO item antes de o bot
# parar de insistir e perguntar a verdade. Tres e o numero em que "lembrete"
# ja virou "cobranca" na cabeca de quem recebe.
SNOOZE_LIMITE = 3


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
                data_criacao    TEXT NOT NULL,
                -- Colunas que nasceram como ALTER TABLE e agora moram aqui
                -- também: banco novo tem que sair pronto, e o rebuild da
                -- v6.5 recria a tabela A PARTIR DESTE DDL.
                data_conclusao    TEXT,
                codigo_pagamento  TEXT,
                codigo_tipo       TEXT,
                avisar_dias       TEXT
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

            """ + _MSGLOG_DDL + """

            -- M2.5: acao administrativa (reset de trial, hoje) deixa
            -- rastro. Sem isso, "o trial de todo mundo voltou" nao tem
            -- resposta pra "quem fez, quando, e em quantos".
            CREATE TABLE IF NOT EXISTS admin_acoes (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                quando  TEXT NOT NULL,
                acao    TEXT NOT NULL,
                alvo    TEXT,
                por     TEXT,
                detalhe TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_admin_acoes_acao
                ON admin_acoes(acao, quando);
            """
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
                         ("dia_resumo", "TEXT DEFAULT 'Segunda-feira'"),
                         # M1.2: carimbo do aceite explícito da LGPD — prova
                         # de consentimento (não só a UI ter mostrado botão).
                         ("lgpd_aceite_em", "TEXT"),
                         # M2.5: relogio do trial, separado da data de
                         # cadastro. Ver `_base_do_trial`.
                         ("trial_base", "TEXT"),
                         # M2.5 rodada 2: o final da placa. Sem ele, o
                         # "Anotei o final N da sua placa" era so texto.
                         ("placa_final", "INTEGER"),
                         # M2.9 — assinatura aprovada NA MÃO pelo dono.
                         # `plano`: 'mensal' | 'anual'. `pago_em`: o dia em
                         # que o Kevin conferiu no Mercado Pago e aprovou —
                         # é ele que inicia o ciclo, não a data do cadastro
                         # nem a do pedido. O bot não tem como saber se o
                         # cartão passou, então quem sabe carimba.
                         ("plano", "TEXT"),
                         ("pago_em", "TEXT")]:
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
        # M2.8: QUANDO o item foi concluído, não só que foi.
        #
        # Sem esta coluna a pergunta que valida o produto era impossível de
        # responder: "o bot lembrou e a pessoa deu baixa?". Dava pra ver que
        # um item estava concluído, nunca se a baixa veio DEPOIS do lembrete
        # nem quanto tempo levou. É a diferença entre "o bot mandou
        # mensagem" e "o bot resolveu alguma coisa pra alguém".
        #
        # Linhas antigas ficam com NULL — conclusão anterior à coluna, cuja
        # data ninguém sabe. O painel conta essas à parte em vez de inventar.
        if "data_conclusao" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN data_conclusao TEXT")
        # M3.5: o código de pagamento em coluna própria.
        #
        # Ele NUNCA vai pra descrição — `boleto.sem_codigo_de_pagamento`
        # existe porque isso já aconteceu e o código ficou visível na lista.
        # Aqui ele fica guardado e sai só no aviso de vencimento, que é
        # quando a pessoa precisa dele pra colar no app do banco.
        if "codigo_pagamento" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN codigo_pagamento TEXT")
        if "codigo_tipo" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN codigo_tipo TEXT")
        # M3.5 (auditoria P1-3): antecedência de aviso POR ITEM.
        #
        # A política global é avisar na véspera, e ela está certa pra conta de
        # luz. Está errada pra CNH: um dia antes não dá tempo de marcar exame
        # nem de ir ao Detran. A alternativa era mexer na política por
        # categoria, mas a categoria da CNH é "Outros" — e dar 60 dias de
        # antecedência a "Outros" faria o bot avisar de TUDO com dois meses de
        # antecedência, que é o ruído que faz a pessoa silenciar o bot.
        #
        # CSV de inteiros ("60,30"). NULL = usa a política global, que é o
        # caso de 99% dos itens.
        if "avisar_dias" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN avisar_dias TEXT")
        # M4.2 — mini-podcast. Tudo em `users` porque e preferencia da
        # PESSOA, nao de um item: o nicho que ela escolheu na landing, o dia
        # da semana que ela pediz, e quando saiu o ultimo episodio (que e o
        # que segura o teto de 1x por semana).
        _ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        # M5.4 (auditoria, P1-5): `podcast_recusado_em`. Antes, cancelar
        # so zerava `podcast_nicho` — e "nunca escolheu" ficava igual a
        # "disse nao". O resultado era re-oferecer o audio pra quem tinha
        # acabado de recusar, que e o que a regua da Meta pune num numero
        # ja restringido duas vezes.
        for _c in ("podcast_nicho", "podcast_dia", "podcast_ultimo",
                   "podcast_convite_em", "podcast_dia_perguntado",
                   "podcast_recusado_em",
                   # M11: quem tocou "Nunca mais" num aviso de novidade
                   # nao recebe o proximo. E a promessa que foi escrita
                   # na justificativa submetida a Meta.
                   "novidade_recusada_em",
                   # M9.2: de quantos em quantos dias ela quer o
                   # episodio. TEXT como as vizinhas; quem normaliza o
                   # valor e a funcao frequencia_do_podcast.
                   "podcast_frequencia"):
            if _c not in _ucols:
                conn.execute("ALTER TABLE users ADD COLUMN %s TEXT" % _c)
        # v6.5: CHECK antigo de status não conhece 'vencido' -> rebuild
        sql_items = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='items'").fetchone()
        if sql_items and "vencido" not in (sql_items["sql"] or ""):
            conn.executescript("""
                ALTER TABLE items RENAME TO items_old;
            """)
            conn.executescript(_ITEMS_DDL)
            # A LISTA DE COLUNAS É DESCOBERTA, NÃO ESCRITA À MÃO.
            #
            # Ela era um literal com 12 nomes, e envelhecia a cada coluna
            # nova: `data_conclusao`, `codigo_pagamento`, `codigo_tipo` e
            # `avisar_dias` já estavam de fora. Se este rebuild disparasse
            # num banco antigo, essas quatro colunas — e os dados nelas —
            # sumiam em silêncio. Cruzar as colunas das duas tabelas não
            # envelhece nunca. (Nota lateral da auditoria M3.6.)
            novas = {r["name"] for r in
                     conn.execute("PRAGMA table_info(items)")}
            velhas = [r["name"] for r in
                      conn.execute("PRAGMA table_info(items_old)")
                      if r["name"] in novas]
            cols = ",".join(velhas)
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


def _base_do_trial(user: dict) -> datetime:
    """De quando o relogio do trial conta.

    `data_criacao` e a data de CADASTRO e nao pode ser mexida: ela alimenta
    "novos por dia" no painel e a idade da base. Entao o reset administrativo
    (M2.5) escreve em `trial_base`, e quem quiser saber quanto falta de teste
    passa por aqui. Um so lugar decide isso — o dia em que `trial_days_left`
    e `trial_day_number` discordarem da base, o trial guiado e o fim de trial
    passam a contar dias diferentes pra mesma pessoa.
    """
    bruto = (user.get("trial_base") or user.get("data_criacao") or "")
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(bruto[:19], formato)
        except ValueError:
            continue
    # Sem data legivel, o seguro e tratar como cadastro de AGORA: melhor um
    # trial a mais do que cortar o acesso de alguem por causa de um campo
    # torto (regra 10). A direcao do fallback e a decisao, e por isso ela
    # tem teste — nao so este comentario.
    #
    # E ELE GRITA. Sem log, uma data ilegivel vira trial que nunca expira,
    # em silencio, sem uma linha no servidor (regra 5).
    import logging
    logging.getLogger("resolveai").warning(
        "[trial] data ilegivel no user %s (%r) — contando o trial a partir "
        "de agora", user.get("id"), bruto[:19])
    return tempo.agora()


def trial_days_left_raw(user: dict, trial_days: int = 14) -> int:
    """Dias restantes do trial SEM clamp (negativo = expirado há N dias)."""
    elapsed = (tempo.agora() - _base_do_trial(user)).days
    return trial_days - elapsed


def trial_days_left(user: dict, trial_days: int = 14) -> int:
    """Dias restantes do teste grátis (>= 0, para exibição)."""
    return max(0, trial_days_left_raw(user, trial_days))


def update_user_fields(user_id: int, **fields) -> None:
    """Atualiza campos arbitrários do usuário (whitelist de colunas)."""
    allowed = {"nome", "idade", "profissao", "interesses", "carro_modelo",
               "carro_km", "pet_info", "dia_resumo", "status",
               "onboarding_step", "trial_nudges_sent", "lgpd_aceite_em",
               "trial_base", "placa_final",
               # M2.9 — assinatura aprovada na mão pelo dono.
               "plano", "pago_em",
               # M4.2 — mini-podcast: o nicho escolhido, o dia da semana que
               # a pessoa pediu, quando saiu o último episódio e quando o
               # convite foi feito.
               "podcast_nicho", "podcast_dia", "podcast_ultimo",
               "podcast_convite_em", "podcast_dia_perguntado",
               "podcast_recusado_em", "podcast_frequencia",
               "novidade_recusada_em"}
    cols = {k: v for k, v in fields.items() if k in allowed}
    # DESCARTE SILENCIOSO ERA UMA ARMADILHA. Coluna nova no banco e esquecida
    # aqui vira UPDATE que não acontece, sem erro e sem log: o chamador acha
    # que gravou. Aconteceu com `plano`/`pago_em`, e o sintoma foi um teste
    # de OUTRO arquivo falhando por estado que ninguém conseguiu limpar.
    ignorados = set(fields) - allowed
    if ignorados:
        import logging
        logging.getLogger("resolveai").warning(
            "[db] update_user_fields ignorou campo(s) fora da whitelist: %s "
            "— se a coluna existe, acrescente em `allowed`",
            ", ".join(sorted(ignorados)))
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {sets} WHERE id=?",
                     (*cols.values(), user_id))


def trial_day_number(user: dict) -> int:
    """Em que dia do trial o usuário está (0 = dia da entrada, 1 = dia seguinte...)."""
    return (tempo.agora() - _base_do_trial(user)).days


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
    """Usuários em trial, já com onboarding concluído, dentro do prazo.

    `(x or "done")` E O MESMO IDIOMA DOS OUTROS QUATRO LUGARES que fazem
    esta pergunta (db.py:639, db.py:2404, scheduler.py:889 e 1034). Só aqui
    a comparação era estrita — e quem termina o onboarding fica com `None`,
    não com `"done"` (ver `jornada.py`: "onboarding_step ja e None/'done'").
    Efeito: a fila do trial guiado vinha VAZIA todo dia, e nenhum cliente
    real nunca recebeu um nudge sequer.
    """
    return [u for u in list_users()
            if (u.get("status") or "trial") == "trial"
            and (u.get("onboarding_step") or "done") == "done"
            and trial_days_left_raw(u, trial_days) >= 0]


def set_status(user_id: int, status: str) -> None:
    """trial | ativo | cancelado"""
    update_user_fields(user_id, status=status)


def delete_user(user_id: int) -> None:
    """Exclusão LGPD: apaga o usuário e TUDO que se refere a ele.

    BUG PRÉ-EXISTENTE (achado em auditoria, 11/08/2026): esta função só
    apagava `users`. `items` some por FK CASCADE, mas `msg_log`, `memoria` e
    `dispatches` NÃO têm foreign key — ficavam para trás. Como o webhook
    grava `msg_log(telefone, preview)` com o TEXTO INTEGRAL da mensagem antes
    de qualquer processamento, o conteúdo das conversas continuava no banco,
    indexado por telefone, depois de o bot responder "Todos os seus dados
    foram apagados permanentemente".

    Ou seja: a resposta do comando `apagar meus dados` era falsa. Numa
    obrigação de LGPD, prometer apagamento e não apagar é pior do que não ter
    o comando. Verificado contra o banco (SELECT após o DELETE), não contra a
    mensagem na tela.

    msg_log é apagado por TELEFONE porque grande parte das linhas entra com
    user_id=None — o webhook loga a mensagem recebida antes de resolver quem
    é o usuário. Apagar só por user_id deixaria justamente o texto das
    conversas para trás.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT telefone FROM users WHERE id=?",
                           (user_id,)).fetchone()
        telefone = row["telefone"] if row else None
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.execute("DELETE FROM items WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM dispatches WHERE user_id=?", (user_id,))
        # resumo_mensal entrou aqui na auditoria v23.0 (P1-3): a agregacao
        # do M1.6 e chaveada por user_id e sobrevivia ao "apagar meus dados",
        # que promete "remove tudo, na hora". Nao vazava (users e
        # AUTOINCREMENT, id nao se repete), mas a promessa e "tudo".
        for tabela in ("memoria", "demos", "resumo_mensal"):
            try:
                conn.execute(f"DELETE FROM {tabela} WHERE user_id=?",
                             (user_id,))
            except Exception as _e:
                import logging
                # So "tabela nao existe" e esperado. Qualquer outro erro aqui
                # (schema divergente, coluna faltando) fazia o DELETE falhar
                # calado enquanto o bot respondia "apagados permanentemente".
                if "no such table" not in str(_e).lower():
                    logging.getLogger("resolveai").warning(
                        "[lgpd] FALHA ao purgar %s do user %s", tabela,
                        user_id, exc_info=True)
                    raise
                logging.getLogger("resolveai").info(
                    "[lgpd] tabela %s inexistente na purga do user %s",
                    tabela, user_id)
        if telefone:
            digitos = re.sub(r"\D", "", str(telefone))
            conn.execute(
                "DELETE FROM msg_log WHERE telefone=? OR telefone=? "
                "OR user_id=?", (telefone, digitos, user_id))
        else:
            conn.execute("DELETE FROM msg_log WHERE user_id=?", (user_id,))


def set_created_days_ago(user_id: int, days: int) -> None:
    """Utilitário de teste: retrocede o relógio (simula fim de trial).

    Mexe nos DOIS campos. Só em `data_criacao`, ele virava no-op em qualquer
    usuário que já tivesse passado por um reset — e teste que simula fim de
    trial e não simula nada é teste cego, que é pior que teste ausente.
    """
    when = (tempo.agora() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET data_criacao=?, trial_base=? WHERE id=?",
            (when, when, user_id))


# ---------------------------------------------------------------------------
# ACAO ADMINISTRATIVA (M2.5)
# ---------------------------------------------------------------------------
def registrar_acao_admin(acao: str, alvo=None, por: str = "",
                         detalhe: str = "") -> None:
    """Rastro de quem mexeu na base por fora do fluxo normal."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO admin_acoes (quando, acao, alvo, por, detalhe)
               VALUES (?,?,?,?,?)""",
            (tempo.agora().isoformat(timespec="seconds"), acao,
             None if alvo is None else str(alvo), por or "", detalhe or ""))


def ja_recebeu_acao(acao: str, alvo) -> bool:
    """Esta pessoa ja foi alvo desta acao administrativa alguma vez?

    Existe pra uma ordem que so pode ser cumprida UMA vez por pessoa. O
    numero deste projeto ja foi restringido duas vezes; mandar o mesmo
    template duas vezes pra mesma pessoa e o caminho mais curto pra
    terceira. Na duvida (banco fora, coluna estranha) devolve True — o erro
    seguro aqui e NAO mandar.
    """
    import logging   # LOCAL: `db.py` nao importa logging no topo (ver
                    # `_avisar_dias_limpo`) e um NameError no ramo de erro
                    # derrubaria a checagem inteira.
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT 1 FROM admin_acoes WHERE acao=? AND alvo=? LIMIT 1",
                (acao, str(alvo))).fetchone()
        return r is not None
    except Exception:
        logging.getLogger("resolveai").warning(
            "[admin] nao consegui checar a acao %r do alvo %r", acao, alvo,
            exc_info=True)
        return True


def acoes_administrativas(acao: Optional[str] = None,
                          limite: int = 200) -> list[dict]:
    with get_conn() as conn:
        if acao:
            rows = conn.execute(
                """SELECT * FROM admin_acoes WHERE acao=?
                   ORDER BY id DESC LIMIT ?""", (acao, limite)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM admin_acoes ORDER BY id DESC LIMIT ?",
                (limite,)).fetchall()
    return [dict(r) for r in rows]


def resetar_trial(user_ids=None, por: str = "", trial_days: int = 14) -> list:
    """Devolve o trial inteiro para os usuarios pedidos. So a DATA.

    Devolve a lista de ids efetivamente resetados — vazia quando nao havia
    o que fazer, que e o resultado normal da segunda execucao no mesmo dia.

    TRES RECUSAS, todas deliberadas:

    1. NAO TOCA EM ITEM. A unica escrita e `users.trial_base`. Um comando
       que varre a base inteira de uma vez e o pior lugar possivel pra
       "aproveitar e limpar" qualquer coisa (regra 10 do projeto).
    2. NAO RESSUSCITA QUEM CANCELOU. Quem pediu pra sair nao volta pro
       trial por causa de manutencao — isso e voltar a mandar mensagem pra
       quem pediu silencio, e no WhatsApp isso vira denuncia.
    3. NAO MEXE EM QUEM NAO ESTA EM TRIAL. Assinante nao vira trial, e
       quem foi bloqueado nao volta a receber.
    4. NAO REPETE A REGUA DO TRIAL. `trial_nudges_sent` fica intacto de
       proposito: voltar o relogio faria o trial guiado remandar o d1 pra
       quem ja passou por ele — 11 pessoas recebendo de novo a mensagem de
       boas-vindas e o oposto de "testem as melhorias".

    Idempotente POR DIA: quem ja tem `trial_base` de hoje e pulado. Rodar de
    novo porque a primeira "pareceu nao funcionar" nao pode virar 28 dias.
    """
    agora = tempo.agora()
    hoje = agora.date().isoformat()
    alvos = list(user_ids) if user_ids is not None else [
        u["id"] for u in list_users()]
    tocados = []
    for uid in alvos:
        u = get_user(uid)
        if not u:
            continue
        # SO QUEM ESTA EM TRIAL. A guarda era `!= "cancelado"`, e isso
        # deixava o comando rebaixar ASSINANTE a trial de 14 dias (que
        # depois expira e corta quem paga) e devolver acesso a quem foi
        # BLOQUEADO. Lista fechada em `set_status`: trial | ativo |
        # cancelado | bloqueado — a unica que faz sentido resetar e a
        # primeira. Hoje ha 0 pagantes; e o primeiro que paga a conta.
        if (u.get("status") or "trial") != "trial":
            continue
        if (u.get("trial_base") or "")[:10] == hoje:
            continue                      # ja resetado hoje
        update_user_fields(uid, trial_base=agora.strftime("%Y-%m-%d %H:%M:%S"),
                           status="trial")
        registrar_acao_admin("reset_trial", alvo=uid, por=por,
                             detalhe=f"+{trial_days}d")
        tocados.append(uid)
    return tocados


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

# Teto de antecedência aceito. Existe pelo mesmo motivo que a janela de
# sanidade de data no `boleto`: valor absurdo aqui não dá erro, só faz o aviso
# nunca sair (fica fora da janela de leitura) ou sair anos antes.
AVISO_MAX_DIAS = 90


def _avisar_dias_limpo(csv: Optional[str]) -> Optional[str]:
    """Normaliza "60,30" -> "60,30". Lixo vira None (política padrão).

    `import logging` LOCAL, como nos outros 8 sites deste arquivo: o `db.py`
    não importa logging no topo, e a primeira versão desta função chamava
    `logging.getLogger` sem importar. O ramo de erro estourava NameError e
    derrubava o `add_item` inteiro — auditoria M3.6, P2-2. Era código novo
    que nunca tinha rodado.
    """
    import logging
    if not csv:
        return None
    dias, descartados = [], []
    for parte in str(csv).split(","):
        parte = parte.strip()
        # `0` fora: D-0 fura o guard de `criado_hoje` da rede de segurança e
        # vira eco do que a pessoa acabou de dizer.
        if not parte.isdigit() or not (1 <= int(parte) <= AVISO_MAX_DIAS):
            descartados.append(parte)
            continue
        dias.append(int(parte))
    if descartados:
        # DESCARTE PARCIAL TAMBÉM APARECE NO LOG (M3.6, P2-4). Antes o aviso
        # só saía quando TUDO era lixo — "90,91" virava "90" calado, e a
        # antecedência que alguém escreveu sumia sem deixar rastro.
        logging.getLogger("resolveai").warning(
            "[db] avisar_dias: descartei %r de %r", descartados, csv)
    if not dias:
        return None
    return ",".join(str(d) for d in sorted(set(dias), reverse=True))


def _avisar_dias_final(csv: Optional[str], descricao: Optional[str]) -> Optional[str]:
    """A antecedência do item: a explícita, ou a que a descrição pede.

    AQUI, E NÃO NO CAMINHO DA FOTO. A antecedência de 60/30 dias da CNH
    nascia só quando a pessoa mandava a IMAGEM do documento; quem digitava
    "minha CNH vence 12/03/2027" ganhava só o aviso de véspera. A promessa
    estava na landing e no /dash e o produto cumpria em metade dos caminhos.

    Este é o funil por onde TODO item passa — foto, texto, ajuste, painel,
    motor. Resolvendo aqui, nenhum caminho novo precisa lembrar da regra, que
    é exatamente como o defeito nasceu.

    O explícito sempre ganha: quem passou `avisar_dias` sabe o que quer.
    """
    explicito = _avisar_dias_limpo(csv)
    if explicito:
        return explicito
    try:
        import documento
        dias = documento.avisos_por_descricao(descricao)
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[db] nao consegui derivar avisar_dias de %r", descricao,
            exc_info=True)
        return None
    return _avisar_dias_limpo(",".join(str(d) for d in dias)) if dias else None


def dias_de_aviso(item) -> Optional[set]:
    """Antecedência do item, ou None quando ele segue a política global.

    Aceita dict E `sqlite3.Row` (M3.6, P2-3): a Row não tem `.get`, e o
    `hasattr(item, "get")` da primeira versão devolvia None CALADO — o item
    perdia os 60/30 sem erro nenhum, que é o modo de falha mais caro de
    achar. Hoje `items_due_within` devolve dict, mas nada garante isso.
    """
    if not item:
        return None
    try:
        csv = item["avisar_dias"]
    except (KeyError, IndexError, TypeError):
        return None
    if not csv:
        return None
    dias = {int(x) for x in str(csv).split(",") if x.strip().isdigit()}
    return dias or None


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
    # M3.5 — o código de pagamento mora AQUI, nunca na descrição.
    #
    # `boleto.sem_codigo_de_pagamento` existe justamente porque o código já
    # vazou pra descrição uma vez e ficou visível na lista da pessoa. Coluna
    # própria mantém aquela proteção de pé e ainda permite devolver o código
    # no aviso de vencimento, que é o único momento em que ele serve.
    codigo_pagamento: Optional[str] = None,
    codigo_tipo: Optional[str] = None,      # 'boleto' | 'pix'
    avisar_dias: Optional[str] = None,      # CSV: "60,30". None = padrão.
) -> int:
    if tipo not in VALID_ITEM_TYPES:
        raise ValueError(f"tipo inválido: {tipo!r}")
    if status not in VALID_STATUSES:
        raise ValueError(f"status inválido: {status!r}")
    if categoria not in VALID_CATEGORIES:
        categoria = "Outros"
    if codigo_tipo and codigo_tipo not in ("boleto", "pix"):
        raise ValueError(f"codigo_tipo inválido: {codigo_tipo!r}")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO items
               (user_id, tipo, categoria, descricao, valor_reais,
                data_vencimento, hora_alvo, recorrencia, status,
                link_afiliado, data_criacao, codigo_pagamento, codigo_tipo,
                avisar_dias)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, tipo, categoria, descricao, valor_reais,
             data_vencimento, hora_alvo, recorrencia, status,
             link_afiliado, _now_iso(), codigo_pagamento, codigo_tipo,
             _avisar_dias_final(avisar_dias, descricao)),
        )
    touch_user(user_id)
    return int(cur.lastrowid)


def item_com_codigo_mais_recente(user_id: int) -> Optional[dict]:
    """A conta PENDENTE mais próxima de vencer que tem código de pagamento.

    Ordena por vencimento, não por criação: quem pede o código está pagando
    agora, e o que ela vai pagar é o que vence primeiro — não o último boleto
    que ela fotografou.
    """
    with get_conn() as conn:
        r = conn.execute(
            """SELECT * FROM items
                WHERE user_id=? AND status='pendente'
                  AND codigo_pagamento IS NOT NULL
                  AND TRIM(codigo_pagamento) <> ''
                ORDER BY COALESCE(data_vencimento, '9999-12-31') ASC,
                         id DESC
                LIMIT 1""", (user_id,)).fetchone()
    return dict(r) if r else None


def get_item(item_id: int) -> Optional[dict]:
    """Um item pelo id. None se não existe."""
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM items WHERE id=?",
                         (item_id,)).fetchone()
    return dict(r) if r else None


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
    """Muda o status e, em 'concluido', carimba QUANDO (M2.8).

    O carimbo é escrito aqui porque este é o ponto único por onde toda
    conclusão passa — "pago", "feito", baixa pelo painel, rolagem de
    recorrente. Carimbar em cada chamador seria esquecer em um deles, e o
    esquecido é sempre o que mais importa.

    `COALESCE`: reconcluir um item não empurra a data pra frente — a primeira
    baixa é a que responde "quanto tempo depois do lembrete". Sair de
    'concluido' limpa o carimbo, senão um item reaberto fica alegando uma
    conclusão que não vale mais.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status inválido: {status!r}")
    with get_conn() as conn:
        if status == "concluido":
            conn.execute(
                "UPDATE items SET status=?, "
                "data_conclusao=COALESCE(data_conclusao, ?) WHERE id=?",
                # `_now_iso()` (com ESPAÇO), o mesmo formato de
                # `dispatches.sent_at`. Gravar isoformat com 'T' aqui criaria
                # de novo o bug que `dentro_da_janela` documenta: 'T' (0x54) >
                # ' ' (0x20), então comparar as duas colunas como string daria
                # resultado errado — e é exatamente essa comparação que diz se
                # a baixa veio depois do lembrete.
                (status, _now_iso(), item_id))
        else:
            conn.execute(
                "UPDATE items SET status=?, data_conclusao=NULL WHERE id=?",
                (status, item_id))


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


def recebeu_nos_ultimos_dias(kind: str, dias: int = 2) -> set:
    """Quem recebeu este disparo nos ultimos N dias.

    Existe pra o dono nao repetir um aviso sem querer. O envio manual do
    painel nao deixava rastro por PESSOA — so uma linha no log de acoes com
    o nome do template dentro de um texto — entao nao dava pra perguntar
    "quem ja recebeu isso". Agora da.

    Devolve ids, e nao contagem, porque quem chama precisa cruzar com o
    segmento escolhido: "3 de 14" so ajuda se forem as 3 daquele grupo.
    """
    import datetime as _dt
    corte = (tempo.agora() - _dt.timedelta(days=max(0, int(dias)))
             ).isoformat(timespec="seconds")
    try:
        with get_conn() as conn:
            linhas = conn.execute(
                "SELECT DISTINCT user_id FROM dispatches "
                "WHERE kind=? AND sent_at >= ?", (kind, corte)).fetchall()
            # TAMBEM O LOG DE ACOES, e nao por redundancia.
            #
            # O carimbo em `dispatches` comecou hoje. Sem esta segunda
            # fonte, a trava nasceria cega justamente para os envios que
            # acabaram de sair — que sao os que ela precisa impedir de
            # repetir. `admin_acoes` ja guardava isso desde sempre, so que
            # com o template dentro de um texto.
            antigas = conn.execute(
                "SELECT DISTINCT alvo FROM admin_acoes "
                "WHERE acao='enviar_template' AND quando >= ? "
                "AND detalhe LIKE ?", (corte, kind + " enviado=True%")
            ).fetchall()
        achados = {int(r[0]) for r in linhas if r and r[0] is not None}
        for r in antigas:
            try:
                achados.add(int(r[0]))
            except (TypeError, ValueError):
                pass
        return achados
    except Exception:
        # Na duvida devolve VAZIO: o aviso some, mas nada e bloqueado por
        # engano. Errar pra "nao sei" e melhor que errar pra "ja mandei".
        return set()


def resumo_de_envios(kind: str, dias: int = 2) -> dict:
    """Quantas pessoas receberam este template e quando foi a ultima.

    O painel avisava o resultado do lote num `alert`, que some no primeiro
    OK. Depois disso nao havia jeito de saber se um aviso tinha saido ou
    nao — e a duvida e o que faz o dono clicar de novo.
    """
    import datetime as _dt
    corte = (tempo.agora() - _dt.timedelta(days=max(0, int(dias)))
             ).isoformat(timespec="seconds")
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT COUNT(DISTINCT user_id), MAX(sent_at) "
                "FROM dispatches WHERE kind=? AND sent_at >= ?",
                (kind, corte)).fetchone()
            r2 = conn.execute(
                "SELECT COUNT(DISTINCT alvo), MAX(quando) FROM admin_acoes "
                "WHERE acao='enviar_template' AND quando >= ? "
                "AND detalhe LIKE ?", (corte, kind + " enviado=True%")
            ).fetchone()
    except Exception:
        return {"quantos": 0, "ultimo": ""}
    # As duas fontes se sobrepoem (o carimbo novo e o log antigo gravam o
    # mesmo envio), entao a contagem honesta e a MAIOR das duas, nunca a
    # soma — somar mostraria o dobro do que saiu.
    quantos = max(int((r or [0])[0] or 0), int((r2 or [0])[0] or 0))
    ultimo = max(str((r or ["", ""])[1] or ""), str((r2 or ["", ""])[1] or ""))
    return {"quantos": quantos, "ultimo": ultimo}


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


def pediu_link_e_nao_pagou(dias: int = 2, limite: int = 50) -> list[dict]:
    """Quem pediu o link de pagamento ha N+ dias e ainda nao foi aprovado.

    O carimbo e o dispatch `link-pagamento`, gravado quando a pessoa digita
    "assinar" e recebe os links. Quem o Kevin aprovou no painel vira
    `status='ativo'` e sai desta lista na hora.

    NAO filtra por trial valido de proposito: cobrar quem acabou de vencer e
    justamente o ponto. Filtrar por `user_can_receive` aqui era o que fazia
    a pessoa sumir do radar no dia da decisao.
    """
    import logging   # LOCAL: `db.py` nao importa logging no topo.
    try:
        corte = (tempo.agora() - timedelta(days=max(0, dias))
                 ).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            linhas = conn.execute(
                """SELECT u.*, MIN(d.sent_at) AS pediu_em
                     FROM users u
                     JOIN dispatches d ON d.user_id = u.id
                    WHERE d.kind = 'link-pagamento'
                      AND COALESCE(u.status,'trial') NOT IN ('ativo',
                                                             'cancelado',
                                                             'bloqueado')
                    GROUP BY u.id
                   HAVING MIN(d.sent_at) <= ?
                    ORDER BY MIN(d.sent_at) ASC LIMIT ?""",
                (corte, limite)).fetchall()
        return [dict(r) for r in linhas]
    except Exception:
        logging.getLogger("resolveai").warning(
            "[cobranca] nao consegui montar a fila", exc_info=True)
        return []


def dias_desde_o_pedido_do_link(user_id: int) -> int:
    """Ha quantos dias a pessoa pediu o link. 0 se nao da pra saber."""
    import logging
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT MIN(sent_at) FROM dispatches WHERE user_id=? "
                "AND kind='link-pagamento'", (user_id,)).fetchone()
        if not r or not r[0]:
            return 0
        quando = datetime.fromisoformat(str(r[0]))
        return max(0, (tempo.agora() - quando).days)
    except Exception:
        logging.getLogger("resolveai").warning(
            "[cobranca] data do pedido ilegivel do user %s", user_id,
            exc_info=True)
        return 0


def proativas_sem_resposta(user_id: int) -> int:
    """Quantas mensagens seguidas o bot mandou sem a pessoa responder nada.

    Zera no instante em que ela fala qualquer coisa. E a unica pergunta que
    importa pra saber se vale a pena continuar puxando assunto: falar com
    quem responde nao tem risco nenhum, e falar com quem nunca responde e o
    que a Meta le como spam.

    Na duvida devolve 0 — o erro seguro aqui e continuar entregando, nunca
    calar alguem por causa de um banco que piscou.
    """
    import logging   # LOCAL: `db.py` nao importa logging no topo.
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT ts FROM msg_log WHERE user_id=? AND direcao='in' "
                "ORDER BY ts DESC LIMIT 1", (user_id,)).fetchone()
            desde = r["ts"] if r else ""
            return conn.execute(
                "SELECT COUNT(*) FROM dispatches WHERE user_id=? "
                "AND sent_at > ?", (user_id, desde)).fetchone()[0] or 0
    except Exception:
        logging.getLogger("resolveai").warning(
            "[engajamento] nao consegui medir o silencio do user %s",
            user_id, exc_info=True)
        return 0


def teve_proativa_hoje(user_id: int, horas: int = 4) -> bool:
    """Esta pessoa recebeu alguma mensagem que o bot iniciou nas ultimas N h?

    Serve pra uma coisa so: mensagem de cortesia cede a vez pra mensagem de
    produto, pra as duas nao chegarem coladas. Na duvida devolve True — o
    erro seguro e adiar a cortesia, nunca empilhar duas vibracoes no mesmo
    numero.

    JANELA DE HORAS, NAO O DIA (M6.8). A primeira versao olhava o dia
    inteiro, e isso nao e ceder a vez: e inanicao. Medido em producao — todo
    tester recebia um lembrete, entrava em "adiado" e a ordem de reativacao
    nunca executava; com lembrete todo dia, nunca executaria. Quatro horas
    separam as duas mensagens sem deixar a segunda morrer na fila.
    """
    import logging   # LOCAL: `db.py` nao importa logging no topo.
    try:
        with get_conn() as conn:
            corte = (tempo.agora() - timedelta(hours=max(1, horas))
                     ).strftime("%Y-%m-%d %H:%M:%S")
            r = conn.execute(
                "SELECT 1 FROM dispatches WHERE user_id=? AND sent_at>=? "
                "LIMIT 1", (user_id, corte)).fetchone()
        return r is not None
    except Exception:
        logging.getLogger("resolveai").warning(
            "[dispatch] nao consegui contar as proativas de hoje do user %s",
            user_id, exc_info=True)
        return True


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


# ---------------------------------------------------------------------------
# M1.6 — PURGA DE CONCLUIDOS + LACRE
# ---------------------------------------------------------------------------
# Apaga o TEXTO, guarda o NUMERO. Sem isso a purga destroi a unica municao
# que o produto tem no fim do ano ("voce fechou 47 coisas e pagou R$ 2.310").
# Por isso o agregado roda ANTES do delete — se nascer depois, o historico
# ja nasce perdido.
_RESUMO_DDL = ("CREATE TABLE IF NOT EXISTS resumo_mensal ("
               "user_id INTEGER, mes TEXT, categoria TEXT, "
               "qtd INTEGER, soma REAL, "
               "PRIMARY KEY (user_id, mes, categoria))")

_LACRE_DDL = ("CREATE TABLE IF NOT EXISTS lacre_purga ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT, "
              "corte TEXT, itens INTEGER, usuarios INTEGER, seco INTEGER)")

PURGA_DIAS = 90


def agregar_antes_da_purga(corte_iso: str) -> int:
    """Soma em resumo_mensal tudo que vai ser apagado. Roda ANTES do delete.

    Guarda so numero: mes, categoria, quantidade e soma. Nenhuma descricao,
    nenhum nome de terceiro — nada que identifique o que a pessoa fez.
    """
    with get_conn() as conn:
        conn.execute(_RESUMO_DDL)
        linhas = conn.execute(
            "SELECT user_id, substr(data_criacao,1,7) AS mes, "
            "categoria, COUNT(*) AS qtd, "
            "COALESCE(SUM(valor_reais),0) AS soma "
            "FROM items WHERE status='concluido' AND data_criacao < ? "
            "GROUP BY user_id, mes, categoria", (corte_iso,)).fetchall()
        for r in linhas:
            conn.execute(
                "INSERT INTO resumo_mensal (user_id, mes, categoria, qtd, soma) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(user_id, mes, categoria) DO UPDATE SET "
                "  qtd = qtd + excluded.qtd, soma = soma + excluded.soma",
                (r["user_id"], r["mes"], r["categoria"], r["qtd"], r["soma"]))
    return len(linhas)


def purgar_concluidos(dias: int = PURGA_DIAS, seco: bool = True) -> dict:
    """Apaga itens CONCLUIDOS mais velhos que N dias. Nunca toca em pendente.

    seco=True (padrao) so conta e registra o lacre, sem apagar: e o dry-run
    que precede qualquer delete. Apagar dado de usuario nao estreia direto
    em producao.
    """
    corte = (tempo.agora() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(_LACRE_DDL)
        r = conn.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT user_id) AS u FROM items "
            "WHERE status='concluido' AND data_criacao < ?",
            (corte,)).fetchone()
        n, u = int(r["n"]), int(r["u"])
    if n and not seco:
        agregar_antes_da_purga(corte)      # numero antes, texto depois
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM items WHERE status='concluido' "
                "AND data_criacao < ?", (corte,))
    with get_conn() as conn:
        conn.execute(_LACRE_DDL)
        conn.execute(
            "INSERT INTO lacre_purga (quando, corte, itens, usuarios, seco) "
            "VALUES (?,?,?,?,?)",
            (_now_iso(), corte[:10], n, u, 1 if seco else 0))
    return {"corte": corte[:10], "itens": n, "usuarios": u, "seco": seco}


def ultimo_lacre():
    """Ultima purga real, pro comando meus dados falar em numero."""
    try:
        with get_conn() as conn:
            conn.execute(_LACRE_DDL)
            r = conn.execute(
                "SELECT quando, corte, itens FROM lacre_purga "
                "WHERE seco=0 ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None
    except Exception:
        return None


def resumo_historico(user_id: int) -> dict:
    """Numeros que SOBREVIVEM a purga: quantos fechou e quanto somou."""
    try:
        with get_conn() as conn:
            conn.execute(_RESUMO_DDL)
            r = conn.execute(
                "SELECT COALESCE(SUM(qtd),0) AS q, COALESCE(SUM(soma),0) AS s "
                "FROM resumo_mensal WHERE user_id=?", (user_id,)).fetchone()
        return {"qtd": int(r["q"] or 0), "soma": float(r["s"] or 0)}
    except Exception:
        return {"qtd": 0, "soma": 0.0}


def registrar_adiamento(user_id: int, item_id: int) -> int:
    """Conta que a pessoa empurrou ESTE item. Devolve o total.

    M1.5. Usa a tabela dispatches (que ja existe e ja e o log de tudo que
    acontece com um item) em vez de coluna nova: adiamento e evento, nao
    atributo. Assim o contador nasce com historico e nao exige migracao.
    """
    try:
        log_dispatch(user_id, "adiado", item_id)
        return dispatch_count_item("adiado", item_id)
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[snooze] falha ao contar adiamento do item %s", item_id,
            exc_info=True)
        return 0


def dispatch_count_item(kind: str, item_id: int) -> int:
    """Quantas vezes este KIND aconteceu para ESTE item."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) AS n FROM dispatches WHERE kind=? AND item_id=?",
            (kind, item_id)).fetchone()
    return int(r["n"] if r else 0)


def silenciar_item(item_id: int, user_id: int) -> None:
    """Para de tocar o alarme deste item, sem apagar nada.

    M1.5. O item CONTINUA na lista da pessoa — o que morre e o toque. E o
    freio anti-silenciamento: o alerta se desliga sozinho antes de a pessoa
    desligar o bot inteiro. Perder o item seria perder dado; parar de tocar
    e respeitar quem ja disse "agora nao" tres vezes.
    """
    try:
        log_dispatch(user_id, "silenciado", item_id)
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[snooze] falha ao silenciar item %s", item_id, exc_info=True)


def item_silenciado(item_id: int) -> bool:
    return dispatch_count_item("silenciado", item_id) > 0


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


def ultimo_disparo_em(user_id: int, item_id: int) -> Optional[datetime]:
    """Quando o alarme deste item tocou pela ultima vez.

    Existe para responder UMA pergunta: o alarme e mais novo que a decisao
    pendente que esta na tela? Quem chegou por ultimo e que manda — sem isso
    a palavra "pago" era roubada do menu da foto e concluia outro item.
    """
    with get_conn() as conn:
        r = conn.execute(
            """SELECT sent_at FROM dispatches
               WHERE user_id=? AND item_id=?
                 AND kind IN ('hora','vencimento','vencido')
               ORDER BY sent_at DESC, id DESC LIMIT 1""",
            (user_id, item_id)).fetchone()
    if not r or not r[0]:
        return None
    try:
        return datetime.strptime(str(r[0])[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


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
        # O CÓDIGO DE PAGAMENTO MORRE NA VIRADA (auditoria M3.5, P2).
        #
        # Cada boleto tem o seu: o de setembro não paga outubro. Mantendo a
        # coluna, o aviso do mês seguinte sairia com o código do mês passado
        # — a pessoa cola no banco e ou o banco recusa (e ela para de confiar
        # na mensagem) ou, pior, ela paga de novo a conta que já pagou.
        # Sem código, o aviso continua saindo; só não oferece o que não sabe.
        conn.executemany(
            "UPDATE items SET data_vencimento=?, hora_alvo=?, "
            "status='pendente', codigo_pagamento=NULL, codigo_tipo=NULL "
            "WHERE id=?",
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


def dentro_da_janela(user_id=None, telefone: str = "",
                     horas: int = 24) -> bool:
    """A pessoa falou com o bot nas últimas 24h?

    É o que decide se podemos mandar texto livre ou se precisa de template
    (M2.0). Três coisas que a auditoria do M2.0 provou não serem detalhe:

    1. **Casa por TELEFONE, não só por user_id.** O webhook grava toda
       mensagem de entrada com `user_id=None` (`wa_bot.py`, rota do webhook)
       — é o mesmo motivo pelo qual `conversa_recente` já busca por telefone.
       A primeira versão desta função só olhava `user_id` e por isso NUNCA
       devolvia True em produção: o motor proativo inteiro teria parado.
    2. **Formato de `ts` normalizado.** `log_message` grava
       `isoformat()` ('2026-08-16T09:59:36', com T) e o corte era montado com
       `strftime` (espaço). Em comparação de string 'T' (0x54) > ' ' (0x20),
       então qualquer mensagem do mesmo dia-calendário do corte passava — a
       janela de 24h virava até ~48h. Aqui os dois lados usam 'T'.
    3. **Só mensagem de ENTRADA conta.** Se a saída do bot abrisse a janela,
       ele se autoautorizaria a falar pra sempre — exatamente o que a Meta
       proíbe e o que rendeu duas restrições neste número.

    A fonte é o banco, não memória de processo: dicionário em memória morre
    no redeploy.
    """
    digitos = re.sub(r"\D", "", telefone or "")
    if not user_id and not digitos:
        return False
    corte = (tempo.agora() - timedelta(hours=horas)).isoformat(
        timespec="seconds")
    # O FILTRO GROSSO VAI NO SQL, PRA NÃO MATAR O ÍNDICE.
    #
    # `replace(ts,' ','T') >= ?` é função sobre a coluna: o SQLite ignora o
    # `idx_msglog_ts` e faz SCAN da tabela inteira (medido: 48ms com 200 mil
    # linhas, crescendo linear, num caminho chamado por disparo a cada 5min).
    # Como 'T' (0x54) > ' ' (0x20), cortar pelo formato com ESPAÇO é um
    # superconjunto seguro — nenhuma linha válida escapa — e o índice volta.
    # O refino exato fica no Python, que já estava aqui.
    #
    # `tipo <> 'resgate_painel'`: aquilo é o dono escrevendo pela pessoa no
    # painel, não a pessoa falando. Não abre janela.
    corte_dt = tempo.agora() - timedelta(hours=horas)
    corte_grosso = corte_dt.strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        linhas = conn.execute(
            """SELECT user_id, telefone, ts FROM msg_log
                WHERE direcao='in' AND ts >= ?
                  AND COALESCE(tipo,'') <> 'resgate_painel'""",
            (corte_grosso,)).fetchall()
    for r in linhas:
        try:
            quando = datetime.strptime(
                str(r["ts"])[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        if quando < corte_dt:
            continue
        if user_id and r["user_id"] == user_id:
            return True
        if digitos and re.sub(r"\D", "", r["telefone"] or "") == digitos:
            return True
    return False


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


# Limiares do termômetro anti-bloqueio. Ficam aqui, com nome, porque são
# regra de negócio: quem muda o número tem que ver o motivo ao lado.
# Calibrados no incidente de 04/08 (restrição da Meta com pico de 4/min).
PICO_ALTO = 10          # msg no mesmo minuto: assinatura de robô
PICO_ATENCAO = 6
RAZAO_ALTA = 3.0        # proativas por mensagem recebida: broadcaster
RAZAO_ATENCAO = 1.5


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
    # P1-5 (relatório de 13/08): a régua saía assim, e se lia como mentira —
    #     12/08  🟢 ok    pico 4/min · 6 proativas em 24h
    #     13/08  🔴 alto  pico 1/min · 9 proativas em 24h
    # Pico 4 verde e pico 1 vermelho. A conta estava certa (o 13/08 fechou no
    # RITMO proativo, não no pico), mas quem lê só via os dois números que o
    # texto mostrava. Número que o Kevin lê todo dia e não confia é pior que
    # número nenhum: agora quem decide o risco DIZ o que decidiu.
    if pico >= PICO_ALTO:
        risco, motivo = "🔴 alto", f"pico de {pico} msg no mesmo minuto"
    elif razao >= RAZAO_ALTA:
        risco, motivo = "🔴 alto", f"{razao}x mais proativas que respostas"
    elif pico >= PICO_ATENCAO:
        risco, motivo = "🟡 atenção", f"pico de {pico} msg no mesmo minuto"
    elif razao >= RAZAO_ATENCAO:
        risco, motivo = "🟡 atenção", f"{razao}x mais proativas que respostas"
    else:
        risco, motivo = "🟢 ok", "ritmo normal"
    return {
        "janela_horas": horas,
        "pico_por_minuto": pico,
        "saidas": saidas,
        "entradas": entradas,
        "proativas": proativas,
        "razao_proativa_por_recebida": razao,
        "motivo": motivo,
        "usuario_mais_notificado": (dict(zip(("user_id", "n"), pior_user))
                                    if pior_user else None),
        "risco": risco,
    }


def _sufixo_tel(telefone) -> str:
    """Os 8 últimos dígitos — a única parte estável de um telefone aqui.

    A Meta devolve o `wa_id` brasileiro SEM o 9º dígito (está documentado em
    `meta_cloud.py`), e `msg_log.telefone` guarda exatamente esse `wa_id`.
    Comparar dígito a dígito fazia a exclusão do dono não casar: ele mandava
    40 mensagens e o heatmap contava as 40. `conversa_recente` já usa esta
    regra — agora ela tem nome.
    """
    return re.sub(r"\D", "", str(telefone or ""))[-8:]


def heatmap_constancia(dias: int = 90,
                       excluir_telefones: Optional[list] = None) -> list[dict]:
    """[{data, n}] por dia, SEM buraco — dia sem uso é zero.

    O buraco é o defeito que importa aqui: uma série que só traz os dias com
    atividade desenha dez usos esparsos como dez quadrados seguidos, e o
    heatmap passa a mentir exatamente sobre a coisa que ele existe para
    mostrar. Por isso a série é construída a partir do calendário, não do
    resultado da consulta.

    Conta só ENTRADA (o que a pessoa faz), e o dono fica de fora — mesma
    regra do `engajamento`, pelo mesmo motivo: ele testa o dia inteiro e
    infla a métrica na direção em que a gente quer acreditar.
    """
    if dias <= 0:
        return []
    hoje = tempo.hoje()
    ini = hoje - timedelta(days=dias - 1)
    # `if _sufixo_tel(t)`: sufixo VAZIO viraria coringa e derrubaria
    # toda linha de telefone nulo. É o gêmeo exato da guarda que
    # existe em `conhecidos`, no `engajamento` — a mesma regra tem
    # que valer nos dois lugares.
    fora = {_sufixo_tel(t) for t in (excluir_telefones or [])
            if t and _sufixo_tel(t)}
    contagem: dict = {}
    with get_conn() as conn:
        # FILTRO NA COLUNA CRUA, pra não matar o `idx_msglog_ts`. Função
        # sobre a coluna (`substr(replace(ts,...))`) fazia SCAN da tabela
        # inteira — o mesmo defeito que já está medido e comentado dentro de
        # `dentro_da_janela`, aqui rodando 2x por request a cada 20s.
        # `ini + " "` é superconjunto seguro nos dois formatos de ts,
        # porque 'T' (0x54) > ' ' (0x20) — e é EXATO, não aproximado:
        # provado por varredura (200 mil strings) que nenhuma linha passa
        # no corte e precisaria ser barrada depois. Não há refino no
        # Python, e o comentário que prometia um foi removido junto com o
        # `if` morto que ele descrevia.
        for row in conn.execute(
                """SELECT ts, telefone FROM msg_log
                    WHERE direcao='in' AND ts >= ?
                      AND COALESCE(tipo,'') <> 'resgate_painel'""",
                (ini.isoformat() + " ",)):
            # `resgate_painel` é o DONO digitando pela pessoa no painel. Se
            # contasse, o heatmap inflaria na direção em que a gente quer
            # acreditar — o viés que esta função existe pra evitar.
            if fora and _sufixo_tel(row["telefone"]) in fora:
                continue
            dia = str(row["ts"])[:10]
            contagem[dia] = contagem.get(dia, 0) + 1
    return [{"data": (ini + timedelta(days=i)).isoformat(),
             "n": contagem.get((ini + timedelta(days=i)).isoformat(), 0)}
            for i in range(dias)]


def constancia(dias: int = 90,
               excluir_telefones: Optional[list] = None,
               serie: Optional[list] = None) -> dict:
    """O número que o heatmap resume: em quantos DIAS houve uso.

    A média é por dia ATIVO, não pela janela. Dividir por 90 dilui e esconde
    justamente quem usa muito em poucos dias — que é o perfil que a gente
    precisa distinguir de quem usa pouco todo dia.

    `serie` pronta evita a SEGUNDA varredura: o painel pedia heatmap e
    constância, e cada um varria a tabela inteira.
    """
    if serie is None:
        serie = heatmap_constancia(dias, excluir_telefones)
    ativos = [p for p in serie if p["n"] > 0]
    total = sum(p["n"] for p in serie)
    return {
        "janela_dias": dias,
        "dias_com_uso": len(ativos),
        "total": total,
        "media_por_dia_ativo": round(total / len(ativos), 2) if ativos else 0.0,
        "maior_dia": max((p["n"] for p in serie), default=0),
    }


def gastos_por_categoria(user_id: int, meses: int = 3) -> dict:
    """{categoria: total} das despesas, do maior pro menor.

    Só `tipo='despesa'` com valor: lembrete com valor (uma consulta que vai
    custar 300) não é dinheiro que saiu — contar isso como gasto faria o
    painel mostrar despesa que nunca aconteceu.
    """
    corte = (tempo.hoje() - timedelta(days=31 * max(1, meses))).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT categoria, COALESCE(SUM(valor_reais),0) t
                 FROM items
                WHERE user_id=? AND tipo='despesa'
                  AND valor_reais IS NOT NULL
                  AND substr(data_criacao,1,10) >= ?
                GROUP BY categoria""", (user_id, corte)).fetchall()
    saida = {r["categoria"]: round(float(r["t"]), 2) for r in rows
             if float(r["t"]) > 0}
    return dict(sorted(saida.items(), key=lambda kv: kv[1], reverse=True))


def gastos_da_semana(user_id: int, ref: Optional[date] = None,
                     dias: int = 7) -> dict:
    """{total, por_categoria, n, total_anterior} da semana desta pessoa.

    POR `data_criacao`, e não por vencimento ou baixa. A pergunta que o
    resumo responde é "o que você registrou nesta semana" — e essa é a única
    versão que existe pra todo mundo. Medir pela BAIXA dependeria de a pessoa
    responder "paguei", e quem não responde é justamente quem o resumo
    deveria alcançar: o relatório chegaria vazio pra quase toda a base.
    A mensagem diz "registradas" por isso — não afirma que o dinheiro saiu.

    Só `tipo='despesa'` com valor, a mesma regra do `gastos_por_categoria`.
    Se as duas divergirem, o painel do dono e o WhatsApp do usuário passam a
    contar histórias diferentes sobre o mesmo dado.
    """
    ref = ref or tempo.hoje()
    inicio = (ref - timedelta(days=dias)).isoformat()
    inicio_anterior = (ref - timedelta(days=2 * dias)).isoformat()

    def _linhas(de, ate):
        with get_conn() as conn:
            return conn.execute(
                """SELECT categoria, COALESCE(SUM(valor_reais),0) t,
                          COUNT(*) n
                     FROM items
                    WHERE user_id=? AND tipo='despesa'
                      AND valor_reais IS NOT NULL
                      AND substr(data_criacao,1,10) > ?
                      AND substr(data_criacao,1,10) <= ?
                    GROUP BY categoria""", (user_id, de, ate)).fetchall()

    atual = _linhas(inicio, ref.isoformat())
    por_categoria = {r["categoria"]: round(float(r["t"]), 2) for r in atual
                     if float(r["t"]) > 0}
    # MESMO FILTRO DOS DOIS LADOS. Sem o `> 0` aqui, a semana anterior
    # somava categorias que o lado atual descarta — e a comparacao passava a
    # ter um vies embutido, sempre na mesma direcao.
    anterior = [r for r in _linhas(inicio_anterior, inicio)
                if float(r["t"]) > 0]
    return {
        "total": round(sum(por_categoria.values()), 2),
        "por_categoria": dict(sorted(por_categoria.items(),
                                     key=lambda kv: kv[1], reverse=True)),
        "n": sum(int(r["n"]) for r in atual),
        "total_anterior": round(sum(float(r["t"]) for r in anterior), 2),
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


def engajamento(excluir_telefones: Optional[list] = None,
                ref: Optional[date] = None) -> dict:
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
    # `ref` = ultimo dia da janela. Existe pro relatorio do dono comparar a
    # semana atual com a anterior (M2.5): numero solto nao diz se melhorou, e
    # era exatamente esse o defeito do relatorio das 8h.
    fim = ref or tempo.hoje()
    ini = (fim - timedelta(days=6)).isoformat()
    # Limite SUPERIOR: sem ele, `ref` no passado somaria tudo o que veio
    # depois e as duas janelas dariam quase o mesmo numero — a tendencia
    # apareceria como "estavel" sempre, que e pior que nao ter tendencia.
    # O ' ' no fim do corte e proposital: `log_message` grava com 'T', e
    # 'T' > ' ', entao `< "AAAA-MM-DD "` exclui o dia inteiro.
    lim = (fim + timedelta(days=1)).isoformat() + " "
    fora = {re.sub(r"\D", "", t) for t in (excluir_telefones or []) if t}
    with get_conn() as conn:
        ids_fora = set()
        if fora:
            for row in conn.execute("SELECT id, telefone FROM users"):
                if re.sub(r"\D", "", row["telefone"] or "") in fora:
                    ids_fora.add(row["id"])
        # SUFIXO -> (id, nome) da BASE. Resolver uma vez aqui é o que faz
        # `pessoas`, `top` e `base_comparavel` saírem da MESMA população.
        # Sem isso, `pessoas` contava qualquer número que mandou "oi" e o
        # painel voltava a se contradizer — "3 de 1 pessoa(s) mandaram
        # algo" —, que é o defeito que `_linha_engajamento` existe pra
        # matar, agora com o sinal invertido.
        conhecidos = {}
        for row in conn.execute("SELECT id, nome, telefone FROM users"):
            if row["id"] in ids_fora:
                continue
            _suf = _sufixo_tel(row["telefone"])
            if not _suf:
                # usuario com telefone vazio capturava TODA linha de
                # telefone nulo — um "Fantasma" no topo do ranking.
                continue
            conhecidos[_suf] = (row["id"], row["nome"] or "")
        # POR TELEFONE, NÃO SÓ POR user_id.
        #
        # A cláusula `m.user_id IS NOT NULL` zerava esta métrica em
        # PRODUÇÃO: nenhuma linha `direcao='in'` tem id, porque quem grava é
        # o webhook (`db.log_message(None, num, "in", ...)`) — o mesmo
        # motivo pelo qual `dentro_da_janela` e `conversa_recente` já casam
        # por telefone. Ou seja, "0.0 demandas por pessoa/dia" e "🔴 não
        # virou hábito" eram o que o painel dizia SEMPRE, com qualquer
        # volume de uso. O número principal do negócio estava morto.
        #
        # O M2.3 tornou isso visível ao pendurar no mesmo card um heatmap
        # que conta justamente as linhas que esta consulta ignorava.
        _tel_fora = {_sufixo_tel(t) for t in fora}
        linhas = conn.execute(
            """SELECT user_id, telefone FROM msg_log
                WHERE direcao='in' AND ts >= ? AND ts < ?
                  AND COALESCE(tipo,'') <> 'resgate_painel'""",
            (ini + " ", lim)).fetchall()
        quem = {}
        desconhecidos = 0
        for l in linhas:
            suf = _sufixo_tel(l["telefone"])
            if _tel_fora and suf in _tel_fora:
                continue
            if l["user_id"] and l["user_id"] in ids_fora:
                continue
            if suf in conhecidos:
                quem[suf] = quem.get(suf, 0) + 1
            elif l["user_id"]:
                quem[f"id:{l['user_id']}"] = quem.get(
                    f"id:{l['user_id']}", 0) + 1
            else:
                # Número que não está na base: engano, spam, alguém que
                # nunca completou o cadastro. Vira campo PRÓPRIO, nunca
                # somado em `pessoas` — dois enganos derrubariam a métrica
                # pela metade com 11 usuários, e número que qualquer
                # estranho move não serve pra decidir nada.
                desconhecidos += 1
        n, u = sum(quem.values()), len(quem)
        # `top` SAI DO MESMO DICIONÁRIO. Antes ele fazia
        # `JOIN users ON u.id = m.user_id` — e `user_id` é NULL em toda
        # linha de entrada, que é a raiz consertada logo acima. O painel
        # passaria a mostrar "1.5 demandas por pessoa/dia" com a tabela
        # "quem mais usa" VAZIA, e é o dono olhando pra isso que decide se
        # o produto pegou.
        # `(None, "")` e nao `(None, k)`: com a chave como fallback, uma
        # linha migrada com `user_id` e telefone divergente punha
        # literalmente "id:4" como NOME no painel do dono — e "id:4"
        # e truthy, entao o "sem nome" nunca disparava.
        top = [{"nome": conhecidos.get(k, (None, ""))[1] or "sem nome",
                "n": v}
               for k, v in sorted(quem.items(), key=lambda kv: kv[1],
                                  reverse=True)[:5]]
        # quanto do tráfego era do próprio dono — pro tamanho do viés ficar visível
        rt = conn.execute(
            """SELECT COUNT(*) FROM msg_log WHERE direcao='in'
               AND ts >= ? AND ts < ?
               AND COALESCE(tipo,'') <> 'resgate_painel'""",
            (ini + " ", lim)).fetchone()
        total = int(rt[0]) if rt else 0
    por_dia = round(n / (u * 7), 2) if u else 0.0
    # BASE COMPARÁVEL (auditoria v23.4, P2-9): o denominador do painel era
    # COUNT(*) FROM users, que INCLUI o dono — enquanto `pessoas` o exclui.
    # Dava "0 de 11 (sem contar você)" com o você dentro dos 11. Duas contas
    # certas medindo populações diferentes é o jeito mais fácil de fazer o
    # dono parar de confiar no próprio painel.
    with get_conn() as conn:
        r_tot = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    base = max(0, int(r_tot[0] if r_tot else 0) - len(ids_fora))
    return {"despejos_7d": n, "pessoas": u, "por_pessoa_dia": por_dia,
            "base_comparavel": base,
            # A exclusão do dono agora é por TELEFONE (ele nem sempre tem
            # linha em `users`). Ler a flag só de `ids_fora` fazia a copy
            # dizer o contrário do que o código fez: as 30 mensagens dele
            # eram descontadas e o "(sem contar você)" sumia da linha.
            "dono_excluido": bool(ids_fora or _tel_fora),
            "desconhecidos_7d": desconhecidos,
            "mensagens_do_dono_7d": max(0, total - n - desconhecidos),
            "veredito": ("🟢 virou hábito" if por_dia >= 2 else
                         "🟡 no limite" if por_dia >= 1 else
                         "🔴 não virou hábito" if u else
                         "⚪ sem usuário real ainda"),
            "top": top}


PRECO_MENSAL = float(os.environ.get("PRECO_MENSAL", "19.90"))

# ═══════════════════════════════════════════════════════════════════════
# CUSTOS — TODOS, exceto as horas do Kevin (decisão dele)
# ═══════════════════════════════════════════════════════════════════════
# Ajuste no EasyPanel, nunca aqui. Cada linha é nomeada porque "outros: 200"
# é o jeito mais rápido de perder o controle de onde o dinheiro vai.
#
# ── FIXOS: pagos todo mês, independem de ter 2 ou 200 usuários ──────────
# Ferramenta de desenvolvimento (Claude). Kevin informou ~R$100/mês.
# Sim, entra na conta: sem ela o produto não existe e não evolui.
CUSTO_CLAUDE_MES = float(os.environ.get("CUSTO_CLAUDE_MES", "100"))
# Piso/assinatura da OpenAI, se houver. O consumo por mensagem vai embaixo.
CUSTO_OPENAI_MES = float(os.environ.get("CUSTO_OPENAI_MES", "0"))
# Gateway do WhatsApp (WasenderAPI) — mensalidade.
CUSTO_WASENDER_MES = float(os.environ.get("CUSTO_WASENDER_MES", "0"))
# Servidor na nuvem onde roda o bot (o VPS do 177.153.58.163).
CUSTO_VPS_MES = float(os.environ.get("CUSTO_VPS_MES", "0"))
# Domínio resolveai.ia.br (Registro.br é anual — divida por 12 aqui).
CUSTO_DOMINIO_MES = float(os.environ.get("CUSTO_DOMINIO_MES", "0"))
# Chip dedicado do número do bot (linha + plano).
CUSTO_CHIP_MES = float(os.environ.get("CUSTO_CHIP_MES", "0"))
# Qualquer outra assinatura recorrente.
CUSTO_OUTROS_MES = float(os.environ.get("CUSTO_OUTROS_MES", "0"))

# ── VARIÁVEIS: crescem com o uso ───────────────────────────────────────
# Cada mensagem recebida vira ~1 chamada de LLM (gpt-4o-mini + Whisper/visão
# quando é áudio ou foto). É o custo que assusta em escala, porque o usuário
# MAIS ENGAJADO é o MAIS CARO — a pior curva de custo que existe.
CUSTO_LLM_POR_MSG = float(os.environ.get("CUSTO_LLM_POR_MSG", "0.02"))
# Custo por mensagem ENVIADA. Zero no Wasender; ~R$0,035 na API oficial.
CUSTO_MSG_ENVIADA = float(os.environ.get("CUSTO_MSG_ENVIADA", "0"))

# ── CUSTO POR SERVICO, e nao uma taxa unica por mensagem ───────────────
#
# `CUSTO_LLM_POR_MSG` sozinho cobrava o mesmo por um "ok" de texto e por um
# audio de 40s que passa por transcricao + LLM. E deixava de fora categorias
# INTEIRAS: a locucao do podcast, a leitura de boleto por foto e o custo de
# conversa da Meta. Margem calculada assim erra pra cima justamente no
# usuario mais engajado — que e o mais caro.
#
# ATENCAO, e vale a mesma regra da tabela de CLT: estes numeros sao
# ESTIMATIVA de tabela publica, convertidos a dolar aproximado. Cada um e
# variavel de ambiente pra ser corrigido com a fatura na mao, e o painel
# mostra que sao estimativa ate alguem confirmar.
DOLAR = float(os.environ.get("DOLAR", "5.40"))

# gpt-4o-mini com prompt do sistema + historico: ~4k entrada, ~400 saida.
CUSTO_LLM_TEXTO = float(os.environ.get("CUSTO_LLM_TEXTO", "0.006"))
# Whisper ~US$0.006/min. Audio tipico de 30s.
CUSTO_TRANSCRICAO = float(os.environ.get("CUSTO_TRANSCRICAO", "0.02"))
# Visao: a foto vira ~1,1k tokens de entrada, alem do prompt.
CUSTO_VISAO = float(os.environ.get("CUSTO_VISAO", "0.03"))
# TTS ~US$15/1M caracteres. Um episodio de 400 palavras ~2,4k caracteres.
# E o item mais caro por uso do produto inteiro.
CUSTO_TTS_EPISODIO = float(os.environ.get("CUSTO_TTS_EPISODIO", "0.20"))
# Conversa iniciada pela empresa na Cloud API. Texto livre DENTRO da janela
# de 24h nao custa — so o template abre conversa paga.
CUSTO_TEMPLATE_UTILITY = float(os.environ.get("CUSTO_TEMPLATE_UTILITY",
                                              "0.045"))
CUSTO_TEMPLATE_MARKETING = float(os.environ.get("CUSTO_TEMPLATE_MARKETING",
                                                "0.35"))
# Enquanto ninguem conferir uma fatura, o painel avisa que e estimativa.
CUSTOS_CONFERIDOS = os.environ.get("CUSTOS_CONFERIDOS", "0") == "1"

# ── SOBRE O FATURAMENTO ────────────────────────────────────────────────
# % retido pela plataforma de pagamento (Kirvano/Stripe/Mercado Pago).
TAXA_PAGAMENTO_PCT = float(os.environ.get("TAXA_PAGAMENTO_PCT", "0"))
# Imposto sobre faturamento (MEI/Simples). 0 até formalizar.
IMPOSTO_PCT = float(os.environ.get("IMPOSTO_PCT", "0"))

_FIXOS = [
    ("Claude (dev)", "CUSTO_CLAUDE_MES"),
    ("OpenAI (assinatura)", "CUSTO_OPENAI_MES"),
    ("WasenderAPI", "CUSTO_WASENDER_MES"),
    ("Servidor (VPS)", "CUSTO_VPS_MES"),
    ("Domínio", "CUSTO_DOMINIO_MES"),
    ("Chip do bot", "CUSTO_CHIP_MES"),
    ("Outros", "CUSTO_OUTROS_MES"),
]


def fixos_do_painel() -> dict:
    """Os custos fixos que o dono digitou na tela, por variavel.

    Antes so existiam como variavel de ambiente, e o efeito pratico era que
    ninguem preenchia: o VPS ficou em ZERO por meses, entao o custo fixo do
    painel mostrava R$ 100 quando o real era maior. Custo que exige deploy
    pra corrigir e custo que fica errado.
    """
    import json as _j
    try:
        bruto = get_setting("custos_fixos")
        if bruto:
            d = _j.loads(bruto)
            return {k: float(v) for k, v in d.items() if v not in (None, "")}
    except Exception:
        pass
    return {}


def valor_fixo(var: str) -> float:
    """O que a tela diz, e so entao o ambiente.

    A tela ganha do ambiente de proposito: quem corrige o numero e o dono,
    olhando a fatura, e ele nao deve precisar de deploy pra isso.
    """
    guardados = fixos_do_painel()
    if var in guardados:
        return guardados[var]
    try:
        return float(globals().get(var, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def zerar_cliente(user_id: int, por: str = "") -> bool:
    """Apaga TUDO de um cliente pra ele voltar como usuário novo (M3.0).

    Pedido do Kevin pro caso do Davi, que travou e não conseguia mais mandar
    mensagem: em vez de caçar o estado corrompido, zera e ele reentra pela
    landing page.

    É `delete_user` com log de admin. `delete_user` já apaga items, msg_log,
    memoria e dispatches — isso foi auditado em 11/08/2026, quando se
    descobriu que ele só limpava `users` e o texto das conversas continuava
    no banco depois de o bot dizer que tinha apagado tudo.

    IRREVERSÍVEL, e por isso registrado: daqui a um mês ninguém lembra quem
    sumiu da base nem por quê.
    """
    u = get_user(user_id)
    if not u:
        return False
    apelido = (u.get("nome") or "?").split()[0]
    telefone = u.get("telefone") or ""
    delete_user(user_id)
    registrar_acao_admin("zerar_cliente", alvo=user_id, por=por,
                         detalhe=f"{apelido} {telefone} — apagado por completo")
    return True


# Quem é quem, pro envio em lote. Cada segmento é uma pergunta de negócio,
# não um filtro genérico: "quem sumiu", "quem entrou e não usou".
SEGMENTO_DESENGAJADO_DIAS = 10


def segmentos(excluir_telefones: Optional[list] = None,
              ref: Optional[datetime] = None) -> dict:
    """Agrupa a base nos recortes que o dono usa pra decidir uma ação.

    O dono é excluído: mandar template pra si mesmo gasta cota e polui o
    teste do que ele está tentando medir.
    """
    agora = ref or tempo.agora()
    fora = {re.sub(r"\D", "", t) for t in (excluir_telefones or []) if t}
    out: dict = {"todos": [], "desengajados": [], "sem_itens": [],
                 "trial": [], "ativos": []}
    for u in list_users():
        if re.sub(r"\D", "", u.get("telefone") or "") in fora:
            continue
        visto = _dias_desde(u.get("ultima_interacao"), agora)
        with get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM items WHERE user_id=?",
                             (u["id"],)).fetchone()["c"]
        p = {"id": u["id"], "nome": (u.get("nome") or "?").split()[0],
             "telefone": u.get("telefone"), "status": u.get("status"),
             "itens": n, "visto_ha": visto}
        out["todos"].append(p)
        if visto is not None and visto > SEGMENTO_DESENGAJADO_DIAS:
            out["desengajados"].append(p)
        if not n:
            out["sem_itens"].append(p)
        if (u.get("status") or "trial") == "trial":
            out["trial"].append(p)
        elif u.get("status") == "ativo":
            out["ativos"].append(p)
    return out


PLANOS = ("mensal", "anual")
# Quantos dias depois do vencimento a assinatura vira "atrasada" no painel.
# Zero: o Mercado Pago cobra no dia, então no dia seguinte já vale conferir.
ASSINATURA_TOLERANCIA_DIAS = 0


def _soma_meses(d: date, meses: int) -> date:
    """Mesmo dia do mês N meses depois; em mês curto, o último dia.

    31/01 + 1 mês não existe. Cair em 28/02 é o que o Mercado Pago faz e o
    que a pessoa espera; estourar exceção aqui derrubaria o painel inteiro
    num dia 31.
    """
    ano = d.year + (d.month - 1 + meses) // 12
    mes = (d.month - 1 + meses) % 12 + 1
    if mes == 12:
        ultimo = 31
    else:
        ultimo = (date(ano + (mes == 12), mes % 12 + 1, 1)
                  - timedelta(days=1)).day
    return date(ano, mes, min(d.day, ultimo))


def aprovar_pagamento(user_id: int, plano: str, em: Optional[str] = None,
                      por: str = "") -> bool:
    """O dono confirmou no Mercado Pago: vira ativo e o ciclo começa HOJE.

    `em` existe pra lançar retroativo (ele conferiu na segunda um pagamento
    de sexta), mas o padrão é o dia da aprovação — foi o que o Kevin pediu:
    "no dia que eu colocar é o dia que começa a contar".
    """
    if plano not in PLANOS:
        raise ValueError(f"plano inválido: {plano!r} (use {PLANOS})")
    quando = em or tempo.hoje().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET status='ativo', plano=?, pago_em=? WHERE id=?",
            (plano, quando, user_id))
        ok = cur.rowcount > 0
    if ok:
        registrar_acao_admin("aprovar_pagamento", alvo=user_id, por=por,
                             detalhe=f"plano={plano} inicio={quando}")
    return ok


def assinatura(user: dict, hoje: Optional[date] = None) -> dict:
    """Onde a assinatura desta pessoa está no ciclo.

    NÃO dispara nada. É leitura pro painel — a decisão de cobrar de novo é
    do dono, num botão, porque só ele viu o extrato do Mercado Pago.
    """
    hoje = hoje or tempo.hoje()
    plano = (user or {}).get("plano")
    pago_em = (user or {}).get("pago_em")
    vazio = {"plano": None, "pago_em": None, "vence_em": None,
             "dias_para_vencer": None, "dias_atraso": 0, "atrasado": False}
    if plano not in PLANOS or not pago_em:
        return vazio
    try:
        inicio = date.fromisoformat(str(pago_em)[:10])
    except (ValueError, TypeError):
        return vazio
    vence = (_soma_meses(inicio, 1) if plano == "mensal"
             else _soma_meses(inicio, 12))
    faltam = (vence - hoje).days
    atraso = max(0, -faltam - ASSINATURA_TOLERANCIA_DIAS)
    return {"plano": plano, "pago_em": str(pago_em)[:10],
            "vence_em": vence.isoformat(), "dias_para_vencer": faltam,
            "dias_atraso": atraso, "atrasado": atraso > 0}


def aguardando_aprovacao(limite: int = 50) -> list[dict]:
    """Quem pediu o link de pagamento e ainda não foi aprovado pelo dono.

    A fila existe porque o pedido e a confirmação são eventos diferentes: o
    bot manda o link na hora, mas quem diz se o dinheiro entrou é o Kevin,
    olhando o Mercado Pago. Sem esta lista o pedido se perderia no meio das
    conversas.
    """
    hoje = tempo.agora()
    with get_conn() as conn:
        linhas = conn.execute(
            """SELECT u.id, u.nome, u.telefone, u.status,
                      MAX(d.sent_at) AS pediu_em
                 FROM users u JOIN dispatches d ON d.user_id=u.id
                WHERE d.kind='link-pagamento' AND u.status <> 'ativo'
                GROUP BY u.id ORDER BY pediu_em DESC LIMIT ?""",
            (limite,)).fetchall()
    return [{"id": r["id"], "nome": (r["nome"] or "?").split()[0],
             "telefone": r["telefone"], "status": r["status"],
             "pediu_em": r["pediu_em"],
             "pediu_ha_dias": _dias_desde(r["pediu_em"], hoje)}
            for r in linhas]


VALIDACAO_ITENS_PARA_ATIVAR = 3  # cadastrou de verdade, não só deu "oi"
VALIDACAO_SUMIDO_DIAS = 10       # sem falar com o bot há mais que isso
VALIDACAO_RETIDO_DIAS = 7


def _dias_desde(carimbo, agora=None):
    """Dias inteiros entre um carimbo do banco e agora. None se não dá.

    Aceita os dois formatos que convivem no banco ('T' e espaço) porque esta
    função lê `users.ultima_interacao` e `users.data_criacao`, gravados por
    caminhos diferentes ao longo de várias versões.
    """
    if not carimbo:
        return None
    agora = agora or tempo.agora()
    try:
        quando = datetime.strptime(
            str(carimbo)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return max(0, (agora - quando).days)


def validacao(trial_days: int = 14, excluir_telefones: Optional[list] = None,
              ref: Optional[datetime] = None) -> dict:
    """As três perguntas que decidem se isto vira negócio.

    NÃO é quantas mensagens o bot mandou — esse número sobe sozinho e não
    prova nada. As perguntas são:

      1. ATIVAÇÃO — a pessoa registra sozinha? (>= 3 itens criados)
      2. "AHA"    — o bot avisou e ela deu baixa? (a única métrica que prova
                    que o produto fez alguma coisa por alguém)
      3. RETENÇÃO — ela volta? (falou nos últimos 7 dias)

    A pergunta 2 depende de `items.data_conclusao` (M2.8) e compara com
    `dispatches.sent_at`. As duas colunas gravam no MESMO formato, com
    espaço: comparar 'T' com ' ' daria a resposta errada silenciosamente.

    Devolve também a lista de pessoas COM NOME, porque com 11 usuários a
    ação que o painel precisa provocar é "ligar pra fulano", não "melhorar
    a métrica".
    """
    agora = ref or tempo.agora()
    fora = {re.sub(r"\D", "", t) for t in (excluir_telefones or []) if t}
    pessoas: list[dict] = []
    with get_conn() as conn:
        linhas = conn.execute(
            """SELECT u.id, u.nome, u.telefone, u.status, u.data_criacao,
                      u.ultima_interacao,
                      (SELECT COUNT(*) FROM items i
                        WHERE i.user_id=u.id) AS itens,
                      (SELECT COUNT(*) FROM items i
                        WHERE i.user_id=u.id
                          AND i.status='concluido') AS baixas,
                      (SELECT COUNT(DISTINCT i.id) FROM items i
                         JOIN dispatches d ON d.item_id=i.id
                        WHERE i.user_id=u.id
                          AND i.status='concluido'
                          AND i.data_conclusao IS NOT NULL
                          AND d.sent_at <= i.data_conclusao)
                          AS baixas_apos_lembrete
                 FROM users u ORDER BY u.id""").fetchall()

    for r in linhas:
        if re.sub(r"\D", "", r["telefone"] or "") in fora:
            continue
        visto = _dias_desde(r["ultima_interacao"], agora)
        casa = _dias_desde(r["data_criacao"], agora)
        pessoas.append({
            "id": r["id"],
            "nome": (r["nome"] or "?").split()[0],
            "telefone": r["telefone"],
            "status": r["status"] or "trial",
            "dias_de_casa": casa,
            "visto_ha": visto,
            "itens": r["itens"] or 0,
            "baixas": r["baixas"] or 0,
            "baixas_apos_lembrete": r["baixas_apos_lembrete"] or 0,
            "ativado": (r["itens"] or 0) >= VALIDACAO_ITENS_PARA_ATIVAR,
            "salvo": (r["baixas_apos_lembrete"] or 0) > 0,
            # "sumido" só vale pra quem teve tempo de sumir: alguém que
            # entrou ontem e não falou hoje não é churn, é segunda-feira.
            "sumido": (visto is not None
                       and visto > VALIDACAO_SUMIDO_DIAS
                       and (casa or 0) > VALIDACAO_SUMIDO_DIAS),
            "retido": visto is not None and visto <= VALIDACAO_RETIDO_DIAS,
        })

    base = len(pessoas)
    ativados = sum(1 for p in pessoas if p["ativado"])
    salvos = sum(1 for p in pessoas if p["salvo"])
    retidos = sum(1 for p in pessoas if p["retido"])
    pagantes = sum(1 for p in pessoas if p["status"] == "ativo")

    # O VEREDITO DIZ O QUE FAZER, NÃO SÓ COMO ESTÁ.
    #
    # Ordem proposital: o gargalo mais a montante manda. Não adianta falar de
    # conversão com ninguém ativado — o problema seria outro, e a ação
    # sugerida ("mande o link") desperdiçaria a semana.
    if not base:
        veredito = "Sem base pra medir ainda."
    elif ativados == 0:
        veredito = ("Ninguém registrou 3 itens. O gargalo é o CADASTRO, não "
                    "o lembrete: descubra por que não usam antes de "
                    "construir mais.")
    elif salvos == 0:
        veredito = ("Registram, mas o bot ainda não salvou ninguém. Confira "
                    "se os lembretes estão saindo antes de vender.")
    elif retidos < max(1, base // 2):
        veredito = (f"{salvos} de {base} foram salvos pelo bot, mas só "
                    f"{retidos} voltaram essa semana. É retenção, não "
                    f"aquisição, que trava.")
    elif pagantes == 0:
        veredito = (f"Produto funciona ({salvos} salvos, {retidos} ativos) e "
                    f"ninguém paga. Agora o gargalo é PEDIR.")
    else:
        veredito = (f"{pagantes} pagante(s), {salvos} salvos. Hora de "
                    f"prospectar fora dos conhecidos.")

    return {
        "base": base,
        "ativados": ativados,
        "salvos": salvos,
        "retidos": retidos,
        "pagantes": pagantes,
        "pessoas": pessoas,
        "veredito": veredito,
    }


def cobertura_da_jornada() -> dict:
    """Quem esta no trial recebeu quantas etapas da jornada de 14 dias.

    A pergunta que o painel nao respondia e que o dono fez direto: "todos
    tem a jornada?". Sem isto a resposta era fe — a jornada existe no
    codigo, entao presume-se que roda.

    Ela nao roda pra todo mundo, e o motivo e estrutural: os doze nudges
    nao tem template da Meta, entao so saem pra quem falou com o bot nas
    ultimas 24h. Quem esfriou — que e justamente quem os nudges existem
    pra resgatar — nao recebe nenhum.

    Este numero e o que separa "a jornada existe" de "a jornada chegou".
    """
    hoje = tempo.hoje()
    try:
        with get_conn() as conn:
            # SO OS NUDGES DA REGUA. `LIKE 'trial%'` pegava tambem
            # `trial-ending` e `trial-estendido`, que nao sao licoes — a
            # contagem inflava e o painel dizia que a jornada chegou quando
            # o que chegou foi o aviso de fim de teste.
            # `trial_d%` sem escape casaria `trial-ending` tambem: no LIKE
            # do SQL o `_` e curinga de um caractere qualquer. Sem o ESCAPE
            # o filtro nao filtra nada, e a string precisa ser crua senao o
            # Python come a barra antes de o SQLite ver.
            envios = conn.execute(
                r"SELECT user_id, kind, COUNT(*) n FROM dispatches "
                r"WHERE kind LIKE 'trial\_d%' ESCAPE '\' "
                r"GROUP BY user_id, kind").fetchall()
    except Exception:
        return {"pessoas": [], "etapas_possiveis": 0, "sem_nenhuma": 0}

    por_pessoa: dict = {}
    for r in envios:
        uid = r["user_id"]
        if uid is None:
            continue
        por_pessoa.setdefault(int(uid), set()).add(r["kind"])

    linhas = []
    for u in list_users():
        if (u.get("status") or "trial") not in ("trial", "ativo"):
            continue
        try:
            nasceu = date.fromisoformat(str(u.get("data_criacao"))[:10])
            dias = (hoje - nasceu).days
        except Exception:
            dias = 0
        etapas = por_pessoa.get(u["id"], set())
        linhas.append({
            "user_id": u["id"], "nome": u.get("nome") or "?",
            "dias_de_casa": dias,
            "etapas_recebidas": len(etapas),
            "quais": sorted(etapas),
        })
    linhas.sort(key=lambda x: (x["etapas_recebidas"], -x["dias_de_casa"]))
    return {
        "pessoas": linhas,
        # SEIS LICOES MAIS O FECHAMENTO. Era 12, do desenho antigo de
        # nudges diarios — quem recebesse a jornada INTEIRA aparecia como
        # "7/12" e parecia falha onde nao havia.
        "etapas_possiveis": 7,
        "sem_nenhuma": sum(1 for x in linhas if not x["etapas_recebidas"]),
    }


def custo_por_usuario(dias: int = 30) -> list:
    """O que CADA pessoa custou nos ultimos N dias, linha por linha.

    Sem isto a margem era uma media que escondia o que importa: numa base
    pequena, uma pessoa que manda audio todo dia e recebe podcast custa
    varias vezes o que custa quem manda dois textos por semana. A media diz
    "esta tudo bem" ate o cliente caro virar a maioria.

    JUNTA POR TELEFONE, e nao por user_id. A mensagem RECEBIDA e gravada com
    `user_id` nulo (o webhook loga antes de resolver quem e), entao contar
    por user_id daria zero de entrada pra todo mundo — o custo do LLM, que e
    o principal, sumiria da conta. O telefone esta nos dois lados.
    """
    ini = (tempo.hoje() - timedelta(days=max(1, int(dias)) - 1)).isoformat()
    fora = {"", None}
    try:
        with get_conn() as conn:
            # `substr(telefone,-11)` casa os dois lados mesmo quando um tem
            # o 55 e o outro nao — divergencia comum e silenciosa.
            linhas = conn.execute(
                """SELECT u.id AS uid, u.nome AS nome, u.status AS status,
                          m.direcao AS direcao, m.tipo AS tipo,
                          COUNT(*) AS n
                     FROM msg_log m
                     JOIN users u
                       ON substr(u.telefone,-11)=substr(m.telefone,-11)
                    WHERE substr(m.ts,1,10) >= ?
                    GROUP BY u.id, m.direcao, m.tipo""", (ini,)).fetchall()
    except Exception:
        return []

    # O PODCAST EM TRY PROPRIO, e nao junto com a consulta principal.
    #
    # `podcast_log` so nasce quando o modulo do podcast roda pela primeira
    # vez. Com as duas no mesmo `try`, a tabela ausente derrubava a conta
    # INTEIRA e a funcao devolvia lista vazia — o painel diria "custo zero"
    # com a base toda gastando. Uma parte faltando vira uma linha zerada,
    # nunca um relatorio vazio.
    eps = []
    try:
        with get_conn() as conn:
            eps = conn.execute(
                """SELECT user_id, COUNT(*) AS n FROM podcast_log
                    WHERE substr(quando,1,10) >= ? AND ok=1
                    GROUP BY user_id""", (ini,)).fetchall()
    except Exception:
        eps = []

    por_ep = {int(r["user_id"]): int(r["n"]) for r in eps
              if r["user_id"] is not None}
    gente: dict = {}
    for r in linhas:
        uid = int(r["uid"])
        p = gente.setdefault(uid, {
            "user_id": uid, "nome": r["nome"] or "?",
            "status": r["status"] or "trial",
            "texto_in": 0, "audio_in": 0, "imagem_in": 0,
            "templates": 0, "livres_out": 0, "episodios": 0})
        tipo = (r["tipo"] or "").lower()
        n = int(r["n"] or 0)
        if r["direcao"] == "in":
            if "audio" in tipo or "voz" in tipo:
                p["audio_in"] += n
            elif "imagem" in tipo or "image" in tipo or "foto" in tipo:
                p["imagem_in"] += n
            else:
                p["texto_in"] += n
        elif r["direcao"] == "out":
            if tipo == "template":
                p["templates"] += n
            else:
                p["livres_out"] += n

    saida = []
    for uid, p in gente.items():
        p["episodios"] = por_ep.get(uid, 0)
        # O audio e a foto tambem passam pelo LLM depois de transcritos ou
        # lidos: a transcricao e a visao SOMAM ao turno, nao substituem.
        turnos = p["texto_in"] + p["audio_in"] + p["imagem_in"]
        p["custo_llm"] = round(turnos * CUSTO_LLM_TEXTO, 2)
        p["custo_audio"] = round(p["audio_in"] * CUSTO_TRANSCRICAO, 2)
        p["custo_visao"] = round(p["imagem_in"] * CUSTO_VISAO, 2)
        p["custo_podcast"] = round(p["episodios"] * CUSTO_TTS_EPISODIO, 2)
        # Marketing e utility tem preco muito diferente; sem separar, a
        # conta erra pra baixo justamente em quem recebeu anuncio.
        p["custo_template"] = round(p["templates"] * CUSTO_TEMPLATE_UTILITY, 2)
        # Texto livre dentro da janela de 24h nao custa nada na Cloud API —
        # e por isso que fazer a pessoa RESPONDER e economia, nao so
        # engajamento.
        p["custo_livre"] = 0.0
        p["custo_total"] = round(
            p["custo_llm"] + p["custo_audio"] + p["custo_visao"]
            + p["custo_podcast"] + p["custo_template"], 2)
        p["paga"] = (p["status"] == "ativo")
        receita = PRECO_MENSAL if p["paga"] else 0.0
        desconto = receita * (TAXA_PAGAMENTO_PCT + IMPOSTO_PCT) / 100
        p["receita"] = round(receita, 2)
        p["margem"] = round(receita - desconto - p["custo_total"], 2)
        saida.append(p)
    # CUSTO CHEIO: o variavel dele MAIS a fatia do fixo que ele consome.
    #
    # So o variavel (R$ 0,07) faz o produto parecer quase de graca e leva a
    # concluir que o preco pode cair. Mas os R$ 100 de fixo existem e alguem
    # paga: rateados por 13 pessoas dao R$ 7,69 cada — cem vezes o variavel.
    # E o custo cheio, nao o variavel, que decide preco.
    #
    # O RATEIO E PELA BASE INTEIRA, e nao so por quem paga. Ratear so entre
    # pagantes daria "custo infinito por cliente" enquanto ninguem paga, o
    # que e verdade contabil e inutil pra decidir. Pela base, o numero
    # responde: "se todos virassem pagantes hoje, quanto custaria cada um".
    fixo_mes = round(sum(valor_fixo(var) for _nome, var in _FIXOS), 2)
    rateio = round(fixo_mes / len(saida), 2) if saida else 0.0
    for p in saida:
        p["fixo_rateado"] = rateio
        p["custo_cheio"] = round(p["custo_total"] + rateio, 2)
        p["margem_cheia"] = round(p["margem"] - rateio, 2)
    saida.sort(key=lambda x: -x["custo_total"])
    return saida


def custo_medio_por_usuario(dias: int = 30) -> dict:
    """O retrato da base: quanto custa a media, e quanto custa o mais caro.

    Devolve os dois de proposito. Decidir preco pela media, numa base
    pequena, e como decidir pelo cliente que menos usa.
    """
    linhas = custo_por_usuario(dias)
    if not linhas:
        return {"pessoas": 0, "medio": 0.0, "maior": 0.0, "conferido":
                CUSTOS_CONFERIDOS, "topo": []}
    totais = [x["custo_total"] for x in linhas]
    cheios = [x["custo_cheio"] for x in linhas]
    fixo = linhas[0]["fixo_rateado"] * len(linhas) if linhas else 0.0
    return {
        "pessoas": len(linhas),
        "fixo_mes": round(fixo, 2),
        "fixo_rateado": linhas[0]["fixo_rateado"] if linhas else 0.0,
        "cheio_medio": round(sum(cheios) / len(cheios), 2),
        "cheio_maior": round(max(cheios), 2),
        # Quanto sobra de CADA cliente se ele pagar, ja descontado o fixo
        # dele. E este numero que diz se o preco fecha.
        "sobra_por_cliente": round(
            PRECO_MENSAL * (1 - (TAXA_PAGAMENTO_PCT + IMPOSTO_PCT) / 100)
            - (sum(cheios) / len(cheios)), 2),
        # O TOTAL da base, e nao so a media: e ele que soma com o fixo pra
        # dar o custo do mes. Media sozinha nao fecha com nada.
        "total": round(sum(totais), 2),
        "medio": round(sum(totais) / len(totais), 2),
        "maior": round(max(totais), 2),
        "mediana": round(sorted(totais)[len(totais) // 2], 2),
        "conferido": CUSTOS_CONFERIDOS,
        "preco": PRECO_MENSAL,
        "topo": linhas[:5],
    }


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

    fixos_itens = [{"nome": nome, "valor": valor_fixo(var), "var": var}
                   for nome, var in _FIXOS]
    fixos_itens = [x for x in fixos_itens if x["valor"] > 0]
    fixos = round(sum(x["valor"] for x in fixos_itens), 2)
    # cada mensagem recebida vira ~1 chamada de LLM
    custo_llm = round(msgs_in * CUSTO_LLM_POR_MSG, 2)
    custo_envio = round(msgs_out * CUSTO_MSG_ENVIADA, 2)
    variaveis = round(custo_llm + custo_envio, 2)
    taxa = round(bruto * TAXA_PAGAMENTO_PCT / 100, 2)
    imposto = round(bruto * IMPOSTO_PCT / 100, 2)
    custo_total = round(fixos + variaveis + taxa + imposto, 2)
    liquido = round(bruto - custo_total, 2)

    # ── MARGEM POR CLIENTE ──────────────────────────────────────────────
    # Duas contas diferentes, e confundir as duas leva a decisão errada:
    #
    # MARGEM DE CONTRIBUIÇÃO = preço − taxa − imposto − custo variável dele.
    #   É o que CADA cliente novo acrescenta. Se for positiva, crescer ajuda;
    #   se for negativa, cada cliente novo aumenta o prejuízo e escalar é a
    #   pior coisa a fazer.
    #
    # MARGEM LÍQUIDA = margem de contribuição − fixo rateado.
    #   É o que sobra de verdade hoje. Ela é negativa no começo por definição
    #   (poucos clientes dividindo o mesmo fixo) e isso NÃO significa que o
    #   negócio é ruim — significa que ainda falta volume.
    n = len(ativos)
    var_por_cliente = round(variaveis / n, 2) if n else round(
        variaveis / max(1, len(users)), 2)
    desconto_pct = (TAXA_PAGAMENTO_PCT + IMPOSTO_PCT) / 100
    receita_liq_unit = round(PRECO_MENSAL * (1 - desconto_pct), 2)
    contrib = round(receita_liq_unit - var_por_cliente, 2)
    fixo_rateado = round(fixos / n, 2) if n else None
    margem_cliente = round(contrib - fixo_rateado, 2) if n else None
    breakeven = (int(-(-fixos // contrib)) if contrib > 0 and fixos else
                 (0 if not fixos else None))

    return {
        "margem": {
            "preco": PRECO_MENSAL,
            "receita_liquida_unit": receita_liq_unit,
            "custo_variavel_cliente": var_por_cliente,
            "margem_contribuicao": contrib,
            "margem_contribuicao_pct": (round(contrib / PRECO_MENSAL * 100, 1)
                                        if PRECO_MENSAL else 0),
            "fixo_rateado": fixo_rateado,
            "margem_liquida_cliente": margem_cliente,
            "leitura": ("🔴 cada cliente novo AUMENTA o prejuízo — não escale"
                        if contrib <= 0 else
                        "🟢 cada cliente novo ajuda — falta volume pro fixo"
                        if (margem_cliente is None or margem_cliente < 0) else
                        "🟢 lucrativo por cliente"),
        },
        "fixos_detalhe": fixos_itens,
        "aviso": "MRR é ESTIMATIVA — não há integração de pagamento ligada",
        "assinantes": len(ativos),
        "mrr_estimado": bruto,
        "bruto": bruto,
        "liquido": liquido,
        "custo_total": custo_total,
        "custos": {"fixos": fixos, "llm": custo_llm, "envio": custo_envio,
                   "taxa_pagamento": taxa, "imposto": imposto},
        "msgs_30d": {"recebidas": msgs_in, "enviadas": msgs_out},
        "custo_por_assinante": var_por_cliente,
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
            # `u.*` E NAO UMA LISTA DE COLUNAS. Esta era a unica
            # consulta de usuario escrita a mao no projeto, e por isso a
            # unica que nao viu o `trial_base` nascer (M2.5): o painel
            # passou a mostrar os dias do `data_criacao` enquanto o bot
            # contava pelo relogio novo. O dono clicava em "+7 dias", o
            # numero nao mexia, ele clicava de novo — e cada clique dava +7
            # de verdade. Coluna nova nao pode depender de alguem lembrar
            # de vir aqui.
            """SELECT u.*,
                      (SELECT COUNT(*) FROM items i WHERE i.user_id=u.id) AS n_itens,
                      (SELECT COUNT(*) FROM items i WHERE i.user_id=u.id
                       AND i.status='pendente') AS n_pendentes
               FROM users u ORDER BY u.data_criacao DESC""").fetchall()
    # WHITELIST NA SAIDA, e nao no SELECT.
    #
    # O `SELECT u.*` conserta o painel (ele precisa do `trial_base` pra nao
    # mentir sobre o trial), mas passar a linha inteira adiante levou junto
    # `idade`, `profissao`, `carro_modelo`, `pet_info`, `placa_final`... da
    # base toda, para um endpoint cuja chave viaja em query string. Nao sai
    # do perimetro do token, mas o estrago de uma URL vazada saltou de
    # "nome e telefone" para dossie. Os dois lados no lugar certo: o SQL
    # traz tudo (coluna nova nao depende de memoria), a saida entrega o que
    # o painel usa.
    campos = ("id", "nome", "telefone", "status", "data_criacao",
              "ultima_interacao", "interesses", "n_itens", "n_pendentes")
    out = []
    for r in rows:
        bruto = dict(r)
        d = {k: bruto.get(k) for k in campos}
        d["dias_trial_restantes"] = trial_days_left(bruto)
        # M2.9 — onde a assinatura está no ciclo. Vai junto do usuário porque
        # o painel decide por PESSOA ("esse aqui venceu, vou conferir no
        # Mercado Pago"), não por agregado.
        d["assinatura"] = assinatura(bruto)
        out.append(d)
    return out


def admin_extend_trial(user_id: int, dias_extra: int) -> bool:
    """Estende o trial empurrando o RELÓGIO DO TRIAL (dá mais dias grátis).

    ESCREVE EM `trial_base`, e não em `data_criacao`. Os dois campos existem
    e são coisas diferentes: `data_criacao` é a data de CADASTRO (alimenta
    "novos por dia" e a idade da base) e `trial_base` é de quando o teste
    conta. Quem decide isso é `_base_do_trial`, e ele lê `trial_base`
    primeiro.

    Enquanto esta função escrevia em `data_criacao`, bastava um reset
    administrativo (M2.5) pra ela virar NO-OP: `trial_base` passava a
    existir, o relógio ignorava `data_criacao`, e a pessoa lia "liberei +7
    dias — agora são 14" na mesma frase, com 14 sendo o número de antes.
    Pior: o `log_dispatch("extensao-trial")` era queimado sem a extensão ter
    acontecido, e ela é UMA por usuário. Achado na rodada 2 da auditoria
    M2.5, e é o padrão que o CLAUDE.md registra como o mais caro do projeto
    — dedup marcado por quem não executou.
    """
    try:
        u = get_user(user_id)
        if not u:
            return False
        nova = _base_do_trial(u) + timedelta(days=dias_extra)
        # NAO DESBLOQUEIA NINGUEM. O `status='trial'` era incondicional, e
        # o botao "+dias" do painel devolvia acesso a quem foi BLOQUEADO —
        # a mesma porta que o `resetar_trial` fechou nesta fase e esta
        # funcao tinha deixado aberta.
        if (u.get("status") or "trial") == "bloqueado":
            import logging
            logging.getLogger("resolveai").warning(
                "[trial] recusei estender o trial do user %s: status "
                "bloqueado", user_id)
            return False
        with get_conn() as conn:
            conn.execute(
                "UPDATE users SET trial_base=?, status='trial' WHERE id=?",
                (nova.strftime("%Y-%m-%d %H:%M:%S"), user_id))
        return True
    except Exception:
        # Sem log, esta falha some — e quem chama devolve "não consegui" sem
        # nada no servidor pra dizer por quê (regra 5).
        import logging
        logging.getLogger("resolveai").warning(
            "[trial] falha ao estender o trial do user %s", user_id,
            exc_info=True)
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
                  "recorrencia", "categoria", "status", "tipo", "avisar_dias"}
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
    # DESCRIÇÃO NOVA, ANTECEDÊNCIA NOVA (auditoria M3.9, P2-6).
    #
    # A derivação só rodava no `add_item`, então quem CORRIGIA a descrição
    # ficava com a antecedência do texto antigo: "comprar pão" virado em
    # "renovar CNH" não ganhava o D-60 (a promessa da landing), e o caminho
    # inverso mantinha um D-60 que não fazia mais sentido. Corrigir a
    # descrição é justamente o que o bot OFERECE quando erra a leitura.
    #
    # Só quando não veio explícito: quem passa `avisar_dias` manda.
    if campos.get("avisar_dias") is not None:
        limpos["avisar_dias"] = _avisar_dias_limpo(campos["avisar_dias"])
    elif "descricao" in limpos:
        # SO QUANDO A DERIVACAO ACHA ALGO (auditoria M4.0).
        #
        # Sobrescrever com None apagava a antecedencia que veio da FOTO — o
        # tipo detectado na imagem sabe coisas que o texto nao diz, e o
        # proprio bot e quem OFERECE corrigir a descricao. A pessoa aceitava
        # a correcao e perdia calada o aviso de 60 dias da CNH e o de 30 da
        # nota fiscal. Ganhar antecedencia por texto e um bonus; perder a
        # que ja existia e quebrar promessa.
        _novo = _avisar_dias_final(None, limpos["descricao"])
        if _novo:
            limpos["avisar_dias"] = _novo
    sets = ", ".join(f"{k}=?" for k in limpos)
    try:
        with get_conn() as conn:
            conn.execute(f"UPDATE items SET {sets} WHERE id=?",
                         (*limpos.values(), int(item_id)))
        return True
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[db] falha ao atualizar item %s", item_id, exc_info=True)
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


# ---------------------------------------------------------------------------
# MINI-PODCAST (M4.2)
# ---------------------------------------------------------------------------

def podcast_a_convidar(ref=None, horas: int = 6, limite: int = 20) -> list[dict]:
    """Quem escolheu nicho, ja passou das N horas do cadastro e nunca ouviu.

    O CONVITE E UMA VEZ SO. `podcast_convite_em` carimba quem ja recebeu, e
    quem disse "agora nao" nao volta a ser convidado por este caminho — ele
    entra no ciclo semanal normal. Insistir com quem nao respondeu e como se
    perde alguem que so estava ocupado.
    """
    agora = ref or tempo.agora()
    corte = (agora - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        linhas = conn.execute(
            """SELECT * FROM users
                WHERE podcast_nicho IS NOT NULL
                  AND TRIM(podcast_nicho) <> ''
                  AND podcast_convite_em IS NULL
                  AND podcast_recusado_em IS NULL
                  AND data_criacao <= ?
                ORDER BY data_criacao ASC LIMIT ?""",
            (corte, limite)).fetchall()
    return [dict(r) for r in linhas]


def podcast_registrar_episodio(user_id, nicho: str, segundos: float = 0.0,
                              ok: bool = True, erro: str = "") -> None:
    """Grava o que aconteceu numa geracao de episodio.

    NUNCA LEVANTA: e telemetria. Derrubar a entrega do audio pra registrar
    que o audio foi entregue seria o cumulo.
    """
    import logging
    try:
        with get_conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS podcast_log (
                       id       INTEGER PRIMARY KEY AUTOINCREMENT,
                       quando   TEXT NOT NULL,
                       user_id  INTEGER,
                       nicho    TEXT,
                       segundos REAL,
                       ok       INTEGER NOT NULL DEFAULT 1,
                       erro     TEXT)""")
            conn.execute(
                "INSERT INTO podcast_log (quando,user_id,nicho,segundos,ok,erro)"
                " VALUES (?,?,?,?,?,?)",
                (_now_iso(), user_id, str(nicho or "")[:40],
                 float(segundos or 0.0), 1 if ok else 0, str(erro or "")[:120]))
    except Exception:
        logging.getLogger("resolveai").warning(
            "[podcast] nao consegui registrar o episodio", exc_info=True)


def podcast_episodio_do_dia(nicho: str, dias: int):
    """O audio ja gerado hoje pra este tema e esta janela, ou None.

    UM EPISODIO POR TEMA, NAO POR PESSOA (M12). Trinta pessoas em "games" na
    mesma janela ouviam o mesmo conteudo — e a gente pagava trinta sinteses
    de TTS por isso, porque o roteiro trazia o primeiro nome de cada uma.

    A chave inclui a JANELA porque ela e o que define o material: quem pediu
    5 dias e quem pediu 30 recebem episodios diferentes do mesmo tema, e isso
    e correto.

    NA DUVIDA, DEVOLVE None: gerar de novo custa dinheiro, mas entregar audio
    errado custa cliente.
    """
    import logging
    try:
        hoje = tempo.agora().strftime("%Y-%m-%d")
        with get_conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS podcast_episodio (
                       chave  TEXT PRIMARY KEY,
                       quando TEXT NOT NULL,
                       audio  BLOB NOT NULL)""")
            r = conn.execute(
                "SELECT audio FROM podcast_episodio WHERE chave = ?",
                ("%s|%d|%s" % (str(nicho or "")[:40], int(dias or 7), hoje),)
            ).fetchone()
        return bytes(r["audio"]) if r and r["audio"] else None
    except Exception:
        logging.getLogger("resolveai").warning(
            "[podcast] nao consegui ler o episodio do dia", exc_info=True)
        return None


def podcast_guardar_episodio_do_dia(nicho: str, dias: int, audio) -> None:
    """Guarda o audio do dia pra este tema e janela. NUNCA LEVANTA.

    Limpa o que passou: sao arquivos de centenas de KB e o de ontem nao serve
    mais pra ninguem.
    """
    import logging
    if not audio:
        return
    try:
        agora = tempo.agora()
        hoje = agora.strftime("%Y-%m-%d")
        with get_conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS podcast_episodio (
                       chave  TEXT PRIMARY KEY,
                       quando TEXT NOT NULL,
                       audio  BLOB NOT NULL)""")
            conn.execute(
                "INSERT OR REPLACE INTO podcast_episodio "
                " (chave, quando, audio) VALUES (?,?,?)",
                ("%s|%d|%s" % (str(nicho or "")[:40], int(dias or 7), hoje),
                 _now_iso(), sqlite3.Binary(audio)))
            conn.execute("DELETE FROM podcast_episodio WHERE quando < ?",
                         ((agora - timedelta(days=2)
                           ).strftime("%Y-%m-%d %H:%M:%S"),))
    except Exception:
        logging.getLogger("resolveai").warning(
            "[podcast] nao consegui guardar o episodio do dia", exc_info=True)


def podcast_lote_interrompido(user_id, horas: int = 48, ultimo=None) -> set:
    """Os assuntos que JA CHEGARAM num lote que o canal interrompeu.

    Serve pra uma coisa so: quando o envio cai no meio (a pessoa tem tres
    assuntos e o segundo audio nao sai), a gente NAO carimba o envio — senao
    ela ficaria trancada ate a proxima janela por um erro nosso. Sem carimbo
    ela pode tocar "quero ouvir" de novo, e ai isto evita remandar o que ja
    tinha chegado: audio repetido e TTS pago duas vezes pelo mesmo conteudo.

    O CORTE E O ULTIMO LOTE CONCLUIDO (`ultimo` = `podcast_ultimo`), nao um
    prazo. Entrega parcial nao carimba — entao "sem carimbo desde X" e a
    definicao exata de "lote em aberto", e ela nao vence. Com corte de tempo
    (6h, que foi a primeira versao), quem voltasse no dia seguinte recebia
    repetido: um audio duplicado e um TTS pago duas vezes pelo mesmo texto.
    O `horas` (48) e o piso pra quem NUNCA teve carimbo — quem so tem entrega
    parcial na vida. Depois de dois dias a noticia envelheceu e reenviar sai
    mais barato que raciocinar sobre um lote de anteontem.

    E EXIGE UMA FALHA REGISTRADA, que e o que impede dois outros estragos:

      - sem isso, um reenvio que o dono peca de proposito (ele zera o
        `podcast_ultimo`) nao mandaria nada;
      - e, principalmente, o episodio do PROXIMO periodo encontraria a
        entrega do periodo passado e seria pulado — com frequencia semanal,
        a pessoa parava de receber, calada. Foi assim que a primeira versao
        disto quase subiu; a suite pegou.

    Sem lote em aberto, devolve conjunto vazio e ninguem e pulado.
    """
    import logging
    vazio: set = set()
    try:
        corte = (tempo.agora() - timedelta(hours=max(1, int(horas or 1)))
                 ).strftime("%Y-%m-%d %H:%M:%S")
        # O CARIMBO MANDA quando existe e e mais recente que o piso — e vale
        # a partir do SEGUNDO SEGUINTE. O carimbo e escrito no mesmo segundo
        # das ultimas linhas do lote que ele fecha; sem o "+1s", essas linhas
        # entrariam de novo e um lote concluido pareceria em aberto.
        _carimbo = str(ultimo or "").strip().replace("T", " ")[:19]
        if _carimbo > corte:
            try:
                corte = (datetime.strptime(_carimbo, "%Y-%m-%d %H:%M:%S")
                         + timedelta(seconds=1)
                         ).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                corte = _carimbo
        with get_conn() as conn:
            existe = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='podcast_log'").fetchone()
            if not existe:
                return vazio
            houve_falha = conn.execute(
                "SELECT 1 FROM podcast_log "
                " WHERE user_id = ? AND ok = 0 AND quando >= ? LIMIT 1",
                (user_id, corte)).fetchone()
            if not houve_falha:
                return vazio
            linhas = conn.execute(
                "SELECT DISTINCT nicho FROM podcast_log "
                " WHERE user_id = ? AND ok = 1 AND segundos > 0"
                "   AND quando >= ?", (user_id, corte)).fetchall()
        return {l["nicho"] for l in linhas if l["nicho"]}
    except Exception:
        logging.getLogger("resolveai").warning(
            "[podcast] nao consegui ler o lote interrompido", exc_info=True)
        return vazio


def podcast_farois(dias: int = 7) -> dict:
    """Os tres numeros do dash: esta funcionando, quanto dura, quantos sairam.

    So contagens e medias — nada que identifique pessoa.
    """
    import logging
    vazio = {"estado": "sem dados", "ok": 0, "falhas": 0,
             "segundos_medio": 0, "na_semana": 0, "ultimo": ""}
    try:
        corte = (tempo.agora() - timedelta(days=max(1, dias))
                 ).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            existe = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='podcast_log'").fetchone()
            if not existe:
                return vazio
            r = conn.execute(
                "SELECT SUM(ok) AS bons, COUNT(*) AS tudo, "
                "       AVG(CASE WHEN ok=1 AND segundos>0 THEN segundos END) AS med, "
                "       MAX(quando) AS ult "
                "  FROM podcast_log WHERE quando >= ?", (corte,)).fetchone()
        bons = int(r["bons"] or 0)
        tudo = int(r["tudo"] or 0)
        falhas = max(0, tudo - bons)
        if not tudo:
            return vazio
        # VERDE so com tudo saindo. Uma falha ja e sinal: significa que uma
        # fonte secou ou a voz recusou, e o sintoma disso pro cliente e
        # simplesmente nao receber — que ele nao reclama, so cancela.
        estado = ("ok" if falhas == 0 else
                  "atencao" if bons >= falhas else "quebrado")
        return {"estado": estado, "ok": bons, "falhas": falhas,
                "segundos_medio": int(round(r["med"] or 0)),
                "na_semana": bons, "ultimo": r["ult"] or ""}
    except Exception:
        logging.getLogger("resolveai").warning(
            "[podcast] farois falharam", exc_info=True)
        return vazio


def podcast_a_ofertar(ref=None, horas: int = 24,
                      limite: int = 20) -> list[dict]:
    """Quem NAO escolheu assunto, nao recusou, e ainda nao foi ofertado.

    Quem marcou "Depois eu escolho" no formulario sumia do recurso pra
    sempre: a fila do convite exige nicho preenchido. Esta e a fila que
    devolve essas pessoas pro jogo — uma vez so, carimbada pelo mesmo
    `podcast_convite_em`.

    `horas` da folga pro onboarding acontecer antes: oferecer um extra no
    mesmo minuto do cadastro e falar de sobremesa antes do prato.
    """
    agora = ref or tempo.agora()
    corte = (agora - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        linhas = conn.execute(
            """SELECT * FROM users
                WHERE (podcast_nicho IS NULL OR TRIM(podcast_nicho) = '')
                  AND podcast_convite_em IS NULL
                  AND podcast_recusado_em IS NULL
                  AND data_criacao <= ?
                ORDER BY data_criacao ASC LIMIT ?""",
            (corte, limite)).fetchall()
    return [dict(r) for r in linhas]


def podcast_assinantes(limite: int = 200) -> list[dict]:
    """Quem tem nicho e ja ouviu pelo menos um episodio.

    SEM DIA DA SEMANA (decisao do Kevin, 29/08/2026: "1x por semana pode
    ser, o importante e todo cliente ter" + "nao pode deixar de mandar").
    O dia fixo era incompativel com as duas coisas: o convite so sai DENTRO
    da janela de 24h, entao quem nao mandasse mensagem naquela sexta perdia
    a semana inteira em silencio — e com uso episodico, isso era a maioria
    das semanas.

    Agora o episodio sai no PRIMEIRO dia em que a pessoa estiver por perto,
    respeitando o teto de 1x por semana. E o teto que garante que "mais
    alcance" nao vire "mais ruido".
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT * FROM users
                WHERE podcast_nicho IS NOT NULL
                  AND TRIM(podcast_nicho) <> ''
                  AND podcast_ultimo IS NOT NULL
                ORDER BY id ASC LIMIT ?""", (limite,)).fetchall()]


# AS QUATRO REGULARIDADES. O teto de 5 dias e do dono ("no maximo a cada 5
# dias"): mais frequente que isso multiplica proativa num numero ja
# restringido duas vezes, e o custo de voz cresce na mesma proporcao.
FREQUENCIAS = (5, 7, 15, 30)
FREQUENCIA_PADRAO = 7


def frequencia_do_podcast(user: dict) -> int:
    """De quantos em quantos dias esta pessoa quer o episodio.

    Vale como JANELA tambem: quem escolheu 15 dias ouve as noticias dos
    ultimos 15. Valor estranho no banco cai no padrao em vez de virar erro —
    o episodio sair no ritmo errado e recuperavel, nao sair nao e.
    """
    try:
        n = int((user or {}).get("podcast_frequencia") or 0)
    except Exception:
        n = 0
    return n if n in FREQUENCIAS else FREQUENCIA_PADRAO


def podcast_convite_recente(user_id: int, dias: int = 7, ref=None) -> bool:
    """Ja convidamos esta pessoa nos ultimos N dias?

    O TETO DE 1X POR SEMANA TEM QUE OLHAR O CONVITE, NAO SO O EPISODIO
    (auditoria M4.5, P0).

    `podcast_ultimo` so muda quando a pessoa TOCA no botao. Quem recebeu o
    convite e nao respondeu ficava com o campo parado — e, sem dia fixo
    segurando, o convite renascia TODO DIA, pra sempre. Medido: 14 convites
    por pessoa em 14 dias. Isso e assinatura de ritmo, que e o que ja rendeu
    duas restricoes neste numero.

    `podcast_convite_em` e carimbado por QUEM ENVIA, entao convite que nao
    saiu nao conta.
    """
    quando = None
    with get_conn() as conn:
        r = conn.execute("SELECT podcast_convite_em FROM users WHERE id=?",
                         (int(user_id),)).fetchone()
        if r:
            quando = r["podcast_convite_em"]
    if not quando:
        return False
    try:
        marcado = datetime.strptime(str(quando)[:19].replace("T", " "),
                                    "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        # Carimbo ilegivel conta como RECENTE: o erro seguro aqui e convidar
        # de menos.
        import logging
        logging.getLogger("resolveai").warning(
            "[podcast] podcast_convite_em ilegivel (%r) no user %s",
            quando, user_id)
        return True
    return (ref or tempo.agora()) - marcado < timedelta(days=dias)


def podcast_marcar_envio(user_id: int, quando=None) -> None:
    """Carimba o episodio que saiu. E o que segura o teto de 1x por semana."""
    q = (quando or tempo.agora()).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("UPDATE users SET podcast_ultimo=? WHERE id=?",
                     (q, int(user_id)))


def podcast_marcar_convite(user_id: int, quando=None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET podcast_convite_em=? WHERE id=?",
            ((quando or tempo.agora()).strftime("%Y-%m-%d %H:%M:%S"),
             int(user_id)))


def podcast_a_perguntar_o_dia(ref=None, minutos: int = 10,
                              limite: int = 20) -> list[dict]:
    """Quem ouviu o primeiro episodio ha N minutos e ainda nao escolheu dia.

    A pergunta vem DEPOIS do audio, nao junto: perguntar antes de a pessoa
    ouvir e pedir compromisso sobre algo que ela ainda nao sabe se gosta.

    Uma vez so — `podcast_dia_perguntado` carimba. Quem nao respondeu nao e
    cobrado de novo: ela ouviu, nao quis assinar, e insistir e o caminho pro
    bloqueio.
    """
    agora = ref or tempo.agora()
    corte = (agora - timedelta(minutes=minutos)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        linhas = conn.execute(
            """SELECT * FROM users
                WHERE podcast_ultimo IS NOT NULL
                  AND podcast_ultimo <= ?
                  AND (podcast_dia IS NULL OR TRIM(podcast_dia) = '')
                  AND podcast_dia_perguntado IS NULL
                ORDER BY podcast_ultimo ASC LIMIT ?""",
            (corte, limite)).fetchall()
    return [dict(r) for r in linhas]


def podcast_marcar_pergunta_do_dia(user_id: int, quando=None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET podcast_dia_perguntado=? WHERE id=?",
            ((quando or tempo.agora()).strftime("%Y-%m-%d %H:%M:%S"),
             int(user_id)))


# ---------------------------------------------------------------------------
# O QUE A PESSOA JA OUVIU NO PODCAST (M4.8)
# ---------------------------------------------------------------------------
# O dedup do `noticias` so vale DENTRO de uma coleta: ele impede a mesma
# materia sair duas vezes no mesmo audio. Nada impedia ela voltar na semana
# seguinte — e feed de noticia repete manchete por dias.
#
# O Kevin foi direto: "nunca repetir conteudo". Repetir e o jeito mais rapido
# de a pessoa concluir que o audio nao vale o tempo dela.
_PODCAST_OUVIDO_DDL = """
CREATE TABLE IF NOT EXISTS podcast_ouvido (
    user_id   INTEGER NOT NULL,
    chave     TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    PRIMARY KEY (user_id, chave)
);
"""

# Quanto tempo a memoria segura. Noventa dias: passado isso, uma materia
# voltar nao e repeticao, e retrospectiva — e a tabela nao cresce pra sempre.
PODCAST_MEMORIA_DIAS = 90


def _podcast_chave(item: dict) -> str:
    """O que identifica a materia. Link quando ha; titulo normalizado senao.

    O LINK GANHA porque a manchete muda entre coletas ("Palmeiras vence" vira
    "Palmeiras bate o Flamengo") e o titulo sozinho deixaria a mesma materia
    passar por nova.
    """
    link = (item.get("link") or "").strip().lower()
    if link:
        return re.sub(r"[?#].*$", "", link)[:300]
    t = (item.get("titulo") or "").strip().lower()
    t = re.sub(r"[^\w\sà-ÿ]", "", t)
    return re.sub(r"\s+", " ", t)[:200]


def podcast_ineditas(user_id: int, itens: list) -> list:
    """So o que esta pessoa ainda nao ouviu."""
    if not itens:
        return []
    chaves = {_podcast_chave(i): i for i in itens if isinstance(i, dict)}
    if not chaves:
        return []
    with get_conn() as conn:
        conn.execute(_PODCAST_OUVIDO_DDL)
        marcas = ",".join("?" * len(chaves))
        ja = {r["chave"] for r in conn.execute(
            "SELECT chave FROM podcast_ouvido WHERE user_id=? "
            "AND chave IN (%s)" % marcas,
            [int(user_id)] + list(chaves)).fetchall()}
    return [i for i in itens if _podcast_chave(i) not in ja]


def podcast_registrar_ouvidas(user_id: int, itens: list, quando=None) -> None:
    """Carimba o que foi PRO AUDIO. So depois de o envio dar certo.

    Registrar antes faria a pessoa perder a materia por um envio que falhou —
    ela nunca mais ouviria aquilo.
    """
    if not itens:
        return
    q = (quando or tempo.agora()).strftime("%Y-%m-%d %H:%M:%S")
    linhas = [(int(user_id), _podcast_chave(i), q)
              for i in itens if isinstance(i, dict) and _podcast_chave(i)]
    if not linhas:
        return
    corte = ((quando or tempo.agora())
             - timedelta(days=PODCAST_MEMORIA_DIAS)
             ).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(_PODCAST_OUVIDO_DDL)
        conn.executemany(
            "INSERT OR IGNORE INTO podcast_ouvido (user_id, chave, criado_em) "
            "VALUES (?,?,?)", linhas)
        conn.execute("DELETE FROM podcast_ouvido WHERE criado_em < ?",
                     (corte,))
