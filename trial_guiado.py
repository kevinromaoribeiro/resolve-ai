# -*- coding: utf-8 -*-
"""
trial_guiado.py — Régua de engajamento do trial de 7 dias (CRM / funil).
=========================================================================
Objetivo: garantir que no D7 a pessoa QUEIRA ficar. Cada dia do trial tem
um objetivo de funil diferente — não é "mandar mensagem", é mover a pessoa
de "testei uma vez" até "não quero mais perder isso".

FILOSOFIA (chapéu de sênior de CRM):
- Nudge é para quem ESFRIOU. Se a pessoa usou nas últimas 24h, não empurra —
  quem já está engajado não precisa de lembrete, precisa de espaço.
- Personalizado pelos INTERESSES do onboarding. Falar de carro pra quem
  escolheu "contas" é ruído. Relevância > frequência.
- 1 toque por dia no MÁXIMO, e só se fizer sentido. Silêncio proposital em
  dias que a pessoa está ativa.
- Cada nudge tem UMA ação clara (CTA), curto, com prova de valor imediata.
- Dedup total via db.mark_nudge_sent: ninguém recebe o mesmo nudge 2x.

RÉGUA (objetivo de funil por dia):
  D1  ATIVAÇÃO   — quem não registrou nada: puxa o 1º uso (o "aha" mais cedo)
  D2  HÁBITO     — sugere 2º caso de uso, dentro do interesse escolhido
  D3  AMPLIAÇÃO  — mostra um interesse que ela escolheu mas ainda não usou
  D4  AHA PROATIVO— evidencia o valor único: "eu te avisei sem você pedir"
  D5  PROVA      — resume o que já tirou da cabeça dela (valor acumulado)
  D6  CONVERSÃO  — fim do trial amanhã + link de pagamento (fecha o funil)

Cada função devolve dispatches no formato do scheduler:
  {"user_id","user_nome","telefone","item_id":None,"kind","message"}
"""
from __future__ import annotations

import os
from typing import Optional

import db
import tempo

# OS MESMOS DEFAULTS REAIS DO `wa_bot`, e pelo mesmo motivo documentado la:
# o default antigo aqui era "https://SEU-LINK-DE-PAGAMENTO", e uma VPS sem a
# variavel entregava esse placeholder literal no fechamento do trial — o
# unico momento em que o produto pede dinheiro. Link de cobranca e publico
# por natureza; versiona-lo nao expoe segredo nenhum.
PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "https://mpago.la/2oashdp")
PAYMENT_LINK_ANUAL = os.environ.get("PAYMENT_LINK_ANUAL",
                                    "https://mpago.la/2n5pEVS")
# MENOR QUE A JANELA DE 24h, e isso não é ajuste fino: é o que faz a régua
# existir. O nudge sai como texto livre (nenhum `trial_d*` tem template, de
# propósito), e texto livre só passa pra quem falou nas ÚLTIMAS 24h. Com
# `INACTIVE_HOURS = 24` as duas condições se excluíam — "calado há 24h+" e
# "dentro da janela de 24h" não acontecem juntos. A mensagem era gerada e
# descartada na linha seguinte, sem erro e sem log.
#
# 18h abre uma faixa real de 6 horas: quem falou ontem à noite recebe o
# toque na tarde seguinte. Quem sumiu de vez só com template aprovado, e não
# existe um pra nudge de trial — mas quem sumiu de vez também não é quem
# esta régua existe pra ativar.
INACTIVE_HOURS = int(os.environ.get("TRIAL_INACTIVE_HOURS", "18"))
# Dia em que a régua fecha e pede a assinatura. Trial de 14 dias -> D13,
# para a mensagem de conversão chegar ANTES do prazo acabar, não depois.
# MESMA ENV QUE O `wa_bot` e o `db` leem. Sem isto o D5 dizia "faltam 2
# dias" — verdade num trial de 7 dias, mentira nos 14 de hoje.
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "14"))
# O MESMO numero que o `wa_bot` libera de verdade. O texto dizia "falo com
# o Kevin e libero alguns dias" — ninguem e consultado (a extensao e
# automatica) e "alguns" deixava a pessoa esperando uma semana pra
# receber dois dias. Promessa vaga no fechamento e a pior hora pra
# frustrar.
TRIAL_EXTENSAO_DIAS = int(os.environ.get("TRIAL_EXTENSAO_DIAS", "2"))
DIA_FECHAMENTO = int(os.environ.get("TRIAL_DIA_FECHAMENTO", "13"))


