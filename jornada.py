# -*- coding: utf-8 -*-
"""
jornada.py — A experiencia do usuario, do primeiro "oi" ate a assinatura.
=========================================================================
Este arquivo existe por causa de um numero: 5 cadastros, 0 itens criados.

O diagnostico nao foi de copy. Foi de TEMPO ATE O VALOR:

    o "aha" do Resolve AI nao e registrar — e ser AVISADO de uma coisa
    que ja tinha saido da cabeca.

Se a pessoa cadastra uma conta que vence dia 20 e hoje e dia 5, ela espera
QUINZE DIAS pra sentir o produto uma vez. O trial tem 14. Ou seja: o
produto acabava antes de provar que funciona. Nenhuma copy conserta isso.

  1. DEMONSTRACAO EM 90 SEGUNDOS
     No primeiro item, agenda um aviso de amostra pra 90s depois. A pessoa
     ve a mensagem chegar sozinha, sem ter pedido — que e exatamente a
     coisa pela qual ela vai pagar. Tempo ate o aha: de 14 dias pra 2 min.

  2. SUGESTOES QUE CABEM NUMA TELA
     Botao do WhatsApp aceita 3 opcoes. LISTA aceita 10. As 8 sugestoes de
     uso viram lista rolavel: a pessoa escolhe uma pra comecar e as outras
     7 continuam la, nao somem.

  3. MENSAGENS COM RITMO
     Uma ideia por bloco, emoji como pontuacao (nao como enfeite), sempre
     um proximo passo obvio. Uso e 100% celular: ninguem le paragrafo de
     cinco linhas na tela do telefone com o filho chorando do lado.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("resolveai")

SEGUNDOS_DEMO = 90          # tempo ate o aviso de amostra
MAX_LINHAS_LISTA = 10       # limite do WhatsApp


# ===========================================================================
# 1. COPY
# ===========================================================================
BOAS_VINDAS = (
    "Oi, {nome}! \U0001F44B Eu sou o *Resolve AI*.\n\n"
    "Eu guardo suas contas, consultas e prazos \u2014 e te aviso *antes*, "
    "sozinho, aqui no Zap.\n\n"
    "\U0001F381 Seus *{dias} dias gr\u00e1tis* j\u00e1 come\u00e7aram. Sem cart\u00e3o, sem pegadinha.\n\n"
    "Vamos direto ao ponto: *me manda uma coisa que voc\u00ea n\u00e3o pode esquecer.*\n\n"
    "Pode ser a foto de um boleto, um \u00e1udio, ou s\u00f3 uma linha:\n"
    "_\"luz 187 vence dia 20\"_\n"
    "_\"dentista dia 15 \u00e0s 14h\"_"
)

RODAPE_LEGAL = (
    "_Ao usar, voce aceita os Termos ({termos}). "
    "Mande *apagar meus dados* quando quiser._"
)

# --- a demonstracao: o coracao deste arquivo ------------------------------
DEMO = (
    "\u23F0 *Olha eu aqui.*\n\n"
    "Do nada, sem voc\u00ea pedir:\n"
    "*{descricao}*{quando}\n\n"
    "Isso foi s\u00f3 uma amostra, pra voc\u00ea ver como \u00e9. "
    "O aviso de verdade chega {aviso_real} \u2014 na hora que ainda d\u00e1 pra resolver.\n\n"
    "\u00c9 isso que eu fa\u00e7o todo dia, no autom\u00e1tico. \U0001F91D"
)

# --- fim do trial: quem usou e quem nao usou nao merecem a mesma coisa ----
COBRANCA_USOU = (
    "{nome}, seus {dias} dias acabam hoje.\n\n"
    "Nesse tempo eu guardei *{n_itens} coisas* pra voce e te avisei "
    "*{n_avisos} vezes*. Cada aviso foi uma coisa a menos pra voce carregar "
    "na cabeca.\n\n"
    "Pra continuar do mesmo jeito, sem interrupcao:\n\n"
    "\U0001F49A *R$ 19,90/mes* — cancela quando quiser\n"
    "{link_mensal}\n\n"
    "\u2B50 *R$ 149/ano* — sai por R$ 12,40/mes\n"
    "{link_anual}\n\n"
    "_Uma unica multa de boleto evitada ja paga meio ano._\n\n"
    "Se voce nao assinar agora, nada e apagado: seus itens ficam guardados "
    "por 30 dias esperando voce voltar."
)

COBRANCA_NAO_USOU = (
    "{nome}, seus {dias} dias acabam hoje — e eu preciso ser honesto: "
    "voce nao chegou a me usar de verdade.\n\n"
    "*Nao vou te cobrar por uma coisa que voce nao viu funcionar.*\n\n"
    "Me manda uma unica coisa agora — uma conta, uma consulta, uma data — "
    "que eu te libero mais 7 dias.\n\n"
    "_\"luz 187 vence dia 20\"_ — e literalmente isso.\n\n"
    "Se ainda nao fizer sentido, tudo bem. A gente fica por aqui, sem cobranca."
)


# ===========================================================================
# 2. AS SUGESTOES DE USO — viram lista, nao somem
# ===========================================================================
# (id, titulo <=24 chars, descricao <=72 chars, exemplo que o bot devolve)
SUGESTOES = [
    ("contas", "\U0001F4A1 Contas e boletos",
     "Aviso 3 dias antes, 1 dia antes e no dia",
     "Manda a foto do boleto — ou escreve:\n_\"luz 187 vence dia 20\"_\n\n"
     "Eu te aviso *3 dias antes*, *1 dia antes* e *no dia*. Multa nunca mais."),

    ("saude", "\U0001FA7A Consultas e exames",
     "Consulta, exame, remedio que acaba",
     "Escreve assim:\n_\"dentista dia 15 as 14h\"_\n\n"
     "Eu te lembro na vespera e no dia, com tempo de sair de casa."),

    ("datas", "\U0001F382 Aniversarios e datas",
     "Eu aviso com tempo de comprar o presente",
     "Escreve:\n_\"aniversario da minha mae e 03/09\"_\n\n"
     "Todo ano eu te aviso *com antecedencia* — da tempo de comprar presente."),

    ("carro", "\U0001F697 Carro e documentos",
     "IPVA, licenciamento, revisao, troca de oleo",
     "Escreve:\n_\"troquei o oleo hoje, 45 mil km\"_\n\n"
     "Eu volto a falar disso na hora certa. IPVA e licenciamento tambem."),

    ("mercado", "\U0001F6D2 Compras que acabam",
     "Racao, fralda, cafe — reposicao na hora certa",
     "Escreve:\n_\"comprei racao de 15kg hoje\"_\n\n"
     "Quando estiver acabando, eu aviso — antes de faltar."),

    ("encomendas", "\U0001F4E6 Encomendas e prazos",
     "Prazo de entrega, troca, garantia",
     "Escreve:\n_\"comprei um fone, chega dia 12\"_\n\n"
     "Eu acompanho o prazo e te aviso se passar da data."),

    ("pet", "\U0001F43E Pet",
     "Vacina, vermifugo, banho, racao",
     "Escreve:\n_\"vacina do Thor foi hoje\"_\n\n"
     "Eu te aviso quando a proxima estiver chegando."),

    ("burocracia", "\U0001F4C4 Burocracia",
     "Documento vencendo, imposto, renovacao",
     "Escreve:\n_\"minha CNH vence em marco\"_\n\n"
     "Eu te aviso com folga pra resolver sem correria."),
]

_POR_ID = {s[0]: s for s in SUGESTOES}


def exemplo_de(escolha_id: str) -> Optional[str]:
    """Texto que o bot responde quando a pessoa toca numa sugestao."""
    s = _POR_ID.get((escolha_id or "").strip().lower())
    return s[3] if s else None


def linhas_da_lista(interesses: str = "") -> list:
    """Monta as linhas da lista. Interesse declarado sobe pro topo.

    Ninguem perde opcao: quem marcou "contas" ve contas primeiro, mas as
    outras 7 continuam rolaveis logo abaixo.
    """
    marcados = {p.strip().lower() for p in (interesses or "").split(",") if p.strip()}
    preferidas = [s for s in SUGESTOES if s[0] in marcados]
    resto = [s for s in SUGESTOES if s[0] not in marcados]
    ordenadas = (preferidas + resto)[:MAX_LINHAS_LISTA]
    return [{"id": sid, "title": titulo[:24], "description": desc[:72]}
            for sid, titulo, desc, _ex in ordenadas]


# ===========================================================================
# 3. ENVIO DE LISTA (botao aceita 3 opcoes; a lista aceita 10)
# ===========================================================================
def enviar_lista(number: str, corpo: str, rotulo_botao: str,
                 linhas: list, titulo: str = "") -> bool:
    """Manda mensagem de lista interativa. False se a Meta recusar."""
    import httpx
    import meta_cloud

    if not meta_cloud.configurado() or not linhas:
        return False

    to = re.sub(r"\D", "", str(number or ""))
    if not to:
        return False

    interactive = {
        "type": "list",
        "body": {"text": corpo[:1024]},
        "action": {
            "button": rotulo_botao[:20],
            "sections": [{"title": (titulo or "Escolha")[:24],
                          "rows": linhas[:MAX_LINHAS_LISTA]}],
        },
    }

    try:
        r = httpx.post(
            f"{meta_cloud.GRAPH}/{meta_cloud.PHONE_NUMBER_ID}/messages",
            headers=meta_cloud._HEADERS,
            json={"messaging_product": "whatsapp", "recipient_type": "individual",
                  "to": to, "type": "interactive", "interactive": interactive},
            timeout=25,
        )
        if r.status_code == 200 and (r.json().get("messages") or [{}])[0].get("id"):
            return True
        log.warning("[lista] Meta recusou (%s): %s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        log.warning("[lista] erro: %r", e)
        return False


# ===========================================================================
# 4. A DEMONSTRACAO DE 90 SEGUNDOS
# ===========================================================================
def _conn():
    import db
    return db.get_conn()


_DDL = ("CREATE TABLE IF NOT EXISTS demos ("
        "user_id INTEGER PRIMARY KEY, descricao TEXT, "
        "quando TEXT, criado_em TEXT, enviado_em TEXT, item_id INTEGER)")


def agendar_demo(user_id: int, descricao: str, quando: str = "",
                 item_id=None) -> bool:
    """Agenda o aviso de amostra. So UMA vez por usuario, no primeiro item.

    Tabela criada aqui de proposito: e um recurso isolado e nao vale uma
    migracao no db.py, que e grande e mexido por todo mundo.
    """
    import tempo
    try:
        with _conn() as conn:
            conn.execute(_DDL)
            if conn.execute("SELECT 1 FROM demos WHERE user_id=?",
                            (user_id,)).fetchone():
                return False   # cada pessoa so se impressiona uma vez
            try:
                conn.execute("ALTER TABLE demos ADD COLUMN item_id INTEGER")
            except Exception:
                pass   # ja existe — banco antigo migra sozinho
            conn.execute("INSERT INTO demos (user_id, descricao, quando, "
                         "criado_em, item_id) VALUES (?,?,?,?,?)",
                         (user_id, (descricao or "")[:120], quando or "",
                          tempo.agora().isoformat(), item_id))
        log.info("[demo] agendada para user %s em %ds", user_id, SEGUNDOS_DEMO)
        return True
    except Exception:
        log.warning("[demo] falha ao agendar", exc_info=True)
        return False


def demos_prontas() -> list:
    """Quem ja passou dos 90 segundos e ainda nao recebeu a amostra."""
    import datetime as dt
    import tempo
    try:
        with _conn() as conn:
            conn.execute(_DDL)
            try:
                conn.execute("ALTER TABLE demos ADD COLUMN item_id INTEGER")
            except Exception:
                pass
            linhas = conn.execute(
                "SELECT user_id, descricao, quando, criado_em, item_id "
                "FROM demos WHERE enviado_em IS NULL").fetchall()
        agora = tempo.agora()
        prontas = []
        for uid, desc, quando, criado, item_id in linhas:
            try:
                c = dt.datetime.fromisoformat(criado)
            except Exception:
                continue
            if (agora - c).total_seconds() < SEGUNDOS_DEMO:
                continue
            # Item ja concluido? Cancela a amostra.
            # Em 05/08 o usuario deu baixa em "comer" as 15:00 e recebeu a
            # demonstracao daquele mesmo item as 15:02. Mostrar amostra de
            # algo que a pessoa acabou de resolver passa a impressao de que
            # o bot nao acompanha — e a primeira impressao e essa.
            if item_id and _ja_concluido(item_id):
                marcar_demo_enviada(uid)
                log.info("[demo] cancelada: item %s ja concluido", item_id)
                continue
            prontas.append({"user_id": uid, "descricao": desc,
                            "quando": quando})
        return prontas
    except Exception:
        log.warning("[demo] falha ao listar", exc_info=True)
        return []


def _ja_concluido(item_id) -> bool:
    """O item ja foi resolvido? Entao nao ha o que demonstrar."""
    try:
        with _conn() as conn:
            r = conn.execute("SELECT status FROM items WHERE id=?",
                             (int(item_id),)).fetchone()
        return bool(r) and str(r[0]).lower() in ("concluido", "concluído",
                                                 "arquivado", "cancelado")
    except Exception:
        return False   # na duvida, mostra a amostra


def _ja_passou(iso: str) -> bool:
    """A data e hoje ou ja passou?

    Prometer "o aviso de verdade chega 05/08" no dia 05/08 nao faz sentido
    e foi o que apareceu no primeiro teste real.
    """
    import datetime as dt
    import tempo
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    if not m:
        return False
    try:
        d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d <= tempo.hoje()
    except Exception:
        return False


def marcar_demo_enviada(user_id: int) -> None:
    import tempo
    try:
        with _conn() as conn:
            conn.execute(_DDL)
            conn.execute("UPDATE demos SET enviado_em=? WHERE user_id=?",
                         (tempo.agora().isoformat(), user_id))
    except Exception:
        log.warning("[demo] falha ao marcar enviada", exc_info=True)


def _data_br(iso: str) -> str:
    """2026-08-05 -> 05/08.

    Data crua em ISO na cara do usuario e feio, e foi exatamente o que
    apareceu no primeiro teste real no WhatsApp.
    """
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return f"{m.group(3)}/{m.group(2)}" if m else str(iso or "")


def texto_demo(descricao: str, quando: str = "") -> str:
    """Monta a mensagem da amostra.

    Sem data a frase muda: prometer "aviso de verdade" para um item sem
    data seria mentira, e mentira no minuto 2 custa o cliente.
    """
    if _ja_passou(quando):
        # data de hoje ou passada: a amostra vira a propria confirmacao,
        # sem prometer um aviso futuro que nao vai existir.
        return DEMO.format(
            descricao=descricao,
            quando=f" \u2014 {_data_br(quando)}" if quando else "",
            aviso_real="*na pr\u00f3xima vez que voc\u00ea marcar uma data*")
    quando = _data_br(quando)
    if quando:
        return DEMO.format(descricao=descricao, quando=f" — {quando}",
                           aviso_real=f"*{quando}*")
    return DEMO.format(descricao=descricao, quando="",
                       aviso_real="*assim que voce me disser a data*")
