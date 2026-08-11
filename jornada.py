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
    "\U0001F381 Seus *{dias} dias gr\u00e1tis* j\u00e1 come\u00e7aram. Sem cart\u00e3o, sem pegadinha."
)

# --- M1.2: onboarding em 3 mensagens, aceite da LGPD como ATO EXPLICITO ---
# ANTES: o aceite ("Ao usar, voce aceita os Termos...") vinha enterrado
# dentro do bloco de boas-vindas — ninguem clicava em nada, so seguia
# escrevendo. Com 11 pessoas reais em trial isso e exposicao juridica, nao
# detalhe de copy. AGORA: mensagem propria, com botao. A pessoa so avanca
# pro pedido de demanda (mensagem 3) depois de tocar em "Concordo".
RODAPE_LEGAL = (
    "_Ao usar, voce aceita os Termos ({termos}). "
    "Mande *apagar meus dados* quando quiser._"
)

LGPD_AVISO = (
    "\U0001F512 Antes de come\u00e7ar, preciso do seu aceite.\n\n"
    "Suas mensagens s\u00e3o processadas com seguran\u00e7a s\u00f3 para te atender — "
    "nada \u00e9 vendido ou compartilhado. Termos completos aqui: {termos}\n\n"
    "_A qualquer momento: mande \"apagar meus dados\" e eu apago tudo._\n\n"
    "*Voc\u00ea concorda com os Termos?*"
)

# AUDITORIA: a versao anterior dizia "nao vou guardar nada seu" DEPOIS de ja
# ter gravado nome, idade e interesses vindos da landing. Mentir na mensagem
# juridica e pior do que nao ter a mensagem. Agora o texto descreve o que o
# codigo REALMENTE faz: wa_bot chama db.delete_user() na recusa, antes de
# mandar isto. A frase so pode existir porque o apagamento existe.
LGPD_RECUSA = (
    "Tudo bem — e j\u00e1 apaguei o que tinha seu aqui. \U0001F5D1\uFE0F\n\n"
    "Sem o aceite eu n\u00e3o guardo nada, ent\u00e3o n\u00e3o fica registro nenhum.\n\n"
    "Se mudar de ideia, \u00e9 s\u00f3 mandar um *oi* que a gente come\u00e7a do zero."
)

PEDIDO_DEMANDA = (
    "Perfeito, {nome}! \u2705\n\n"
    "Vamos direto ao ponto: *me manda uma coisa que voc\u00ea n\u00e3o pode esquecer.*\n\n"
    "Pode ser a foto de um boleto, um \u00e1udio, ou s\u00f3 uma linha:\n"
    "_\"luz 187 vence dia 20\"_\n"
    "_\"dentista dia 15 \u00e0s 14h\"_"
)

PEDIDO_NOME = "Perfeito! \u2705 Pra come\u00e7ar: *como voc\u00ea quer ser chamado?*"

# AUDITORIA: quem manda "luz 187 vence dia 20" antes de aceitar tinha a
# mensagem descartada em SILENCIO — o bot devolvia o aviso identico e a
# pessoa saia achando que anotou. Perder dado de usuario e o pior defeito
# possivel aqui. Agora: eco explicito do que NAO foi registrado, e o texto
# fica guardado em memoria pra ser processado assim que o aceite chegar.
LGPD_NAO_REGISTREI = (
    "\u26A0\uFE0F Ainda *n\u00e3o registrei* o que voc\u00ea mandou — preciso do seu aceite "
    "primeiro. Guardei aqui e registro assim que voc\u00ea confirmar.\n\n"
)

# Comando mandado antes do aceite: nao guardamos pra executar depois.
# Replayar comando destrutivo e como assinar um cheque em branco com data
# futura — a pessoa nem lembra mais que pediu.
LGPD_COMANDO_ANTES = (
    "Esse comando eu s\u00f3 consigo executar depois do seu aceite — e a\u00ed voc\u00ea "
    "me manda de novo, pra ter certeza de que ainda \u00e9 o que voc\u00ea quer.\n\n"
)