# ── Sugestões por interesse (CTA único, prova de valor no minuto 1) ──────
# Alinhado com USE_CASE_EXAMPLES do textos.py, mas em tom de reengajamento.
_POR_INTERESSE = {
    "contas": "manda a foto de um boleto (ou digita _\"luz 180 vence dia 20\"_). "
              "Eu te aviso 3 dias antes, 1 dia antes e no dia — multa nunca mais.",
    "mercado": "fala _\"acabou o café\"_ que eu lembro na próxima compra e "
               "aviso quando for hora de repor.",
    "carro": "manda _\"troquei o óleo, 74.200 km\"_ — eu calculo a próxima "
             "troca e te aviso com folga. IPVA e seguro também.",
    "saude": "diz _\"dermato dia 15/08 às 14h\"_ que eu te lembro na véspera "
             "e no dia. Remédio contínuo também.",
    "datas": "manda _\"aniversário da minha mãe é 03/09\"_ — eu te aviso todo "
             "ano com antecedência pra dar tempo do presente.",
    "encomendas": "fala _\"minha encomenda chega até sexta\"_ que eu fico de "
                  "olho no prazo por você.",
    "pet": "manda _\"vacina da Mel dia 30\"_ — eu aviso antes, e lembro da "
           "ração quando estiver acabando.",
    "burocracia": "diz _\"IPVA vence 15/01\"_ ou _\"renovar CNH em março\"_ — "
                  "eu te aviso com folga, sem susto.",
}
_ORDEM_GENERICA = ["contas", "saude", "carro", "datas",
                   "encomendas", "pet", "mercado", "burocracia"]


def _first_name(user: dict) -> str:
    return (user.get("nome") or "").split()[0] or "Oi"


def _interesses(user: dict) -> list[str]:
    raw = (user.get("interesses") or "").strip()
    return [i for i in raw.split(",") if i] if raw else []


def _hours_since_interaction(user: dict) -> float:
    ts = user.get("ultima_interacao")
    if not ts:
        return 9999.0
    try:
        from datetime import datetime
        last = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return (tempo.agora() - last).total_seconds() / 3600.0
    except Exception:
        return 9999.0


def _is_cold(user: dict) -> bool:
    """Só nudge em quem esfriou (não fala há INACTIVE_HOURS+)."""
    return _hours_since_interaction(user) >= INACTIVE_HOURS


def _registrou_algo(user_id: int) -> bool:
    try:
        return len(db.list_items(user_id)) > 0
    except Exception:
        return False


def _interesse_nao_usado(user: dict) -> Optional[str]:
    """Um interesse que a pessoa escolheu mas ainda não gerou item —
    a melhor 'próxima sugestão'. Se não achar, cai no 1º interesse."""
    ints = _interesses(user)
    if not ints:
        return None
    # heurística simples: sugere o 2º interesse (o 1º já foi puxado no D1/D2)
    return ints[1] if len(ints) > 1 else ints[0]


def _sugestao_para(user: dict, prefer: Optional[str] = None) -> tuple[str, str]:
    """Retorna (chave_interesse, texto_cta). Usa o interesse pedido, senão
    o 1º do onboarding, senão o genérico 'contas'."""
    ints = _interesses(user)
    chave = prefer or (ints[0] if ints else "contas")
    if chave not in _POR_INTERESSE:
        chave = next((i for i in _ORDEM_GENERICA if i in _POR_INTERESSE), "contas")
    return chave, _POR_INTERESSE[chave]


def _mk(user: dict, kind: str, message: str, nudge: str = "",
        capacidade: str = "", faz: str = "") -> dict:
    """Monta o disparo. `nudge` e a chave do dedup — e quem marca e QUEM ENVIA.

    AUDITORIA M2.0 (16/08/2026): o `db.mark_nudge_sent` era chamado aqui, na
    GERACAO. Com o M2.0 recusando envio fora da janela, o nudge era queimado
    sem nunca ter saido e nao voltava mais — inclusive o `d6_fim`, que e a
    UNICA mensagem de conversao do trial, com o link de pagamento. Marcar
    antes de enviar e apagar dado do usuario em silencio.

    `capacidade` e `faz` sao o que a etapa ENSINA, e viram as variaveis do
    `resolveai_novidade` quando a pessoa esta fora da janela de 24h. Sem
    eles a etapa so alcancaria quem falou com o bot nas ultimas 24h — que
    e justamente quem nao precisa dela.
    """
    return {
        "user_id": user["id"],
        "user_nome": user.get("nome", ""),
        "telefone": user["telefone"],
        "item_id": None,
        "kind": kind,
        "message": message,
        "nudge": nudge,
        "nome_da_novidade": capacidade,
        "o_que_ela_faz": faz,
    }


# ── A régua ───────────────────────────────────────────────────────────────
def run_trial_nudges() -> list[dict]:
    """Roda a régua para todos os usuários em trial. Chamada pelo scheduler.
    Retorna a lista de dispatches (o scheduler envia e marca como enviado)."""
    dispatches: list[dict] = []
    for user in db.active_trial_users():
        dia = db.trial_day_number(user)          # 0=entrou hoje, 1=amanhã...
        first = _first_name(user)

        # ÚLTIMO DIA do trial: CONVERSÃO com tom de mordomo. Sempre manda.
        if dia >= DIA_FECHAMENTO and not db.nudge_already_sent(user, "d6_fim"):
            n_itens = len(db.list_items(user["id"]))
            palavra = "coisa" if n_itens == 1 else "coisas"
            prova = (f"Nesses dias, já tirei *{n_itens} {palavra}* da sua "
                     f"cabeça. " if n_itens else "")
            planos = f"*1* — Mensal, R$ 19,90/mês\n{PAYMENT_LINK}"
            if PAYMENT_LINK_ANUAL:
                planos += (f"\n\n*2* — Anual, R$ 149,90/ano _(2 meses grátis)_\n"
                           f"{PAYMENT_LINK_ANUAL}")
            msg = (f"{first}, foram duas semanas boas. 🤝 Eu nunca fiz nada "
                   f"por você — quem paga, compra e resolve continua sendo "
                   f"você. O que eu fiz foi *não deixar passar*.\n\n"
                   f"{prova}Mas por hoje o seu teste "
                   f"chega ao fim. Pra continuar comigo, é só escolher:\n\n"
                   f"{planos}\n\n"
                   f"Toca no plano que preferir e pronto. 💛\n\n"
                   f"E se você ainda não teve tempo de me testar direito, "
                   f"responde *mais tempo* que eu libero mais "
                   f"*{TRIAL_EXTENSAO_DIAS}* dias na hora. Não quero que "
                   f"você decida sem ter visto o que eu faço. Nada some: "
                   f"seus itens ficam aqui.")
            dispatches.append(_mk(user, "trial_d6", msg,
                                         nudge="d6_fim"))
            continue

        # Dias 1–5: só toca quem ESFRIOU (inativo 24h+). Quem usa hoje, deixa em paz.
        if not _is_cold(user):
            continue

        # A REGUA, DIA SIM DIA NAO.
        #
        # Antes eram nove toques com D1 a D5 em dias seguidos. Cinco
        # mensagens em cinco dias pra quem esta calado e o padrao que a
        # Meta pune e que a pessoa bloqueia. Sete toques em quatorze dias
        # e regularidade; cinco em cinco dias e insistencia.
        #
        # Cada etapa ENSINA UMA CAPACIDADE, e e por isso que ela cabe no
        # `resolveai_novidade`: `capacidade` vira o nome e `faz` vira a
        # explicacao. `faz` recebe o usuario porque a explicacao muda com o
        # que a pessoa marcou no cadastro — falar de boleto pra quem
        # escolheu "pet" e ruido.
        #
        # `rico` e o texto de dentro da janela: mais longo, com emoji e
        # formatacao. Fora da janela sai o template, que e mais seco por
        # obrigacao. Os dois dizem a mesma coisa.
        # CADA DIA TEM A SUA LICAO. Decisao do dono, 05/09.
        #
        # Cheguei a fazer a regua avancar pela proxima licao NAO enviada,
        # pra quem estava no meio do trial nao perder as primeiras. O Kevin
        # preferiu manter a regua oficial e tratar os testers como cliente
        # real: quem esta no dia 7 recebe a licao do 7, e as dos dias que
        # ja passaram nao voltam.
        #
        # A consequencia esta aceita e registrada: quem entra no meio perde
        # as licoes anteriores. Pra cliente novo, que comeca no dia 1, a
        # jornada e inteira.
        for etapa in _ETAPAS:
            if dia != etapa["dia"]:
                continue
            if db.nudge_already_sent(user, etapa["nudge"]):
                break
            dispatches.append(_mk(
                user, "trial_d%d" % etapa["dia"], etapa["rico"](user),
                nudge=etapa["nudge"],
                capacidade=etapa["capacidade"], faz=etapa["faz"](user)))
            break

    return dispatches