# Quando a fila de pre-aceite lotou, o LGPD_NAO_REGISTREI vira mentira: ele
# promete "guardei aqui" e nao guardou nada. Mesma classe de erro do "ja
# apaguei o que tinha seu" — a copy tem que acompanhar o que o codigo faz.
LGPD_NAO_GUARDEI = (
    "\u26A0\uFE0F N\u00e3o consegui guardar mais nada antes do aceite — *me manda de novo* "
    "depois de confirmar, que eu registro tudo.\n\n"
)

# Audio e foto sao IRRECUPERAVEIS antes do aceite: o download fica travado
# ate o consentimento, e quando ele chega o msg_id da midia ja morreu. Dizer
# "guardei aqui" pra uma foto de boleto seria mentira — e ela so descobriria
# no vencimento.
LGPD_NAO_GUARDEI_MIDIA = (
    "\u26A0\uFE0F Recebi, mas *n\u00e3o consigo abrir \u00e1udio nem foto* antes do seu aceite.\n"
    "Depois de confirmar, me manda de novo que eu leio e registro.\n\n"
)

# AUDITORIA: os usuarios que ja existiam quando a M1.2 subiu nunca passam
# pelo fluxo novo (onboarding_step ja e None/"done"). Sem isto, 100% da base
# real segue sem aceite explicito — que e exatamente a exposicao juridica
# que a M1.2 veio fechar. SOFT de proposito: anexa o pedido na resposta em
# vez de bloquear. Travar quem ja usa o produto todo dia por causa de um
# aceite retroativo e trocar um risco juridico por um churn certo.
LGPD_RECONSENTIMENTO = (
    "\n\n— — —\n"
    "\U0001F512 Atualizei meus Termos: {termos}\n"
    "Continua tudo igual — suas mensagens s\u00e3o usadas s\u00f3 pra te atender, "
    "nada \u00e9 vendido. Me manda *concordo* quando puder pra eu registrar seu "
    "aceite. _(*apagar meus dados* apaga tudo, na hora.)_"
)

# AUDITORIA. A primeira versao aceitava "ok" e "sim" como consentimento.
# Isso RECRIA o problema que a M1.2 existe pra matar: "ok" e o preenchimento
# mais comum do WhatsApp, nao um ato de vontade. Aceite juridico so por
# palavra inequivoca. Tambem aceitava "concordo?" (pergunta virava aceite) e
# "sim, mas nao quero que guardem nada", e RECUSAVA "eu concordo" pelo ^.
_SIM_LGPD_RE = re.compile(
    r"^\s*(?:eu\s+)?(?:\u2705\s*)?(?:concordo|de\s*acordo|aceito)\b", re.I)
# Esta regex terminava em `|n[\u00e3a]o)\b` — um "nao" SOLTO. E recusa dispara
# db.delete_user(). Provado em teste: "nao sei", "nao entendi", "nao, o que
# e isso?" APAGAVAM a conta, sem confirmacao e sem volta. Enquanto isso o
# comando `apagar meus dados` — mesma destruicao — exige dois passos.
_NAO_LGPD_RE = re.compile(
    r"^\s*(?:n[\u00e3a]o\s*(?:concordo|aceito)|discordo|recuso|"
    r"n[\u00e3a]o\s*quero)\b", re.I)
# Marcas de que a frase NAO e um ato de vontade limpo: pergunta ou ressalva.
_DUVIDA_LGPD_RE = re.compile(r"\?|\bmas\b|\bpor[\u00e9e]m\b|\bs[\u00f3o] que\b", re.I)


def parse_aceite(texto: str) -> Optional[bool]:
    """True = concordou, False = recusou, None = nao deu pra entender.

    Determinismo em Python, nao no LLM: aceite de LGPD e o tipo de coisa
    que nao pode depender de "o modelo entendeu a intencao 80% das vezes".

    FAIL-CLOSED: na duvida devolve None e o fluxo repete o aviso. Nunca
    presume aceite — presumir e exatamente o que a feature veio corrigir.
    """
    t = (texto or "").strip()
    if not t:
        return None
    # A duvida invalida os DOIS lados. No sim, porque "concordo?" nao e ato
    # de vontade. No nao, porque a recusa APAGA A CONTA.
    duvida = bool(_DUVIDA_LGPD_RE.search(t))
    # Recusa primeiro: "nao concordo" contem "concordo".
    if _NAO_LGPD_RE.match(t):
        return None if duvida else False
    if _SIM_LGPD_RE.match(t):
        return None if duvida else True
    return None

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