# ── A JORNADA DE 14 DIAS ──────────────────────────────────────────────────
#
# Sete toques, dia sim dia nao. Cada um ensina UMA capacidade do produto, na
# ordem em que ela faz sentido: primeiro registrar, depois as formas mais
# faceis de registrar (foto, audio), depois o que o bot devolve (aviso,
# resumo, podcast), e por fim o fechamento.
#
# O `faz` de cada etapa e a explicacao que vai no template — e ela e
# personalizada pelo interesse do cadastro sempre que a capacidade permite.


def _faz_registrar(user: dict) -> str:
    _chave, cta = _sugestao_para(user)
    # O CTA ja vem com formatacao de WhatsApp (asterisco, underline), que
    # dentro de uma variavel de template a Meta nao interpreta — sai o
    # simbolo cru na tela da pessoa.
    return ("Escreve do seu jeito e eu guardo: " + _sem_formatacao(cta))


def _faz_frentes(user: dict) -> str:
    """A explicacao do D9 usa o interesse que a pessoa AINDA NAO usou.

    Sem isto ela repetia palavra por palavra a do D1, e receber a mesma
    frase duas vezes em nove dias e o jeito mais rapido de ensinar que
    nossas mensagens nao valem leitura.
    """
    _chave, cta = _sugestao_para(user, prefer=_interesse_nao_usado(user))
    # PREFIXO CURTO. A variavel do template e cortada em 200 caracteres, e
    # o CTA sozinho ja passa de 120 — prefixo longo empurra a frase pro
    # corte, que acontece no envio, depois de a etapa ja ter sido gasta.
    return "Cuido de varios assuntos ao mesmo tempo: " + _sem_formatacao(cta)


def _faz_foto(_user: dict) -> str:
    return ("Tira foto de um boleto ou manda o PDF da conta. Eu leio o "
            "codigo de barras, o valor e o vencimento sozinho.")


def _faz_audio(_user: dict) -> str:
    return ("Manda um audio de ate 2 minutos, do jeito que voce fala. Eu "
            "transcrevo e guardo tudo que tiver ali dentro.")


def _faz_aviso(_user: dict) -> str:
    return ("Eu te aviso antes de cada coisa vencer, e de novo no dia. "
            "Voce so responde feito quando resolver.")


def _faz_gastos(_user: dict) -> str:
    return ("Pergunta quanto gastei esse mes e eu somo por categoria, sem "
            "voce fazer planilha nenhuma.")


def _sem_formatacao(texto: str) -> str:
    """Tira asterisco e underline: variavel de template nao formata.

    O CTA foi escrito pra texto livre do WhatsApp, onde *isto* fica em
    negrito. Dentro de uma variavel de template a Meta nao interpreta, e a
    pessoa le o asterisco cru.
    """
    return (texto or "").replace("*", "").replace("_", "")


def _rico_d1(user: dict) -> str:
    first = _first_name(user)
    if not _registrou_algo(user["id"]):
        _chave, cta = _sugestao_para(user)
        return (f"{first}, voce comecou comigo mas ainda nao me deu nada pra "
                f"cuidar. 😊 Bora testar em 10 segundos: {cta}\n\n"
                f"E so mandar — eu faco o resto.")
    return (f"{first}, vi que voce ja comecou a usar. 🙌 Manda mais uma coisa "
            f"que te preocupa hoje — conta, consulta, compra — que eu tiro "
            f"da sua cabeca.")


def _rico_d3(user: dict) -> str:
    return (f"{_first_name(user)}, um atalho que quase ninguem descobre "
            f"sozinho: *tira foto do boleto* e me manda. 📄\n\n"
            f"Eu leio o codigo de barras, o valor e o vencimento, guardo "
            f"tudo e te aviso antes de vencer. Serve pra PDF de conta "
            f"tambem.")


def _rico_d5(user: dict) -> str:
    return (f"{_first_name(user)}, se digitar der preguica, *manda audio*. "
            f"🎤\n\nAte 2 minutos, do jeito que voce fala, tudo junto e "
            f"misturado. Eu separo o que e compromisso, o que e gasto e o "
            f"que e lembrete.")


def _rico_d7(user: dict) -> str:
    first = _first_name(user)
    try:
        n = len(db.list_items(user["id"]))
    except Exception:
        n = 0
    if n:
        return (f"{first}, uma semana comigo. Ja tenho *{n} coisa(s)* suas "
                f"guardadas — e eu aviso antes de cada uma vencer, sem voce "
                f"precisar abrir nada. ⏰\n\nEsse e o ponto: voce esquece, "
                f"eu nao.")
    return (f"{first}, faz uma semana e eu ainda nao te avisei de nada — "
            f"porque voce ainda nao me deu nada pra cuidar. ⏰\n\n"
            f"Me da um vencimento ou uma data que eu provo nos proximos "
            f"dias o que eu faco de diferente de uma listinha.")


def _rico_d9(user: dict) -> str:
    prox = _interesse_nao_usado(user)
    _chave, cta = _sugestao_para(user, prefer=prox)
    return (f"{_first_name(user)}, voce me disse que tambem se preocupa com "
            f"isso — entao: {cta}\n\nPosso cuidar de varias frentes ao "
            f"mesmo tempo, sem voce se perder.")


def _rico_d11(user: dict) -> str:
    return (f"{_first_name(user)}, alem de lembrar, eu *somo*. 💰\n\n"
            f"Manda os gastos do dia como eles acontecem — _45 mercado_, "
            f"_12 uber_ — e depois pergunta _quanto gastei esse mes_. Eu "
            f"respondo por categoria, sem planilha nenhuma.")


# Os dias em que a regua fala. Derivado da tabela, e nao escrito de novo:
# duas listas do mesmo ritmo divergem na primeira vez que alguem muda uma.
_ETAPAS = [
    {"dia": 1, "nudge": "d1", "capacidade": "escrever do seu jeito",
     "faz": _faz_registrar, "rico": _rico_d1},
    {"dia": 3, "nudge": "d3", "capacidade": "foto de boleto",
     "faz": _faz_foto, "rico": _rico_d3},
    {"dia": 5, "nudge": "d5", "capacidade": "audio",
     "faz": _faz_audio, "rico": _rico_d5},
    {"dia": 7, "nudge": "d7", "capacidade": "aviso antes de vencer",
     "faz": _faz_aviso, "rico": _rico_d7},
    {"dia": 9, "nudge": "d9", "capacidade": "varias frentes ao mesmo tempo",
     "faz": _faz_frentes, "rico": _rico_d9},
    {"dia": 11, "nudge": "d11", "capacidade": "quanto voce gastou",
     "faz": _faz_gastos, "rico": _rico_d11},
]

_DIAS_DE_TOQUE = tuple(e["dia"] for e in _ETAPAS)
