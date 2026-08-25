"""
wa_bot.py — Gateway real de WhatsApp do RESOLVE AI via Evolution API (QR Code).

Arquitetura:
    WhatsApp <-> Evolution API (Docker, conecta via QR Code)
                     |  webhook HTTP
                     v
                wa_bot.py (FastAPI) --> ai_engine.py --> db.py (SQLite)
                     |
                     +--> resposta via REST da Evolution API

Custo: R$ 0 de mensageria (Evolution usa o WhatsApp Web do seu número).
ATENÇÃO: API não-oficial. Use um CHIP DEDICADO (não seu número pessoal) —
a Meta pode banir números que operam bots fora da API oficial. Aceitável
para validar com 20-50 beta users; migre para a API oficial ao escalar.

Config via variáveis de ambiente (.env):
    EVOLUTION_URL=http://localhost:8080
    EVOLUTION_APIKEY=troque-esta-chave
    EVOLUTION_INSTANCE=resolveai
    OPENAI_API_KEY=...        (opcional: LLM + Whisper + visão)
    ANTHROPIC_API_KEY=...     (opcional: LLM + visão)

Execução:
    uvicorn wa_bot:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import base64
import os
import re
import unicodedata
from datetime import datetime, date, timedelta
import tempo
from typing import Any, Optional

import db
import textos
import ai_engine
import boleto  # M2.1: le conta de foto/PDF. Le e lembra — nunca paga.
import calendario  # M2.2: datas que o bot sabe sozinho (IPVA, feriados)
import scheduler
import canal as wasender  # camada de canal: Meta oficial OU WasenderAPI (ver canal.py)
import meta_cloud  # handshake e assinatura do webhook da Meta
import botoes  # botao de resposta rapida (decidido em Python, nao no LLM)
import jornada  # jornada: demo de 90s, lista de sugestoes, copy de cobranca
import motor_v8  # mordomo híbrido: entende linguagem natural fora do script

db.init_db()

# Marcador de build. Trocar a cada deploy — é o que permite confirmar em 1
# request (/health) se o código novo subiu, em vez de deduzir pelo
# comportamento do bot.
BUILD = "v23.9-m25-ajustes-2026-08-18"

# ---------------------------------------------------------------------------
# M1.2 — ACEITE DE LGPD COMO ATO EXPLICITO
# ---------------------------------------------------------------------------
# ATENCAO DE DEPLOY: os dicionarios abaixo sao POR PROCESSO. Rode o uvicorn
# com UM worker so (o padrao). Com 2+ workers a fila pre-aceite e o CONFIRM
# ficam em processos diferentes e a demanda da pessoa se perde calada.
LGPD_STEPS = ("lgpd_landing", "lgpd_organico")

# O UNICO conjunto de comandos que atravessa o gate antes do aceite. Todos
# protegem o usuario ou explicam o tratamento — nenhum grava dado novo nem
# vende nada. "assinar" e "meu nome e X" ficam DE FORA de proposito.
_CMD_PRE_ACEITE = {"apagar meus dados", "apagar dados", "excluir meus dados",
                   "deletar meus dados", "privacidade", "termos", "lgpd",
                   "meus dados", "ajuda"}

# Comandos que NUNCA podem ser guardados na fila e replayados depois. Todos
# abrem confirmacao destrutiva ou mexem em assinatura: replay arma o CONFIRM
# (que nao tem TTL) por conta de uma frase antiga, e um "sim" ou "apagar"
# solto dias depois executa a acao. Comando e ordem do momento.
_CMD_NUNCA_ENFILEIRA = _CMD_PRE_ACEITE | {
    "cancelar", "cancelar assinatura", "quero cancelar",
    "apagar", "sim", "confirmo", "assinar", "planos", "quero assinar",
    "pagar"}

# Mensagens mandadas ANTES do aceite. Em memoria de proposito: e efemera e
# nao vira base de dado sem consentimento — gravar no SQLite seria justamente
# o que o aceite ainda nao autorizou.
# LISTA por telefone: a primeira versao sobrescrevia, entao quem mandava 3
# demandas recebia a promessa "guardei" 3 vezes e so a ultima sobrevivia.
PRE_ACEITE: dict = {}
PRE_ACEITE_MAX = 500        # teto de telefones
PRE_ACEITE_MAX_MSGS = 5     # teto de mensagens por pessoa
PRE_ACEITE_TTL_S = 3600     # some depois de 1h sem aceitar

# Telefones que recebem o pedido de reconsentimento ANEXADO na proxima
# resposta. Mesmo padrao do TRIAL_VENCIDO.
RECONSENTIR: dict = {}

# Quem acabou de recusar e teve os dados apagados. Sem isto, recusar e mandar
# "oi" recria o usuario e dispara o onboarding INTEIRO de novo — a rajada que
# rendeu 2 restricoes da Meta em 04/08.
RECEM_APAGADOS: dict = {}
CARENCIA_POS_RECUSA_S = 300

# Ultimo envio da abertura completa. Sem isto cada "oi" de quem esta parado
# no aceite dispara DUAS mensagens.
ULTIMO_AVISO_LGPD: dict = {}
REABRIR_ONBOARDING_S = 600

# Confirmacao pendente cancelada porque a pessoa mandou outra coisa.
CANCELADO_AVISO: dict = {}

# Telefones cuja fila esta sendo reprocessada AGORA. Distingue "mensagem que
# a pessoa acabou de mandar" de "mensagem antiga sendo colhida".
EM_REPROCESSO: set = set()
_SEM_CONFIRM = object()

# phone -> (user_id, n_itens_antes, [demandas]). Conferido no webhook depois
# que a resposta saiu: as demandas guardadas viraram item MESMO?
CONFERIR_FILA: dict = {}

# ---------------------------------------------------------------------------
# M1.3 — KITS DE ROTINA
# ---------------------------------------------------------------------------
# phone -> {"kit": <id>, "quando": datetime}. Estado do fluxo de 2 passos:
# a pessoa toca no kit (passo 1) e depois na opcao (passo 2).
# Em memoria porque e efemero e morre em 15 min — nao vale coluna no banco.
KIT_ETAPA: dict = {}
KIT_JANELA_S = 900

# phone -> True. Marca que a lista ja foi oferecida depois do 1o item, pra
# nao repetir a oferta em toda mensagem.
KIT_JA_OFERECIDO: dict = {}

# M1.5 — phone -> descricao do item que acabou de entrar em modo silencioso.
# O aviso vai ANEXADO na resposta: a pessoa precisa saber que o bot parou de
# tocar, senao "parou de funcionar" vira o diagnostico dela.
# v23.4: era escrito aqui e lido em lugar NENHUM — o item era silenciado e a
# pessoa nunca sabia. Agora o aviso sai na hora, no proprio fluxo do
# adiamento, e o dicionario deixou de existir.

# M1.4 — phone -> {uid, antes, esperado, txt}. Marcado quando um audio e
# transcrito, conferido no webhook depois que o motor rodou.
AUDIO_ESPERADO: dict = {}

# phone -> datetime da oferta. A oferta termina com uma pergunta sim/nao,
# entao a resposta natural e "sim" — e se "sim" nao funcionar, queimamos o
# unico pico de confianca do funil. Dentro desta janela, sim/quero/bora/ok
# valem como "kits".
KIT_CONVITE: dict = {}
KIT_CONVITE_S = 600
# Auditoria v23.0: "ok", "claro", "pode" e "manda" sozinhos sao respostas
# comuns a QUALQUER coisa. Dentro dos 600s do convite, isso sequestrava a
# frase e jogava a pessoa num kit que ela nao pediu. Ficaram so as formas
# que so fazem sentido como aceite do convite.
_KIT_SIM_RE = re.compile(
    r"^\s*(?:pode\s+mandar|quero\s+sim|manda\s+(?:os\s+)?kits?|"
    r"mostra\s+(?:os\s+)?kits?|sim|quero|bora|vamos)\s*[.!]?\s*$", re.I)

# item_id -> True. Evita repetir o aviso de "essa hora ja passou" a cada
# mensagem seguinte sobre o mesmo item.
PASSADO_AVISADO: dict = {}

# AVISO DE VENCIMENTO: SÓ UM DIA ANTES.
# O scheduler vinha avisando em D-3, D-1 e no próprio dia — três mensagens
# pelo mesmo boleto. Isso não é ajudar, é encher o saco, e é assim que o
# usuário silencia o bot. Fica só o D-1.
# (O alarme de hora marcada continua: aquilo o usuário pediu explicitamente,
# não é aviso automático de vencimento.)
#
# A EXCEÇÃO É OBRIGAÇÃO ANUAL DE VEÍCULO (M2.5): licenciamento e IPVA têm
# prazo de mês, valor alto e nenhuma segunda chance. Avisar isso em D-1 é
# avisar tarde. Lá a régua é D-30/D-7/D-1, que a `DUE_ALERT_DAYS_POR_CATEGORIA`
# do scheduler define.
#
# Passa pela função, e não por atribuição direta, porque a JANELA de consulta
# (`DUE_WINDOW_DAYS`) tem que ser recalculada junto: com a janela em 1, o item
# de D-30 nem é lido do banco e o aviso some sem erro nenhum.
scheduler.definir_politica_de_aviso(padrao={1})

EVOLUTION_URL = os.environ.get("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
EVOLUTION_APIKEY = os.environ.get("EVOLUTION_APIKEY", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "resolveai")

# Link de pagamento (Kirvano, Mercado Pago Assinaturas, Stripe Payment Link…)
PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "https://SEU-LINK-DE-PAGAMENTO")
PAYMENT_LINK_ANUAL = os.environ.get("PAYMENT_LINK_ANUAL", "")
# 14 dias, não 7. O produto só prova valor quando o usuário É LEMBRADO de algo
# que ele tinha esquecido — e uma conta com vencimento mensal não cabe numa
# janela de 7 dias. Trial curto demais cobra antes de a pessoa ter vivido o
# momento pelo qual ela pagaria.
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "14"))
# v6: teto de duração de áudio (custo Whisper) e link dos Termos/Privacidade
AUDIO_MAX_SECONDS = int(os.environ.get("AUDIO_MAX_SECONDS", "120"))
# ---------------------------------------------------------------------------
# FREIO ANTI-BLOQUEIO (v18.1)
# ---------------------------------------------------------------------------
# Em 04/08 a Meta restringiu o número por 3h: "atividade pode caracterizar
# envio de spam, mensagens automáticas ou em massa". O gatilho não foi o
# conteúdo — foi o RITMO. Às 07:59 saíram 4 mensagens em um minuto.
#
# E o pior nem tinha acontecido ainda: com DISPATCH_MAX_PER_CYCLE=60 e o cron
# rodando a cada 60s, a configuração PERMITIA 60 mensagens num único minuto.
# Com 20 usuários e alarmes coincidindo às 8h isso acontece sozinho. Nenhuma
# pessoa manda 60 mensagens em um minuto — é essa assinatura que a Meta lê, e
# ela não sabe nem se importa que sejam lembretes úteis.
#
# Três freios independentes, porque um só sempre tem um caso que escapa:
#   1. teto por ciclo      — quantas saem de uma vez
#   2. espaçamento         — quanto tempo entre uma e outra (com variação:
#                            intervalo exato também é assinatura de robô)
#   3. teto por usuário/dia— um caso extremo não estoura a conta sozinho
DISPATCH_MAX_PER_CYCLE = int(os.environ.get("DISPATCH_MAX_PER_CYCLE", "5"))
ENVIO_INTERVALO_MIN = float(os.environ.get("ENVIO_INTERVALO_MIN", "8"))
ENVIO_INTERVALO_MAX = float(os.environ.get("ENVIO_INTERVALO_MAX", "15"))
MAX_PROATIVAS_POR_USUARIO_DIA = int(
    os.environ.get("MAX_PROATIVAS_POR_USUARIO_DIA", "6"))
TERMS_URL = os.environ.get(
    "TERMS_URL",
    "https://resolveai.ia.br/termos.html")
# Número do dono (só ele pode ativar assinaturas manualmente no MVP)
ADMIN_PHONE = re.sub(r"\D", "", os.environ.get("ADMIN_PHONE", ""))

# ---------------------------------------------------------------------------
# TRAVA DO PAINEL
# ---------------------------------------------------------------------------
# O painel e o /painel/acao ficavam abertos na porta 8000 sem nenhuma
# autenticação: qualquer um com o IP apagava usuário e item, lia conversa e
# disparava mensagem em nome do Resolve AI. Aceitável enquanto era só o dono
# testando; inaceitável com cliente pagante.
#
# FAIL-CLOSED de propósito: sem PAINEL_TOKEN definido, o painel fica FECHADO.
# O contrário (abrir quando falta config) é como buracos assim nascem.
PAINEL_TOKEN = os.environ.get("PAINEL_TOKEN", "").strip()


def _painel_autorizado(request) -> bool:
    """Token via ?k=... (para abrir no navegador) ou header X-Painel-Token
    (para o JS da própria página). Comparação em tempo constante."""
    import secrets
    if not PAINEL_TOKEN:
        return False
    enviado = (request.query_params.get("k")
               or request.headers.get("x-painel-token")
               or "")
    return secrets.compare_digest(str(enviado), PAINEL_TOKEN)


def _negado(request):
    """Resposta única para acesso sem token. Não revela se o token existe,
    nem devolve dado nenhum."""
    from fastapi.responses import JSONResponse
    import logging
    logging.getLogger("resolveai").warning(
        "[painel] acesso negado (%s)",
        "PAINEL_TOKEN nao configurado" if not PAINEL_TOKEN else "token invalido")
    return JSONResponse(status_code=401, content={"erro": "nao autorizado"})


# ---------------------------------------------------------------------------
# Núcleo TESTÁVEL (sem FastAPI/HTTP): payload Evolution -> resposta
# ---------------------------------------------------------------------------

# Decisões pendentes por telefone (Regra de Ouro da imagem silenciosa)
PENDING: dict[str, dict] = {}
# Quantas vezes seguidas a pessoa respondeu algo que o menu 1/2 não entendeu.
# 12-14/08: o PENDING não tinha saída — cada resposta fora do menu devolvia
# "Não entendi" e RE-ARMAVA a decisão. O Kevin ficou 3 dias preso nisso, e
# toda mensagem dele (inclusive "feito") caía no menu. Decisão pendente é
# convite, não jaula: depois de PENDING_MAX_ERROS respostas fora do menu o bot
# solta a decisão, salva o item como lembrete (nada se perde) e segue a
# conversa normal.
PENDING_ERROS: dict[str, int] = {}
PENDING_MAX_ERROS = 2
# Quando a decisao foi armada. Decisao pendente sem prazo deixa de ser
# convite e vira jaula: um PENDING de dias antes sequestrava a mensagem
# seguinte, e o menu ("pag" em "pagar") transformava um pedido novo em
# "Despesa Paga" — com o pedido real descartado calado (14/08).
# (dia, item_id, kind, motivo) que ja gerou linha de nao-entrega.
#
# O DIA FAZ PARTE DA CHAVE (auditoria M2.0 rodada 2, P1-3): sem ele, o
# container que fica semanas de pe registrava a falha UMA VEZ e nunca mais.
# O "N falha(s) de envio ontem" do dash matinal le o msg_log do dia — e
# mostrava zero enquanto o lembrete continuava sem sair a cada 5 minutos.
# Trocar ruido permanente por silencio permanente e o defeito que a regra 5
# existe pra matar. Chaves de dias anteriores sao podadas a cada ciclo.
FALHA_JA_LOGADA: set = set()
PENDING_EM: dict[str, object] = {}
PENDING_TTL_S = 1200        # 20 min: o tempo de responder um menu, nao 3 dias
# Confirmações pendentes de comandos destrutivos: phone -> 'cancelar'|'apagar'
CONFIRM: dict[str, str] = {}
# Trial vencido nesta mensagem: phone -> primeiro nome. A mensagem é
# processada normalmente e o convite de assinatura é anexado no fim.
TRIAL_VENCIDO: dict[str, str] = {}

USE_CASES = {
    "1": ("contas", "💡 Contas de casa"),
    "2": ("mercado", "🛒 Compras de mercado"),
    "3": ("carro", "🚗 Manutenções do carro"),
    "4": ("saude", "🩺 Consultas e exames"),
    "5": ("datas", "🎂 Aniversários e datas"),
    "6": ("encomendas", "📦 Encomendas e prazos"),
    "7": ("pet", "🐾 Cuidados com pet"),
    "8": ("burocracia", "📄 Documentos e burocracias"),
}
USE_CASE_EXAMPLES = textos.USE_CASE_EXAMPLES



def _interesses_menu(first_name: str) -> str:
    linhas = "\n".join(f"*{n}* {label}" for n, (_, label) in USE_CASES.items())
    return (f"Prazer, {first_name}! 🤝\n\n"
            f"*Para que você quer me usar?* Responda com os números "
            f"(ex.: *1 3 7*) ou escreva do seu jeito:\n\n{linhas}\n\n"
            f"_(pode escolher vários — ou responder \"pular\")_")


def _parse_interesses(text: str) -> list[str]:
    low = text.lower()
    keys = [k for n, (k, _) in USE_CASES.items() if n in re.findall(r"\d", low)]
    keyword_map = {"conta": "contas", "boleto": "contas", "mercado": "mercado",
                   "compra": "mercado", "carro": "carro", "óleo": "carro",
                   "oleo": "carro", "consulta": "saude", "saúde": "saude",
                   "saude": "saude", "exame": "saude", "aniversário": "datas",
                   "aniversario": "datas", "data": "datas",
                   "encomenda": "encomendas", "prazo": "encomendas",
                   "pet": "pet", "ração": "pet", "racao": "pet",
                   "gato": "pet", "cachorro": "pet",
                   "documento": "burocracia", "ipva": "burocracia",
                   "burocracia": "burocracia"}
    for kw, key in keyword_map.items():
        if kw in low and key not in keys:
            keys.append(key)
    return keys


def _onboarding_done_msg(first_name: str, keys: list[str]) -> str:
    """Primeira mensagem de quem chega pela landing.

    ANTES: despejava SUGESTOES_ABERTURA + ate 4 blocos de exemplo +
    RODAPE — mais de mil caracteres de cardapio, como PRIMEIRA mensagem,
    sem nenhuma pergunta. Resultado medido em 05/08: 8 cadastros, 1 item
    (e o item era do dono). Cardapio sem garcom ninguem pede.

    AGORA: uma unica acao concreta, com exemplo pronto pra copiar. As 8
    sugestoes nao sumiram — viraram a lista de jornada.SUGESTOES, que o
    usuario abre quando quiser, e a regua do trial_guiado continua
    puxando as que ele nao usou.
    """
    # M1.2: o pedido de demanda saiu de BOAS_VINDAS e virou PEDIDO_DEMANDA
    # (mensagem 3). Este ponto e o FIM do fluxo organico — apontar pra
    # BOAS_VINDAS aqui faria o cadastro terminar sem pedir nada, e a
    # ativacao morreria em silencio.
    return jornada.PEDIDO_DEMANDA.format(nome=first_name)


def _payment_msg(first_name: str) -> str:
    anual = (f"\n📅 Anual (R$ 149 ≈ R$ 12,40/mês): {PAYMENT_LINK_ANUAL}"
             if PAYMENT_LINK_ANUAL else "")
    return (f"{first_name}, seus {TRIAL_DAYS} dias grátis terminaram — "
            f"espero ter tirado umas boas coisas da sua cabeça. 🙂\n\n"
            f"Para continuar com lembretes ilimitados:\n"
            f"💳 Mensal (R$ 19,90): {PAYMENT_LINK}{anual}\n\n"
            f"Assim que o pagamento confirmar, eu reativo tudo aqui — "
            f"seus dados estão guardados te esperando.")


def _phone_from_jid(jid: str) -> str:
    """'5511999990000@s.whatsapp.net' -> '5511999990000'"""
    return jid.split("@")[0]


MASTER_PHONE = re.sub(r"\D", "", os.environ.get("MASTER_PHONE", ""))
# Numero PUBLICO do bot, usado na assinatura da delegacao (M1.7). Nao
# confundir com MASTER_PHONE, que e o numero do dono e serve de gate do
# comando de reset — mandar AQUELE pra terceiro seria expor o Kevin.
BOT_PHONE = re.sub(r"\D", "", os.environ.get("BOT_PHONE", ""))
_MASTER_RESET_RE = re.compile(
    r"^(reset|resetar|zerar|/reset|novo teste|reiniciar teste|sou novo)\b",
    re.IGNORECASE)


def _parse_landing_payload(text: str) -> Optional[dict]:
    """Decodifica a mensagem estruturada que a landing page envia via wa.me.
    Formato: '#RESOLVE|nome|idade|interesse1,interesse2'
    Retorna {'nome','idade','interesses'} ou None se não for payload da landing.
    """
    if not text or not text.strip().startswith("#RESOLVE"):
        return None
    try:
        _, resto = text.split("#RESOLVE", 1)
        # lstrip("|") comia TODOS os pipes da esquerda e deslocava os campos:
        # "#RESOLVE||30|contas" virava nome="30" e o bot dizia "Oi, 30!".
        if resto.startswith("|"):
            resto = resto[1:]
        partes = [p.strip() for p in resto.split("|")]
        nome = partes[0] if len(partes) > 0 and partes[0] else ""
        # Filtro FRACO de proposito: _is_not_a_name corta em 5+ palavras e em
        # virgula, o que rejeitava "Ana Carolina de Souza Lima" e fazia o bot
        # cumprimentar pelo pushName do WhatsApp.
        if nome and _nao_e_nome_de_formulario(nome):
            nome = ""
        idade_raw = partes[1] if len(partes) > 1 else ""
        idade = int(re.sub(r"\D", "", idade_raw)) if re.sub(r"\D", "", idade_raw) else None
        interesses = ""
        if len(partes) > 2 and partes[2]:
            # normaliza contra as chaves conhecidas
            valid = {"contas", "mercado", "carro", "saude", "datas",
                     "encomendas", "pet", "burocracia"}
            ints = [i.strip().lower() for i in partes[2].split(",")]
            interesses = ",".join(i for i in ints if i in valid)
        return {"nome": nome, "idade": idade, "interesses": interesses}
    except Exception:
        return None


def _get_or_create_user(phone: str, push_name: str = "") -> tuple[dict, bool]:
    """Retorna (user, is_new)."""
    for u in db.list_users():
        if re.sub(r"\D", "", u["telefone"]) == phone:
            return u, False
    uid = db.create_user(nome=push_name or f"Usuário {phone[-4:]}",
                         telefone=phone)
    # Aqui gravava onboarding_step="nome", e o reset do MASTER_PHONE passava
    # por este caminho SEM ser `is_new` — o dono resetava, mandava "oi", e a
    # mensagem ia direto pro LLM sem nenhum aceite. Dois estragos: um caminho
    # processando dado sem consentimento, e o dono testando a M1.2 sem nunca
    # ver a M1.2. Todo usuario nasce parado no aceite. Sem excecao.
    db.update_user_fields(uid, onboarding_step="lgpd_organico", status="trial")
    return db.get_user(uid), True


def _maybe_master_reset(phone: str, text: str) -> Optional[str]:
    """Se o NÚMERO MASTER mandar 'reset' (ou similar), apaga os dados dele e
    recomeça do zero — para testar cada feature como usuário novo.
    Retorna a mensagem de confirmação, ou None se não for o caso."""
    if not MASTER_PHONE or phone != MASTER_PHONE:
        return None
    if not _MASTER_RESET_RE.match(text.strip()):
        return None
    # apaga tudo desse número e recria como novo
    for u in db.list_users():
        if re.sub(r"\D", "", u["telefone"]) == phone:
            db.delete_user(u["id"])
            break
    _get_or_create_user(phone, "")
    return ("🧪 *Modo teste:* seus dados foram zerados. Você é um usuário "
            "novo agora — pode testar o fluxo desde o início. Manda um *oi*.")


# M2.5 — a frase exata, e nada perto dela. Ver o comentario no handler.
_RESET_TRIAL_RE = re.compile(
    r"^\s*resetar\s+(?:o\s+)?trial\s+(?:de\s+)?todos\s*[.!]?\s*$",
    re.IGNORECASE)


def _handle_commands(user: dict, phone: str, text: str) -> Optional[str]:
    """Comandos globais (LGPD, assinatura, admin). Retorna resposta ou None."""
    low = text.strip().lower()
    # split() em nome vazio/so espaco estoura IndexError e derruba o webhook
    # inteiro — e como _ja_processada() ja marcou a mensagem, o reenvio da
    # Meta e engolido pelo dedup e a mensagem some de vez.
    first_name = ((user.get("nome") or "").split() or [""])[0]

    # --- confirmações pendentes ------------------------------------------
    if phone in CONFIRM:
        action = CONFIRM.pop(phone)
        if action == "cancelar" and low in ("sim", "s", "confirmo"):
            db.set_status(user["id"], "cancelado")
            return (f"Assinatura cancelada, {first_name}. Sem cobrança, sem "
                    f"drama. Seus dados continuam guardados por 30 dias caso "
                    f"volte — ou mande *apagar meus dados* para sumir tudo "
                    f"agora. Foi um prazer. 👋")
        if action == "apagar" and low == "apagar":
            db.delete_user(user["id"])
            PENDING.pop(phone, None)
            PRE_ACEITE.pop(phone, None)
            RECONSENTIR.pop(phone, None)
            return ("Feito. Todos os seus dados foram apagados "
                    "permanentemente — registros, lembretes, tudo. "
                    "Se um dia quiser voltar, é só mandar um oi. 👋")
        # NAO retorna aqui. Este `return "Ok, nao fiz nada"` engolia a
        # mensagem: quem pedia "apagar meus dados", desistia, e mandava
        # "dentista dia 15 as 14h" recebia "Ok, nao fiz nada" e o item NUNCA
        # era criado — perda silenciosa. A confirmacao ja foi cancelada pelo
        # pop; a mensagem segue o fluxo e vira item, com aviso curto colado.
        CANCELADO_AVISO[phone] = tempo.agora()
        return None

    # _handle_commands roda ANTES do gate de aceite. Correto para "apagar
    # meus dados" — e a saida de emergencia. Mas levava junto "meu nome e
    # Fernanda", que gravava nome real sem consentimento, e "assinar", que
    # mandava link de pagamento pra quem nao aceitou os Termos.
    if user.get("onboarding_step") in LGPD_STEPS and low not in _CMD_PRE_ACEITE:
        return None

    # --- comandos ----------------------------------------------------------
    if low in ("cancelar", "cancelar assinatura", "quero cancelar"):
        CONFIRM[phone] = "cancelar"
        return (f"{first_name}, confirma o cancelamento da assinatura? "
                f"Responda *SIM* para confirmar ou qualquer outra coisa "
                f"para continuar comigo.")
    if low in ("apagar meus dados", "apagar dados", "excluir meus dados",
               "deletar meus dados"):
        CONFIRM[phone] = "apagar"
        return ("⚠️ Isso apaga *permanentemente* tudo: registros, lembretes "
                "e seu cadastro (LGPD). Não tem volta.\n\n"
                "Responda *APAGAR* para confirmar ou qualquer outra coisa "
                "para cancelar.")
    if low in ("assinar", "planos", "quero assinar", "pagar"):
        anual = (f"\n📅 Anual (R$ 149 ≈ R$ 12,40/mês): {PAYMENT_LINK_ANUAL}"
                 if PAYMENT_LINK_ANUAL else "")
        return (f"Bora, {first_name}! 🚀\n"
                f"💳 Mensal (R$ 19,90): {PAYMENT_LINK}{anual}\n\n"
                f"Pagou, me avisa aqui que eu ativo na hora.")
    # M1.6 — "meus dados" responde com NUMERO, nao com juridiques.
    if low in ("meus dados", "meus dado"):
        try:
            _ativos = len(db.list_items(user["id"], status="pendente"))
            _hist = db.resumo_historico(user["id"])
            _lac = db.ultimo_lacre()
        except Exception:
            # Auditoria v23.0: devolver zeros aqui e MENTIR numa resposta de
            # LGPD. A pessoa via "0 itens ativos" com cara de verdade e nao
            # tinha como saber que o banco falhou. Regra #5: erro nao passa
            # calado.
            import logging
            logging.getLogger("resolveai").warning(
                "[lgpd] falha ao montar 'meus dados' do user %s",
                user.get("id"), exc_info=True)
            return ("Não consegui ler seus dados agora — deu erro do meu "
                    "lado. \U0001F615\n\nTenta de novo daqui a pouco. Se "
                    "insistir, me chama que eu vejo na mão.")
        _linhas = ["\U0001F512 *Seus dados, em número:*", ""]
        _linhas.append("• " + str(_ativos) + " itens ativos")
        _linhas.append("• Áudio eu transcrevo e descarto")
        if _hist.get("qtd"):
            _v = ("R$ " + f"{_hist['soma']:,.2f}"
                  .replace(",", "X").replace(".", ",").replace("X", "."))
            _linhas.append("• Já fechei " + str(_hist["qtd"]) +
                           " coisas com você (" + _v + ")")
        if _lac:
            _linhas.append("• Em " + str(_lac["quando"])[:10] + " apaguei " +
                           str(_lac["itens"]) + " concluídos antigos")
        else:
            _linhas.append("• Concluído com mais de 90 dias\n  eu apago sozinho")
        _linhas += ["", "Guardo o resumo em número, não o texto.",
                    "", "Manda *apagar meus dados* que some tudo, na hora."]
        return "\n".join(_linhas)

    if low in ("privacidade", "termos", "lgpd"):
        return ("🔒 *Privacidade em 4 linhas:*\n"
                "• Suas mensagens, fotos e áudios são processados por IA "
                "(OpenAI, servidores no exterior) só para te atender.\n"
                "• Nunca vendemos nem compartilhamos seus dados.\n"
                "• Eu *lembro* você de pagar — nunca pago, compro ou "
                "transfiro nada.\n"
                "• *apagar meus dados* remove tudo, na hora (LGPD).\n\n"
                f"Termos completos: {TERMS_URL}")
    # M1.7 (delegacao) e M1.3 (kits) SAIRAM daqui — auditoria v23.0, P1-2.
    # _handle_commands roda ANTES dos gates de acesso (~1795) e e chamado
    # ANTES do aceite LGPD (~1650). Aqui dentro, "kits" respondia pra quem o
    # admin bloqueou e pra quem ainda nao aceitou os termos. O gate existe
    # exatamente pra isso. Os dois blocos foram pro handle_incoming, depois
    # de todo mundo passar no gate. NAO trazer de volta pra ca.
    if low in ("ajuda", "menu", "comandos"):
        return ("Eu entendo linguagem natural — manda texto, áudio ou foto "
                "do seu jeito. Comandos úteis:\n"
                "*assinar* · *cancelar* · *apagar meus dados* · "
                "*privacidade* · *ajuda*")

    # --- "mais tempo": auto-extensão única do trial -------------------------
    # A régua promete "responde *mais tempo* que eu libero". Promessa feita
    # pelo bot que só o dono consegue cumprir é promessa quebrada — e no beta
    # o custo de dar 7 dias a mais é zero perto de perder o feedback da
    # pessoa. Uma vez por usuário, registrado no log de disparos.
    if _MAIS_TEMPO_RE.match(text.strip()):
        if (user.get("status") or "trial") != "trial":
            return None            # assinante não precisa; deixa o motor falar
        if db.dispatched_ever("extensao-trial", user["id"]):
            faltam = db.trial_days_left(user, TRIAL_DAYS)
            return (f"Já te dei uma extensão, {user['nome'].split()[0]} — "
                    f"restam *{faltam} dia(s)*. Se precisar de mais, me fala "
                    f"que eu aviso o Kevin. 🙂")
        # QUEM MARCA O DEDUP E QUEM EXECUTOU. O retorno era ignorado e o
        # `log_dispatch` gravava do mesmo jeito: com o UPDATE falhando, a
        # pessoa lia "liberei +7 dias" (o `faltam` relia o usuario nao
        # alterado) e o `dispatched_ever` a bloqueava PARA SEMPRE — a
        # extensao e uma por usuario. E a regra que o CLAUDE.md registra
        # como o defeito mais caro daqui, no ultimo caminho onde ela ainda
        # estava aberta (auditoria M2.5, rodada 3).
        if not db.admin_extend_trial(user["id"], TRIAL_EXTENSAO_DIAS):
            return ("Não consegui liberar os dias agora — o erro está no "
                    "log e *nada foi gasto*. Me chama daqui a pouco que eu "
                    "tento de novo.")
        db.log_dispatch(user["id"], "extensao-trial")
        faltam = db.trial_days_left(db.get_user(user["id"]), TRIAL_DAYS)
        return (f"Feito. ✅ Liberei *+{TRIAL_EXTENSAO_DIAS} dias* pra você — "
                f"agora são *{faltam} dia(s)* de teste.\n\n"
                f"Aproveita pra me dar uma conta com vencimento: é quando eu "
                f"te aviso sozinho que você vê pra que eu sirvo.")

    # --- "meu nome é X" / "me chama de X" ----------------------------------
    # v16.3. Nasceu do bug do "Feito", mas não é remendo: o usuário TEM que
    # poder corrigir como o bot o chama, sem depender do dono do sistema mexer
    # no banco. É a única informação que o bot repete em toda mensagem.
    # Em Python e não no LLM: um nome trocado errado é pior que nome errado.
    m = _RENOMEAR_RE.match(text.strip())
    # No REPROCESSO da fila pre-aceite, nao. O texto guardado e antigo e a
    # pessoa ja respondeu o nome depois dele, no passo do cadastro.
    if m and phone in EM_REPROCESSO:
        m = None
    if m:
        novo = m.group("nome").strip(" .!?,;\"'")[:60]
        if _is_not_a_name(novo) or len(novo) < 2:
            return (f"Esse não parece um nome — não vou te chamar de "
                    f"_\"{novo}\"_. 😅 Manda de novo: *meu nome é ...*")
        anterior = user.get("nome") or ""
        db.update_user_fields(user["id"], nome=novo)
        # nome corrigido no meio do cadastro conclui o passo
        if user.get("onboarding_step") == "nome":
            db.update_user_fields(user["id"], onboarding_step="interesses")
            return _interesses_menu(novo.split()[0])
        return (f"Pronto, agora é *{novo.split()[0]}*. "
                f"{'Eu vinha te chamando de ' + anterior.split()[0] + ' — foi mal. ' if anterior and anterior.split()[0].lower() != novo.split()[0].lower() else ''}"
                f"Anotado pra sempre. ✅")

    # --- admin: reset de trial da base inteira (M2.5) -----------------------
    #
    # PORTA ESTREITA, e nao por preciosismo: este comando escreve em TODA a
    # base de uma vez. "me lembra de resetar o trial amanha" e um LEMBRETE,
    # e um `startswith("resetar")` transformaria essa frase numa acao de
    # banco — o mesmo modo de falha do menu 1/2 que custou a FASE 1 inteira.
    # Por isso: frase exata, e so do numero do dono.
    if ADMIN_PHONE and phone == ADMIN_PHONE and _RESET_TRIAL_RE.match(text):
        alvos = [u["id"] for u in db.list_users()
                 if re.sub(r"\D", "", u.get("telefone") or "") != ADMIN_PHONE]
        try:
            tocados = db.resetar_trial(alvos, por=phone)
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[admin] reset de trial falhou", exc_info=True)
            return ("Não consegui resetar agora — o erro está no log. "
                    "*Nenhum trial foi alterado.*")
        if not tocados:
            return ("Nenhum trial pra resetar: todo mundo já foi resetado "
                    "hoje (ou cancelou). Nada foi alterado.")
        return (f"♻️ *{len(tocados)} pessoa(s)* voltaram a ter *14 dias* de "
                f"teste, contados de hoje.\n\n"
                f"Item, lembrete e histórico: nada foi tocado. Quem cancelou "
                f"não voltou. Rodar de novo hoje não muda mais nada.")

    # --- admin: "ativar 5511999990000" -------------------------------------
    if ADMIN_PHONE and phone == ADMIN_PHONE and low.startswith("ativar"):
        target = re.sub(r"\D", "", text)
        for u in db.list_users():
            if re.sub(r"\D", "", u["telefone"]) == target:
                db.set_status(u["id"], "ativo")
                return f"✅ Assinatura de {u['nome']} ({target}) ativada."
        return f"Número {target} não encontrado."

    return None


# Quantos dias a auto-extensão libera (1x por usuário).
TRIAL_EXTENSAO_DIAS = int(os.environ.get("TRIAL_EXTENSAO_DIAS", "7"))

_MAIS_TEMPO_RE = re.compile(
    r"^\s*(?:mais\s+tempo|preciso\s+de\s+mais\s+tempo|"
    r"me\s+d[áa]\s+mais\s+tempo|quero\s+mais\s+tempo|"
    r"estender\s+(?:o\s+)?(?:teste|trial)|mais\s+dias|"
    r"prorrogar(?:\s+o\s+(?:teste|trial))?)\s*[.!]?\s*$",
    re.IGNORECASE)

_RENOMEAR_RE = re.compile(
    r"^\s*(?:meu\s+nome\s+(?:é|e|eh)|me\s+chama(?:r)?\s+de|"
    r"pode\s+me\s+chamar\s+de|na\s+verdade\s+(?:meu\s+nome\s+(?:é|e)|"
    r"me\s+chamo)|meu\s+nome\s+n[ãa]o\s+é\s+.*?[,;]\s*(?:é|e)|"
    r"me\s+chamo|prefiro\s+ser\s+chamad[oa]\s+de|"
    r"troca\s+meu\s+nome\s+(?:para|pra))\s+(?P<nome>.{2,60})$",
    re.IGNORECASE)

_LOOKS_LIKE_QUESTION = re.compile(
    r"(\?|^(quem|qual|quais|quando|onde|como|quanto|quantos|quantas|"
    r"porque|por que|pq)\b|^(me\s+)?(lembr|anota|marca|avisa|agenda))",
    re.IGNORECASE)


_SAUDACOES = {"oi", "ola", "olá", "opa", "eai", "eaí", "e ai", "e aí", "eii",
              "ei", "eae", "iai", "aí", "ai", "psiu", "psit", "oie", "oi!",
              "bom dia", "boa tarde", "boa noite", "hey", "hello", "hi", "alo",
              "alô", "oi tudo bem", "tudo bem", "blz", "beleza", "salve",
              "coé", "cue", "fala", "fala ai", "fala aí", "yo", "test",
              "teste", "testando", "oii", "oiii", "olar", "helloo"}

# palavras que NUNCA são nome (verbos/comandos comuns no início)
#
# v16.3 — ESTA LISTA CUSTOU CARO.
# Em produção o usuário 23 estava cadastrado com o nome "Feito": ele respondeu
# "feito" (dando baixa num lembrete) enquanto o onboarding esperava o nome, e
# o validador deixou passar porque só conhecia saudação e verbo de pedido —
# não conhecia as PRÓPRIAS PALAVRAS DE COMANDO do bot. A partir daí todo
# resumo, todo alarme e todo bom-dia saíam com "Bom dia, Feito.".
# Regra: se a palavra é um comando que o bot entende, ela não pode ser nome.
_COMANDOS_DO_BOT = {"feito", "feita", "pronto", "pronta", "pago", "paga",
                    "paguei", "resolvido", "resolvida", "concluido",
                    "concluído", "concluida", "concluída", "ok", "okay",
                    "adiar", "adia", "adiado", "cancelar", "cancela",
                    "apagar", "apaga", "deletar", "remover", "listar",
                    "lista", "itens", "status", "parar", "pausar", "sair",
                    "assinar", "pagar", "comprar", "sim", "não", "nao",
                    "nenhum", "nada", "tudo", "todos", "reset", "resetar",
                    # M1.2 — as palavras dos botoes de aceite. Sem isto, o
                    # segundo toque em "Concordo" (o botao continua clicavel,
                    # e o msg_id e outro, entao o dedup nao pega) chega no
                    # passo "nome" e a pessoa passa a se chamar "Concordo".
                    # E o bug do "Feito" da v16.3, no passo mais critico.
                    "concordo", "concorda", "concordou", "aceito", "aceita",
                    "aceitar", "acordo", "discordo", "discorda", "recuso",
                    "recusa", "confirmo", "confirma", "termos", "privacidade"}
_NAO_NOME_PALAVRAS = {"quero", "preciso", "pode", "queria", "gostaria",
                      "me", "legal", "bora", "vamos", "help", "ajuda",
                      "menu", "start", "começar", "comecar", "obrigado",
                      "obrigada", "valeu", "vlw", "certo", "claro",
                      "entendi", "aham", "uhum", "kkk", "kk", "haha",
                      } | _COMANDOS_DO_BOT


def _is_not_a_name(text: str) -> bool:
    """True se o texto claramente NÃO é um nome (saudação, pergunta, comando,
    frase longa, ou palavra funcional)."""
    t = text.strip()
    low = t.lower().strip("!?.,;")
    if low in _SAUDACOES or low in _NAO_NOME_PALAVRAS:
        return True
    # TITULO DE BOTAO COM EMOJI. O texto que volta do clique e o titulo
    # INTEIRO, emoji incluido; sem tirar o emoji "Concordo" passa como nome.
    _limpo = re.sub(r"[^\wÀ-ÿ\s]", "", low).strip()
    if _limpo and (_limpo in _NAO_NOME_PALAVRAS or _limpo in _SAUDACOES):
        return True
    if _limpo.split() and _limpo.split()[0] in _COMANDOS_DO_BOT:
        return True
    # Rotulo de botao de confirmacao. Reusa as MESMAS regex que interpretam
    # o clique, em vez de manter uma segunda lista que sai de sincronia.
    for _re_ack in (_ACK_CONFIRMOU_RE, _ACK_MUDAR_RE, _ACK_ADD_RE):
        if _re_ack.match(t):
            return True
    if _LOOKS_LIKE_QUESTION.search(t):
        return True
    if "," in t:                   # frase com vírgula não é nome
        return True
    palavras = t.split()
    if len(palavras) > 4:          # nome não tem 5+ palavras
        return True
    # primeira palavra é comando/verbo comum? não é nome
    if palavras and palavras[0].lower().strip("!?.,;") in _NAO_NOME_PALAVRAS:
        return True
    # 1 palavra curtinha (<=3 letras) e minúscula: quase sempre interjeição
    # ("eii", "aí", "yo"). Nomes reais curtos ("Ana", "Bia") vêm com maiúscula.
    nomes_curtos_ok = {"ana", "bia", "gal", "leo", "rui", "ivo", "noe"}
    if (len(palavras) == 1 and len(low) <= 3
            and t.islower() and low not in nomes_curtos_ok):
        return True
    return False


def _handle_onboarding(user: dict, text: str) -> Optional[str]:
    """Fluxo conversacional de cadastro. Retorna resposta ou None se concluído.
    IMPORTANTE: não sequestra perguntas/comandos — se o usuário pergunta algo
    no meio do cadastro, devolve None para o motor responder, sem travar."""
    step = user.get("onboarding_step")
    if not step:
        return None
    if step in LGPD_STEPS:
        # O aceite e resolvido em _resolver_aceite, chamado de dentro do
        # handle_incoming — la existe acesso ao content, que e o que permite
        # REPROCESSAR a mensagem guardada antes do aceite. Aqui so blindamos.
        return jornada.LGPD_AVISO.format(termos=TERMS_URL)
    if step == "nome":
        # Se claramente é uma pergunta/comando, não trata como nome:
        # deixa o motor responder e repete o convite do nome depois.
        if _is_not_a_name(text):
            resposta_motor = _answer_and_reprompt_name(user, text)
            return resposta_motor
        nome = text.strip().split("\n")[0][:60]
        if len(nome) < 2:
            return "Não peguei — como você quer ser chamado?"
        db.update_user_fields(user["id"], nome=nome,
                              onboarding_step="interesses")
        return _interesses_menu(nome.split()[0])
    if step == "interesses":
        low = text.strip().lower()
        # pergunta no meio? responde e mantém no passo de interesses
        if _LOOKS_LIKE_QUESTION.search(text) and low not in ("pular", "depois"):
            eng = ai_engine.converse(user["id"], user["nome"].split()[0],
                                     "texto", text)
            base = eng.get("reply", "")
            return (base + "\n\n_Voltando ao cadastro: me diz os números do "
                    "que te interessa (ex.: *1 3 7*) ou responda *pular*._")
        keys = [] if low in ("pular", "depois") else _parse_interesses(text)
        db.update_user_fields(user["id"], interesses=",".join(keys) or None,
                              onboarding_step=None)
        return _onboarding_done_msg(user["nome"].split()[0], keys)
    return None


_KITS_RE = re.compile(
    r"^\s*(?:kits?|montar\s+(?:a\s+)?rotina|minha\s+rotina|rotina)"
    r"\s*[.!?]?\s*$", re.I)


def _enviar_lista_kits(user: dict, phone: str, corpo: str = "") -> str:
    """Manda os Kits de Rotina como lista interativa. (M1.3)

    Devolve "" quando a lista saiu (nada mais a responder) ou um TEXTO de
    fallback se a Meta recusou o interativo.

    O fallback nao e enfeite: jornada.enviar_lista devolve False quando a
    Meta recusa (fora da janela de 24h, credencial, formato). Sem ele a
    pessoa pede "kits" e nao recebe nada — falha silenciosa, que e o
    defeito que este projeto mais persegue.
    """
    import casos_de_uso
    # Ordena pelos interesses que ela marcou na landing: quem escolheu
    # "carro" ve o kit do carro primeiro. Nenhum kit some.
    linhas = casos_de_uso.linhas_kits(user.get("interesses") or "")
    corpo = corpo or ("Bora tirar mais coisa da sua cabe\u00e7a. \U0001F9E0\n\n"
                      "Escolhe uma frente que eu monto\n"
                      "com voc\u00ea, uma pergunta por vez.")
    KIT_ETAPA.pop(phone, None)
    if jornada.enviar_lista(phone, corpo, "Ver os kits", linhas,
                            titulo="Kits de Rotina"):
        KIT_ETAPA[phone] = {"kit": None, "quando": tempo.agora()}
        try:
            db.log_message(user.get("id"), phone, "out", "texto",
                           "[lista] Kits de Rotina")
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[kits] falha ao logar a lista no painel", exc_info=True)
        return ""
    # Meta recusou o interativo: cai pra texto numerado, sem perder o pedido.
    KIT_ETAPA[phone] = {"kit": None, "quando": tempo.agora()}
    itens = "\n".join("*" + str(n + 1) + "* " + k[1]
                      for n, k in enumerate(casos_de_uso.KITS))
    return (corpo + "\n\n" + itens +
            "\n\n_Responda com o n\u00famero._")


def _resposta_de_kit(user: dict, phone: str, text: str):
    """Fluxo de 2 passos dos Kits. Deterministico, em Python.

    Passo 1: tocou no kit  -> pergunta QUAL item (lista de opcoes)
    Passo 2: tocou na opcao -> faz UMA pergunta, com exemplo pronto

    Nao cria item aqui de proposito. Quem cria e o motor, com a frase que
    ela mandar no passo seguinte — assim o item nasce com data, e item sem
    data nao avisa ninguem. O kit tira o trabalho de LEMBRAR o que
    cadastrar; nao tira o trabalho de dizer quando.
    """
    # Auditoria v23.2: a guarda de LGPD estava so na ETAPA 1 (lista de
    # kits). A etapa 2 era alcancavel por outra porta: usuario da base
    # antiga criava o 1o item, recebia o convite e respondia "sim" — e
    # levava os kits sem nunca ter aceitado os termos. Guarda na origem
    # cobre convite e KIT_ETAPA de uma vez.
    if not user or not user.get("lgpd_aceite_em"):
        return None
    import casos_de_uso
    t = (text or "").strip()
    if not t:
        return None

    # "sim" logo depois da oferta pos-primeiro-item
    _conv = KIT_CONVITE.get(phone)
    if _conv and _KIT_SIM_RE.match(t):
        try:
            if (tempo.agora() - _conv).total_seconds() < KIT_CONVITE_S:
                KIT_CONVITE.pop(phone, None)
                return _enviar_lista_kits(user, phone)
        except Exception:
            KIT_CONVITE.pop(phone, None)

    estado = KIT_ETAPA.get(phone) or {}
    fresco = False
    try:
        fresco = (tempo.agora() - estado["quando"]).total_seconds() < KIT_JANELA_S
    except Exception:
        fresco = False

    # --- passo 2: ja escolheu o kit, agora escolheu a opcao --------------
    if fresco and estado.get("kit"):
        kit = casos_de_uso.kit_por_id(estado["kit"])
        opc = casos_de_uso.opcao_por_rotulo(kit, t)
        if not opc and re.fullmatch(r"[1-9]", t):
            try:
                opc = kit[3][int(t) - 1]
            except Exception:
                opc = None
        if opc:
            KIT_ETAPA.pop(phone, None)
            return casos_de_uso.texto_passo2(kit, opc)

    # --- passo 1: escolheu o kit ----------------------------------------
    kit = casos_de_uso.kit_por_rotulo(t)
    if not kit and fresco and estado.get("kit") is None \
            and re.fullmatch(r"[1-9]", t):
        # numero SO vale logo depois da lista ter sido mostrada: um "3"
        # solto no meio de qualquer conversa nao pode virar kit.
        try:
            kit = casos_de_uso.KITS[int(t) - 1]
        except Exception:
            kit = None
    if not kit:
        return None

    KIT_ETAPA[phone] = {"kit": kit[0], "quando": tempo.agora()}
    linhas = casos_de_uso.linhas_opcoes(kit)
    corpo = casos_de_uso.texto_passo1(kit)
    if jornada.enviar_lista(phone, corpo, "Escolher", linhas,
                            titulo=kit[1][:24]):
        try:
            db.log_message(user.get("id"), phone, "out", "texto",
                           "[lista] " + kit[1])
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[kits] falha ao logar opcoes no painel", exc_info=True)
        return ""
    opts = "\n".join("*" + str(n + 1) + "* " + o
                     for n, o in enumerate(kit[3]))
    return corpo + "\n\n" + opts + "\n\n_Responda com o n\u00famero._"


# M1.5 — respostas do escalonamento (3 adiamentos).
_SNOOZE_REMARCAR_RE = re.compile(
    r"^\s*(?:\U0001F4C5\s*)?remarcar\s*[.!]?\s*$", re.I)
_SNOOZE_TIRAR_RE = re.compile(
    r"^\s*(?:\u2716\uFE0F?\s*)?tirar\s+da\s+lista\s*[.!]?\s*$", re.I)
# Sinonimos de adiar, pra CONTAR o snooze. O ai_engine ja trata o adiamento
# em si; aqui so registramos que aconteceu — sem contador nao existe 3a vez.
_ADIOU_RE = re.compile(
    r"^\s*(?:adiar|adia|depois|mais\s+tarde|amanh[aã]|"
    r"deixa\s+pra\s+(?:depois|amanh[aã]))\b", re.I)


def _resposta_de_snooze(user: dict, phone: str, text: str):
    """M1.5 — a pessoa respondeu ao escalonamento das 3 vezes.

    Duas saidas, as duas honestas: remarcar ou sair da lista. Insistir uma
    quarta vez seria o bot virando a voz que cobra — o oposto do que ele
    vende. Quem adiou tres vezes ja disse alguma coisa; o certo e perguntar
    o que, nao repetir mais alto.
    """
    t_ = (text or "").strip()
    if not t_:
        return None
    try:
        alvo = db.ultimo_alarme_disparado(user["id"]) or db.ultimo_item(user["id"])
    except Exception:
        alvo = None
    if not alvo:
        return None
    desc = (alvo.get("descricao") or "isso").strip()

    if _SNOOZE_TIRAR_RE.match(t_):
        # NAO apaga: conclui. Perder item de usuario e o pior defeito
        # possivel — e ela pode querer ver depois que resolveu.
        try:
            db.update_item_status(alvo["id"], "concluido")
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[snooze] falha ao tirar da lista", exc_info=True)
        return ("Tirei *" + desc + "* da lista. \u2705\n\n"
                "Se voltar a fazer sentido, é só me falar de novo.")

    if _SNOOZE_REMARCAR_RE.match(t_):
        return ("Beleza. Pra quando?\n"
                "_\"sexta 9h\"_ · _\"dia 20\"_\n\n"
                "Se a hora tava ruim, me diz outra que\n"
                "eu passo a te chamar nela.")
    return None


# M2.0 — as formas de pedir a lista. UMA fonte: o regex de `consulta_agenda`
# no ai_engine reusa esta lista (auditoria M2.0, P2-6: duas listas escritas
# à mão já divergiam em "vertudo" e "ver lista").
LISTA_COMANDOS = ("ver tudo", "vertudo", "ver lista", "lista", "listar",
                  "itens", "pendentes", "minha lista")
_LISTA_RE = re.compile(
    r"^\s*(?:" + "|".join(c.replace(" ", r"\s+") for c in LISTA_COMANDOS)
    + r")\s*[.!?]*\s*$", re.I)


_ACK_CONFIRMOU_RE = re.compile(
    r"^\s*(?:\u2705\s*)?isso\s*mesmo\s*[.!]?\s*$", re.I)
_ACK_MUDAR_RE = re.compile(
    r"^\s*(?:\u270F\uFE0F?\s*)?(?:quero\s+mudar|mudar(?:\s+a\s+data)?)\s*[.!]?\s*$",
    re.I)
_ACK_ADD_RE = re.compile(
    r"^\s*(?:\u2795\s*)?(?:quero\s+adicionar\s+outro|add\s+outro|"
    r"adicionar\s+outro)\s*[.!]?\s*$", re.I)


def _resposta_de_botao(user: dict, phone: str, text: str) -> Optional[str]:
    """Responde ao CLIQUE nos botoes de confirmacao. Em Python, nao no LLM.

    BUG REAL DE PRODUCAO (visto no painel, 11/08):
        10:52 usuario  "me lembra de olhar os ponto as 11:20"
        10:52 bot      "Anotado. Voce pediu pra olhar os pontos as 11:20."
        10:53 usuario  [toca Isso mesmo]
        10:53 bot      "Como vai?"                    <- do nada
    e tambem:
        08:22 bot      "Anotado. Guardei: comprar chocolates"
        08:22 usuario  [toca Isso mesmo]
        08:22 bot      "O que voce gostaria de fazer agora?"

    Causa: botoes.py monta cada botao como (titulo, payload), mas enviar()
    so transmite o TITULO — e meta_cloud.to_evolution_shape devolve esse
    titulo como {"conversation": ...}, ou seja texto livre. Ninguem trata,
    e o LLM, que nao tem como saber que aquilo e um ACK, puxa conversa.

    "Isso mesmo" e FIM de assunto: o item ja esta guardado, a pessoa so
    confirmou. Resposta curta e terminal, sem pergunta nova. Cada pergunta
    desnecessaria e uma vibracao a mais no celular de alguem que abriu o
    app pra ter MENOS coisa na cabeca.

    As regex casam SO os rotulos que botoes.py emite (titulo E payload).
    Aceitar "perfeito", "correto" ou "confirmo" seria sequestrar fala livre
    — e responder "ta guardado" pra quem nao guardou nada.
    O \u270F vem com VARIATION SELECTOR-16 do WhatsApp; por isso o \uFE0F?.
    """
    t = (text or "").strip()
    if not t:
        return None
    if _ACK_CONFIRMOU_RE.match(t):
        return "Perfeito, t\u00e1 guardado. \U0001F44D"
    if _ACK_MUDAR_RE.match(t):
        try:
            ultimo = db.ultimo_item(user["id"]) or {}
        except Exception:
            ultimo = {}
        desc = (ultimo.get("descricao") or "").strip()
        if desc:
            return ("Beleza — o que muda em *" + desc + "*?\n"
                    "Pode mandar s\u00f3 o que est\u00e1 errado "
                    "(ex.: _na verdade \u00e9 dia 20_).")
        return "Beleza — me manda o que est\u00e1 errado que eu ajusto."
    if _ACK_ADD_RE.match(t):
        return "Manda a pr\u00f3xima. \U0001F442"
    return None


# P0-1 (12, 13 e 14/08) — "feito" nao dava baixa.
#
#   Bot:   chegou a hora: Estudar Product Manager
#          Responda feito que eu dou baixa, ou adiar 1h.
#   Kevin: feito
#   Bot:   Nao entendi. Responda *1* (despesa paga), *2* (agendar lembrete)
#
# O bot pedia a palavra e recusava a palavra, tres dias seguidos. Causa
# provada por execucao: o bloco de decisao pendente (PENDING) roda antes de
# tudo e manda a mensagem pro menu 1/2, que nao conhece a palavra "feito".
#
# Baixa e regra de negocio, entao e Python (regra 2 do CLAUDE.md) e vem ANTES
# de qualquer PENDING. A palavra tem que estar SOZINHA na mensagem: "o bolo ta
# feito de chocolate" nao e baixa. E so vale se um alarme REALMENTE tocou nas
# ultimas 12h — sem isso volta o caso Fabio (05/08), em que "Feito" queria
# dizer "terminei de listar" e o bot apagou a lista inteira da pessoa.
# AUDITORIA v23.4 (P0-2 do auditor): o scheduler manda, com todas as letras,
#   "Responde *feito* + o nome do que ja resolveu"        (scheduler.py:278)
#   "Responda *feito* + o nome do item que eu dou baixa"  (scheduler.py:574)
# A primeira versao deste regex exigia a palavra SOZINHA — ou seja, recusava
# de novo a forma que o proprio bot pede, justamente na mensagem que sai
# quando a pessoa tem varias coisas vencidas. Agora a cauda e aceita e vira
# busca pelo item; se ela nao apontar pra exatamente UM pendente, ninguem
# conclui nada e a mensagem segue o fluxo normal.
#
# A cauda de pontuacao aceita emoji DEPOIS da palavra ("feito ✅", "feito 👍"):
# no WhatsApp mobile esse e o jeito mais natural de responder, e a versao
# anterior so aceitava emoji ANTES.
_BAIXA_RE = re.compile(
    r"^\s*(?:✅\s*)?"
    r"(feito|feita|pronto|pronta|fiz|ja\s+fiz|j[áa]\s+fiz|ja\s+foi|j[áa]\s+foi|"
    r"t[áa]\s+feito|ta\s+feito|resolvi|resolvido|resolvida|conclui|conclu[íi]|"
    r"conclu[íi]do|terminei|quitei|pago|paga|paguei|ja\s+paguei|j[áa]\s+paguei)"
    r"\b(?P<cauda>.*)$", re.I)

# Fim de mensagem que e so pontuacao/emoji — nao e nome de item.
_SO_ENFEITE_RE = re.compile(r"^[\W_]*$", re.UNICODE)

# Palavras de baixa que TAMBEM sao resposta legitima do menu 1/2 da imagem
# ("1 = despesa paga"). Quando existe decisao pendente mais recente que o
# alarme, o menu ganha — ver _baixa_deterministica.
_BAIXA_AMBIGUA = {"pago", "paga", "paguei", "ja paguei", "já paguei"}


# A pessoa mandou palavra de baixa, mas o Python NAO consegue apontar um
# item unico. Nao e "nao e baixa" (ai o LLM assumiria e chutaria, que foi o
# estrago do 14/08) nem "e este aqui". E "pergunte".
AMBIGUO = object()
# "feito" sozinho: nao ha nome pra casar, o alvo e o item do alarme.
SEM_CAUDA = object()

# Palavras que aparecem na cauda mas nao nomeiam item nenhum.
_CAUDA_SEM_NOME = {"isso", "esse", "essa", "aquele", "aquela", "tudo", "ja",
                   "já", "agora", "hoje", "ontem", "com", "sim", "nao", "não",
                   "que", "pra", "por", "dos", "das", "meu", "minha"}

# Escolha pendente de baixa: telefone -> {"ids": [...], "quando": datetime}.
# Sem isto a pergunta "qual deles?" nao tem saida — a pessoa responde, o bot
# reavalia a mesma cauda ambigua e devolve a mesma pergunta. Numero e a saida
# mais curta (regra 7: menos digitacao, menos ambiguidade).
BAIXA_ESCOLHA: dict[str, dict] = {}
BAIXA_ESCOLHA_TTL_S = 600


def _sem_acento(palavra: str) -> str:
    """"agua" tem que casar com "Água".

    AUDITORIA v23.4 rodada 3 (P1-2): o placar comparava com acento, entao
    "feito agua" nao casava com "Conta de Água", o caminho deterministico
    devolvia None e a decisao voltava pro motor — justamente o que o P0-2
    existia pra evitar. No WhatsApp brasileiro digitar sem acento e a regra,
    nao a excecao.
    """
    return "".join(c for c in unicodedata.normalize("NFD", palavra.lower())
                   if unicodedata.category(c) != "Mn")


def _baixa_sem_alvo(user: dict, texto: str) -> bool:
    """Casou palavra de baixa, tem cauda, e o Python NAO achou o item.

    AUDITORIA v23.4 rodada 4 (P1-1): a versao anterior perguntava "isso
    parece frase?" (virgula ou mais de 4 palavras) — e por isso deixava
    passar "feito o pagamento da luz" e "fiz o cadastro no site", que
    fechavam o item errado no caminho degradado. A pergunta certa nao e
    sobre a forma da mensagem: e se o Python conseguiu apontar o item. Se
    ele nao conseguiu, o LLM tambem nao pode concluir.

    Sem cauda ("feito" sozinho) devolve False de proposito: ai a baixa e
    legitima e quem resolve o alvo e o caminho de sempre.

    ASSIMETRIA PROPOSITAL (auditoria v23.4 rodada 5, P2-1): aqui NAO existe
    o portao do alarme que o `_alvo_da_baixa` aplica. Sem alarme tocado, o
    caminho deterministico se recusa a fechar qualquer coisa (caso Fabio),
    mas o LLM continua autorizado quando a pessoa NOMEIA o item — "paguei a
    conta de luz" tem que dar baixa mesmo sem alarme nenhum, e isso esta na
    lista de capacidades que o bot anuncia. O que a lista do Fabio precisa e
    que "Feito" SOZINHO nao feche nada, e isso continua garantido.
    """
    m = _BAIXA_RE.match((texto or "").strip())
    if not m:
        return False
    achado = _casar_cauda(user, m.group("cauda"))
    if achado is SEM_CAUDA:
        return False
    return achado is None or achado is AMBIGUO


def _alvo_da_baixa(user: dict, cauda: str):
    """Qual item a pessoa quis fechar: o do alarme, ou o que ela nomeou.

    Sem cauda -> o item cujo alarme tocou (comportamento do alarme unico).
    Com cauda -> tem que apontar pra exatamente UM pendente. Zero ou dois
    significa que o bot NAO sabe, e chutar aqui e concluir item errado —
    o estrago do 14/08.
    """
    try:
        alarmado = db.ultimo_alarme_disparado(user["id"])
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[baixa] falha ao buscar o alarme mais recente", exc_info=True)
        return None
    if not alarmado:
        return None            # sem alarme nao existe baixa (caso Fabio)
    achado = _casar_cauda(user, cauda)
    return alarmado if achado is SEM_CAUDA else achado


def _casar_cauda(user: dict, cauda_bruta: str):
    """Casa o que veio depois da palavra de baixa contra os pendentes.

    Devolve: o item | AMBIGUO (mais de um) | None (nenhum) | SEM_CAUDA.
    """
    # A VIRGULA E TESTADA NA CAUDA BRUTA. O strip abaixo comia justamente a
    # virgula colada na palavra ("feito, me avisa" virava "me avisa") e a
    # metade "virgula" da regra nunca valia nesse caso.
    tem_virgula = "," in (cauda_bruta or "")
    cauda = (cauda_bruta or "").strip(" .!,…✅\U0001F44D")
    if not cauda or _SO_ENFEITE_RE.match(cauda):
        return SEM_CAUDA
    # Frase, nao nome: "feito isso, me avisa" nao nomeia item nenhum — e
    # alguem combinando uma proxima etapa.
    if tem_virgula or len(cauda.split()) > 4:
        return None
    palavras = {_sem_acento(p) for p in re.findall(r"\w+", cauda)
                if len(p) >= 3 and _sem_acento(p) not in _CAUDA_SEM_NOME}
    if not palavras:
        return None
    try:
        pendentes = db.list_items(user["id"], status="pendente")
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[baixa] falha ao listar pendentes", exc_info=True)
        return None

    # AUDITORIA v23.4 rodada 2 (P0-2): decidir por "casou / nao casou" fazia
    # "feito conta de luz" empatar com "conta de agua" — as duas tem "conta" —
    # e o bot devolvia a MESMA pergunta pra sempre, inclusive quando a pessoa
    # respondia exatamente o rotulo que ele acabou de listar. Vocabulario de
    # conta e curto (luz, agua, gas, PIX), entao o corte de 4 letras apagava
    # justamente o que distingue. Agora conta SOBREPOSICAO, com corte em 3.
    placar = []
    for item in pendentes:
        alvo_pal = {_sem_acento(p) for p in re.findall(
            r"\w+", item.get("descricao") or "")}
        n = sum(1 for p in palavras
                if p in alvo_pal or any(len(a) >= 4 and len(p) >= 4
                                        and a[:4] == p[:4] for a in alvo_pal))
        if n:
            # desempate: em empate de pontos, ganha a descricao com menos
            # palavra sobrando — o nome EXATO vence o superset ("conta de
            # luz" ganha de "conta de luz do escritorio").
            placar.append((n, -max(0, len(alvo_pal) - n), item))
    if not placar:
        # AUDITORIA v23.4 rodada 2 (P0-1): aqui devolvia AMBIGUO, e o bot
        # respondia "qual deles?" a um "paguei 250 no mercado" — comendo o
        # registro da despesa. Se a cauda nao aponta pra NADA da lista, a
        # mensagem nao e sobre a lista.
        return None
    placar.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if len(placar) == 1 or (placar[0][0], placar[0][1]) > (placar[1][0],
                                                           placar[1][1]):
        return placar[0][2]
    return AMBIGUO


def _escolha_de_baixa(user: dict, phone: str, text: str) -> Optional[str]:
    """A pessoa respondeu o numero da pergunta "qual deles eu dou baixa?".

    So vale logo depois da pergunta (BAIXA_ESCOLHA_TTL_S) e so para os ids
    que o bot listou — um "2" solto no meio de outra conversa nao conclui
    nada. Fora da janela, o estado morre em vez de ficar armado.
    """
    estado = BAIXA_ESCOLHA.get(phone)
    if not estado:
        return None
    try:
        velho = (tempo.agora() - estado["quando"]).total_seconds() \
            > BAIXA_ESCOLHA_TTL_S
    except Exception:
        velho = True
    if velho:
        BAIXA_ESCOLHA.pop(phone, None)
        return None
    m = re.fullmatch(r"\s*([1-9])\s*[.!)\-–]?\s*", text or "")
    if not m:
        return None
    ids = estado.get("ids") or []
    idx = int(m.group(1)) - 1
    if idx < 0 or idx >= len(ids):
        return None
    BAIXA_ESCOLHA.pop(phone, None)
    item_id = ids[idx]
    # Dono do item conferido contra o banco, nao contra o que veio na tela.
    if not _meu_item(user["id"], item_id):
        return None
    try:
        alvo = next((i for i in db.list_items(user["id"])
                     if i["id"] == item_id), None)
        db.update_item_status(item_id, "concluido")
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[baixa] falha ao concluir item %s pela escolha", item_id,
            exc_info=True)
        return None
    desc = ((alvo or {}).get("descricao") or "").strip()
    return f"Dei baixa em *{desc}*. ✅" if desc else "Dei baixa. ✅"


def _baixa_deterministica(user: dict, phone: str, text: str) -> Optional[str]:
    """Da baixa no item que a pessoa acabou de resolver. Sem LLM, sem menu.

    Devolve a resposta pronta, ou None quando a mensagem nao e uma baixa
    (ai o fluxo normal segue intacto).
    """
    t = (text or "").strip()
    m = _BAIXA_RE.match(t) if t else None
    if not m:
        return None

    # AUDITORIA v23.4 (P1-4 do auditor): se a pessoa esta respondendo ao menu
    # 1/2 de uma foto, "pago" e resposta do MENU, nao baixa do alarme. Roubar
    # essa palavra concluia um item que ela nao citou e ainda rebaixava a
    # despesa da foto pra lembrete. Regra: so a decisao mais NOVA manda.
    if phone in PENDING and m.group(1).lower() in _BAIXA_AMBIGUA:
        armado = PENDING_EM.get(phone)
        if armado and not _alarme_depois_de(user, armado):
            return None

    alvo = _alvo_da_baixa(user, m.group("cauda"))
    if alvo is None:
        return None
    if alvo is AMBIGUO:
        # PERGUNTAR e a unica saida honesta. Devolver None aqui entregaria a
        # decisao pro motor, e foi assim que "feito" fechou o item errado.
        # A pergunta e NUMERADA e a escolha fica guardada: sem isso a pessoa
        # responde o proprio rotulo que o bot listou, a cauda continua
        # ambigua, e a mesma pergunta volta pra sempre (rodada 2, P0-2).
        try:
            pend = [i for i in db.list_items(user["id"], status="pendente")
                    if (i.get("descricao") or "").strip()][:3]
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[baixa] falha ao listar pendentes pra pergunta", exc_info=True)
            pend = []
        if not pend:
            return None
        BAIXA_ESCOLHA[phone] = {"ids": [i["id"] for i in pend],
                                "quando": tempo.agora()}
        opcoes = "\n".join(f"*{n}* — {i['descricao']}"
                           for n, i in enumerate(pend, 1))
        return (f"Qual deles eu dou baixa?\n\n{opcoes}\n\n"
                f"_Responde o número._")
    try:
        db.update_item_status(alvo["id"], "concluido")
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[baixa] falha ao concluir item %s", alvo.get("id"), exc_info=True)
        return None
    try:
        db.touch_user(user["id"])
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[baixa] falha ao atualizar ultima_interacao", exc_info=True)

    # A decisao pendente perde a vez — mas o que estava nela NAO evapora em
    # silencio (regra 5). Vira lembrete, que e o unico destino que nao mente:
    # nada e marcado como pago sem a pessoa ter dito que pagou.
    resgatado = _resgatar_pendencia(user, phone)

    desc = (alvo.get("descricao") or "isso").strip()
    resposta = f"Dei baixa em *{desc}*. ✅"
    if resgatado:
        resposta += f"\n\n_(Guardei também: {resgatado})_"
    return resposta


# Resposta de menu 1/2: "1", "2", os rotulos que o menu oferece, ou uma
# correcao com numero ("valor 210,50 vence 25/07"). O resto e mensagem nova.
_MENU_ESCOLHA_RE = re.compile(
    r"^\s*(?:1️⃣|2️⃣|[12])\s*[.!)\-–]?\s*$")
_MENU_ROTULO_RE = re.compile(
    r"^\s*(despesa\s*paga|salvar\s+como\s+despesa(\s+paga)?|"
    r"agendar(\s+lembrete)?(\s+de\s+cobran[çc]a)?|lembrete\s+de\s+cobran[çc]a|"
    r"pago|paga|paguei|lembrete)\s*[.!]?\s*$", re.I)
# Confirmacao do fluxo v6 (dados extraidos de imagem): "sim", "confere"...
_MENU_CONFIRMA_RE = re.compile(
    r"^\s*(sim|s|confere|confirmo|ok|isso|isso\s*mesmo|pode|certo|exato|"
    r"n[aã]o|errado|errada|corrigir)\s*[.!]?\s*$", re.I)
# Pedido novo nunca e resposta de menu, mesmo carregando numero e data.
_PEDIDO_NOVO_RE = re.compile(
    r"\b(me\s+lembr|lembra|anota|agenda|marca|guarda|preciso|tenho\s+que|"
    r"comprar|pagar)\w*", re.I)


def _e_resposta_de_menu(text: str, pending) -> bool:
    """A mensagem responde ao menu, ou e assunto novo?

    AUDITORIA v23.4 (P0-1 do auditor): esta pergunta nao existia. O
    `resolve_pending_decision` do ai_engine testa `"pag" in c`, entao
    "me lembra de PAGar o condominio dia 25" era lido como "1 = despesa
    paga": o boleto velho virava despesa concluida e o pedido novo do
    usuario era descartado sem uma linha de aviso. Quem separa resposta de
    menu de mensagem nova e Python (regra 2), e o criterio e estreito.
    """
    t = (text or "").strip()
    if not t:
        return False
    if _MENU_ESCOLHA_RE.match(t) or _MENU_ROTULO_RE.match(t):
        return True
    if isinstance(pending, dict) and "_confirm" in pending \
            and _MENU_CONFIRMA_RE.match(t):
        return True
    # Correcao de dado ("valor 210,50 vence 25/07"): numero + palavra de
    # valor/data, em mensagem CURTA. O teto de palavras nao e enfeite — sem
    # ele "me lembra de pagar o condominio dia 25" (8 palavras) entrava aqui
    # por causa do "dia 25", virava correcao do boleto velho e o pedido real
    # sumia. Correcao de dado e telegrafica; pedido e frase.
    if (len(t) <= 60 and len(t.split()) <= 6 and re.search(r"\d{2,}", t)
            # "reais"/"conto"/"pila" na lista: sem elas, "é 250 reais" nao era
            # lido como correcao, a pendencia da foto virava lembrete e o
            # motor ainda criava um item chamado "é" na lista da pessoa.
            and re.search(r"(r\$|valor|vence|venc|dia|reais?|conto|pila|/|,\d{2})",
                          t, re.I)
            and not _PEDIDO_NOVO_RE.search(t)):
        return True
    return False


def _armar_pending(phone: str, payload) -> None:
    """Arma a decisao pendente COM hora. Decisao sem prazo vira jaula.

    AUDITORIA v23.4 (P0-1 do auditor): sem carimbo de tempo, um PENDING de
    dias antes continuava valendo e sequestrava a primeira mensagem seguinte
    — foi assim que "me lembra de pagar o condominio dia 25" virou
    "Feito. Arquivado como Despesa Paga" (o `"pag" in c` do menu casou com
    "pagar") e o pedido real do Kevin foi descartado calado.
    """
    if not isinstance(payload, dict):
        PENDING.pop(phone, None)
        PENDING_EM.pop(phone, None)
        PENDING_ERROS.pop(phone, None)
        return
    PENDING[phone] = payload
    PENDING_EM[phone] = tempo.agora()
    PENDING_ERROS.pop(phone, None)
    # AUDITORIA v23.4 rodada 3 (P1-1): decisao nova mata pergunta velha. Sem
    # este pop, a pessoa mandava foto DEPOIS da pergunta "qual deles?", via o
    # menu 1/2 na tela, respondia "1" pra ele — e o "1" era capturado pela
    # pergunta antiga, concluindo um item que ela nao citou e deixando o
    # boleto da foto pendurado. Mesma regra que ja governa PENDING_EM.
    BAIXA_ESCOLHA.pop(phone, None)


def _pending_vencido(phone: str) -> bool:
    armado = PENDING_EM.get(phone)
    if armado is None:
        return phone in PENDING     # sem carimbo (processo antigo): expira ja
    try:
        return (tempo.agora() - armado).total_seconds() > PENDING_TTL_S
    except Exception:
        return True


def _alarme_depois_de(user: dict, quando) -> bool:
    """Tocou algum alarme depois deste instante?"""
    try:
        alvo = db.ultimo_alarme_disparado(user["id"])
        if not alvo:
            return False
        ultimo = db.ultimo_disparo_em(user["id"], alvo["id"])
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[baixa] falha ao datar o ultimo alarme", exc_info=True)
        return False
    return bool(ultimo and ultimo > quando)


def _numero_ou_none(valor):
    """Payload de decisao pendente nao e dado confiavel: veio do LLM/OCR."""
    if valor is None:
        return None
    try:
        return float(str(valor).replace("R$", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


_DATA_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _data_iso_ou_none(valor):
    """AUDITORIA v23.4 (P1-5 do auditor): '20/08/2026' gravado neste campo
    derrubava o `check_overdue` INTEIRO (`map(int, venc.split("-"))`), e
    ninguem — nenhum usuario — recebia aviso de vencimento naquele ciclo.
    Data que nao e ISO nao entra no banco."""
    if not valor:
        return None
    v = str(valor)[:10]
    return v if _DATA_ISO_RE.match(v) else None


def _resgatar_pendencia(user: dict, phone: str) -> str:
    """Tira a decisao pendente do caminho sem perder o que havia nela.

    Em 14/08 o payload preso no PENDING era de OUTRO assunto (uma foto de
    dias antes). Ele sequestrou a conversa e, quando o Kevin respondeu "1",
    virou uma "Despesa Paga" que ninguem tinha pago. Descartar calado
    tambem nao serve: seria perder dado. Entao o item pendente e salvo como
    LEMBRETE pendente — reversivel, e nao afirma nada que a pessoa nao disse.
    """
    pend = PENDING.pop(phone, None)
    PENDING_ERROS.pop(phone, None)
    PENDING_EM.pop(phone, None)
    if not isinstance(pend, dict):
        return ""
    desc = (pend.get("descricao") or "").strip()
    if not desc:
        return ""
    try:
        db.add_item(user_id=user["id"], tipo="lembrete",
                    categoria=pend.get("categoria") or "Outros",
                    descricao=desc[:120],
                    valor_reais=_numero_ou_none(pend.get("valor_reais")),
                    data_vencimento=_data_iso_ou_none(
                        pend.get("data_vencimento")),
                    hora_alvo=pend.get("hora_alvo"),
                    status="pendente")
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[pending] falha ao resgatar decisao pendente", exc_info=True)
        return ""
    return desc


# P0-3 (11/08 23:44) — o motor devolveu JSON com `reply` vazio:
#   {"intent":"conversa","reply":"","itens":[],...}
# e a pessoa recebeu "Entendi, mas nao ficou claro o que voce gostaria de
# registrar ou resolver. Tem algo especifico em mente?" — uma pergunta
# generica logo depois de ela ter CONFIRMADO um item. O gatilho daquele dia
# (botao "Isso mesmo") morreu no v23.3, mas o modo de falha nao: qualquer
# resposta vazia do LLM ainda vira improviso.
#
# Resposta vazia nao pode virar pergunta improvisada. O Python sabe o estado
# da conversa — usa o que sabe.
def _resposta_de_emergencia(user: dict) -> str:
    try:
        pendentes = db.list_items(user["id"], status="pendente")
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[emergencia] falha ao ler pendentes", exc_info=True)
        pendentes = []
    # AUDITORIA v23.4 (P2-8 do auditor): `list_items` ordena por
    # COALESCE(data_vencimento, data_criacao) — e data_criacao e datetime de
    # HOJE, entao "comprar pao" (sem data, criado agora) passava na frente da
    # luz que vence amanha. Chamar isso de "seu proximo pendente" e mentir.
    # Proximo e o que tem data mais proxima; sem data nao ha "proximo".
    com_data = [i for i in pendentes if i.get("data_vencimento")]
    if com_data:
        prox = com_data[0]
        venc = ""
        if prox.get("data_vencimento"):
            venc = (f" (vence {prox['data_vencimento'][8:10]}/"
                    f"{prox['data_vencimento'][5:7]})")
        return (f"Tá guardado. 👍\n\n"
                f"Seu próximo pendente é *{prox['descricao']}*{venc}. "
                f"Se quiser, responda *feito* que eu dou baixa.")
    return ("Tá guardado. 👍\n\n"
            "Se quiser me passar mais alguma coisa, é só mandar — conta, "
            "consulta, lembrete.")


def _nao_e_nome_de_formulario(nome: str) -> bool:
    """Filtro FRACO, so pro campo "nome" da landing page.

    _is_not_a_name e calibrado pra conversa: corta 5+ palavras e qualquer
    virgula, porque ali o risco e gravar uma frase inteira como nome. Num
    formulario o risco e o oposto — "Maria da Conceicao Silva Santos" e nome
    de gente e estava sendo descartado, fazendo o bot chamar a pessoa pelo
    pushName do WhatsApp.
    """
    t = (nome or "").strip()
    if len(t) < 2 or len(t) > 60:
        return True
    if re.fullmatch(r"[\d\s./-]+", t):
        return True
    if _LOOKS_LIKE_QUESTION.search(t):
        return True
    low = re.sub(r"[^\w\u00C0-\u00ff\s]", "", t.lower()).strip()
    if low in _NAO_NOME_PALAVRAS or low in _SAUDACOES:
        return True
    return not re.search(r"[A-Za-z\u00C0-\u00ff]", t)


def _purgar_pre_aceite() -> None:
    """Tira da memoria o que ficou pra tras (quem abandonou o cadastro)."""
    agora = tempo.agora()
    for tel in list(PRE_ACEITE):
        fila = [(x, q) for x, q in PRE_ACEITE.get(tel) or []
                if (agora - q).total_seconds() < PRE_ACEITE_TTL_S]
        if fila:
            PRE_ACEITE[tel] = fila
        else:
            PRE_ACEITE.pop(tel, None)
    for mapa, ttl in ((RECEM_APAGADOS, PRE_ACEITE_TTL_S),
                      (ULTIMO_AVISO_LGPD, PRE_ACEITE_TTL_S),
                      (CANCELADO_AVISO, 120)):
        for tel in list(mapa):
            try:
                if (agora - mapa[tel]).total_seconds() > ttl:
                    mapa.pop(tel, None)
            except Exception:
                mapa.pop(tel, None)


def _purgar_kits() -> None:
    """Estado do fluxo de kits e efemero: 15 min e some."""
    agora = tempo.agora()
    for tel in list(KIT_CONVITE):
        try:
            if (agora - KIT_CONVITE[tel]).total_seconds() > KIT_CONVITE_S:
                KIT_CONVITE.pop(tel, None)
        except Exception:
            KIT_CONVITE.pop(tel, None)
    for tel in list(KIT_ETAPA):
        try:
            if (agora - KIT_ETAPA[tel]["quando"]).total_seconds() \
                    > KIT_JANELA_S:
                KIT_ETAPA.pop(tel, None)
        except Exception:
            KIT_ETAPA.pop(tel, None)


def _pop_pre_aceite(phone: str) -> list:
    """Tira a fila e devolve LISTA de textos.

    Ponto unico de leitura de proposito. A conversao de string para fila foi
    feita em dois lugares e esquecida num terceiro (o ramo da landing), que
    devolvia a lista crua pro fluxo de texto e derrubava o webhook com
    AttributeError — 500, dedup engolindo o reenvio, e a demanda que o bot
    tinha ACABADO de prometer registrar perdida.
    """
    return [x for x, _q in (PRE_ACEITE.pop(phone, None) or [])]


def _repor_pre_aceite(phone: str, textos_: list) -> None:
    """Devolve a fila pra memoria quando o envio falhou."""
    if textos_:
        agora = tempo.agora()
        PRE_ACEITE[phone] = [(x, agora) for x in textos_]


def _hora_ja_passou(data_iso, hora_alvo) -> bool:
    """A data+hora combinada ja ficou pra tras?

    Nasceu do caso da Carol (11/08): as 21:43 ela cadastrou "dentista
    11/08 as 16:00" — cinco horas no passado. O bot confirmou sem piscar e
    o cron disparou "chegou a hora" um minuto depois.

    Um lembrete cuja hora ja passou nao e lembrete: ou a pessoa errou o dia
    (o caso dela: tinha dito "amanha"), ou errou a hora. Nos dois casos o
    certo e perguntar, nao gravar calado.
    """
    if not data_iso or not hora_alvo:
        return False
    try:
        d = datetime.strptime(str(data_iso)[:10], "%Y-%m-%d").date()
        h, m = str(hora_alvo)[:5].split(":")
        quando = datetime.combine(d, datetime.min.time()).replace(
            hour=int(h), minute=int(m))
        return quando < tempo.agora()
    except Exception:
        return False


def _enviar_avulsa(phone: str, texto: str, user_id=None) -> bool:
    """Mensagem fora do retorno do webhook, com log no painel e alerta."""
    ok = send_whatsapp(phone, texto)
    try:
        db.log_message(user_id, phone, "out" if ok else "out_falhou",
                       "texto", texto)
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[envio] falha ao logar mensagem avulsa", exc_info=True)
    if not ok:
        _alertar_dono("Nao consegui entregar uma resposta avulsa",
                      phone, texto[:60])
    return ok


def _abrir_onboarding_lgpd(phone: str, texto_abertura: str,
                           user_id=None) -> bool:
    """Manda as mensagens 1 e 2 do onboarding e registra as duas no painel.

    Funcao unica porque ha TRES portas de entrada (usuario novo, landing e
    saudacao de quem esta parado) e a auditoria pegou uma delas escapando.
    Porta duplicada e regra que diverge.

    Estas duas mensagens saem FORA do caminho normal do webhook, entao o
    db.log_message de saida — que mora dentro do if reply: — nunca rodava.
    O painel mostrava o "oi" recebido e nenhuma resposta.
    """
    aviso = jornada.LGPD_AVISO.format(termos=TERMS_URL)
    # Marca aqui, nao em quem chama: sao tres portas, e marcar em uma so
    # deixava a rajada viva pelas outras.
    ULTIMO_AVISO_LGPD[phone] = tempo.agora()
    ok1 = send_whatsapp(phone, texto_abertura)
    ok2 = botoes.enviar_resposta(phone, aviso, send_whatsapp)
    for ok, txt in ((ok1, texto_abertura), (ok2, aviso)):
        try:
            db.log_message(user_id, phone, "out" if ok else "out_falhou",
                           "texto", txt)
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[onboarding] falha ao logar no painel", exc_info=True)
    if not (ok1 and ok2):
        _alertar_dono("ONBOARDING: nao consegui mandar boas-vindas/LGPD "
                      "(pessoa ficou no vacuo no primeiro contato)",
                      phone, texto_abertura[:60])
    return ok1 and ok2


def _conferir_fila_virou_item(phone: str) -> None:
    """Confere no BANCO se cada demanda guardada virou item. Em Python.

    A fila volta pro motor como bloco multi-linha, e quantos itens nascem
    dali e decisao do LLM — justamente onde o bot fez promessa por escrito
    ("registro assim que voce confirmar"). Cinco linhas virando um item e
    perda silenciosa de quatro demandas.

    Nao conserta o motor: detecta e faz barulho, em vez de deixar a pessoa
    descobrir no vencimento.
    """
    pend = CONFERIR_FILA.pop(phone, None)
    if not pend:
        return
    user_id, antes, demandas = pend
    if len(demandas) < 2:
        return
    try:
        criados = len(db.list_items(user_id)) - antes
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[fila] nao consegui conferir itens criados", exc_info=True)
        return
    if criados >= len(demandas):
        return
    faltam = len(demandas) - criados
    detalhe = "; ".join(d[:40] for d in demandas)
    _alertar_dono(
        "FILA PRE-ACEITE: " + str(len(demandas)) + " demandas viraram " +
        str(criados) + " item(ns) — " + str(faltam) + " pode(m) ter se "
        "perdido no motor. [" + detalhe + "]", phone, detalhe[:120])


def _reprocessar_fila(user: dict, phone: str, fila: list):
    """Passa cada demanda guardada pelo caminho normal de uma mensagem.

    Linha a linha, nao em bloco: com a fila unida por quebra de linha, o
    low in ("assinar", ...) do _handle_commands compara contra o blob
    inteiro e nunca casa.
    """
    demandas, respostas = [], []
    EM_REPROCESSO.add(phone)
    # Cinto e suspensorio: mesmo com o filtro de enfileiramento, o reprocesso
    # nao pode DEIXAR uma confirmacao destrutiva armada.
    _confirm_antes = CONFIRM.get(phone, _SEM_CONFIRM)
    try:
        for texto in fila:
            resposta = _handle_commands(user, phone, texto)
            if resposta:
                respostas.append(resposta)
            else:
                demandas.append(texto)
    finally:
        EM_REPROCESSO.discard(phone)
        if _confirm_antes is _SEM_CONFIRM:
            CONFIRM.pop(phone, None)
        else:
            CONFIRM[phone] = _confirm_antes
    # A fila INTEIRA e percorrida, nao ate o primeiro comando. Parar no
    # primeiro descartava tudo que viesse depois.
    return ("\n\n".join(respostas) if respostas else None,
            "\n".join(demandas))


def _colher_pre_aceite(user: dict, phone: str, fechou_cadastro: bool) -> list:
    """Devolve as demandas guardadas quando o cadastro acabou de terminar.

    A pessoa mandou "luz 187 vence dia 20" antes de aceitar. O bot respondeu,
    por escrito, "Guardei aqui e registro assim que voce confirmar". No fluxo
    organico ainda faltavam dois passos (nome e interesses), entao a promessa
    so pode ser cumprida AQUI.
    """
    if not phone or not fechou_cadastro:
        return []
    return _pop_pre_aceite(phone)


def _resolver_aceite(user: dict, phone: str, kind: str,
                     content: str) -> tuple:
    """Gate do aceite. Devolve (resposta_ou_None, demandas_a_reprocessar).

    Enquanto nao ha aceite, NENHUMA mensagem vira item nem chega no LLM.
    """
    step = user.get("onboarding_step")
    aviso = jornada.LGPD_AVISO.format(termos=TERMS_URL)

    # Evento que nao e pedido da pessoa: protocolo/recibo, reacao e
    # figurinha. Responder o muro juridico completo a um emoji e ruido puro
    # — e cada saida dessas conta no limite de envio da Meta.
    if kind in ("reacao", "figurinha"):
        return (None, [])
    if kind in ("desconhecido", "texto") and not (content or "").strip():
        return (None, [])

    # Midia antes do aceite. NAO usa LGPD_NAO_REGISTREI: aquele texto afirma
    # "guardei aqui", e a fila so aceita texto. Audio e foto sao
    # irrecuperaveis — o download e gated ate o aceite, entao o msg_id da
    # midia ja morreu quando o consentimento chega.
    if kind != "texto":
        return (jornada.LGPD_NAO_GUARDEI_MIDIA + aviso, [])

    aceite = jornada.parse_aceite(content)

    if aceite is None:
        texto = (content or "").strip()
        e_saudacao = texto.lower().strip("!?.,;") in _SAUDACOES
        if e_saudacao or not texto:
            # "oi" de quem esta parado no aceite = esta comecando agora.
            # So uma vez a cada REABRIR_ONBOARDING_S: repetir a abertura a
            # cada "oi" transforma 3 saudacoes em 6 mensagens.
            ultimo = ULTIMO_AVISO_LGPD.get(phone)
            if ultimo and (tempo.agora() - ultimo).total_seconds() \
                    < REABRIR_ONBOARDING_S:
                return (aviso, [])
            abertura = (textos.WELCOME_MSG_ABERTURA
                        .format(trial_days=TRIAL_DAYS)
                        if step == "lgpd_organico" else
                        jornada.BOAS_VINDAS.format(
                            nome=((user.get("nome") or "").split() or [""])[0],
                            dias=TRIAL_DAYS))
            _abrir_onboarding_lgpd(phone, abertura, user.get("id"))
            return (None, [])

        # COMANDO NUNCA VAI PRA FILA. Guardar "apagar meus dados" ou
        # "cancelar" como demanda e replayar depois do aceite ARMAVA a
        # confirmacao destrutiva: dias depois, um "apagar" solto apagava a
        # conta por causa de uma frase mandada antes de existir cadastro.
        if texto.lower().strip("!?.,;") in _CMD_NUNCA_ENFILEIRA:
            return (jornada.LGPD_COMANDO_ANTES + aviso, [])

        # Mandou conteudo de verdade: guarda pra processar depois do aceite
        # e AVISA que ainda nao registrou (nunca descarta em silencio).
        fila = PRE_ACEITE.setdefault(phone, []) \
            if len(PRE_ACEITE) < PRE_ACEITE_MAX or phone in PRE_ACEITE else None
        if fila is not None and len(fila) < PRE_ACEITE_MAX_MSGS:
            fila.append((texto[:500], tempo.agora()))
            return (jornada.LGPD_NAO_REGISTREI + aviso, [])
        return (jornada.LGPD_NAO_GUARDEI + aviso, [])

    if aceite is False:
        # RECUSA = APAGA. A copy de LGPD_RECUSA diz "ja apaguei o que tinha
        # seu" — essa frase so pode existir porque este delete existe.
        PRE_ACEITE.pop(phone, None)
        PENDING.pop(phone, None)
        CONFIRM.pop(phone, None)
        try:
            db.delete_user(user["id"])
        except Exception:
            import logging
            logging.getLogger("resolveai").error(
                "[lgpd] FALHA AO APAGAR na recusa — usuario %s",
                user.get("id"), exc_info=True)
            _alertar_dono("LGPD: recusa sem apagamento (prometi apagar e "
                          "nao apaguei — risco juridico)", phone, "recusa")
            return ("N\u00e3o consegui concluir agora. \U0001F615 J\u00e1 avisei o "
                    "respons\u00e1vel e isso vai ser resolvido — se quiser "
                    "garantir, manda *apagar meus dados*.", [])
        RECEM_APAGADOS[phone] = tempo.agora()
        return (jornada.LGPD_RECUSA, [])

    # --- ACEITOU ---------------------------------------------------------
    db.update_user_fields(user["id"],
                          lgpd_aceite_em=tempo.agora().isoformat())

    if step == "lgpd_organico":
        # NAO consome o PRE_ACEITE aqui. Este ramo ainda vai pedir nome e
        # interesses; se o texto guardado fosse devolvido agora, ele cairia
        # no step "nome" e viraria o NOME da pessoa.
        db.update_user_fields(user["id"], onboarding_step="nome")
        return (jornada.PEDIDO_NOME, [])

    guardado = _pop_pre_aceite(phone)
    nome = ((user.get("nome") or "").split() or [""])[0]
    ints = [i for i in (user.get("interesses") or "").split(",") if i]
    cta = ""
    if ints:
        try:
            import trial_guiado
            _, sugestao = trial_guiado._sugestao_para(
                {"interesses": ",".join(ints)})
            cta = "\n\nPra j\u00e1 come\u00e7ar: " + sugestao
        except Exception:
            import logging
            logging.getLogger("resolveai").info(
                "[onboarding] sem sugestao do trial_guiado", exc_info=True)
            cta = ""

    if guardado:
        msg3 = (jornada.PEDIDO_DEMANDA.format(nome=nome)
                + "\n\n_J\u00e1 vou registrar o que voc\u00ea tinha mandado._")
    else:
        msg3 = jornada.PEDIDO_DEMANDA.format(nome=nome) + cta

    # ENVIA ANTES DE FECHAR O ESTADO. A ordem anterior gravava
    # onboarding_step=None e so entao enviava, com o retorno ignorado: se o
    # envio falhasse, o aceite ficava gravado, o cadastro "concluido", a
    # pessoa sem receber nada e ninguem alertado.
    ok3 = send_whatsapp(phone, msg3)
    try:
        db.log_message(user["id"], phone, "out" if ok3 else "out_falhou",
                       "texto", msg3)
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[onboarding] falha ao logar mensagem 3 no painel", exc_info=True)
    if not ok3:
        _alertar_dono("ONBOARDING: aceite registrado mas a mensagem 3 nao "
                      "chegou (pessoa fica sem saber o que fazer)",
                      phone, msg3[:60])
        # a fila VOLTA pra memoria: consumi-la num envio que nao chegou
        # perderia o dado duas vezes.
        _repor_pre_aceite(phone, guardado)
        return (None, [])

    db.update_user_fields(user["id"], onboarding_step=None)
    return (None, guardado)


def _answer_and_reprompt_name(user: dict, text: str) -> str:
    """Responde a pergunta/comando feita durante o passo 'nome' e, em seguida,
    repete gentilmente o convite pra dizer o nome — sem gravar lixo como nome."""
    eng = ai_engine.converse(user["id"], "", "texto", text)
    base = eng.get("reply", "")
    return (base + "\n\n😊 Ah, e pra eu te chamar direito: "
            "*como você quer ser chamado?*")


def _classify_message(msg: dict) -> tuple[str, str]:
    """
    Mapeia a mensagem da Evolution para (kind, content) do ai_engine.
    kinds: texto | audio | imagem_silenciosa | imagem_com_texto | documento |
           video | figurinha | reacao | desconhecido
    """
    if "conversation" in msg and msg["conversation"]:
        return "texto", msg["conversation"]
    ext = msg.get("extendedTextMessage") or {}
    if ext.get("text"):
        return "texto", ext["text"]
    # RESPOSTA DE BOTAO. O canal oficial normaliza o clique para
    # "conversation", entao na pratica isto raramente roda. Mas se o titulo
    # vier vazio, ou se o bot cair no canal reserva (wasender), que NAO tem
    # esse branch, o clique vira "desconhecido" — e com o gate da M1.2 no ar
    # isso TRANCA a pessoa: ela toca em "Concordo", recebe o aviso de novo, e
    # nunca sai. Botao e o caminho principal do produto.
    for chave, id_campo, txt_campo in (
            ("buttonsResponseMessage", "selectedButtonId", "selectedDisplayText"),
            ("templateButtonReplyMessage", "selectedId", "selectedDisplayText"),
            ("listResponseMessage", "selectedRowId", "title")):
        node = msg.get(chave) or {}
        if node:
            titulo = (node.get(txt_campo) or node.get(id_campo) or "")
            if not titulo and chave == "listResponseMessage":
                titulo = ((node.get("singleSelectReply") or {})
                          .get("selectedRowId") or "")
            return "texto", titulo
    inter = msg.get("interactive") or {}
    reply_btn = (inter.get("button_reply") or inter.get("list_reply")
                 or inter.get("nfm_reply") or {})
    if reply_btn:
        return "texto", (reply_btn.get("title") or reply_btn.get("id") or "")
    if "audioMessage" in msg:
        return "audio", ""          # arquivo é buscado via wasender.baixar_midia
    if "imageMessage" in msg:
        caption = (msg["imageMessage"] or {}).get("caption", "") or ""
        return ("imagem_com_texto" if caption.strip() else "imagem_silenciosa"), caption
    if "documentMessage" in msg:
        # Boleto/comprovante em PDF cai aqui. Antes virava "desconhecido" e o
        # usuário levava um "não suportado" — agora tem tratamento próprio.
        doc = msg["documentMessage"] or {}
        legenda = (doc.get("caption") or "").strip()
        nome = (doc.get("fileName") or "").strip()
        mime = (doc.get("mimetype") or "").lower()
        # PDF/foto mandados como "documento" (sem compressão) são lidos igual
        # a uma imagem; o resto pedimos em foto.
        if mime.startswith("image/"):
            return ("imagem_com_texto" if legenda else "imagem_silenciosa"), legenda
        return "documento", (legenda or nome)
    if "videoMessage" in msg:
        return "video", ""
    if "stickerMessage" in msg:
        return "figurinha", ""
    if "reactionMessage" in msg:
        emoji = (msg.get("reactionMessage") or {}).get("text", "") or ""
        return "reacao", emoji
    return "desconhecido", ""


def _fetch_media_base64(payload: dict) -> str:
    """Busca o base64 da mídia ativamente na Evolution. Loga o erro real
    (visível no log do EasyPanel) em vez de engolir silenciosamente."""
    import logging
    log = logging.getLogger("resolveai")
    try:
        import httpx
        data = payload.get("data") or {}
        key = data.get("key") or {}
        msg_id = key.get("id")
        if not msg_id:
            log.warning("[media] sem message.id no payload — não dá pra buscar base64")
            return ""
        url = f"{EVOLUTION_URL}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE}"
        r = httpx.post(
            url,
            headers={"apikey": EVOLUTION_APIKEY, "Content-Type": "application/json"},
            json={"message": {"key": {"id": msg_id}}, "convertToMp4": False},
            timeout=25)
        if r.status_code in (200, 201):
            b64 = (r.json() or {}).get("base64", "") or ""
            log.info("[media] base64 obtido: %d chars", len(b64))
            return b64
        log.warning("[media] Evolution respondeu %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("[media] erro ao buscar base64: %r", e)
    return ""


_VERBOS_ACAO = (
    "pagar", "paga", "marcar", "marca", "comprar", "compra", "ligar",
    "liga", "levar", "leva", "buscar", "busca", "agendar", "agenda",
    "renovar", "renova", "trocar", "troca", "lembrar", "lembra",
    "resolver", "resolve", "mandar", "manda", "avisar", "avisa",
)
_CONECTIVOS_LISTA = (" e ", " tambem", " também", " ai ", " aí ",
                     " depois ", ", ")


# M1.7 — TRAVA DURA. O bot NAO manda mensagem pra numero de terceiro.
#
# Fora da janela de 24h a Meta so entrega template aprovado, e escrever pra
# quem nunca falou com o bot e spam. Este numero JA levou duas restricoes da
# Meta; a terceira e banimento, e sem receita nao da pra reconstruir base num
# numero novo. Isso e regra de codigo, nao decisao de produto que alguem
# reverte no calor de uma demanda.
PODE_ENVIAR_EXTERNO = False

_DELEGAR_RE = re.compile(
    r"\b(?:avisa|avisar|lembra|lembrar|manda|mandar|fala|falar)\s+"
    r"(?:a|o|pra|para|pro|com)?\s*"
    r"(?:minha|meu|a|o)?\s*"
    r"(esposa|marido|mulher|namorad[ao]|m[ãa]e|pai|filh[ao]|irm[ãa][o]?|"
    r"s[óo]ci[ao]|chefe|secret[áa]ri[ao]|di[áa]rista)\b", re.I)


def _link_delegacao(texto: str, quem: str) -> str:
    """M1.7 — monta a mensagem e devolve um link, sem escrever pra ninguem.

    A promessa "avisa minha esposa" e cumprida: o bot escreve o recado e a
    pessoa envia com um toque, do WhatsApp DELA. O consentimento e o de
    sempre (gente falando com gente), o risco pra Meta e zero, e o
    destinatario que quiser vira usuario iniciando a conversa — que e o
    unico jeito legitimo de abrir a janela de 24h.
    """
    recado = (texto or "").strip()
    # Auditoria v23.0, P1-4: aqui ia MASTER_PHONE, o numero do DONO. Sem a
    # env setada o texto saia como "wa.me/)" pra todo mundo; com ela setada,
    # o WhatsApp pessoal do Kevin ia parar no celular de estranhos — num
    # numero que ja levou duas restricoes da Meta. Sem BOT_PHONE, a frase do
    # convite simplesmente nao sai. Link quebrado nao vai pro ar.
    _bot = re.sub(r"\D", "", BOT_PHONE or "")
    if _bot:
        _assina = ("\n\n(quem me lembrou disso foi o Resolve AI — "
                   "se quiser, ele te lembra também: wa.me/" + _bot + ")")
    else:
        _assina = "\n\n(quem me lembrou disso foi o Resolve AI)"
    msg = recado + _assina
    from urllib.parse import quote
    return ("Não mando mensagem pro número de\n"
            "outra pessoa — ela não me autorizou. \U0001F512\n\n"
            "Mas deixei pronto: toca aqui e vai\n"
            "do *seu* WhatsApp, num toque.\n\n"
            "https://wa.me/?text=" + quote(msg) + "\n\n"
            "_E eu te lembro de mandar, se quiser:\n"
            "me diz o dia._")


def _quantas_tarefas(texto: str) -> int:
    """Quantas coisas a pessoa provavelmente pediu neste audio. (M1.4)

    Em PYTHON, nao no prompt. O LLM as vezes ouve "preciso pagar a luz,
    marcar o dentista e comprar racao" e devolve UM item — e a pessoa so
    descobre no vencimento dos outros dois. Contar verbo de acao e
    conectivo de lista e grosseiro, mas e deterministico: serve de PISO
    pra checar a saida do modelo, nao pra criar item sozinho.
    """
    t_ = " " + (texto or "").lower().strip() + " "
    if not t_.strip():
        return 0
    verbos = sum(1 for v in _VERBOS_ACAO if (" " + v + " ") in t_)
    conect = sum(1 for c in _CONECTIVOS_LISTA if c in t_)
    return max(1, min(verbos, conect + 1) if verbos else 1)


def _transcribe_audio(b64: str) -> Optional[str]:
    """Transcreve áudio via OpenAI Whisper. Loga o erro real se falhar."""
    import logging
    log = logging.getLogger("resolveai")
    if not os.environ.get("OPENAI_API_KEY"):
        log.warning("[audio] sem OPENAI_API_KEY — não transcreve")
        return None
    if not b64:
        log.warning("[audio] base64 vazio — nada pra transcrever")
        return None
    try:
        import io
        from openai import OpenAI
        audio_bytes = base64.b64decode(b64)
        log.info("[audio] decodificado: %d bytes, transcrevendo…", len(audio_bytes))
        buf = io.BytesIO(audio_bytes)
        buf.name = "audio.ogg"   # WhatsApp manda opus/ogg; whisper aceita .ogg
        client = OpenAI()
        result = client.audio.transcriptions.create(
            model="whisper-1", file=buf, language="pt"
        )
        txt = result.text
        log.info("[audio] transcrito: %r", (txt or "")[:80])
        return txt
    except Exception as e:
        log.warning("[audio] ERRO no Whisper: %r", e)
        return None


# A pessoa mandando a foto E dizendo que já pagou. O que ela escreve sobre a
# própria conta vale mais do que a leitura da imagem.
# `paga` SOZINHA saiu: "essa conta é paga todo mês no débito automático" é
# presente habitual, não passado — e marcava como quitada uma conta que
# nunca mais seria lembrada. "está paga" continua valendo.
_LEGENDA_JA_PAGO_RE = re.compile(
    r"\b(paguei|pago|quitei|quitado|j[áa]\s+foi|est[áa]\s+paga|"
    r"foi\s+paga)\b", re.I)

# NEGAÇÃO ANTES DO VERBO. "essa eu ainda não paguei" contém "paguei", e sem
# esta guarda a conta era marcada como paga — some da lista, nenhum lembrete
# dispara. E é a legenda MAIS provável: quem fotografa boleto costuma
# comentar que falta pagar.
_NEGACAO_RE = re.compile(
    r"\b(n[ãa]o|ainda|nunca|falta|preciso|tenho\s+que|vou|quero|"
    r"esqueci|lembra)\b", re.I)


def _legenda_diz_que_pagou(legenda: str) -> bool:
    """A legenda afirma que ESTA conta já foi paga?

    Na dúvida, NÃO. O erro caro aqui é assimétrico: marcar como paga uma
    conta pendente tira ela da lista e nenhum lembrete dispara; deixar
    pendente uma conta paga custa uma mensagem a mais.
    """
    texto = (legenda or "").strip()
    m = _LEGENDA_JA_PAGO_RE.search(texto)
    if not m:
        return False
    antes, depois = texto[:m.start()], texto[m.end():]
    # "ainda não paguei", "preciso pagar essa"
    if _NEGACAO_RE.search(antes):
        return False
    # "paguei? não, ainda não" — pergunta não é afirmação.
    if "?" in texto[:m.end() + 2] or _NEGACAO_RE.search(depois[:20]):
        return False
    # "paguei A LUZ, essa aqui é a água" — o verbo tem OBJETO antes do
    # contraste, ou seja, ela pagou outra coisa. Sem objeto ("paguei, essa
    # era a última" / "quitei, essa fechou o mês") o contraste é sobre a
    # mesma conta e a legenda vale. A primeira versão recusava as duas.
    _contraste = re.search(r",\s*(essa|esta|este|esse|aqui)\b", depois, re.I)
    if _contraste and depois[:_contraste.start()].strip(" ,"):
        return False
    return True


def _conta_ja_guardada(user_id: int, descricao: str, valor, data_venc):
    """Mesma conta = mesma descrição E mesmo valor E mesmo vencimento.

    Busca nos dois status: comprovante entra como `concluido`, e procurar só
    entre pendentes deixava o gasto do mês dobrar quando a pessoa mandava o
    comprovante duas vezes.
    """
    try:
        for item in db.list_items(user_id):
            if (_norm_desc(item.get("descricao") or "") == _norm_desc(descricao)
                    and item.get("valor_reais") == valor
                    and (item.get("data_vencimento") or None) == (data_venc or None)):
                return item
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[boleto] falha ao procurar conta repetida", exc_info=True)
    return None


def _conta_pendente_equivalente(user_id: int, dados: dict):
    """O pendente que este comprovante quita — só com evidência ESTRUTURAL.

    Casa por VALOR + VENCIMENTO DO TÍTULO, os dois impressos no próprio
    comprovante. Nada de comparar nome.

    POR QUE SEM NOME (16/08/2026, depois de 3 rodadas de auditoria):
    esta é a única parte do M2.1 que ESCREVE estado a partir de inferência —
    o resto só lê e grava o que leu. E foi a única que produziu achado grave
    em três rodadas seguidas, sempre pela mesma porta:
      - interseção de uma palavra: fechava "ENEL SP" com comprovante da
        "ENEL RJ", porque a sigla tem 2 letras e era descartada;
      - placar por sobreposição: `conta` é palavra de TODA descrição
        (`descricao_de` gera "conta <quem>"), então um comprovante da ENEL
        casava com a conta da SABESP e o bot dizia "o comprovante confere".
    Cada rodada consertou uma via e abriu outra, porque o critério era
    semelhança de texto. Valor + vencimento é chave, não semelhança: o
    documento ou traz os dois iguais, ou não quita nada.

    Sem vencimento do título no comprovante, não fecha nada. O caminho de
    saída continua existindo e está testado: a pessoa responde "paguei X",
    que é o que a própria mensagem do bot ensina.
    """
    venc_titulo = dados.get("vencimento_titulo")
    valor = dados.get("valor_reais")
    if not venc_titulo or not valor:
        return None
    try:
        casados = [i for i in db.list_items(user_id, status="pendente")
                   if i.get("valor_reais") == valor
                   and i.get("data_vencimento") == venc_titulo]
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[boleto] falha ao procurar pendente equivalente", exc_info=True)
        return None
    if len(casados) != 1:
        if casados:
            import logging
            logging.getLogger("resolveai").info(
                "[boleto] %d pendentes com mesmo valor e vencimento — nao "
                "dou baixa no escuro", len(casados))
        return None

    # VETO POR CONTRADIÇÃO DE NOME — não é placar.
    #
    # A chave (valor + vencimento) SELECIONA, mas não identifica: vencimento
    # se concentra em 10/15/20 e valor redondo se repete (condomínio,
    # mensalidade, seguro). Medido: comprovante da ENEL de R$ 150,00 vencendo
    # 20/09 quitava a conta da SABESP de R$ 150,00 vencendo 20/09.
    #
    # A diferença pro que falhou nas rodadas 6 e 7: aqui o nome só pode
    # VETAR, nunca causar. Palavra genérica não fecha nada sozinha — por isso
    # as genéricas saem dos dois lados antes da comparação.
    # AUSÊNCIA DE EVIDÊNCIA NÃO É EVIDÊNCIA DE COMPATIBILIDADE.
    #
    # `if quem and alvo` tratava conjunto vazio como permissão: um
    # beneficiário como "Agora Ltda" — cujas duas palavras são genéricas —
    # zerava os tokens, o veto não rodava e o comprovante fechava a conta da
    # SABESP. Fail-open no lugar exato onde o bloco inteiro declarou
    # fail-closed.
    #
    # Distinção que importa: comprovante SEM beneficiário é contrato
    # declarado (a chave decide sozinha, não há contradição possível).
    # Beneficiário PREENCHIDO que não sobrou token é o bot não sabendo nada
    # sobre aquele nome — e aí não fecha.
    tem_nome = bool((dados.get("beneficiario") or "").strip())
    quem = _tokens_de_nome(dados.get("beneficiario"))
    alvo = _tokens_de_nome(casados[0].get("descricao"))
    if tem_nome and not (quem & alvo):
        import logging
        logging.getLogger("resolveai").info(
            "[boleto] comprovante de %r nao bate com o pendente %r — nao "
            "fecho", dados.get("beneficiario"), casados[0].get("descricao"))
        return None
    return casados[0]


# Palavras que aparecem em toda descrição de conta e não distinguem nada.
#
# CONECTIVO É O QUE MAIS APARECE em razão social brasileira ("Companhia DE
# Saneamento", "Banco DO Brasil", "Cia DE Gás") — e `de` tem 2 letras, então
# passava no filtro e virava token válido dos dois lados. Medido: um
# comprovante de "PGTO DE ENERGIA" quitou a conta da "Companhia de
# Saneamento Basico" tendo `de` como ÚNICO token em comum. Sufixo societário
# (ltda, s.a., cia, eireli, me, epp) tem o mesmo problema.
_NOME_GENERICO = ({"conta", "contas", "pagamento", "pgto", "boleto",
                   "fatura", "documento", "titulo", "título", "para", "com",
                   "de", "da", "do", "das", "dos", "em", "no", "na", "nas",
                   "nos", "ltda", "sa", "cia", "eireli", "epp", "ref",
                   "referente"}
                  | {p for p in _CAUDA_SEM_NOME})


def _pendente_de_mesmo_valor(user_id: int, valor):
    """Um pendente com este valor exato — pra oferecer a baixa manual.

    Só serve pra sugerir texto ao usuário; não decide nada sozinho.
    """
    if not valor:
        return None
    try:
        iguais = [i for i in db.list_items(user_id, status="pendente")
                  if i.get("valor_reais") == valor]
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[boleto] falha ao procurar pendente de mesmo valor",
            exc_info=True)
        return None
    return iguais[0] if len(iguais) == 1 else None


def _tokens_de_nome(texto):
    return {_sem_acento(p) for p in re.findall(r"[\wÀ-ÿ]{2,}", texto or "")
            if _sem_acento(p) not in _NOME_GENERICO}


def _sugestao_de_baixa(descricao: str) -> str:
    """A frase que a mensagem manda a pessoa responder pra dar baixa.

    Tem que caber na cauda que o `_casar_cauda` aceita (até 4 palavras) —
    "paguei conta Condomínio Residencial São José 450" estourava o limite, o
    bot respondia "Registrado" criando item fantasma, e a conta REAL ficava
    pendente. A pessoa lia "Registrado" e achava que tinha quitado.
    """
    palavras = [p for p in re.findall(r"[\wÀ-ÿ]+", descricao or "")
                if p.lower() not in ("conta", "pagamento", "para", "de", "da",
                                     "do")]
    return "paguei " + " ".join(palavras[:2]) if palavras else "paguei"


# M2.2 — só entra no caminho do calendário quem FALOU de placa/IPVA. Sem
# esta guarda, qualquer texto com 3 letras e 4 dígitos ("nota 1234 do ABC")
# viraria pedido de lembrete de carro.
_PLACA_PEDIDO_RE = re.compile(
    r"\b(placa|ipva|licenciamento|emplacamento)\b", re.I)

_MESES_POR_EXTENSO = {1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
                      5: "maio", 6: "junho", 7: "julho", 8: "agosto",
                      9: "setembro", 10: "outubro", 11: "novembro",
                      12: "dezembro"}


def _lembretes_do_calendario(user: dict, texto: str) -> Optional[str]:
    """Placa -> IPVA e licenciamento, com data de tabela.

    INVARIANTE DO BLOCO (M2.2): fonte fora do ar não pode fazer lembrete
    sumir NEM nascer com data errada. Aqui isso vira três recusas:
      - sem tabela do ano, não cria nada (o calendário muda todo ano; usar o
        do ano passado é o jeito mais fácil de avisar no dia errado);
      - data no passado não vira lembrete (nasceria vencido e cobraria na
        hora);
      - item que a PESSOA já criou não é tocado — o bot não corrige o dono.
    """
    final = calendario.final_da_placa(texto)
    if final is None:
        return None
    # GUARDA O FINAL. "Anotei o final *N* da sua placa" aparecia em quatro
    # respostas diferentes e nao anotava nada em lugar nenhum — o valor vivia
    # numa variavel local e morria no return. Agora e verdade, e no dia em
    # que a tabela do ano seguinte entrar da pra criar o lembrete sem pedir a
    # placa de novo. Achado na rodada 2 da auditoria M2.5.
    try:
        db.update_user_fields(user["id"], placa_final=final)
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[calendario] nao consegui guardar o final da placa do user %s",
            user.get("id"), exc_info=True)
    hoje = tempo.hoje()
    try:
        venc = (calendario.vencimentos("SP", final, hoje.year, hoje=hoje)
                + calendario.vencimentos("SP", final, hoje.year + 1, hoje=hoje))
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[calendario] falha ao consultar a tabela", exc_info=True)
        venc = []
    if not venc:
        return ("Anotei o final *" + str(final) + "* da sua placa. 🚗\n\n"
                "Ainda não tenho o calendário oficial desse ano aqui. Quando "
                "sair, *me manda a placa de novo* que eu crio os lembretes. "
                "Se você já sabe a data, me manda que eu guardo "
                "(_\"IPVA vence 20/03\"_).")

    # SÓ A PRÓXIMA OCORRÊNCIA DE CADA TIPO.
    #
    # A consulta cobre dois anos (o corrente e o seguinte) porque em agosto
    # o IPVA que interessa já é o do ano que vem. Mas para os finais 9 e 0 o
    # licenciamento dos DOIS anos ainda está no futuro — e aí nasciam dois
    # itens com a descrição idêntica ("Licenciamento (final 9)"). Três
    # estragos de uma vez: lista com dois itens indistinguíveis, `ver tudo`
    # sem o ano, e a BAIXA quebrada — "feito Licenciamento" não fechava
    # nenhum e ainda criava um item chamado "feito Licenciamento".
    #
    # No bot cujo contrato é "me diz *feito* que eu tiro da sua lista", isso
    # é o pior defeito possível. E lembrete com 16 meses de antecedência não
    # é serviço nenhum: quando chegar a hora, o do ano seguinte é criado.
    futuros = [v for v in venc if v["data"] >= hoje.isoformat()]
    proximos = {}
    for v in sorted(futuros, key=lambda x: x["data"]):
        proximos.setdefault(v["tipo"], v)

    # O PRAZO QUE JÁ PASSOU (M2.5). No meio do ano isso é o caso COMUM, não a
    # exceção: em agosto, o IPVA de janeiro já foi, e o licenciamento dos
    # finais 1 e 2 também. O erro de produto aqui não é criar o lembrete
    # errado — é o SILÊNCIO. A pessoa manda a placa, o bot responde alguma
    # coisa simpática, e ela sai achando que está coberta pelo resto do ano.
    # Por isso o que passou é dito com todas as letras, mesmo custando uma
    # mensagem mais longa e menos agradável.
    passados = {}
    for v in sorted([x for x in venc if x.get("passado")],
                    key=lambda x: x["data"], reverse=True):
        passados.setdefault(v["tipo"], v)
    for tipo in proximos:
        passados.pop(tipo, None)    # com a próxima data em mãos, o que passou
        # já não é notícia: o lembrete cobre a pessoa.
    venc = list(proximos.values())

    criados = []
    ja_tinha = False
    for v in venc:
        if v["data"] < hoje.isoformat():
            continue                       # não nasce vencido
        # JÁ TEM = mesmo assunto NO MESMO ANO, em qualquer status.
        #
        # Comparar sem o ANO fazia o IPVA de 2026 já concluído bloquear a
        # criação do IPVA de 2027 — quem usa o produto direito (deu baixa
        # quando pagou) era exatamente quem perdia o lembrete do ano
        # seguinte, e a resposta abria com "Pronto, guardei" listando só o
        # outro item: não havia como perceber.
        #
        # SEM filtro de status. Quem resolve o P1-4 (item do ano passado
        # bloqueando o do ano novo) é a comparação de ANO, sozinha — medido
        # por mutação. Filtrar por `pendente` tornava invisível o item que a
        # pessoa FECHOU, e o bot recriava idêntico: em SP existe desconto
        # por antecipação, então pagar o IPVA do ano seguinte em dezembro e
        # dar baixa é o comportamento premiado — e era exatamente ele que
        # ganhava um item fantasma de volta.
        try:
            ja_tem = [i for i in db.list_items(user["id"])
                      if v["tipo"] in (i.get("descricao") or "").lower()
                      and (i.get("data_vencimento") or "")[:4] == v["data"][:4]]
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[calendario] falha ao checar itens existentes",
                exc_info=True)
            ja_tem = [1]                   # na dúvida, não mexe
        if ja_tem:
            ja_tinha = True
            continue
        try:
            db.add_item(user_id=user["id"], tipo="lembrete",
                        # "Carro" NÃO existe em db.VALID_CATEGORIES: era
                        # trocado por "Outros" em silêncio, e o dash de
                        # gastos por categoria perdia o veículo.
                        categoria="Veículo", descricao=v["rotulo"],
                        data_vencimento=v["data"], status="pendente")
            criados.append(v)
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[calendario] falha ao gravar %s", v["tipo"], exc_info=True)

    def _br(iso):
        return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}"

    def _linha_passada(v):
        if v["tipo"] == "licenciamento":
            return (f"• *{v['rotulo']}* — o prazo de {v['data'][:4]} ia até "
                    f"{_br(v['data'])} e já passou. Se ainda não licenciou, "
                    f"resolva assim que der: circular sem licenciamento é "
                    f"infração gravíssima, com multa e apreensão.")
        return (f"• *{v['rotulo']}* — venceu em {_br(v['data'])}. Se ficou "
                f"pra trás, tem multa e juros correndo.")

    def _sobre_o_ano_que_vem():
        # O SILÊNCIO SOBRE O ANO SEGUINTE É O DEFEITO. Sem esta frase, quem
        # perdeu o prazo deste ano supõe que pelo menos o do ano que vem está
        # agendado — e não está, porque o edital ainda não saiu.
        prox = hoje.year + 1
        if calendario.vencimentos("SP", final, prox):
            return ""
        # NÃO PROMETE O QUE NÃO CUMPRE. A frase anterior dizia "quando sair,
        # eu crio o lembrete sozinho" — e não existe job que releia a tabela
        # e crie nada. Quando 2027 entrasse, quem leu isso simplesmente não
        # seria avisado, sem jeito de descobrir. Promessa que o código não
        # cumpre é pior que promessa nenhuma: ela faz a pessoa parar de
        # procurar a informação em outro lugar.
        return (f"\n\nO calendário de {prox} ainda não foi publicado — e eu "
                f"não vou chutar data só pra parecer que está agendado. "
                f"Quando sair, *me manda a placa de novo* que eu crio na "
                f"hora. Se você já souber a data, me diz que eu guardo.")

    if not criados:
        # Distinguir "já está na lista" de "o prazo passou" de "o calendário
        # acabou". Sem isso, de julho a dezembro do último ano da tabela o bot
        # afirmava ter itens que não tinha — justamente quando a manutenção
        # anual está atrasada e o aviso mais importa.
        if ja_tinha:
            return ("Esses eu já tenho na sua lista. 👍 Se quiser conferir, "
                    "manda *ver tudo*.")
        if passados:
            corpo = "\n".join(_linha_passada(v) for v in passados.values())
            # A NOTA DO IPVA VALE AQUI TAMBÉM. Era só o ramo de sucesso que
            # contava do desconto e do parcelamento — ou seja, os finais 1 e
            # 2, que perderam TUDO deste ano, eram justamente os únicos sem
            # a informação útil pro ano que vem.
            nota = ("\n" + calendario.NOTA_IPVA_ANO_QUE_VEM
                    if any(v["tipo"] == "ipva" for v in passados.values())
                    else "")
            return (f"Anotei o final *{final}* da sua placa. 🚗\n\n"
                    f"Não criei lembrete nenhum, e é de propósito — o que eu "
                    f"tenho pra esse final já venceu:\n\n{corpo}{nota}"
                    f"{_sobre_o_ano_que_vem()}")
        return ("Anotei o final *" + str(final) + "* da sua placa. 🚗\n\n"
                "As datas que eu tinha pra esse final já passaram, e o "
                "calendário do ano que vem ainda não saiu. Quando sair, *me "
                "manda a placa de novo* — ou me diz a data que você souber.")

    def _linha(v):
        # COM O ANO: uma mensagem só lista datas de anos diferentes (o
        # licenciamento deste ano e o IPVA do que vem). Sem o ano, "19/01"
        # lida em agosto parece data que já passou.
        if v["tipo"] == "licenciamento":
            # PRAZO DE MÊS, não data marcada. Escrever só "vence 31/08" faz a
            # pessoa deixar pro dia 31 — que é justamente o dia em que ela
            # pode não conseguir resolver.
            mes = _MESES_POR_EXTENSO[v.get("prazo_mes") or int(v["data"][5:7])]
            base = (f"• *{v['rotulo']}* — dá pra pagar durante {mes} inteiro; "
                    f"o último dia é {_br(v['data'])}")
        else:
            base = f"• *{v['rotulo']}* — {_br(v['data'])}"
        fer = calendario.aviso_de_feriado(v["data"])
        if not fer:
            return base
        artigo = "no" if fer in ("sábado", "domingo") else "em"
        return f"{base} _(cai {artigo} {fer} — banco fechado)_"

    linhas = "\n".join(_linha(v) for v in criados)
    extra = ""
    # A NOTA DO PARCELAMENTO GRUDA NO IPVA, e não na mensagem.
    #
    # Ela vale só pro IPVA: licenciamento não tem cota única nem 5x. Solta
    # logo depois do bloco de itens criados, ela ficava colada na linha do
    # LICENCIAMENTO e passava a descrever a coisa errada — e "pague em 5x"
    # embaixo do licenciamento é informação falsa sobre dinheiro.
    if any(v["tipo"] == "ipva" for v in criados):
        extra += "\n\n" + calendario.NOTA_IPVA
    if passados:
        extra += ("\n\n⚠️ *O que já passou:*\n"
                  + "\n".join(_linha_passada(v) for v in passados.values()))
        # Presa ao item CRIADO, a nota aparecia só em janeiro — e quem manda
        # a placa em agosto é justamente quem ainda dá tempo de se planejar
        # pro IPVA do ano que vem.
        if any(v["tipo"] == "ipva" for v in passados.values()):
            extra += "\n" + calendario.NOTA_IPVA_ANO_QUE_VEM
    # O ANO SEGUINTE TAMBÉM PRECISA SER DITO AQUI, E POR ÚLTIMO.
    #
    # Esta frase só existia no ramo "não criei nada", e o buraco era o pior
    # possível: quem RECEBE um lembrete termina lendo "eu te aviso com
    # antecedência" e supõe que está coberto — inclusive o final 0, cujo
    # licenciamento cai em 31/12 e cujo IPVA vem 23 dias depois, sem
    # lembrete nenhum. É o mesmo "pular calado pra 2027" que esta fase
    # existe pra impedir, no ramo que ninguém tinha testado.
    #
    # A RESSALVA VEM DEPOIS DA PROMESSA. Na primeira versão do conserto ela
    # ficava no meio, e a última linha da mensagem voltava a ser "eu te aviso
    # com antecedência", sem qualificação — que é a frase que faz a pessoa
    # parar de procurar a informação em outro lugar.
    return (f"Pronto 🚗 Guardei pelo final da sua placa:\n\n{linhas}{extra}\n\n"
            f"Eu te aviso com antecedência. _(Calendário de SP; se o seu "
            f"carro é de outro estado, me diz a data certa que eu ajusto.)_"
            f"{_sobre_o_ano_que_vem()}")


def _registrar_documento_financeiro(user: dict, phone: str, texto_lido: str,
                                    legenda: str = "") -> Optional[str]:
    """M2.1 — foto/PDF de conta vira item com data. Nunca vira pagamento.

    Devolve a resposta pronta, ou None quando o texto NÃO é documento
    financeiro (aí o fluxo antigo segue: menu 1/2 da Regra de Ouro).

    O que este caminho NÃO faz, por decisão de produto: não devolve linha
    digitável, não oferece pagar, não gera PIX. O `boleto.extrair` já
    descarta o código de pagamento antes de qualquer coisa chegar aqui.
    """
    try:
        dados = boleto.extrair(texto_lido)
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[boleto] falha ao extrair — caio no fluxo antigo", exc_info=True)
        return None
    if not dados or not dados.get("valor_reais"):
        return None
    # Sem data não dá pra prometer aviso: o menu antigo pergunta melhor do
    # que este caminho chutaria.
    if not dados.get("data_vencimento") and dados["tipo"] != "comprovante":
        return None

    desc = boleto.descricao_de(dados)
    concluido = dados["status_sugerido"] == "concluido"
    # A legenda da pessoa vale mais que a leitura da imagem: se ela escreveu
    # "essa eu já paguei", o bot não pode agendar cobrança em cima disso.
    # `search` com guarda de negação: a legenda é frase inteira ("essa eu já
    # paguei ontem"), não comando no início da mensagem.
    pago_pela_legenda = bool(legenda) and _legenda_diz_que_pagou(legenda)
    if pago_pela_legenda:
        concluido = True

    # DEDUP POR (descrição + valor + vencimento), não só descrição.
    #
    # Mandar a mesma foto duas vezes acontece (o WhatsApp reenvia, a pessoa
    # confere se chegou) e dobrar a conta seria transformar zelo em erro.
    # Mas comparar SÓ a descrição fundia contas diferentes: Enel de agosto e
    # Enel de setembro viravam um item só, e a segunda sumia. Perder conta
    # do usuário é pior do que ter uma repetida.
    # COMPROVANTE DO QUE JÁ ESTÁ NA LISTA = BAIXA, não item novo.
    #
    # É o fluxo que a própria mensagem convida: guardar a conta e, depois,
    # mandar o comprovante. Como a data do recibo é a do PAGAMENTO e a da
    # conta é a do VENCIMENTO, as chaves do dedup divergem por construção e
    # nunca casariam — o resultado eram dois itens, o gasto do mês contado
    # duas vezes e o lembrete da conta JÁ PAGA disparando no vencimento.
    if concluido:
        _pendente = _conta_pendente_equivalente(user["id"], dados)
        if _pendente:
            try:
                db.update_item_status(_pendente["id"], "concluido")
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[boleto] falha ao dar baixa pelo comprovante",
                    exc_info=True)
            else:
                return (f"Baixa dada ✅\n"
                        f"*{_pendente['descricao']}*"
                        f"{_fmt_dinheiro(_pendente['valor_reais'])} — "
                        f"o comprovante confere.\n\n"
                        f"Tirei da sua lista de pendentes.")

    _rec = _conta_ja_guardada(user["id"], desc, dados["valor_reais"],
                              dados["data_vencimento"])
    if _rec:
        return (f"Essa eu já tenho: *{_rec['descricao']}*"
                f"{_fmt_dinheiro(_rec['valor_reais'])}"
                f"{_fmt_venc(_rec['data_vencimento'])}.\n\n"
                f"Se mudou alguma coisa, me diz o que é que eu ajusto.")

    try:
        db.add_item(
            user_id=user["id"],
            tipo="despesa",
            categoria=ai_engine.classify_category(desc),
            descricao=desc,
            valor_reais=dados["valor_reais"],
            data_vencimento=dados["data_vencimento"],
            status="concluido" if concluido else "pendente")
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[boleto] falha ao gravar o item", exc_info=True)
        # NUNCA dizer "guardei" sobre o que não foi gravado.
        return ("Consegui ler a conta, mas falhei em guardar aqui. 😕 "
                "Me manda de novo, por favor?")

    if concluido:
        # A data só sai como "pago em" quando veio do DOCUMENTO. Se quem
        # disse que pagou foi a legenda, o que está no papel é o
        # VENCIMENTO — escrever "pago em 20/08" ali seria inventar a data do
        # pagamento a partir de outra coisa.
        _quando = ("" if pago_pela_legenda
                   else _fmt_venc(dados["data_vencimento"], "pago em"))
        # ERRO VISÍVEL TEM QUE SER VISÍVEL DE VERDADE.
        #
        # Quando o veto barra a baixa automática (sigla no comprovante ×
        # razão social no boleto, por exemplo), a conta continua pendente e
        # a mensagem não dava sinal nenhum — a pessoa só descobria quando o
        # lembrete cobrasse. A escolha registrada no DECISOES.md é ficar com
        # o erro corrigível; então ele precisa aparecer na hora, com o
        # comando pronto.
        # P2-1 DA AUDITORIA DO M2.1, CONSIDERADO E RECUSADO.
        #
        # O auditor apontou que a dica pode nomear justamente o par que o
        # veto recusou. É verdade — e é de propósito. As duas coisas não são
        # a mesma: o VETO impede o bot de decidir sozinho; a DICA entrega a
        # decisão pra pessoa, com a conta nomeada por extenso e uma frase
        # condicional que ela precisa digitar.
        #
        # Suprimir a dica no caso do veto tira exatamente a correção que a
        # decisão registrada no DECISOES.md promete ("fico com o erro
        # visível porque ele é corrigível"). Sem ela o erro volta a ser
        # invisível, e aí a escolha entre os dois erros perde o sentido.
        _sobrou = _pendente_de_mesmo_valor(user["id"], dados["valor_reais"])
        _dica = ""
        if _sobrou:
            _dica = (f"\n\nSe esse pagamento era da *{_sobrou['descricao']}* "
                     f"que está na sua lista, me diz "
                     f"_\"{_sugestao_de_baixa(_sobrou['descricao'])}\"_ que "
                     f"eu dou baixa nela.")
        return (f"{'Marquei como paga ✅' if pago_pela_legenda else 'Comprovante registrado ✅'}\n"
                f"*{desc}*{_fmt_dinheiro(dados['valor_reais'])}{_quando}.\n\n"
                f"Entra no seu gasto do mês.{_dica}")
    # A porta de correção fica ABERTA na própria mensagem: o menu 1/2 antigo
    # perguntava "já pagou ou é pra lembrar?", e quem lê um boleto legível
    # não precisa dessa pergunta — mas quem fotografou uma conta JÁ paga
    # precisa de um jeito de dizer isso sem procurar comando nenhum.
    # M2.2 — conta que vence em feriado ou fim de semana não pode ser paga
    # no dia. Quem descobre isso na hora paga multa; o bot sabe a data e
    # sabe o feriado, então juntar as duas é o serviço.
    _fer = calendario.aviso_de_feriado(dados.get("data_vencimento"))
    _alerta = (f"\n⚠️ Esse dia é *{_fer}* — banco fechado. Se der, pague "
               f"antes." if _fer else "")
    return (f"Guardei sua conta 📄\n"
            f"*{desc}*{_fmt_dinheiro(dados['valor_reais'])}"
            f"{_fmt_venc(dados['data_vencimento'])}.{_alerta}\n\n"
            f"Eu te aviso antes de vencer. _(Eu lembro e organizo — quem "
            f"paga é você.)_\n"
            f"Se essa já está paga, é só me dizer "
            f"_\"{_sugestao_de_baixa(desc)}\"_.")


def _fmt_dinheiro(valor) -> str:
    """R$ 1.234,56 — com separador de milhar, como se lê em português."""
    if not valor:
        return ""
    return " — R$ " + f"{valor:,.2f}".replace(",", "@").replace(
        ".", ",").replace("@", ".")


def _fmt_venc(data_iso, rotulo: str = "vence") -> str:
    if not data_iso:
        return ""
    return f", {rotulo} {str(data_iso)[8:10]}/{str(data_iso)[5:7]}"


def _read_image(b64: str) -> Optional[str]:
    """Extrai texto da imagem via visão (Anthropic ou OpenAI). Loga erro real."""
    import logging
    log = logging.getLogger("resolveai")
    # O prompt antigo só procurava boleto (descrição + valor + vencimento).
    # Print de conversa, comprovante, cardápio, receita ou etiqueta não se
    # encaixavam e vinham vazios. Agora descreve QUALQUER imagem em uma linha
    # útil, puxando valor/data só quando existem.
    prompt = (
        "Você lê imagens que uma pessoa manda no WhatsApp para um assistente "
        "pessoal que organiza contas, compras, consultas e lembretes.\n"
        "Descreva o conteúdo em UMA linha objetiva, em português, incluindo:\n"
        "- o que é (boleto, comprovante, print de conversa, receita médica, "
        "etiqueta de produto, cardápio, foto de algo, etc.);\n"
        "- valor em R$ se houver;\n"
        "- data/prazo se houver;\n"
        "- nome do estabelecimento/empresa/remetente se houver.\n"
        "Não invente dado que não está visível. Não use listas nem rótulos "
        "como 'Descrição:'.\n"
        # M2.1: a cauda estruturada existe pro Python não ter que adivinhar
        # dentro da frase. Se o modelo ignorar, o parser varre o texto livre
        # do mesmo jeito — a cauda é atalho, não dependência.
        "SE (e somente se) for boleto, fatura, conta ou comprovante, "
        "acrescente no fim uma linha começando com 'DADOS:' no formato "
        "DADOS: valor=<0,00>; vencimento=<dd/mm/aaaa>; beneficiario=<nome>; "
        "tipo=<boleto|comprovante>. "
        "Nunca inclua código de barras ou linha digitável.")
    if not b64:
        log.warning("[imagem] base64 vazio — nada pra ler")
        return None
    try:
        if os.environ.get("ANTHROPIC_API_KEY"):
            import anthropic
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model="claude-3-haiku-20240307", max_tokens=200,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt}]}])
            txt = resp.content[0].text
            log.info("[imagem] lida (claude): %r", (txt or "")[:80])
            return txt
        if os.environ.get("OPENAI_API_KEY"):
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=200,
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url":
                     {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt}]}])
            txt = resp.choices[0].message.content
            log.info("[imagem] lida (openai): %r", (txt or "")[:80])
            return txt
        log.warning("[imagem] sem chave de IA — não lê imagem")
    except Exception as e:
        log.warning("[imagem] ERRO na visão: %r", e)
    return None


def handle_incoming(payload: dict) -> Optional[dict]:
    """
    Processa um webhook 'messages.upsert' da Evolution API.
    Retorna {"number": ..., "text": ...} para enviar, ou None para ignorar.
    PURO no caminho de texto: testável offline.
    """
    data = payload.get("data") or {}
    key = data.get("key") or {}
    if key.get("fromMe"):
        return None  # ignora mensagens enviadas pelo próprio bot

    jid = key.get("remoteJid", "")
    if not jid or "@g.us" in jid:
        return None  # ignora grupos no MVP

    phone = _phone_from_jid(jid)
    push_name = data.get("pushName", "") or ""

    # --- 0a. NÚMERO MASTER: comando de reset para testar como usuário novo ---
    msg = data.get("message") or {}
    kind, content = _classify_message(msg)
    if kind == "texto":
        reset_reply = _maybe_master_reset(phone, content)
        if reset_reply:
            return {"number": phone, "text": reset_reply}

    user, is_new = _get_or_create_user(phone, push_name)
    # split()[0] em nome vazio estoura IndexError e derruba o webhook com
    # 500. E como _ja_processada() marca a mensagem ANTES do processamento,
    # o reenvio da Meta e descartado como duplicado: a mensagem se perde pra
    # sempre e a pessoa trava em todas as seguintes.
    first_name = ((user.get("nome") or "").split() or [""])[0]

    # Purga dos dicionarios efemeros, no topo e nao escondida num ramo:
    # quando morava so dentro do gate, os mapas so eram varridos se ALGUEM
    # mandasse conteudo antes de aceitar.
    _purgar_pre_aceite()
    _purgar_kits()

    # OBS: o download da midia foi movido pra DEPOIS do gate de aceite
    # (bloco 0a2). Baixar significa decriptar o audio/foto da pessoa, e "sem
    # dado sem aceite" nao combina com decriptar primeiro e perguntar depois.

    # --- 0. boas-vindas: primeiro contato inicia o onboarding --------------
    # Veio da landing page com dados no payload? Cria perfil completo e
    # pula o onboarding — a pessoa chega CONHECIDA, não jogada no vácuo.
    # Vale para usuário novo E para quem ainda está no meio do cadastro
    # (ex.: logo após um RESET, que recria o registro no passo "nome").
    # Sem isso o payload seria gravado como se fosse o nome da pessoa.
    landing = _parse_landing_payload(content) if kind == "texto" else None
    if landing and (landing.get("nome") or landing.get("interesses")) and (
            is_new or user.get("onboarding_step") in ("nome", "interesses")):
        fn = ((landing["nome"] or "").split() or [""])[0] or first_name
        db.update_user_fields(
            user["id"],
            nome=landing["nome"] or user["nome"],
            idade=landing.get("idade"),
            interesses=landing.get("interesses") or None,
            onboarding_step="lgpd_landing")
        _abrir_onboarding_lgpd(
            phone, jornada.BOAS_VINDAS.format(nome=fn, dias=TRIAL_DAYS),
            user["id"])
        return None

    if is_new:
        # Sem payload da landing: LGPD -> pergunta nome -> interesses.
        db.update_user_fields(user["id"], onboarding_step="lgpd_organico")
        # Carencia pos-recusa: quem acabou de recusar e voltou nao leva o
        # onboarding inteiro de novo. Sem isto, "Nao concordo" + "oi" vira
        # rajada e reinicia o trial a cada volta.
        _apagado_em = RECEM_APAGADOS.get(phone)
        if _apagado_em and (tempo.agora() - _apagado_em).total_seconds() \
                < CARENCIA_POS_RECUSA_S:
            return {"number": phone, "text":
                    ("Pra continuar eu preciso do seu aceite nos Termos ("
                     + TERMS_URL + ").\n\nÉ só mandar *concordo*. 🙂")}
        RECEM_APAGADOS.pop(phone, None)
        _abrir_onboarding_lgpd(
            phone,
            textos.WELCOME_MSG_ABERTURA.format(trial_days=TRIAL_DAYS),
            user["id"])
        return None

    # --- 0a. GATE DO ACEITE DE LGPD ---------------------------------------
    # POSICAO IMPORTA. Este gate ja esteve ACIMA dos blocos de landing/is_new
    # e o efeito foi matar o funil da landing: como todo usuario nasce em
    # "lgpd_organico", o gate retornava antes de _parse_landing_payload
    # rodar, e quem vinha da landing perdia nome, idade e interesses.
    # Cobre TODOS os formatos — antes so texto passava por
    # _handle_onboarding, entao audio e imagem escapavam pro motor de IA.
    if user.get("onboarding_step") in LGPD_STEPS:
        cmd_pre = (_handle_commands(user, phone, content)
                   if kind == "texto" else None)
        if cmd_pre:
            return {"number": phone, "text": cmd_pre}
        resposta, reprocessar = _resolver_aceite(user, phone, kind, content)
        if resposta is not None:
            return {"number": phone, "text": resposta}
        if not reprocessar:
            return None          # mensagem 3 ja saiu direto
        user = db.get_user(user["id"]) or user
        first_name = ((user.get("nome") or "").split() or [""])[0]
        try:
            _cmd, _blob = _reprocessar_fila(user, phone, reprocessar)
        except Exception:
            # A fila JA foi popada. Sem devolver, uma excecao aqui apaga em
            # silencio a demanda que o bot prometeu registrar.
            _repor_pre_aceite(phone, reprocessar)
            _alertar_dono("FILA: erro ao reprocessar demandas guardadas "
                          "(devolvidas pra memoria)", phone,
                          "; ".join(reprocessar)[:120])
            raise
        if _cmd and not _blob.strip():
            return {"number": phone, "text": _cmd}
        if _cmd:
            _enviar_avulsa(phone, _cmd, user.get("id"))
        if not _blob.strip():
            return None
        try:
            CONFERIR_FILA[phone] = (user["id"], len(db.list_items(user["id"])),
                                    _blob.split("\n"))
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[fila] nao consegui contar itens antes", exc_info=True)
        kind, content = "texto", _blob

    # --- 0a2. DOWNLOAD DA MIDIA (so depois do aceite) ---------------------
    # Decriptar o audio de alguem que ainda nao aceitou os Termos e processar
    # dado sem consentimento, mesmo que o conteudo nunca chegue ao LLM.
    media_b64 = ""
    # "documento" entrou na lista no M2.1: sem baixar o arquivo não há PDF
    # pra ler. Continua DEPOIS do aceite de LGPD, pelo mesmo motivo dos
    # outros formatos — decriptar arquivo de quem não aceitou os termos é
    # tratar dado sem consentimento.
    if kind in ("audio", "imagem_silenciosa", "imagem_com_texto",
                "documento"):
        media_b64 = wasender.baixar_midia(
            msg_id=data.get("_msg_id", "") or "",
            tipo=data.get("_media_tipo", "") or "",
            node=data.get("_media_node") or {})
        if not media_b64:
            import logging
            logging.getLogger("resolveai").warning(
                "[media] %s nao pode ser lido (tipo=%s)",
                kind, data.get("_media_tipo"))

    # --- 0b. RECONSENTIMENTO DA BASE ANTIGA -------------------------------
    # Quem ja existia quando a M1.2 subiu tem onboarding_step None/"done" e
    # nunca passa pelo gate acima. Sem isto, 100% da base real — as 11
    # pessoas que motivaram a feature — seguiria sem aceite explicito.
    # SOFT de proposito: anexa o pedido na resposta em vez de bloquear.
    if kind == "texto" and not user.get("lgpd_aceite_em"):
        _resp_legado = jornada.parse_aceite(content)
        if _resp_legado is True:
            db.update_user_fields(user["id"],
                                  lgpd_aceite_em=tempo.agora().isoformat())
            return {"number": phone, "text":
                    "Obrigado! ✅ Aceite registrado. Seguimos. 🤝"}
        if _resp_legado is False:
            # Aqui NAO apagamos automaticamente: essa pessoa tem semanas de
            # itens e apagar sem confirmacao seria o pior defeito possivel.
            RECONSENTIR.pop(phone, None)
            try:
                db.log_dispatch(user["id"], "lgpd_recusou")
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[lgpd] falha ao registrar objecao do legado",
                    exc_info=True)
            _alertar_dono("LGPD: usuario da base antiga RECUSOU os termos "
                          "(decidir se mantem a conta)", phone, content)
            return {"number": phone, "text":
                    ("Entendido — não vou mais te pedir isso. 🤝\n\n"
                     "Seus lembretes continuam funcionando normalmente. "
                     "Se preferir que eu apague tudo, é só mandar "
                     "*apagar meus dados*.")}
        try:
            if not db.dispatched_today("reconsentimento", user["id"]) \
                    and not db.dispatched_ever("lgpd_recusou", user["id"]):
                RECONSENTIR[phone] = True
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[lgpd] falha no dedup do reconsentimento", exc_info=True)

    # --- 1. comandos globais e onboarding --------------------------------
    if kind == "texto":
        cmd_reply = _handle_commands(user, phone, content)
        if cmd_reply:
            return {"number": phone, "text": cmd_reply}
        _step_antes = user.get("onboarding_step")
        onb_reply = _handle_onboarding(user, content)
        user = db.get_user(user["id"]) or user
        _fechou = bool(_step_antes) and not user.get("onboarding_step")
        if onb_reply:
            # Terminou o cadastro agora? Entao e a hora de honrar a promessa
            # feita la atras em LGPD_NAO_REGISTREI.
            pendente = _colher_pre_aceite(user, phone, _fechou)
            if pendente:
                _ok = send_whatsapp(phone, onb_reply)
                try:
                    db.log_message(user["id"], phone,
                                   "out" if _ok else "out_falhou",
                                   "texto", onb_reply)
                except Exception:
                    import logging
                    logging.getLogger("resolveai").warning(
                        "[onboarding] falha ao logar no painel", exc_info=True)
                if not _ok:
                    _alertar_dono("ONBOARDING: fim do cadastro nao chegou "
                                  "no usuario", phone, onb_reply[:60])
                    _repor_pre_aceite(phone, pendente)
                    return None
                first_name = ((user.get("nome") or "").split() or [""])[0]
                try:
                    _cmd2, _blob2 = _reprocessar_fila(user, phone, pendente)
                except Exception:
                    _repor_pre_aceite(phone, pendente)
                    _alertar_dono("FILA: erro ao reprocessar demandas "
                                  "guardadas (devolvidas pra memoria)",
                                  phone, "; ".join(pendente)[:120])
                    raise
                if _cmd2 and not _blob2.strip():
                    return {"number": phone, "text": _cmd2}
                if _cmd2:
                    _enviar_avulsa(phone, _cmd2, user.get("id"))
                if not _blob2.strip():
                    return None
                try:
                    CONFERIR_FILA[phone] = (
                        user["id"], len(db.list_items(user["id"])),
                        _blob2.split("\n"))
                except Exception:
                    import logging
                    logging.getLogger("resolveai").warning(
                        "[fila] nao consegui contar itens antes", exc_info=True)
                kind, content = "texto", _blob2
            else:
                return {"number": phone, "text": onb_reply}

    # --- 2. gates de acesso -------------------------------------------------
    status = user.get("status") or "trial"
    if status == "bloqueado":
        return None  # usuário bloqueado pelo admin: ignora em silêncio
    if status == "cancelado":
        return {"number": phone, "text":
                (f"{first_name}, sua assinatura está cancelada. Quer voltar? "
                 f"Mande *assinar* que eu reativo tudo — seus dados estão "
                 f"guardados. 🙂")}
    # TRIAL VENCIDO: GRAVA PRIMEIRO, COBRA DEPOIS.
    #
    # Aqui havia um `return _payment_msg(...)` ANTES de processar a mensagem.
    # Efeito: no dia 15 a pessoa mandava "paguei a luz 180", recebia a régua de
    # pagamento e o item NÃO era gravado — sem nenhum aviso. Ela achava que
    # tinha anotado. É a falha silenciosa que a regra #5 existe pra matar, e
    # acontecia justamente no dia em que a gente quer que ela veja valor.
    #
    # Agora a mensagem é processada normalmente (o item entra no banco) e o
    # convite de assinatura é ANEXADO na resposta, no webhook. Ninguém perde
    # nada por não ter pago ainda — perder dado de quem está decidindo assinar
    # é o jeito mais rápido de garantir que ela não assine.
    if status == "trial" and db.trial_days_left(user, TRIAL_DAYS) <= 0:
        TRIAL_VENCIDO[phone] = first_name

    # --- BAIXA: Python, e antes de qualquer decisao pendente ---------------
    # O bot PEDE a palavra no alarme ("responda feito"). Recusar a propria
    # palavra que pediu foi o pior bug do produto: aconteceu 3x com o Kevin.
    # AUDITORIA v23.4 (P0-3 do auditor): a guarda era `not onboarding_step`,
    # mas "done" tambem e cadastro fechado — e a base antiga tem esse valor
    # (ver db.py:305/384 e o comentario do reconsentimento). Com a guarda
    # velha, quem tem "done" recebia o menu 1/2 no lugar da baixa: a correcao
    # inteira ficava desligada, em silencio, justo pros usuarios mais antigos.
    _cadastro_fechado = (user.get("onboarding_step") or "done") == "done"
    if kind == "texto" and _cadastro_fechado:
        # o numero respondido a "qual deles eu dou baixa?" vem primeiro: e a
        # resposta a uma pergunta que o bot acabou de fazer.
        _esc = _escolha_de_baixa(user, phone, content)
        if _esc:
            return {"number": phone, "text": _esc}
        _baixa = _baixa_deterministica(user, phone, content)
        if _baixa:
            return {"number": phone, "text": _baixa}

    # Decisao pendente vencida nao decide mais nada: solta antes de tudo.
    if kind == "texto" and phone in PENDING and _pending_vencido(phone):
        _velho = _resgatar_pendencia(user, phone)
        if _velho:
            _enviar_avulsa(
                phone,
                f"_(Aquela pendência de *{_velho}* ficou sem resposta, "
                f"então guardei como lembrete pra não perder.)_",
                user.get("id"))

    # --- decisão pendente (menu 1/2) tem prioridade -----------------------
    if kind == "texto" and phone in PENDING:
        # Adiar tambem nao pode ser sequestrado: quem respondeu "adiar 1h" ao
        # alarme nao esta respondendo ao menu de despesa. Solta a decisao e
        # deixa o fluxo de snooze (M1.5) fazer o trabalho dele.
        if _ADIOU_RE.match(content.strip()) and db.ultimo_alarme_disparado(
                user["id"]):
            _resgatar_pendencia(user, phone)
        elif not _e_resposta_de_menu(content, PENDING[phone]):
            # AUDITORIA v23.4 (P0-1 do auditor): o menu aceitava QUALQUER
            # coisa como resposta — `"pag" in c` fazia "me lembra de pagar o
            # condominio" virar "Despesa Paga" e o pedido real sumia. Quem
            # decide se aquilo e resposta de menu e Python, e o criterio e
            # estreito: 1, 2, o titulo do botao ou uma correcao com numero.
            _resg = _resgatar_pendencia(user, phone)
            if _resg:
                _enviar_avulsa(
                    phone,
                    f"_(Guardei *{_resg}* como lembrete — a gente resolve "
                    f"depois.)_", user.get("id"))
        else:
            result = ai_engine.converse(
                user["id"], first_name, "decisao", content,
                pending=PENDING[phone],
            )
            if not result["needs_decision"]:
                PENDING.pop(phone, None)
                PENDING_ERROS.pop(phone, None)
                PENDING_EM.pop(phone, None)
                return {"number": phone, "text": result["reply"]}
            # Menu que nao entendeu a resposta NAO pode se re-armar pra
            # sempre: foi assim que o Kevin ficou 3 dias preso. Depois de
            # PENDING_MAX_ERROS tentativas o bot solta a decisao, salva o
            # item como lembrete (nada se perde) e trata a mensagem atual
            # como uma mensagem normal.
            _erros = PENDING_ERROS.get(phone, 0) + 1
            if _erros < PENDING_MAX_ERROS:
                _armar_pending(phone, result["pending_payload"])
                PENDING_ERROS[phone] = _erros
                return {"number": phone, "text": result["reply"]}
            _resg = _resgatar_pendencia(user, phone)
            if _resg:
                _enviar_avulsa(
                    phone,
                    f"Deixei *{_resg}* guardado como lembrete pra não perder. "
                    f"Se já pagou, é só me dizer que eu dou baixa. 👍",
                    user.get("id"))

    # --- 1b. CLIQUE NOS BOTOES DE CONFIRMACAO -----------------------------
    # Deterministico, e antes do motor. Sem isto o titulo do botao vira
    # texto livre e o LLM responde "Como vai?" a um "Isso mesmo" — foi o
    # que aconteceu com o Kevin em 11/08, duas vezes no mesmo dia.
    #
    # POSICAO IMPORTA: depois dos gates de acesso (bloqueado/cancelado) e
    # depois do bloco de decisao PENDENTE. Acima deles, este ACK responderia
    # a usuario bloqueado, roubaria a mensagem de quem cancelou, e engoliria
    # a resposta de uma decisao pendente — deixando o PENDING travado.
    #
    # AUDITORIA v23.4 rodada 2 (P0-3): a guarda aqui era `not onboarding_step`
    # e deixava a base antiga (step="done") fora de TUDO que esta neste bloco
    # — kits, delegacao, ACK de botao, resposta ao escalonamento e, o pior, o
    # CONTADOR de adiamento do M1.5. Medido: 4 "adiar" seguidos com
    # step="done" davam adiamentos=0, silenciado=False. O escalonamento
    # inteiro era codigo inalcancavel pra essa base.
    if kind == "texto" and _cadastro_fechado:
        # M1.3 — toque na lista de kits. Vem antes do motor pelo mesmo
        # motivo do ACK: o titulo da lista chega como texto livre e o LLM
        # nao tem como saber que aquilo foi um toque, nao uma frase.
        _kit = _resposta_de_kit(user, phone, content)
        if _kit is not None:
            try:
                db.touch_user(user["id"])
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[kit] falha ao atualizar ultima_interacao", exc_info=True)
            if _kit == "":
                return None      # a lista de opcoes ja saiu direto
            return {"number": phone, "text": _kit}

        # M2.0 — "ver tudo" / "lista". Comando determinístico, e AQUI, não
        # no _handle_commands.
        #
        # AUDITORIA M2.0 rodada 2 (P1-1): eu tinha posto no _handle_commands,
        # que roda ANTES dos gates de acesso — e o comentário daquele bloco
        # proíbe isso com nome e data (auditoria v23.0, P1-2: "kits" respondia
        # pra quem o admin bloqueou). Medido: usuário BLOQUEADO recebia a
        # lista inteira de itens, e quem CANCELOU recebia a lista em vez do
        # convite de reativação — o único ponto de volta do produto.
        #
        # Os templates aprovados mandam responder isso, então tem que
        # funcionar; mas funcionar pra quem tem direito de receber resposta.
        # O `.strip(" *_")` existe porque o corpo do template mostra
        # *ver tudo* com asterisco, e quem copia o texto cru manda junto.
        if _LISTA_RE.match(content.strip().strip(" *_.!?")):
            try:
                db.touch_user(user["id"])
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[lista] falha ao atualizar ultima_interacao",
                    exc_info=True)
            return {"number": phone,
                    "text": ai_engine.texto_pendentes(user["id"])}

        # M2.2 — a pessoa mandou a placa. O bot já sabe as datas de IPVA e
        # licenciamento: elas são tabela pública por final de placa.
        if _PLACA_PEDIDO_RE.search(content):
            _cal = _lembretes_do_calendario(user, content)
            if _cal:
                return {"number": phone, "text": _cal}

        # M1.7 — pedido de avisar outra pessoa. MOVIDO de _handle_commands
        # (auditoria v23.0, P1-2): aqui ja passou pelo aceite LGPD e pelos
        # gates de acesso, entao usuario bloqueado nao recebe link nenhum.
        # user.get("lgpd_aceite_em") na guarda: o gate de cima chaveia por
        # onboarding_step, e usuario da BASE ANTIGA nao tem nem um nem outro
        # — passava direto e usava feature nova sem ter aceitado os termos.
        # Guarda so nestes dois blocos: mexer na guarda geral tiraria snooze
        # e ACK do legado, que sempre funcionaram.
        _dl = _DELEGAR_RE.search(content)
        if _dl and not PODE_ENVIAR_EXTERNO and user.get("lgpd_aceite_em"):
            return {"number": phone,
                    "text": _link_delegacao(content, _dl.group(1))}

        # M1.3 — Kits de Rotina. Tambem movido. Vem DEPOIS de
        # _resposta_de_kit porque aquele trata a etapa 2 (escolha dentro do
        # kit); este trata a etapa 1 ("kits", "rotina").
        if user.get("lgpd_aceite_em") and _KITS_RE.match(content.strip()):
            _lk = _enviar_lista_kits(user, phone)
            if _lk:
                return {"number": phone, "text": _lk}
            return None      # a lista interativa ja saiu direto

        # M1.5 — resposta ao escalonamento
        _sn = _resposta_de_snooze(user, phone, content)
        if _sn:
            return {"number": phone, "text": _sn}

        # M1.5 — CONTA o adiamento. Sem contador nao existe terceira vez, e
        # sem terceira vez o escalonamento nunca acontece.
        if _ADIOU_RE.match(content.strip()):
            _calou = ""
            try:
                _alvo = (db.ultimo_alarme_disparado(user["id"])
                         or db.ultimo_item(user["id"]))
                if _alvo:
                    _n = db.registrar_adiamento(user["id"], _alvo["id"])
                    if _n > db.SNOOZE_LIMITE:
                        # passou do limite e ainda empurrou: para de tocar.
                        db.silenciar_item(_alvo["id"], user["id"])
                        _calou = (_alvo.get("descricao") or "").strip()
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[snooze] falha ao registrar adiamento", exc_info=True)
            # AUDITORIA v23.4 (P1-7 do auditor): o silenciamento era gravado
            # num dicionario que NINGUEM lia (`SILENCIOU_AGORA`, escrito e
            # nunca consumido em todo o repo). Ou seja: o bot parava de tocar
            # pra sempre e a pessoa continuava achando que seria lembrada.
            # Parar de tocar e legitimo; nao avisar e falha silenciosa.
            if _calou:
                return {"number": phone, "text":
                        (f"Beleza — paro de te cobrar sobre *{_calou}*. 🤝\n\n"
                         f"Ele continua na sua lista, sem alarme. Quando "
                         f"quiser, me diga _\"me lembra de {_calou[:40]}\"_ "
                         f"que eu volto a avisar.")}

        ack = _resposta_de_botao(user, phone, content)
        if ack:
            try:
                db.touch_user(user["id"])
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[ack] falha ao atualizar ultima_interacao", exc_info=True)
            return {"number": phone, "text": ack}

    # --- roteamento por tipo ----------------------------------------------
    if kind == "audio":
        # v6: teto de duração — áudio longo custa ~20x um texto no Whisper
        secs = int((msg.get("audioMessage") or {}).get("seconds") or 0)
        if secs > AUDIO_MAX_SECONDS:
            return {"number": phone, "text": textos.AUDIO_LONGO.format(
                audio_max_min=AUDIO_MAX_SECONDS // 60)}
        transcript = _transcribe_audio(media_b64) if media_b64 else None
        if transcript is None:
            return {"number": phone, "text": textos.AUDIO_INDISPONIVEL}
        kind, content = "audio", transcript
        # M1.4 — quantas tarefas esse audio provavelmente tem?
        try:
            _esp = _quantas_tarefas(transcript)
            if _esp >= 2:
                AUDIO_ESPERADO[phone] = {
                    "uid": user["id"],
                    "antes": len(db.list_items(user["id"])),
                    "esperado": _esp, "txt": transcript}
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[audio] falha ao estimar tarefas", exc_info=True)

    elif kind in ("imagem_silenciosa", "imagem_com_texto"):
        ocr = _read_image(media_b64) if media_b64 else None
        if ocr is None:
            return {"number": phone, "text": textos.IMAGEM_PEDIR_CONTEXTO}

        # M2.1 — o Python tenta ler a conta ANTES de perguntar qualquer
        # coisa. Quando valor e data estão lá, o menu 1/2 vira uma pergunta
        # que o bot já sabe responder — e a Regra de Ouro existe pra imagem
        # AMBÍGUA, não pra boleto legível.
        _resp_boleto = _registrar_documento_financeiro(
            user, phone, ocr, legenda=content)
        if _resp_boleto:
            return {"number": phone, "text": _resp_boleto}

        instruction = content
        # Mesmo quando o extrator recusa (boleto sem data legível, foto
        # ruim), o código de pagamento não segue viagem: sem isto o OCR cru
        # virava descrição do item e a linha digitável ficava guardada na
        # lista da pessoa — justo o que o M2.1 existe pra não fazer.
        content = boleto.sem_codigo_de_pagamento(ocr)
        kind = "imagem_com_texto" if instruction.strip() else "imagem_silenciosa"
        result = ai_engine.converse(
            user["id"], first_name, kind, content, instruction=instruction
        )
        if result["needs_decision"]:
            _armar_pending(phone, result["pending_payload"])
        return {"number": phone, "text": result["reply"]}

    elif kind == "documento":
        # M2.1 — PDF de banco é TEXTO, não imagem: dá pra ler sem OCR.
        # Quando não dá (PDF escaneado, `pypdf` fora do build, download
        # falhou), o caminho antigo continua valendo — pedir print resolve e
        # é melhor do que um beco sem saída.
        _pdf_texto = None
        if media_b64:
            try:
                import base64 as _b64
                _pdf_texto = boleto.texto_de_pdf(_b64.b64decode(media_b64))
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[pdf] falha ao decodificar o arquivo", exc_info=True)
        if _pdf_texto:
            # `legenda=content`: PDF com legenda "já paguei" recebe o mesmo
            # tratamento da foto. Sem isso, o mesmo texto dava resultado
            # diferente dependendo do formato do anexo.
            _resp_pdf = _registrar_documento_financeiro(
                user, phone, _pdf_texto, legenda=content)
            if _resp_pdf:
                return {"number": phone, "text": _resp_pdf}
        pista = (content or "").strip()
        contexto = f" Vi que é *{pista}*." if pista else ""
        return {"number": phone, "text":
                (f"Recebi seu arquivo 📄{contexto}\n\n"
                 f"Não consegui ler valor e vencimento aí dentro, mas "
                 f"resolvo fácil: me manda *print da tela* (foto) que eu "
                 f"leio na hora — ou me diz em uma linha, tipo "
                 f"_\"luz 187 vence dia 20\"_.")}

    elif kind == "video":
        return {"number": phone, "text":
                ("Recebi seu vídeo 🎥 — esse formato eu ainda não leio. "
                 "Se for algo pra eu anotar, me manda em *foto, áudio ou "
                 "texto* que eu registro na hora.")}

    elif kind == "figurinha":
        # Figurinha: responde leve, sem "formato não suportado"
        import random
        return {"number": phone, "text": random.choice([
            "😄 Boa! Manda o que você quer que eu anote — conta, consulta, "
            "compra — que eu cuido.",
            "Haha adorei 😄 Precisa que eu lembre de algo? É só falar.",
            "🙂 Tô aqui! Me diz o que não quer esquecer que eu registro.",
        ])}

    elif kind == "reacao":
        # Reação a uma mensagem (emoji): não precisa responder nada.
        return None

    elif kind == "desconhecido":
        # Nunca dizer "formato não suportado". Redireciona com leveza.
        return {"number": phone, "text":
                "Recebi! 🙂 Pra eu te ajudar melhor, me manda em *texto, "
                "áudio ou foto* — anoto na hora."}

    # V8: mordomo híbrido tenta primeiro (linguagem natural fora do script).
    # Se devolver None (intenção clássica confiável), cai no fluxo de sempre.
    if kind in ("texto", "audio"):
        try:
            # telefone vai junto: é a chave da conversa recente (o webhook
            # grava msg_log com user_id nulo). Sem ele o mordomo é amnésico —
            # foi o que fez "feito" virar baixa em vez de resposta.
            # situacao: em trial o mordomo deve sugerir um uso que dê retorno
            # DENTRO dos dias que faltam — não adianta propor algo que só faz
            # efeito depois que o teste acabou.
            if status == "trial":
                faltam = db.trial_days_left(user, TRIAL_DAYS)
                situacao = (f"em TESTE GRÁTIS, faltam {faltam} dia(s) — "
                            f"sugira usos que ele consegue sentir nesse prazo")
            else:
                situacao = "assinante ativo"
            v8 = motor_v8.route(user["id"], first_name, content, db, ai_engine,
                                telefone=phone, situacao=situacao)
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[v8] erro no motor_v8.route", exc_info=True)
            v8 = None
        if v8 is not None:
            # TRAVA DETERMINISTICA (05/08, perda de dado real):
            #   usuario tocou "Adiar" -> bot respondeu "Concluido."
            #   Fabio digitou "Feito" querendo dizer "acabei de listar"
            #   -> o bot deu baixa e a lista sumiu do "Ver tudo".
            # Concluir e ADIAR sao acoes destrutivas/irreversiveis pra quem
            # esta usando. Quem decide isso e Python lendo a palavra do
            # usuario, nao o LLM interpretando intencao.
            v8 = _travar_acao_destrutiva(
                content if kind == "texto" else "", v8, user)
            _aplicar_v8(user["id"], v8)
            if v8.get("needs_decision"):
                _armar_pending(phone, v8.get("pending_payload"))
            return {"number": phone,
                    "text": ((v8.get("reply") or "").strip()
                             or _resposta_de_emergencia(user))}

    # Texto e áudio passam pela camada de interpretação clássica (intenção + banco)
    result = ai_engine.converse(
        user["id"], first_name, kind, content,
        # Mesma regra da _travar_acao_destrutiva, agora TAMBEM no caminho
        # degradado (v8 fora do ar): "feito isso, me avisa" nao fecha nada.
        permitir_conclusao=not _baixa_sem_alvo(
            user, content if kind == "texto" else ""))
    if result["needs_decision"]:
        _armar_pending(phone, result["pending_payload"])
    return {"number": phone,
            "text": ((result.get("reply") or "").strip()
                     or _resposta_de_emergencia(user))}


_ADIAR_RE = re.compile(
    r"^\s*(adiar|adia|adiar\s*1h|adiar\s*amanh[aã]|depois|mais tarde|"
    r"deixa pra (depois|amanh[aã])|empurra|remarcar)\b", re.I)

# CONCLUSAO: o LLM decide a INTENCAO (ele entende "pode dar baixa",
# "terminei de pagar", "ja resolvi isso"). Python nao tenta adivinhar
# intencao — so bloqueia o que e INEQUIVOCAMENTE errado. A primeira versao
# exigia que a mensagem fosse exatamente "feito", e isso barrava frase
# legitima: regra rigida em cima de entendimento gera bug sem sentido.
#
# Sinais de que a mensagem NAO e uma baixa (aqui Python tem certeza):
#   - contem palavra de adiamento
#   - parece CONTEUDO: lista, varios itens, numero de valor, frase longa
_NAO_E_BAIXA_RE = re.compile(
    r"\b(adiar|adia|depois|mais tarde|amanh[a\u00e3]|semana que vem|ainda n[a\u00e3]o|nao fiz|n[a\u00e3]o fiz|esqueci)\b", re.I)

_PARECE_CONTEUDO_RE = re.compile(
    r"(,\s*\S+\s*,)|(\b\d{2,}\b.*\b\d{2,}\b)|(\bnormalmente\b)|(\bou\b.*\bou\b)", re.I)


def _travar_acao_destrutiva(texto: str, v8: dict, user: dict) -> dict:
    """Impede o LLM de concluir/adiar quando o usuario nao pediu aquilo.

    Dois estragos reais em 05/08:
      1) tocou *Adiar* e o bot marcou como CONCLUIDO — lembrete perdido
      2) o Fabio listou "arroz, leite, pao" e escreveu "Feito" querendo
         dizer "terminei de listar". O bot deu baixa e o "Ver tudo" ficou
         vazio. Ele perdeu a lista inteira no primeiro minuto de uso.

    Regra: concluir so passa se a pessoa escreveu uma palavra INEQUIVOCA de
    conclusao E sozinha na mensagem. "Feito" logo apos criar item vira
    confirmacao de cadastro, nao baixa.
    """
    import logging as _lg   # wa_bot nao importa logging no topo
    if not isinstance(v8, dict):
        return v8
    t = (texto or "").strip()

    # pediu ADIAR -> nunca concluir
    if _ADIAR_RE.match(t) and v8.get("concluir"):
        v8 = dict(v8)
        v8.pop("concluir", None)
        v8["reply"] = ("Adiado. \u23f0 Me diz pra quando: *1 hora*, "
                       "*amanh\u00e3* ou uma data.")
        _lg.getLogger("resolveai").warning(
            "[trava] usuario pediu ADIAR e o motor tentou CONCLUIR")
        return v8

    # AUDITORIA v23.4: "feito isso, me avisa" nao conclui nada. Palavra de
    # baixa seguida de FRASE (virgula ou mais de 4 palavras) e alguem
    # combinando a proxima etapa, nao fechando item. O caminho
    # deterministico ja recusa isso; aqui a mesma regra vale pro LLM.
    # AUDITORIA v23.4 rodada 4: a mesma pergunta do caminho degradado. Se o
    # Python olhou a lista e nao achou o item que a pessoa nomeou, o LLM
    # tambem nao conclui.
    #
    # `user` e OBRIGATORIO (rodada 5, P2-2): quando era opcional, chamar sem
    # ele desligava a regra da baixa em silencio — a trava continuava de pe
    # so pro adiar. Parametro opcional que desliga protecao e buraco mudo
    # esperando o proximo refactor.
    if v8.get("concluir") and _baixa_sem_alvo(user, t):
        v8.pop("concluir", None)
        _lg.getLogger("resolveai").info(
            "[trava] palavra de baixa sem alvo identificavel: %r", t[:60])

    # O LLM entendeu que era baixa. Python so vetoa se houver sinal claro
    # de que NAO era — nao tenta reinterpretar a frase por conta propria.
    if v8.get("concluir") and t and (
            _NAO_E_BAIXA_RE.search(t) or _PARECE_CONTEUDO_RE.search(t)):
        v8 = dict(v8)
        v8.pop("concluir", None)
        _lg.getLogger("resolveai").warning(
            "[trava] motor tentou CONCLUIR sem palavra clara do usuario: %r",
            t[:60])
    return v8


_JANELA_CORRECAO_SEG = 300   # 5 min: depois disso e assunto novo


def _norm_desc(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", "", s).strip()


def _item_recente_igual(user_id: int, descricao: str):
    """Id do item com a MESMA descricao criado nos ultimos 5 min — ou None."""
    import datetime as _dt
    import logging as _lg
    alvo = _norm_desc(descricao)
    if len(alvo) < 3:
        return None
    try:
        with db.get_conn() as conn:
            linhas = conn.execute(
                "SELECT id, descricao, data_criacao FROM items "
                "WHERE user_id=? AND status='pendente' "
                "ORDER BY id DESC LIMIT 5", (user_id,)).fetchall()
        agora = tempo.agora()
        for iid, desc, criado in linhas:
            if _norm_desc(desc) != alvo:
                continue
            try:
                c = _dt.datetime.fromisoformat(str(criado).replace(" ", "T"))
            except Exception:
                continue
            if (agora - c).total_seconds() <= _JANELA_CORRECAO_SEG:
                return iid
    except Exception:
        _lg.getLogger("resolveai").warning(
            "[correcao] falha ao procurar item recente", exc_info=True)
    return None


def _enviar_com_botao(number: str, texto: str) -> bool:
    """Envia disparo proativo tentando botao antes do texto puro.

    O alarme ("chegou a hora... Responda feito") saia so como texto: o
    usuario lia "responda feito" e tinha que digitar. Agora, se ele estiver
    dentro da janela de 24h, recebe os botoes; fora dela, cai pra texto
    igual antes. botoes.enviar_resposta ja faz esse fallback sozinho.
    """
    return botoes.enviar_resposta(number, texto, send_whatsapp)


def _meu_item(user_id: int, item_id) -> bool:
    """O item pertence a este usuario?

    O `item_id` que chega aqui foi escrito pelo LLM, e texto de usuario
    (inclusive texto DENTRO de uma foto) influencia o que o LLM escreve.
    Sem esta checagem, uma injecao de prompt marca como concluido o
    lembrete de remedio de outra pessoa. As funcoes de mutacao do db.py
    nao recebem user_id, entao a trava fica aqui — na fronteira exata em
    que dado de LLM vira escrita.
    """
    try:
        with db.get_conn() as conn:
            dono = conn.execute("SELECT user_id FROM items WHERE id=?",
                                (int(item_id),)).fetchone()
        return bool(dono) and int(dono[0]) == int(user_id)
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[dono] falha ao conferir item %s", item_id, exc_info=True)
        return False   # na duvida, NAO escreve


def _esquecer_processada(msg_id: str) -> None:
    """Desmarca a mensagem pra que o reenvio da Meta possa ser processado.

    So e chamado quando o processamento EXPLODIU. Sem isto, a marca feita
    antes do processamento faz o reenvio ser descartado como duplicado e a
    mensagem da pessoa some para sempre por causa de um bug nosso.
    """
    if not msg_id:
        return
    try:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM msgs_vistas WHERE msg_id=?", (msg_id,))
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[webhook] nao consegui desmarcar msg %s", msg_id, exc_info=True)


def _ja_processada(msg_id: str) -> bool:
    """True se esta mensagem ja foi processada antes.

    A Meta REENVIA o webhook quando nao recebe 200 rapido. Como o motor
    chama LLM antes de responder, timeout nao e hipotetico — e reenvio
    significa item duplicado no banco e mensagem paga cobrada duas vezes.
    """
    if not msg_id:
        return False
    try:
        with db.get_conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS msgs_vistas ("
                         "msg_id TEXT PRIMARY KEY, ts TEXT)")
            ja = conn.execute("SELECT 1 FROM msgs_vistas WHERE msg_id=?",
                              (msg_id,)).fetchone()
            if ja:
                return True
            conn.execute("INSERT INTO msgs_vistas (msg_id, ts) VALUES (?,?)",
                         (msg_id, tempo.agora().isoformat()))
            # nao deixa a tabela crescer pra sempre
            conn.execute("DELETE FROM msgs_vistas WHERE msg_id NOT IN ("
                         "SELECT msg_id FROM msgs_vistas "
                         "ORDER BY ts DESC LIMIT 5000)")
        return False
    except Exception:
        return False   # dedup e otimizacao: nunca pode impedir atendimento


def _aplicar_v8(user_id: int, v8: dict) -> None:
    """Grava no banco o que o mordomo decidiu.

    São 4 efeitos possíveis, nessa ordem de importância:
      atualizar -> completa um item que já existe (informação mandada em
                   partes: "me lembra da luz" + "são 185 reais")
      concluir  -> dá baixa em algo que já foi resolvido
      items     -> cria coisa nova
      memoria   -> guarda fato durável (quanto dura a ração, dia da conta)
                   pra nunca perguntar duas vezes a mesma coisa
    Nenhum deles pode derrubar a resposta ao usuário — por isso cada bloco
    tem seu próprio try.
    """
    import logging
    log_ = logging.getLogger("resolveai")

    atualizar = v8.get("atualizar")
    if isinstance(atualizar, dict) and atualizar.get("id"):
        try:
            if not _meu_item(user_id, atualizar["id"]):
                log_.error("[v8] BLOQUEADO: item %s nao pertence ao user %s",
                           atualizar["id"], user_id)
            else:
                ok = db.atualizar_item(int(atualizar["id"]),
                                       **(atualizar.get("campos") or {}))
                log_.info("[v8] atualizar item %s -> %s", atualizar["id"], ok)
        except Exception:
            log_.warning("[v8] falha ao atualizar item", exc_info=True)

    if v8.get("concluir"):
        try:
            if not _meu_item(user_id, v8["concluir"]):
                log_.error("[v8] BLOQUEADO: concluir item %s de outro dono "
                           "(user %s)", v8["concluir"], user_id)
            else:
                db.atualizar_item(int(v8["concluir"]), status="concluido")
                log_.info("[v8] concluir item %s", v8["concluir"])
        except Exception:
            log_.warning("[v8] falha ao concluir item", exc_info=True)

    falhou_gravar = []
    for item in v8.get("items", []):
        try:
            # CORRECAO EM CIMA DA HORA (05/08, com o proprio dono):
            # audio 1 -> "descer em Utinga 19:50" -> item criado
            # audio 2 -> corrige pra 18:50        -> criava um SEGUNDO item
            # A pessoa acha que corrigiu, o banco fica com dois, e o bot
            # pergunta "terminou?" porque pra ele sao dois assuntos.
            _rec = _item_recente_igual(user_id, item.get("descricao") or "")
            if _rec:
                _campos = {k: v for k, v in {
                    "data_vencimento": item.get("data_vencimento"),
                    "hora_alvo": item.get("hora_alvo"),
                    "valor_reais": item.get("valor_reais"),
                }.items() if v not in (None, "")}
                if _campos:
                    db.atualizar_item(_rec, **_campos)
                    log_.info("[v8] CORRECAO: item %s atualizado (%s)",
                              _rec, _campos)
                    continue
            db.add_item(user_id=user_id, tipo=item.get("tipo", "lembrete"),
                        categoria=item.get("categoria", "Outros"),
                        descricao=(item.get("descricao") or "item")[:120],
                        valor_reais=item.get("valor_reais"),
                        data_vencimento=item.get("data_vencimento"),
                        hora_alvo=item.get("hora_alvo"),
                        recorrencia=item.get("recorrencia"),
                        status=item.get("status", "pendente"),
                        link_afiliado=item.get("link_afiliado"))
            # DEMONSTRACAO DE 90 SEGUNDOS.
            # O aha deste produto nao e registrar, e SER AVISADO. Sem isto,
            # quem cadastra uma conta que vence dia 20 espera duas semanas
            # pra sentir o produto uma vez — e o trial tem 14 dias.
            # agendar_demo() so aceita o PRIMEIRO item de cada pessoa.
            try:
                # id do item recem-criado: sem ele a demo nao consegue
                # checar se a pessoa ja concluiu antes dos 90s
                _iid = None
                try:
                    with db.get_conn() as _c:
                        _r = _c.execute(
                            "SELECT id FROM items WHERE user_id=? "
                            "ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
                    _iid = _r[0] if _r else None
                except Exception:
                    pass
                jornada.agendar_demo(
                    user_id, (item.get("descricao") or "isso")[:120],
                    item.get("data_vencimento") or "", _iid)
            except Exception:
                log_.warning("[demo] falha ao agendar", exc_info=True)
        except Exception as e:
            log_.warning("[v8] falha ao criar item", exc_info=True)
            falhou_gravar.append(f"{item.get('descricao')}: {e!r}")

    # NUNCA dizer "anotado" sobre algo que não foi gravado. Aconteceu de
    # verdade: o bot respondeu "Vou te lembrar da vitamina D todo dia às
    # 09:00" e o item nem existia — o add_item recusou o tipo inventado e o
    # except engoliu. Falha de gravação vira aviso honesto ao usuário.
    if falhou_gravar:
        try:
            motor_v8.ULTIMA_FALHA = ("NAO GRAVOU: " + " | ".join(falhou_gravar))[:600]
        except Exception:
            pass
        v8["reply"] = ("Opa — não consegui guardar isso aqui do meu lado. 😕\n\n"
                       "Me manda de novo, por favor? Prefiro te avisar do que "
                       "deixar você achando que está anotado.")

    for fato in (v8.get("memoria") or []):
        try:
            db.lembrar_fato(user_id, fato.get("chave"), fato.get("valor"))
            log_.info("[v8] memoria: %s = %s", fato.get("chave"), fato.get("valor"))
        except Exception:
            log_.warning("[v8] falha ao gravar memoria", exc_info=True)

    if not (v8.get("items") or atualizar or v8.get("concluir")):
        try:
            db.touch_user(user_id)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Envio via WasenderAPI
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ALERTA AO DONO
# ---------------------------------------------------------------------------
# O motor já registrava toda falha em motor_v8.ULTIMA_FALHA, mas ninguém era
# avisado: só descobria quem abrisse o /health na mão. Com você sozinho dá pra
# olhar; com 10 amigos usando, não — o beta quebra e você fica sabendo pelo
# amigo, dias depois. Agora a falha chega no seu WhatsApp na hora.
_ALERTAS_ENVIADOS: dict[str, float] = {}
ALERTA_JANELA_SEG = int(os.environ.get("ALERTA_JANELA_SEG", "1800"))  # 30 min
ALERTA_MAX_HORA = int(os.environ.get("ALERTA_MAX_HORA", "8"))


def _assinatura_falha(motivo: str) -> str:
    """Agrupa falhas parecidas: o que muda é o nome do item, não o problema."""
    base = re.sub(r"\d+", "#", (motivo or "")[:80])
    return re.sub(r"'[^']*'", "'X'", base)


def _alertar_dono(motivo: str, telefone: Optional[str], texto: str) -> None:
    """Manda a falha pro dono, sem transformar o celular dele em alarme.

    Duas travas: a MESMA falha só volta depois de 30 min, e o total é limitado
    por hora. Alerta que toca demais é alerta que a pessoa silencia — e aí
    volta a não existir.
    """
    if not ADMIN_PHONE:
        return
    import time
    import logging
    agora = time.time()
    try:
        # limpa o que já passou da janela e aplica o teto por hora
        for k in [k for k, t in _ALERTAS_ENVIADOS.items()
                  if agora - t > max(ALERTA_JANELA_SEG, 3600)]:
            _ALERTAS_ENVIADOS.pop(k, None)
        recentes = sum(1 for t in _ALERTAS_ENVIADOS.values() if agora - t < 3600)
        if recentes >= ALERTA_MAX_HORA:
            logging.getLogger("resolveai").warning(
                "[alerta] teto por hora atingido; segurando: %s", motivo[:80])
            return
        chave = _assinatura_falha(motivo)
        if agora - _ALERTAS_ENVIADOS.get(chave, 0) < ALERTA_JANELA_SEG:
            return  # mesma falha, ainda dentro da janela
        _ALERTAS_ENVIADOS[chave] = agora

        quem = ("…" + str(telefone)[-4:]) if telefone else "?"
        corpo = (f"⚠️ *Resolve AI — falha no motor*\n\n"
                 f"Usuário: {quem}\n"
                 f"Mensagem: _{(texto or '')[:80]}_\n\n"
                 f"Motivo: {motivo[:220]}\n\n"
                 f"_Painel:_ {PAINEL_URL_DICA}")
        wasender.send_text(ADMIN_PHONE, corpo)
        logging.getLogger("resolveai").warning("[alerta] enviado ao dono: %s",
                                               motivo[:80])
    except Exception:
        logging.getLogger("resolveai").warning("[alerta] falhou", exc_info=True)


PAINEL_URL_DICA = os.environ.get("PAINEL_URL", "veja /painel?k=SEU_TOKEN")


def send_whatsapp(number: str, text: str) -> bool:
    """Envia texto via WasenderAPI (antes era Whapi/Evolution)."""
    return wasender.send_text(number, text)


def _instance_state() -> str:
    """Consulta o estado da sessão WhatsApp na WasenderAPI ('open' = conectada)."""
    return wasender.instance_state()


def _instance_state_evolution_legado() -> str:
    """(Legado Evolution — não usado. Mantido para referência.)"""
    try:
        import httpx
        r = httpx.get(
            f"{EVOLUTION_URL}/instance/connectionState/{EVOLUTION_INSTANCE}",
            headers={"apikey": EVOLUTION_APIKEY}, timeout=8)
        j = r.json()
        # formatos possíveis: {"instance":{"state":"open"}} | {"state":"open"}
        # | {"instance":{"instanceName":..,"state":"open"}}
        st = None
        if isinstance(j, dict):
            inst = j.get("instance")
            if isinstance(inst, dict):
                st = inst.get("state") or inst.get("connectionStatus")
            st = st or j.get("state") or j.get("connectionStatus")
        if st:
            return st
        # fallback: lista de instâncias
        r2 = httpx.get(
            f"{EVOLUTION_URL}/instance/fetchInstances",
            headers={"apikey": EVOLUTION_APIKEY}, timeout=8)
        arr = r2.json()
        if isinstance(arr, list):
            for it in arr:
                nm = (it.get("instance") or it).get("instanceName") or it.get("name")
                if nm == EVOLUTION_INSTANCE:
                    return ((it.get("instance") or it).get("connectionStatus")
                            or (it.get("instance") or it).get("state") or "unknown")
        return "unknown"
    except Exception:
        return "unknown"


def _restart_evolution_instance() -> bool:
    """Tenta reiniciar a instância na Evolution (recupera sessão travada).
    Tenta /instance/restart; se 404, tenta logout+connect."""
    import logging, httpx
    log = logging.getLogger("resolveai")
    try:
        r = httpx.put(
            f"{EVOLUTION_URL}/instance/restart/{EVOLUTION_INSTANCE}",
            headers={"apikey": EVOLUTION_APIKEY}, timeout=20)
        if r.status_code in (200, 201):
            log.info("[watchdog] instância reiniciada via /restart")
            return True
        log.warning("[watchdog] /restart respondeu %s, tentando connect", r.status_code)
    except Exception as e:
        log.warning("[watchdog] erro no /restart: %r", e)
    # fallback: forçar reconexão
    try:
        httpx.get(f"{EVOLUTION_URL}/instance/connect/{EVOLUTION_INSTANCE}",
                  headers={"apikey": EVOLUTION_APIKEY}, timeout=20)
        log.info("[watchdog] /connect chamado (reconexão forçada)")
        return True
    except Exception as e:
        log.warning("[watchdog] erro no /connect: %r", e)
        return False


def watchdog_check() -> dict:
    """Vigia de auto-recuperação: checa a saúde da sessão e, se estiver
    caída/travada, reinicia sozinho e avisa o admin. Chamado pelo cron."""
    import logging
    log = logging.getLogger("resolveai")
    wa = _instance_state()
    saudavel = wa in ("open", "connected", "online", "connecting")
    resultado = {"estado": wa, "saudavel": saudavel, "acao": "nenhuma"}

    if saudavel:
        db.set_setting("wa_falhas_seguidas", "0")
        return resultado

    # sessão suspeita — conta falhas seguidas antes de agir (evita falso positivo)
    falhas = int(db.get_setting("wa_falhas_seguidas") or "0") + 1
    db.set_setting("wa_falhas_seguidas", str(falhas))
    log.warning("[watchdog] sessão não-saudável (%s), falha seguida #%d", wa, falhas)

    # 2 falhas seguidas (~2 min): na WasenderAPI não dá pra "reiniciar" a
    # sessão via API — se caiu, precisa reescanear o QR no dashboard. Só avisa.
    if falhas >= 2:
        resultado["acao"] = "aviso ao admin"
        db.set_setting("wa_falhas_seguidas", "0")
        if ADMIN_PHONE:
            aviso = ("⚠️ *Resolve AI* — a conexão do WhatsApp (Wasender) caiu "
                     f"(estado: {wa}). Reescaneie o QR no dashboard: "
                     "wasenderapi.com")
            try:
                send_whatsapp(ADMIN_PHONE, aviso)
            except Exception:
                pass
    return resultado


def maybe_admin_report() -> bool:
    """Vigia diário (v6.6.1): 1 mensagem/dia pro ADMIN_PHONE com o pulso do
    sistema. Dispara no primeiro ciclo após as 20h. Dedup via log."""
    if not ADMIN_PHONE:
        return False
    now = tempo.agora()
    if now.hour < 20:
        return False
    admin = db.get_user_by_phone(re.sub(r"\D", "", ADMIN_PHONE)) if hasattr(db, "get_user_by_phone") else None
    admin_id = admin["id"] if admin else 0
    if db.dispatched_today("admin-report", admin_id):
        return False
    hoje = date.today().isoformat()
    with db.get_conn() as conn:
        novos = conn.execute("SELECT COUNT(*) FROM users WHERE substr(data_criacao,1,10)=?", (hoje,)).fetchone()[0]
        trials = conn.execute("SELECT COUNT(*) FROM users WHERE status='trial'").fetchone()[0]
        ativos = conn.execute("SELECT COUNT(*) FROM users WHERE status='ativo'").fetchone()[0]
        disparos = conn.execute("SELECT COUNT(*) FROM dispatches WHERE substr(sent_at,1,10)=?", (hoje,)).fetchone()[0]
        itens_hoje = conn.execute("SELECT COUNT(*) FROM items WHERE substr(data_criacao,1,10)=?", (hoje,)).fetchone()[0]
    wa = _instance_state()
    msg = (f"🤖 *Vigia Resolve AI* — {now.strftime('%d/%m %H:%M')}\n"
           f"WhatsApp: {'🟢 conectado' if wa=='open' else '🔴 '+wa+' — REESCANEIE O QR'}\n"
           f"Hoje: {novos} novo(s) usuário(s) · {itens_hoje} item(ns) · "
           f"{disparos} disparo(s)\n"
           f"Base: {ativos} pagante(s) · {trials} em trial\n"
           f"MRR: R$ {ativos*19.90:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    if _enviar_com_botao(re.sub(r"\D", "", ADMIN_PHONE), msg):
        db.log_dispatch(admin_id, "admin-report")
        return True
    return False


DASH_URL_BASE = os.environ.get("DASH_URL_BASE", "").rstrip("/")


def _dados_do_painel() -> dict:
    """M2.3 — os números do painel, num lugar só e testável.

    A rota `/api/pulso` monta JSON dentro de um handler async, o que torna
    cada campo dela impossível de testar sem subir servidor. O que é conta
    fica aqui; a rota só serializa.
    """
    fora = [p for p in (ADMIN_PHONE, MASTER_PHONE) if p]
    serie = db.heatmap_constancia(dias=90, excluir_telefones=fora)
    # GASTOS AGREGADOS DA BASE, por categoria. Ficaram fora do painel na
    # primeira versão — função escrita, testada e chamada por ninguém, ou
    # seja, metade do escopo do M2.3 não existia pro Kevin.
    gastos: dict = {}
    falhou = 0
    _usuarios = db.list_users()
    for u in _usuarios:
        # `try` por USUÁRIO, não em volta do laço inteiro: envolvendo tudo,
        # uma falha no meio descartava o resto e a soma PARCIAL era
        # desenhada com o rótulo "toda a base". O log não é o que o Kevin
        # vê — ele vê o card.
        try:
            for cat, v in db.gastos_por_categoria(u["id"]).items():
                gastos[cat] = round(gastos.get(cat, 0.0) + v, 2)
        except Exception:
            falhou += 1
            import logging
            logging.getLogger("resolveai").warning(
                "[painel] falha ao somar gastos do user %s", u.get("id"),
                exc_info=True)
    return {
        "heatmap": serie,
        # `serie` pronta: sem isso o painel varria a tabela DUAS vezes por
        # request, a cada 20 segundos.
        "constancia": db.constancia(dias=90, serie=serie),
        "gastos": dict(sorted(gastos.items(), key=lambda kv: kv[1],
                              reverse=True)),
        # Soma incompleta é dita, não maquiada de total.
        "gastos_falharam": falhou,
        "gastos_base": len(_usuarios),
    }


def _linha_risco(env: dict) -> str:
    """P1-5 — a régua tem que dizer POR QUE está naquela cor.

    Antes: "🔴 alto · pico 1/min · 9 proativas" — o número mostrado não era o
    número que decidiu a cor. Agora o motivo vem primeiro e os brutos ficam
    entre parênteses, pra conferência.
    """
    motivo = env.get("motivo") or "ritmo normal"
    return (f"{env['risco']} Número: {motivo}\n"
            f"_(24h: {env['proativas']} proativas · {env['entradas']} "
            f"recebidas · pico {env['pico_por_minuto']}/min)_")


def _linha_engajamento(eng: dict, total_users: int = 0) -> str:
    """P1-6 — "0 pessoa(s)" e "11 pessoa(s)" na mesma tela.

    As duas contas estavam certas e mediam coisas diferentes: 11 é a base
    cadastrada, 0 é quem mandou mensagem nos últimos 7 dias sem contar o
    dono. O que faltava era a copy dizer isso. Métrica que se contradiz na
    tela é métrica que ninguém usa pra decidir.
    """
    ativos = eng.get("pessoas", 0)
    # A base tem que ser a MESMA populacao que o numerador: se o dono sai de
    # um lado, sai do outro. `base_comparavel` ja vem descontada do db.
    base = eng.get("base_comparavel")
    if base is None:
        base = total_users
    sem_dono = " (sem contar você)" if eng.get("dono_excluido") else ""
    return (f"*{eng['por_pessoa_dia']}* demandas por pessoa/dia\n"
            f"_(7d · {ativos} de {base} pessoa(s) mandaram algo"
            f"{sem_dono})_")


# O título da seção de ação mora numa constante porque o teste precisa
# afirmar a AUSÊNCIA dela quando está tudo bem — e comparar contra string
# solta no teste é como a copy e a checagem divergem sem ninguém ver.
TITULO_ACAO = "⚠️ *FAZER HOJE*"

# Teto de linhas de ação. Lista longa não é lista de ação: é relatório com
# outro nome, e volta a ser rolada sem ler.
MAX_ACOES = 3


def _acoes_do_dia(wa: str, env: dict, eng: dict, fin: dict,
                  ontem: dict) -> list:
    """O que exige decisão HOJE, **em ordem de custo se ignorado**.

    Só entra aqui o que tem AÇÃO. "Engajamento 0,8" não entra; "4 pessoas
    não mandam nada há 7 dias, fale com a Ana" entra. Foi essa a diferença
    que o Kevin pediu: relatório que descreve obriga a decidir todo dia do
    zero; relatório que aponta já chega decidido.

    A ORDEM É A PARTE QUE ERRA CALADA. A primeira versão listava na ordem em
    que eu escrevi os `if`, e com o teto de 3 itens isso jogava "decidem em
    até 3 dias" — a única linha em que um dia de atraso custa um assinante —
    para fora do relatório sempre que houvesse três problemas técnicos. O
    corte também era invisível. Agora cada ação carrega um PESO, e o corte
    diz quantas ficaram de fora.
    """
    # (peso, texto). Menor = mais caro se ignorado.
    #   0 — o canal caiu: sem ele, nenhum outro número aqui significa nada
    #   1 — dinheiro com prazo: só isso custa assinante hoje
    #   2 — risco de bloqueio: custa o número inteiro, mas não hoje
    #   3 — falha de envio: já aconteceu, dá pra investigar depois
    #   4 — gente calada: importante, sem prazo
    #   5 — manutenção anual do calendário
    itens = []
    if wa != "open":
        itens.append((0, f"🔴 WhatsApp *{wa.upper()}* — reescaneie o QR agora"))

    decidem = (fin.get("decidem_ate_3_dias") or [])[:3]
    if decidem:
        # `(nome or "").split() or [""]` é o mesmo idioma do
        # `_handle_commands`: nome só com espaço estourava IndexError aqui.
        quem = ", ".join(
            f"{((x.get('nome') or '').split() or ['alguém'])[0]}"
            f" ({'hoje' if x.get('dias') == 0 else str(x.get('dias')) + 'd'})"
            for x in decidem)
        itens.append((1, f"⏳ Decidem em até 3 dias: {quem} — fale com eles"))

    if str(env.get("risco", "")).startswith("🔴"):
        itens.append((2, f"🔴 Risco de bloqueio: "
                         f"{env.get('motivo') or 'ritmo alto'} — segure "
                         f"disparo hoje"))
    if ontem.get("falhas"):
        itens.append((3, f"⚠️ *{ontem['falhas']}* falha(s) de envio ontem — "
                         f"confira o log antes de mandar mais"))

    # QUEM SUMIU. `pessoas` e `base_comparavel` saem da mesma população
    # (auditoria M2.3), então esta subtração é honesta.
    calados = max(0, (eng.get("base_comparavel") or 0)
                  - (eng.get("pessoas") or 0))
    if calados and (eng.get("por_pessoa_dia") or 0) < 1:
        itens.append((4, f"🔇 *{calados}* pessoa(s) sem mandar nada em 7 "
                         f"dias — puxe conversa com uma hoje"))

    aviso = calendario.tabela_expirando(tempo.hoje())
    if aviso and _cobrar_calendario_hoje():
        itens.append((5, f"🗓️ {aviso}"))

    itens.sort(key=lambda x: x[0])
    return [texto for _, texto in itens]


def _cobrar_calendario_hoje(hoje=None) -> bool:
    """A manutenção do calendário só cobra às segundas — a não ser que ela
    já tenha estourado.

    O aviso começa 150 dias antes de a tabela acabar, ou seja, de agosto a
    dezembro. Diário, ele faria a seção *FAZER HOJE* aparecer todos os dias
    por cinco meses — exatamente o que a docstring do relatório proíbe, e o
    jeito de transformar a seção em cabeçalho que se pula.

    Depois que a tabela ESTOUROU é outra história: aí o bot parou de criar
    lembrete de carro, e isso é falha em curso. Cobra todo dia.
    """
    hoje = hoje or tempo.hoje()
    if hoje.year > max(calendario.ANOS_COBERTOS or [hoje.year]):
        return True
    return hoje.weekday() == 0


def _tendencia(agora_v: float, antes_v: float, sufixo: str = "") -> str:
    """"1.8 ▲ +0.4 vs. semana passada" — nunca o número sozinho.

    O relatório mostrava só o valor de hoje. Valor sozinho não responde a
    única pergunta que o dono faz ao abrir: melhorou ou piorou? Sem isso ele
    guardava o número de ontem de cabeça, o que ninguém faz por muito tempo.
    """
    # SEM BASE, NAO HA TENDENCIA. Comparar contra uma semana em que nao
    # havia ninguem produz "▲ 2.00 vs. semana passada" — que le como
    # crescimento e e so o primeiro dado existindo. Numero que sugere
    # progresso onde nao houve e pior que numero nenhum.
    if antes_v <= 0 and agora_v > 0:
        return f"_(primeira semana com base pra comparar){sufixo}_"
    delta = round(agora_v - antes_v, 2)
    if abs(delta) < 0.05:
        return f"→ igual à semana passada{sufixo}"
    seta = "▲" if delta > 0 else "▼"
    return f"{seta} {abs(delta):.2f} vs. semana passada{sufixo}"


def relatorio_matinal() -> bool:
    """O dash resumido no WhatsApp, todo dia às 8h. 1x por dia.

    A PERGUNTA QUE ELE RESPONDE: *preciso agir hoje, e no quê?*

    Reescrito no M2.5. A versão anterior estava correta e era pouco útil:
    empilhava saúde técnica, métrica de negócio e dinheiro na mesma altura
    visual, e mostrava sempre o valor de hoje sem comparação. Quem lê isso
    todo dia acaba rolando até o fim sem decidir nada — e o relatório existe
    justamente pra evitar abrir o painel.

    O desenho novo tem três camadas, nesta ordem:

      1. *FAZER HOJE* — só aparece quando existe algo a fazer, no máximo 3
         itens, cada um com o verbo do que fazer. Seção que aparece todo dia
         vira cabeçalho, e cabeçalho a gente pula.
      2. *O número* — hábito (demandas por pessoa/dia), sempre com a
         tendência contra a semana anterior.
      3. *Contexto* — movimento de ontem, base e dinheiro, comprimidos em
         três linhas. É o que fica no dash pra quem quiser detalhe.

    Cada métrica é lida em `try` próprio: uma consulta quebrada não pode
    apagar o relatório inteiro, porque ele é o único lugar onde o dono
    descobre que algo quebrou.
    """
    if not ADMIN_PHONE:
        return False
    now = tempo.agora()
    if now.hour < 8 or now.hour >= 12:
        return False
    admin = (db.get_user_by_phone(re.sub(r"\D", "", ADMIN_PHONE))
             if hasattr(db, "get_user_by_phone") else None)
    admin_id = admin["id"] if admin else 0
    if db.dispatched_today("dash-manha", admin_id):
        return False

    def _seguro(fn, padrao):
        try:
            return fn()
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[dash-manha] métrica falhou", exc_info=True)
            return padrao

    fora = [ADMIN_PHONE, MASTER_PHONE]
    hoje = tempo.hoje()
    m = _seguro(db.painel_metricas, {})
    serie = _seguro(lambda: db.serie_diaria(2), [])
    eng = _seguro(lambda: db.engajamento(excluir_telefones=fora), {})
    eng_antes = _seguro(
        lambda: db.engajamento(excluir_telefones=fora,
                               ref=hoje - timedelta(days=7)), {})
    env = _seguro(db.pulso_envio, {})
    fin = _seguro(lambda: db.financeiro(TRIAL_DAYS), {})
    if not eng and not m and not fin:
        return False          # base inteira fora do ar: não invento relatório
    ontem = serie[0] if len(serie) > 1 else {}
    wa = _instance_state()

    linhas = [f"☀️ *Resolve AI* — {now.strftime('%d/%m')}"]

    # DENTRO do `_seguro` igual às métricas. A docstring promete que uma
    # consulta quebrada não apaga o relatório inteiro, e a COMPOSIÇÃO estava
    # de fora: um nome só com espaço estourava `IndexError` e o relatório das
    # 8h não saía — e como o `log_dispatch` só grava depois do envio, o cron
    # repetia a falha a cada ciclo, das 8h às 12h, todo dia.
    # SENTINELA, e nao lista vazia. Se a composicao estourar, ausencia de
    # secao le como "nao tem nada a fazer" — que e o estado DEFAULT e o mais
    # perigoso de simular: cai junto a linha "Decidem em ate 3 dias", a unica
    # que custa assinante hoje. Melhor o dono ver que o calculo quebrou.
    acoes = _seguro(lambda: _acoes_do_dia(wa, env, eng, fin, ontem), None)
    if acoes is None:
        acoes = ["⚠️ não consegui montar esta seção — confira o log e o dash"]
    if acoes:
        linhas += ["", TITULO_ACAO] + [f"• {a}" for a in acoes[:MAX_ACOES]]
        # CORTE VISÍVEL. Sumir com o quarto item em silêncio é pior que
        # mostrar quatro: o dono não tem como saber que existe mais.
        if len(acoes) > MAX_ACOES:
            # NÃO diz "no dash": o aviso de calendário, que é o mais
            # provável de ser cortado, não existe no dash. Mandar o dono
            # procurar onde não tem é pior que só contar.
            linhas.append(f"_+{len(acoes) - MAX_ACOES} não mostrada(s)_")

    # O NÚMERO. Um só, com tendência e com o que fazer embutido no veredito.
    if eng:
        linhas += ["", f"*Hábito:* {eng.get('por_pessoa_dia', 0)} msg por "
                       f"pessoa/dia  "
                       f"{_tendencia(eng.get('por_pessoa_dia') or 0, eng_antes.get('por_pessoa_dia') or 0)}",
                   f"{eng.get('veredito', '')} · "
                   f"{eng.get('pessoas', 0)} de "
                   f"{eng.get('base_comparavel', 0)} pessoa(s) usaram"]

    # CONTEXTO, comprimido. Detalhe é papel do dash.
    linhas += ["", f"*Ontem:* {ontem.get('novos', 0)} novo(s) · "
                   f"{ontem.get('recebidas', 0)} msg · "
                   f"{ontem.get('itens', 0)} item(ns)"]
    if fin:
        linhas.append(f"*Base:* {m.get('total_users', 0)} · "
                      f"{fin.get('em_teste', 0)} em teste · "
                      f"{fin.get('assinantes', 0)} pagando")
        linha_dinheiro = (f"💰 Líquido R$ {fin.get('liquido', 0):.2f}"
                          .replace(".", ","))
        if fin.get("breakeven_assinantes"):
            linha_dinheiro += (f" · empata com "
                               f"{fin['breakeven_assinantes']}")
        linhas.append(linha_dinheiro)
    if DASH_URL_BASE and PAINEL_TOKEN:
        linhas.append(f"📊 {DASH_URL_BASE}/dash?k={PAINEL_TOKEN}")

    if _enviar_com_botao(re.sub(r"\D", "", ADMIN_PHONE), "\n".join(linhas)):
        db.log_dispatch(admin_id, "dash-manha")
        return True
    return False


def dispatch_proactive() -> int:
    """Roda o motor proativo, envia e REGISTRA cada disparo (dedup)."""
    import logging
    log = logging.getLogger("resolveai")

    # Demos de 90s vencidas. Fica ANTES do motor proativo de proposito: a
    # amostra e o unico disparo com hora marcada em segundos, e atrasar ela
    # quebra a promessa de "olha eu aqui" no minuto 2.
    try:
        for _d in jornada.demos_prontas():
            _u = db.get_user(_d["user_id"])
            if not _u:
                jornada.marcar_demo_enviada(_d["user_id"])
                continue
            _txt = jornada.texto_demo(_d["descricao"], _d.get("quando") or "")
            # A demo dispara 90s depois do primeiro item, ou seja, SEMPRE
            # dentro da janela de 24h — por isso texto livre aqui é legítimo.
            # Ainda assim passa pelo `falar`: se a pessoa nao estiver na
            # janela (relógio torto, reprocessamento atrasado), a regra vale
            # igual e a amostra não sai queimando o número.
            _res = wasender.falar(re.sub(r"\D", "", _u["telefone"]), _txt,
                                  user_id=_u["id"])
            if _res.get("enviado"):
                try:
                    db.log_message(_u["id"], _u["telefone"], "out", "texto", _txt)
                except Exception:
                    log.warning("[demo] enviada mas nao logada no painel",
                                exc_info=True)
                jornada.marcar_demo_enviada(_d["user_id"])
            else:
                # AUDITORIA M2.0 (P1-5): isto marcava como enviada FORA do
                # if. Envio falhado + marcado = a amostra de 90s, que é o
                # "aha" do produto, sumia no minuto 2 e nunca voltava.
                log.warning("[demo] nao enviada (%s) — user %s, volta no "
                            "proximo ciclo", _res.get("motivo"), _d["user_id"])
    except Exception:
        log.warning("[demo] falha ao disparar amostra", exc_info=True)

    result = scheduler.run_proactive_engine()
    sent = 0
    # TUDO QUE O MOTOR PRODUZIU, derivado da RESPOSTA — nunca de uma lista
    # de chaves escrita a mao.
    #
    # A lista a mao existiu ate o M2.5 e falhou exatamente como se esperava:
    # o `gastos_dispatches` foi criado no scheduler, entrou no `total`,
    # ganhou template, ganhou teste... e nunca foi enviado, porque ninguem
    # acrescentou a chave aqui. Sem erro, sem log, com a suite verde. Cada
    # checagem nova do motor era uma chance de repetir isso.
    #
    # A ORDEM importa e por isso e explicita: alarme de hora primeiro (a
    # pessoa marcou aquela hora), depois o resto. Chave nova que ninguem
    # ordenou entra no fim, mas ENTRA.
    _ordem = ("alarm_dispatches", "resumo_dispatches", "overdue_dispatches",
              "due_dispatches", "churn_dispatches", "trial_dispatches",
              "guided_dispatches", "gastos_dispatches")
    _chaves = list(_ordem) + sorted(
        k for k in result
        if k.endswith("_dispatches") and k not in _ordem)
    all_dispatches = []
    for _k in _chaves:
        _v = result.get(_k) or []
        if isinstance(_v, (list, tuple)):
            all_dispatches += list(_v)
        else:
            # É o P0-1 esperando outro tipo de dado. Descartar em silêncio
            # aqui repetiria exatamente o defeito que a derivação consertou:
            # a lista de disparos some e o log diz "0 pra enviar".
            log.error("[cron] %s nao e lista (%s) — %d disparo(s) "
                      "DESCARTADO(S)", _k, type(_v).__name__,
                      len(_v) if hasattr(_v, "__len__") else 0)
    n_alarm = len(result.get("alarm_dispatches", []))
    n_resumo = len(result.get("resumo_dispatches", []))
    log.info("[cron] motor rodou: %d alarme(s) de hora, %d resumo(s), "
             "%d total pra enviar", n_alarm, n_resumo, len(all_dispatches))
    import random
    import time
    primeiro = True
    # (user_id, kind) -> o disparo com texto desse grupo saiu? Os irmãos sem
    # texto só podem ser carimbados como avisados depois que o cabeça sair.
    # Premissa (confirmada na auditoria): há no máximo UM grupo por
    # (user_id, kind) por ciclo, e o cabeça vem antes dos irmãos. Se o cabeça
    # for cortado por qualquer motivo, a chave nunca é gravada e os irmãos
    # caem no `continue` — fail-closed.
    cabeca_ok: dict = {}
    # Poda do registro de não-entrega: só o dia corrente interessa.
    _hoje_iso = tempo.hoje().isoformat()
    for _k in [k for k in FALHA_JA_LOGADA if k and k[0] != _hoje_iso]:
        FALHA_JA_LOGADA.discard(_k)
    for d in all_dispatches[:DISPATCH_MAX_PER_CYCLE]:
        number = re.sub(r"\D", "", d["telefone"])
        if not number:
            log.warning("[cron] disparo sem número: %s", d.get("message", "")[:40])
            continue
        # Disparo SEM texto é só registro de dedup (vários itens vencidos
        # agrupados numa mensagem só: um carrega o texto, os irmãos carregam
        # só o dedup).
        #
        # AUDITORIA M2.0 (P0-3): isto marcava os irmãos como avisados ANTES
        # de o disparo-cabeça passar pelo `falar`. Com o envio recusado, os
        # itens irmãos ficavam carimbados e NUNCA MAIS voltavam
        # (`dispatched_ever_item`) — a pessoa perdia "conta de água" e "IPVA"
        # pra sempre por causa de uma mensagem que não saiu. Agora o irmão só
        # é carimbado se o cabeça do mesmo (user_id, kind) tiver saído.
        if not (d.get("message") or "").strip():
            if not cabeca_ok.get((d.get("user_id"), d.get("kind"))):
                log.info("[cron] grupo %s do user %s nao saiu — irmao %s "
                         "NAO marcado (volta no proximo ciclo)",
                         d.get("kind"), d.get("user_id"), d.get("item_id"))
                continue
            try:
                db.log_dispatch(d["user_id"], d.get("kind", "outro"),
                                d.get("item_id"))
            except Exception:
                log.warning("[cron] falhei ao registrar dedup", exc_info=True)
            continue

        # FREIO 3: teto diário por usuário.
        # Uma pessoa com 12 lembretes no mesmo dia não pode virar 12 vibrações
        # — nem pra ela, nem pro número. O que não couber hoje sai amanhã: o
        # dedup é por item, então nada se perde.
        if _proativas_hoje(d["user_id"]) >= MAX_PROATIVAS_POR_USUARIO_DIA:
            log.info("[cron] teto diário atingido p/ user %s — adiado",
                     d["user_id"])
            continue

        # FREIO 2: espaçamento com variação.
        # Intervalo EXATO também é assinatura de robô, por isso o random.
        # O sleep é seguro: dispatch_proactive roda em thread separada
        # (asyncio.to_thread), não trava o event loop do FastAPI.
        if not primeiro:
            time.sleep(random.uniform(ENVIO_INTERVALO_MIN,
                                      ENVIO_INTERVALO_MAX))
        primeiro = False

        # M2.0 — TODA proativa passa pela porta única. Dentro da janela sai
        # texto livre; fora, só template aprovado; sem template, não sai.
        #
        # Antes disto o envio era sempre texto livre — e no canal oficial a
        # Meta recusa fora da janela (erro 131047). Ou seja: quem mais
        # precisava do lembrete (quem parou de responder) era exatamente
        # quem não recebia, e o log dizia "FALHOU" sem dizer por quê.
        import templates as _cat
        _tpl, _vars = _cat.para_disparo(d)
        res = wasender.falar(number, d["message"], user_id=d.get("user_id"),
                             template=_tpl, variaveis=_vars)
        ok = res.get("enviado")
        log.info("[cron] envio p/ ...%s (%s): %s", number[-4:],
                 d.get("kind", "?"),
                 ("OK via " + (res.get("via") or "?")) if ok
                 else f"NAO ENVIADO ({res.get('motivo')})")
        cabeca_ok[(d.get("user_id"), d.get("kind"))] = bool(ok)
        if ok:
            sent += 1
            # DEDUP DO TRIAL GUIADO: marcado por QUEM ENVIA, nunca por quem
            # gera (auditoria M2.0, P0-4). Marcado na geração, um nudge
            # recusado pelo `falar` era queimado sem ter saído — inclusive o
            # d6_fim, a única mensagem de conversão do trial.
            if d.get("nudge"):
                try:
                    db.mark_nudge_sent(d["user_id"], d["nudge"])
                except Exception:
                    log.warning("[cron] envio OK mas o nudge %s nao foi "
                                "marcado (user %s) — risco de repetir",
                                d.get("nudge"), d.get("user_id"),
                                exc_info=True)
            try:
                db.log_dispatch(d["user_id"], d.get("kind", "outro"),
                                d.get("item_id"))
            except Exception:
                # Não derruba nada (a mensagem JÁ saiu), mas não pode sumir:
                # sem o dispatch não existe dedup, e a pessoa leva a mesma
                # mensagem de novo no próximo ciclo.
                log.warning("[cron] envio OK mas o dedup nao foi gravado "
                            "(user %s, item %s)", d.get("user_id"),
                            d.get("item_id"), exc_info=True)
        else:
            # NÃO registra dispatch: o dedup é por item, e marcar como
            # enviado algo que não saiu apaga o lembrete pra sempre.
            #
            # Mas registra a NÃO-ENTREGA uma vez por DIA, por
            # (item, kind, motivo). O cron roda a cada 5-15 min e o item
            # volta em todo ciclo enquanto não sai: sem esse freio são ~200
            # linhas por item por dia, e o "N falha(s) de envio ontem" do
            # dash matinal vira ruído que ninguém lê. Com o dia na chave, o
            # sinal reaparece todo dia enquanto o problema existir.
            try:
                _chave = (_hoje_iso, d.get("item_id"), d.get("kind"),
                          res.get("motivo"))
                if _chave not in FALHA_JA_LOGADA:
                    FALHA_JA_LOGADA.add(_chave)
                    db.log_message(d.get("user_id"), number, "out_falhou",
                                   d.get("kind", "outro"),
                                   f"[{res.get('motivo')}] "
                                   f"{d['message'][:200]}")
            except Exception:
                log.warning("[cron] falha ao registrar a nao-entrega",
                            exc_info=True)
    return sent


def _proativas_hoje(user_id: int) -> int:
    """Quantas mensagens PROATIVAS este usuário já recebeu hoje.

    Conta só o que o bot iniciou — resposta a mensagem dele não entra, porque
    responder quem te procurou é o comportamento mais seguro que existe aos
    olhos da Meta e não faz sentido racionar.
    """
    try:
        hoje = tempo.hoje().isoformat()
        with db.get_conn() as conn:
            r = conn.execute(
                "SELECT COUNT(*) FROM dispatches WHERE user_id=? "
                "AND substr(sent_at,1,10)=? AND kind NOT IN "
                "('admin-report','dash-manha','extensao-trial')",
                (user_id, hoje)).fetchone()
        return int(r[0]) if r else 0
    except Exception:
        return 0            # na dúvida, não bloqueia o envio


# ---------------------------------------------------------------------------
# App FastAPI (camada fina)
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI, Request

    app = FastAPI(title="Resolve AI · WhatsApp Gateway",
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health(request: Request):
        """Vigia: 500 só quando a sessão está claramente CAÍDA. Estados
        ambíguos (open/connected/connecting) contam como ok pra não gerar
        falso alarme no monitor."""
        wa = _instance_state()
        conectado = wa in ("open", "connected", "online", "connecting", "unknown")
        body = {"status": "ok" if conectado else "degraded",
                "whatsapp": wa, "instance": EVOLUTION_INSTANCE,
                "llm": "on" if ai_engine.LLM_AVAILABLE else "mock",
                # marcador de build: sem isto não dá pra saber se o deploy
                # realmente trocou o código ou se o container velho ficou de
                # pé. Já perdemos tempo adivinhando isso.
                "build": BUILD,
                # TRIAL_DAYS vem de env e ENV GANHA DO CÓDIGO. Se o EasyPanel
                # ainda tiver "7" setado, a landing promete 14 e o bot entrega
                # 7 — inconsistência que queima confiança no primeiro contato.
                # Sem isto aqui só dava pra saber logando no painel.
                "trial_days": TRIAL_DAYS,
                "extensao_dias": TRIAL_EXTENSAO_DIAS,
                # Termômetro anti-bloqueio. Sem isto só dava pra descobrir
                # que estava no limite depois de a Meta restringir o número.
                "envio": (db.pulso_envio()
                          if hasattr(db, "pulso_envio") else None),
                "freio": {"max_por_ciclo": DISPATCH_MAX_PER_CYCLE,
                          "intervalo_s": f"{ENVIO_INTERVALO_MIN:.0f}-"
                                         f"{ENVIO_INTERVALO_MAX:.0f}",
                          "max_por_usuario_dia": MAX_PROATIVAS_POR_USUARIO_DIA},
                "memoria": hasattr(db, "lembrar_fato"),
                "contexto": hasattr(db, "conversa_recente"),
                "painel": "protegido" if PAINEL_TOKEN else "SEM TOKEN",
                "alerta_dono": "armado" if ADMIN_PHONE else "SEM ADMIN_PHONE"}
        # o diagnóstico do v8 carrega trecho de mensagem de usuário —
        # só sai com token, senão /health vira vazamento de conversa.
        if _painel_autorizado(request):
            body["v8_ultima_falha"] = getattr(motor_v8, "ULTIMA_FALHA", "")
        if wa in ("close", "closed", "disconnected", "removed"):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content=body)
        return body

    @app.get("/webhook")
    async def webhook_handshake(request: Request):
        """Handshake que a Meta faz UMA vez ao cadastrar a URL do webhook.

        Precisa devolver o hub.challenge como TEXTO PURO — devolver JSON aqui
        faz a Meta recusar a URL com um erro generico que nao diz o motivo.
        E a pegadinha mais comum do setup.
        """
        from fastapi.responses import PlainTextResponse, JSONResponse
        q = request.query_params
        challenge = meta_cloud.verificar_handshake(
            q.get("hub.mode", ""), q.get("hub.verify_token", ""),
            q.get("hub.challenge", ""))
        if challenge is None:
            return JSONResponse(status_code=403, content={"erro": "token invalido"})
        return PlainTextResponse(challenge)

    @app.post("/webhook")
    async def webhook(request: Request):
        corpo_bruto = await request.body()

        # ASSINATURA. O webhook ficou aberto na porta 8000 enquanto era so o
        # dono testando: qualquer um com o IP podia forjar mensagem em nome
        # de qualquer usuario. Na Cloud API isso nao se repete.
        # So exige assinatura no canal oficial — no Wasender esse cabecalho
        # nao existe e exigir quebraria o fallback.
        if getattr(wasender, "OFICIAL", False):
            if not meta_cloud.assinatura_valida(
                    corpo_bruto, request.headers.get("x-hub-signature-256", "")):
                import logging
                logging.getLogger("resolveai").warning(
                    "[webhook] assinatura INVALIDA — payload descartado")
                return JSONResponse(status_code=403,
                                    content={"erro": "assinatura invalida"})

        import json as _json
        try:
            raw = _json.loads(corpo_bruto or b"{}")
        except Exception:
            return {"ignored": True}
        # Traduz o payload da WasenderAPI para o formato que handle_incoming entende.
        payload = wasender.to_evolution_shape(raw)
        if not payload:
            return {"ignored": True}
        # log da mensagem recebida (para o painel)
        num, content = None, ""
        try:
            data = payload.get("data") or {}
            key = data.get("key") or {}
            num = (key.get("remoteJid") or "").split("@")[0] or None
            msgobj = data.get("message", {}) if isinstance(data, dict) else {}
            kind, content = _classify_message(msgobj)
            db.log_message(None, num, "in", kind, content)
        except Exception:
            pass

        # Comando do dono para conferir se o canal de alerta está de pé.
        # Alerta que ninguém testou é alerta que você descobre que não
        # funciona no dia em que precisava dele.
        if (MASTER_PHONE and num and re.sub(r"\D", "", num) == MASTER_PHONE
                and (content or "").strip().lower() in ("teste alerta",
                                                        "testar alerta")):
            _ALERTAS_ENVIADOS.clear()   # ignora a janela anti-spam no teste
            _alertar_dono("TESTE MANUAL — se você recebeu isto, o canal de "
                          "alerta está funcionando.", num, "teste alerta")
            return {"ok": True, "alerta": "enviado"}

        # foto do diagnóstico ANTES de processar: se mudar, o motor falhou
        # nesta mensagem e o dono precisa saber na hora.
        falha_antes = getattr(motor_v8, "ULTIMA_FALHA", "")

        _mid = ((payload.get("data") or {}).get("key") or {}).get("id") or ""
        if _ja_processada(_mid):
            import logging
            logging.getLogger("resolveai").info(
                "[webhook] reenvio da Meta ignorado (msg ja processada)")
            return {"ok": True, "duplicada": True}

        # REDE DE SEGURANCA. O _ja_processada acima ja marcou este msg_id,
        # entao qualquer excecao que escape daqui faz a Meta reenviar e o
        # dedup descartar: a mensagem da pessoa some PARA SEMPRE, sem log e
        # sem ninguem saber. Ja aconteceu com um IndexError de nome vazio.
        try:
            reply = handle_incoming(payload)
        except Exception as e:
            import logging
            logging.getLogger("resolveai").error(
                "[webhook] EXCECAO ao processar mensagem de ...%s",
                str(num or "")[-4:], exc_info=True)
            _esquecer_processada(_mid)
            _alertar_dono("ERRO NAO TRATADO no webhook: " + repr(e),
                          num, content)
            reply = ({"number": num, "text":
                      ("Deu um problema aqui do meu lado e eu não consegui "
                       "processar isso. 😕 Me manda de novo, por favor?")}
                     if num else None)

        # FAXINA FINAL, num lugar só.
        # "Precisando de ajuda com algo?" e "Posso ajudar com mais alguma
        # coisa?" saíram por caminhos diferentes do código em 03/08 — não
        # adianta caçar na origem. Aqui passa TODA resposta ao usuário, venha
        # do motor v8, do ai_engine clássico ou do onboarding.
        if reply and reply.get("text"):
            reply["text"] = motor_v8.tirar_enchimento(reply["text"])

        # Trial vencido: o item JÁ foi gravado acima. Agora sim o convite.
        # Confirmacao cancelada por outra mensagem: avisa curto, sem roubar
        # a resposta. O pop acontece SEMPRE que ha marcador, mesmo se reply
        # for None — senao o aviso fica preso e aparece dias depois, colado
        # numa resposta de outro assunto.
        _cancel_em = CANCELADO_AVISO.pop(num, None) if num else None
        if (_cancel_em and reply and reply.get("text")
                and (tempo.agora() - _cancel_em).total_seconds() < 120):
            reply["text"] = ("_(cancelei o pedido anterior)_\n\n"
                             + reply["text"])

        nome_venc = TRIAL_VENCIDO.pop(num, None) if num else None
        if nome_venc and reply and reply.get("text"):
            reply["text"] = (f"{reply['text'].rstrip()}\n\n"
                             f"— — —\n{_payment_msg(nome_venc)}")

        # Reconsentimento da base antiga: anexado, nunca no lugar da
        # resposta. O log_dispatch e gravado AQUI, quando o texto realmente
        # entra na resposta — gravar na marcacao queimava o pedido do dia
        # mesmo quando a resposta era None (usuario bloqueado).
        if num and RECONSENTIR.pop(num, None) and reply and reply.get("text"):
            reply["text"] = (reply["text"].rstrip()
                             + jornada.LGPD_RECONSENTIMENTO.format(
                                 termos=TERMS_URL))
            try:
                _uid2 = None
                for _cand in db.list_users():
                    if re.sub(r"\D", "", _cand["telefone"]) == num:
                        _uid2 = _cand["id"]
                        break
                if _uid2 is not None:
                    db.log_dispatch(_uid2, "reconsentimento")
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[lgpd] falha ao registrar envio do reconsentimento",
                    exc_info=True)

        # A fila pre-aceite virou item no BANCO? Confere aqui, depois do
        # processamento — invariante em Python, verificado contra o banco.
        # Protegido: roda FORA do try/except do handle_incoming e ANTES do
        # envio; sem a protecao, uma excecao aqui deixava o usuario sem
        # resposta e o reenvio era descartado pelo dedup.
        if num:
            try:
                _conferir_fila_virou_item(num)
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[fila] conferencia de itens falhou", exc_info=True)
                _alertar_dono("FILA: conferencia de itens falhou", num, "")

        # HORA NO PASSADO: avisa em vez de deixar a pessoa descobrir com um
        # "chegou a hora" fora de hora. (caso da Carol, 11/08)
        if num and reply and reply.get("text"):
            try:
                _u = None
                for _c in db.list_users():
                    if re.sub(r"\D", "", _c["telefone"]) == num:
                        _u = _c
                        break
                if _u:
                    _ult = db.ultimo_item(_u["id"]) or {}
                    # SO se o item nasceu NESTA mensagem. Sem isto, quem
                    # tem uma conta vencida em aberto levaria o aviso
                    # grudado em qualquer resposta ("bom dia", "quanto
                    # gastei") — o bot cutucando sobre coisa que ela nao
                    # perguntou.
                    _novo = False
                    try:
                        _crit = datetime.strptime(
                            _ult.get("data_criacao") or "",
                            "%Y-%m-%d %H:%M:%S")
                        _novo = (tempo.agora() - _crit).total_seconds() < 30
                    except Exception:
                        _novo = False
                    if (_novo and _ult.get("status") == "pendente"
                            and _hora_ja_passou(_ult.get("data_vencimento"),
                                                _ult.get("hora_alvo"))
                            and not PASSADO_AVISADO.get(_ult.get("id"))):
                        PASSADO_AVISADO[_ult["id"]] = True
                        reply["text"] = (
                            reply["text"].rstrip()
                            + "\n\n⚠️ Só confirmando: essa data e hora *já "
                              "passaram*. Se era pra outro dia, me manda a "
                              "data certa que eu corrijo.")
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[passado] falha ao checar hora no passado", exc_info=True)

        # M1.3 — OFERTA NO PICO DE CONFIANCA.
        # Comando digitado e feature morta: ninguem adivinha "kits". O
        # momento que converte e logo depois do PRIMEIRO item dar certo —
        # ela acabou de ver que funciona. Uma vez por pessoa, anexado, sem
        # roubar a resposta.
        if num and reply and reply.get("text"):
            try:
                _uk = None
                for _c in db.list_users():
                    if re.sub(r"\D", "", _c["telefone"]) == num:
                        _uk = _c
                        break
                if (_uk and not KIT_JA_OFERECIDO.get(num)
                        and len(db.list_items(_uk["id"])) == 1
                        and _uk.get("lgpd_aceite_em")
                        and not _uk.get("onboarding_step")):
                    KIT_JA_OFERECIDO[num] = True
                    KIT_CONVITE[num] = tempo.agora()
                    # ARITMETICA DO TRIAL: se o unico item dela so avisa
                    # depois dos 14 dias (IPVA e anual, revisao e semestral),
                    # ela cancela sem NUNCA ver o produto funcionar. Nesse
                    # caso a oferta puxa algo de repeticao curta, que prova
                    # o valor dentro da janela.
                    _longe = False
                    try:
                        _it = db.list_items(_uk["id"])[0]
                        _dv = (_it.get("data_vencimento") or "")[:10]
                        if _dv:
                            _d = datetime.strptime(_dv, "%Y-%m-%d").date()
                            _longe = (_d - tempo.hoje()).days > 14
                    except Exception:
                        _longe = False
                    if _longe:
                        _oferta = ("\n\n— — —\n"
                                   "\U0001F9E0 Guardado. Mas esse s\u00f3 toca\n"
                                   "l\u00e1 na frente.\n\n"
                                   "Me d\u00e1 algo da sua *semana* que eu\n"
                                   "te mostro funcionando j\u00e1.\n"
                                   "Responda *sim* que eu te ajudo.")
                    else:
                        _oferta = ("\n\n— — —\n"
                                   "\U0001F9E0 Esse foi o primeiro.\n"
                                   "Quer montar o resto da rotina?\n"
                                   "Responda *sim* que eu te mostro.")
                    reply["text"] = reply["text"].rstrip() + _oferta
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[kits] falha na oferta pos-primeiro-item", exc_info=True)

        # M1.4 — o audio virou N itens mesmo?
        # A pessoa desabafa 3 coisas num audio de 20s. Se o motor entendeu
        # 1, ela sai achando que anotou tudo e descobre no vencimento — a
        # falha silenciosa que este projeto persegue. Aqui a gente NAO
        # conserta o modelo: detecta e diz a verdade, com recibo do que
        # entrou.
        _au = AUDIO_ESPERADO.pop(num, None) if num else None
        if _au and reply and reply.get("text"):
            try:
                _criados = len(db.list_items(_au["uid"])) - _au["antes"]
                if _criados < _au["esperado"]:
                    reply["text"] = (
                        reply["text"].rstrip()
                        + "\n\n— — —\n"
                          "\U0001F3A4 Ouvi mais de uma coisa nesse áudio\n"
                          "e registrei " + str(max(_criados, 0)) + ".\n"
                          "Faltou algo? Me manda em texto que eu\n"
                          "completo agora.")
                    _alertar_dono(
                        "AUDIO: esperava ~" + str(_au["esperado"]) +
                        " itens e criou " + str(_criados), num,
                        (_au.get("txt") or "")[:120])
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[audio] falha ao conferir itens do brain dump",
                    exc_info=True)

        falha_depois = getattr(motor_v8, "ULTIMA_FALHA", "")
        if falha_depois and falha_depois != falha_antes:
            # Só alerta o que REALMENTE machucou o usuário. Reconsulta que
            # deu certo, valor resgatado, duplicata evitada e pergunta
            # bloqueada são o sistema se defendendo — o dono não precisa
            # saber. Alertar auto-correção encheu o chat de ⚠️ e some com o
            # sinal no meio do ruído.
            _AUTOCORRECAO = ("reconsultando", "remendado", "resgatei",
                             "atualizei em vez de duplicar", "descartada",
                             "bloqueada",
                             # v17.4 — as auto-correções novas também são
                             # SUCESSO, não falha. Sem isto o dono recebe um
                             # ⚠️ a cada mensagem que o sistema arrumou
                             # sozinho, e alerta que toca sempre é alerta que
                             # ninguém lê — some com o sinal de verdade.
                             "completei na mão", "completei na mao",
                             "tirei", "parei de perguntar",
                             "troquei por confirmação",
                             "troquei por confirmacao",
                             "data relativa calculada",
                             # v17.9: o sistema se corrigindo não é incidente.
                             "resposta trocada pelo real", "reancorei",
                             "lido como CONCLUSÃO", "lido como CONCLUSAO",
                             "fundi", "ancorei em", "hora calculada")
            if not any(p in falha_depois for p in _AUTOCORRECAO):
                _alertar_dono(falha_depois, num, content)
        if reply:
            # Botao de resposta rapida quando o texto pedir. Cai pra texto
            # puro sozinho se o interativo falhar — ver botoes.py.
            ok = botoes.enviar_resposta(reply["number"], reply["text"],
                                        send_whatsapp)
            if not ok:
                # Antes o retorno era ignorado: o painel registrava "enviada"
                # mesmo quando a API recusava, escondendo falhas de credencial
                # e rate limit. Agora a falha aparece no log.
                import logging
                logging.getLogger("resolveai").error(
                    "[webhook] FALHA ao enviar resposta p/ …%s",
                    str(reply["number"])[-4:])
                # bot que não consegue responder é a falha mais grave de
                # todas: o usuário fica no vácuo e nem sabe por quê.
                _alertar_dono("NAO CONSEGUI RESPONDER (envio recusado pela "
                              "Wasender — cheque credencial/rate limit)",
                              reply["number"], content)
            try:
                db.log_message(None, reply["number"], "out" if ok else "out_falhou",
                               "texto", reply["text"])
            except Exception:
                pass
        return {"ok": True}

    @app.post("/cron/proactive")
    async def cron_proactive(request: Request):
        """Disparo manual do motor proativo. O agendamento normal roda SOZINHO
        dentro do app (ver _loop_proativo abaixo) — este endpoint fica só para
        forçar na mão (botão do painel, debug).

        Exige token: aberto, qualquer um forçava o bot a mandar mensagem em
        nome do Resolve AI para os seus usuários."""
        if not _painel_autorizado(request):
            return _negado(request)
        db.registrar_cron_ping()
        sent = dispatch_proactive()
        maybe_admin_report()
        relatorio_matinal()
        return {"sent": sent}

    # -----------------------------------------------------------------------
    # AGENDADOR INTERNO
    # -----------------------------------------------------------------------
    # Antes o motor proativo dependia de um cron externo (cron-job.org) bater
    # em /cron/proactive. Se aquele serviço parasse, NENHUM lembrete tocava e
    # nada avisava — foi exatamente o que aconteceu (motor parado por ~6h).
    # Agora o próprio app se agenda: enquanto o container estiver de pé, os
    # alarmes tocam. Sem dependência externa, sem ponto único de falha.
    # O dedup do db.log_dispatch continua garantindo zero mensagem repetida.
    CRON_INTERNO_SEGUNDOS = int(os.environ.get("CRON_INTERNO_SEGUNDOS", "60"))
    CRON_INTERNO_ATIVO = os.environ.get("CRON_INTERNO", "1") != "0"

    async def _loop_proativo():
        import asyncio
        import logging
        log = logging.getLogger("resolveai")
        await asyncio.sleep(15)   # deixa o app terminar de subir
        log.info("[cron-interno] ativo — ciclo de %ds", CRON_INTERNO_SEGUNDOS)
        while True:
            try:
                db.registrar_cron_ping()
                # dispatch_proactive faz I/O bloqueante (httpx sync + sqlite):
                # roda em thread pra não travar o event loop do FastAPI.
                enviados = await asyncio.to_thread(dispatch_proactive)
                if enviados:
                    log.info("[cron-interno] %d disparo(s)", enviados)
                await asyncio.to_thread(maybe_admin_report)
                await asyncio.to_thread(relatorio_matinal)
            except Exception:
                log.warning("[cron-interno] ciclo falhou", exc_info=True)
            await asyncio.sleep(CRON_INTERNO_SEGUNDOS)

    @app.on_event("startup")
    async def _iniciar_cron_interno():
        if CRON_INTERNO_ATIVO:
            import asyncio
            asyncio.create_task(_loop_proativo())

    @app.post("/watchdog")
    @app.get("/watchdog")
    async def watchdog(request: Request):
        """Vigia de auto-recuperação. Chame a cada 1-2 min no cron-job.org
        usando .../watchdog?k=SEU_PAINEL_TOKEN.
        Se a sessão do WhatsApp travar, reinicia sozinho e avisa o admin.
        Exige token: ele pode reiniciar a sessão do WhatsApp."""
        if not _painel_autorizado(request):
            return _negado(request)
        return watchdog_check()

    @app.get("/painel")
    async def painel(request: Request):
        """Dashboard em tempo real.
        Abra http://SEU-IP:8000/painel?k=SEU_PAINEL_TOKEN no navegador."""
        if not _painel_autorizado(request):
            return _negado(request)
        from fastapi.responses import HTMLResponse
        m = db.painel_metricas()
        wa = _instance_state()
        wa_cor = "#22c55e" if wa == "open" else "#ef4444"
        wa_txt = "🟢 Conectado" if wa == "open" else f"🔴 {wa} — reescaneie o QR"
        # heartbeat do cron: o motor está sendo chamado?
        ultimo_cron = db.ultimo_cron_ping()
        cron_ok = False
        cron_txt = "🔴 NUNCA rodou — configure o cron-job.org!"
        if ultimo_cron:
            from datetime import datetime as _dt
            try:
                delta = (tempo.agora() - _dt.fromisoformat(ultimo_cron)).total_seconds()
                if delta < 1200:  # menos de 20 min
                    cron_ok = True
                    cron_txt = f"🟢 Motor ativo (última checagem há {int(delta/60)} min)"
                else:
                    cron_txt = f"🟠 Motor parado há {int(delta/60)} min — verifique o cron-job.org"
            except Exception:
                cron_txt = f"Última checagem: {ultimo_cron[11:16]}"
        cron_cor = "#22c55e" if cron_ok else "#ef4444"
        linhas = ""
        for r in m["ultimas"]:
            seta = "⬅️ recebida" if r["direcao"] == "in" else "➡️ enviada"
            cor = "#e0f2fe" if r["direcao"] == "in" else "#dcfce7"
            hora = (r["ts"] or "")[11:16]
            tel = (r["telefone"] or "")[-4:] if r["telefone"] else "----"
            prev = (r["preview"] or "").replace("<", "&lt;")[:80]
            linhas += (f'<tr style="background:{cor}"><td>{hora}</td>'
                       f'<td>…{tel}</td><td>{seta}</td><td>{prev}</td></tr>')

        # tabela de usuários com ações de admin
        linhas_users = ""
        for u in db.admin_list_users():
            st = u["status"]
            uid = u["id"]
            cor_st = {"ativo": "#22c55e", "trial": "#3b82f6",
                      "cancelado": "#94a3b8", "bloqueado": "#ef4444"}.get(st, "#64748b")
            tel4 = (u["telefone"] or "")[-4:]
            dias = u["dias_trial_restantes"]
            dias_txt = f"{dias}d" if st == "trial" else "—"
            nome = (u["nome"] or "").replace("<", "&lt;")[:20]
            bs = "cursor:pointer;padding:3px 7px;border-radius:6px;font-size:11px;margin:1px"
            btns = (
                f"<button onclick=\"acao({uid},'estender')\" "
                f"style='{bs};border:1px solid #cbd5e1;background:#fff'>+dias</button>"
                f"<button onclick=\"acao({uid},'ativar')\" "
                f"style='{bs};border:1px solid #86efac;background:#f0fdf4'>ativar</button>")
            if st == "bloqueado":
                btns += (f"<button onclick=\"acao({uid},'liberar')\" "
                         f"style='{bs};border:1px solid #fcd34d;background:#fffbeb'>liberar</button>")
            else:
                btns += (f"<button onclick=\"acao({uid},'bloquear')\" "
                         f"style='{bs};border:1px solid #fca5a5;background:#fef2f2'>bloquear</button>")
            linhas_users += (
                f"<tr><td>{nome}</td><td>…{tel4}</td>"
                f"<td><span style='color:{cor_st};font-weight:600'>{st}</span></td>"
                f"<td>{dias_txt}</td><td>{u['n_itens']}</td><td>{btns}</td></tr>")
        html = f"""<!doctype html><html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>Resolve AI — Painel</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f8fafc;margin:0;padding:16px;color:#0f172a}}
h1{{font-size:20px;margin:0 0 4px}}
.sub{{color:#64748b;font-size:13px;margin-bottom:16px}}
.wa{{display:inline-block;padding:6px 12px;border-radius:8px;color:#fff;font-weight:600;font-size:13px;background:{wa_cor};margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px}}
.card .n{{font-size:26px;font-weight:700;color:#00A86B}}
.card .l{{font-size:12px;color:#64748b;margin-top:2px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;font-size:13px}}
td{{padding:8px 10px;border-bottom:1px solid #f1f5f9}}
th{{text-align:left;padding:8px 10px;background:#f1f5f9;font-size:12px;color:#475569}}
.foot{{color:#94a3b8;font-size:11px;margin-top:12px;text-align:center}}
</style></head><body>
<h1>🟢 Resolve AI — Painel ao vivo</h1>
<div class="sub">Atualiza sozinho a cada 15s · {m['total_users']} usuários no total</div>
<div class="wa">WhatsApp: {wa_txt}</div>
<div style="display:inline-block;padding:6px 12px;border-radius:8px;color:#fff;font-weight:600;font-size:13px;background:{cron_cor};margin-bottom:16px;margin-left:8px">Lembretes: {cron_txt}</div>
<button onclick="testarMotor()" style="cursor:pointer;padding:6px 14px;border-radius:8px;border:1px solid #00A86B;background:#00A86B;color:#fff;font-weight:600;font-size:13px;margin-left:8px">▶ Testar motor agora</button>
<div class="grid">
<div class="card"><div class="n">{m['msgs_in_hoje']}</div><div class="l">mensagens recebidas hoje</div></div>
<div class="card"><div class="n">{m['msgs_out_hoje']}</div><div class="l">respostas enviadas hoje</div></div>
<div class="card"><div class="n">{m['users_hoje']}</div><div class="l">novos usuários hoje</div></div>
<div class="card"><div class="n">{m['itens_hoje']}</div><div class="l">itens criados hoje</div></div>
<div class="card"><div class="n">{m['disparos_hoje']}</div><div class="l">lembretes disparados hoje</div></div>
<div class="card"><div class="n">{m['ativos']}</div><div class="l">assinantes ativos</div></div>
<div class="card"><div class="n">{m['trial']}</div><div class="l">em teste grátis</div></div>
<div class="card"><div class="n">R$ {m['mrr']:.0f}</div><div class="l">MRR</div></div>
</div>
<h1 style="font-size:15px">Últimas mensagens</h1>
<table><tr><th>Hora</th><th>Nº</th><th>Direção</th><th>Conteúdo</th></tr>
{linhas if linhas else '<tr><td colspan=4 style="text-align:center;color:#94a3b8;padding:20px">Nenhuma mensagem ainda. Mande um "oi" pro bot pra testar.</td></tr>'}
</table>

<h1 style="font-size:15px;margin-top:24px">👥 Usuários</h1>
<table><tr><th>Nome</th><th>Nº</th><th>Status</th><th>Trial</th><th>Itens</th><th>Ações</th></tr>
{linhas_users}
</table>
<div class="foot">Resolve AI · painel interno · dados ao vivo do servidor de produção</div>
<script>
// o token que abriu a página segue nas chamadas seguintes
const K = new URLSearchParams(location.search).get('k') || '';
const H = {{'Content-Type':'application/json', 'X-Painel-Token': K}};
async function acao(uid, tipo, extra) {{
  let body = {{user_id: uid, acao: tipo}};
  if (tipo === 'estender') {{
    let d = prompt('Quantos dias extras de trial?', '7');
    if (!d) return;
    body.dias = parseInt(d);
  }}
  const r = await fetch('/painel/acao', {{method:'POST',
    headers:H, body: JSON.stringify(body)}});
  if (r.ok) {{ location.reload(); }} else {{ alert('Falhou. Tente de novo.'); }}
}}
async function testarMotor() {{
  const r = await fetch('/cron/proactive', {{method:'POST', headers:H}});
  const j = await r.json();
  alert('Motor executado! Lembretes disparados agora: ' + (j.sent||0) +
        '\\n\\nSe você tinha um lembrete na hora, ele foi enviado. ' +
        'Recarregando o painel...');
  location.reload();
}}
</script>
</body></html>"""
        return HTMLResponse(html)

    @app.get("/dash")
    def dash(request: Request):
        """Dashboard MOBILE. Abra no celular e salve na tela de início.

        O /painel antigo continua existindo (tela grande, ações de admin).
        Este aqui é feito pra uma mão só: números grandes, rolagem curta, e
        no topo as três perguntas que importam no beta —
        está conectado? · as pessoas estão usando? · o número está em risco?
        """
        from fastapi.responses import HTMLResponse
        if not _painel_autorizado(request):
            return _negado(request)
        tok = (request.query_params.get("k") or "")
        html = """<!doctype html><html lang="pt-BR"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1220">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Resolve AI</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🟢</text></svg>">
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#0b1220;color:#e6edf7;
 font:15px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
 padding:14px 14px calc(28px + env(safe-area-inset-bottom))}
h1{font-size:17px;margin:0 0 2px;font-weight:700}
.sub{color:#8296b3;font-size:12px;margin-bottom:14px}
.card{background:#131d31;border:1px solid #1f2c47;border-radius:14px;
 padding:14px;margin-bottom:10px}
.card h2{font-size:11px;letter-spacing:.09em;text-transform:uppercase;
 color:#8296b3;margin:0 0 10px;font-weight:700}
.big{font-size:30px;font-weight:800;line-height:1}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.kpi{background:#131d31;border:1px solid #1f2c47;border-radius:14px;padding:13px}
.kpi .l{font-size:11px;color:#8296b3;margin-bottom:5px}
.kpi .v{font-size:25px;font-weight:800;line-height:1}
.kpi .u{font-size:11px;color:#8296b3;margin-top:3px}
.st{display:flex;align-items:center;gap:9px;font-weight:600;font-size:15px}
.dot{width:11px;height:11px;border-radius:50%;flex:none}
.ok{background:#22c55e;box-shadow:0 0 9px #22c55e}
.warn{background:#f59e0b;box-shadow:0 0 9px #f59e0b}
.bad{background:#ef4444;box-shadow:0 0 9px #ef4444}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{color:#8296b3;font-weight:600;text-align:right;padding:5px 3px;
 font-size:10.5px;text-transform:uppercase;letter-spacing:.05em}
th:first-child,td:first-child{text-align:left}
td{padding:6px 3px;text-align:right;border-top:1px solid #1c2740}
.hoje td{background:#16233b;font-weight:700}
.bar{height:5px;background:#1f2c47;border-radius:3px;overflow:hidden;margin-top:6px}
.bar>i{display:block;height:100%;background:#22c55e}
.muted{color:#8296b3}
.tag{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;
 background:#1f2c47;color:#a9bcd8}
.err{color:#ef4444;font-weight:700}
footer{color:#54627a;font-size:11px;text-align:center;margin-top:16px}
</style></head><body>
<h1>Resolve AI</h1>
<div class="sub" id="hora">carregando…</div>
<div id="app"></div>
<footer id="rodape"></footer>
<script>
const K=new URLSearchParams(location.search).get('k')||'';
const $=s=>document.querySelector(s);
const n=v=>(v==null?'—':v);
function card(t,c){return `<div class="card"><h2>${t}</h2>${c}</div>`}
// M2.3 — heatmap de constancia. SVG inline: sem biblioteca, sem CDN, sem
// rede. Um quadrado por dia, semanas em coluna.
// Responde o que numero nenhum responde: as pessoas usam com REGULARIDADE,
// ou usaram muito num dia e sumiram?
function heatmap(serie,c){
 if(!serie||!serie.length||!c) return '';
 // ESCALA POR QUANTIL, nao linear contra o maximo.
 // Linear, um unico dia movimentado jogava TODO o resto na faixa mais
 // fraca: o desenho virava "um quadrado aceso e 89 apagados", que e
 // exatamente a leitura errada ("usaram muito num dia e sumiram") que este
 // heatmap existe pra impedir. Com 11 usuarios, um dia atipico basta.
 const ativos=serie.filter(p=>p.n>0).map(p=>p.n).sort((a,b)=>a-b);
 const q=f=>ativos.length?ativos[Math.min(ativos.length-1,
          Math.floor(ativos.length*f))]:1;
 const c1=q(.25),c2=q(.5),c3=q(.75);
 // Paleta com piso >=3:1 de contraste contra a cor de "dia sem uso" — as
 // duas faixas de baixo eram indistinguiveis de vazio (1.26:1 e 2.09:1).
 const CORES=['#16223a','#1f6b46','#17925c','#2ddf8e','#7df3bb'];
 const L=9,G=2,COLS=Math.ceil(serie.length/7);
 let r='';
 serie.forEach((p,i)=>{
  const col=Math.floor(i/7), lin=i%7;
  let t=0;
  if(p.n>0) t = p.n<=c1?1 : p.n<=c2?2 : p.n<=c3?3 : 4;
  r+=`<rect x="${col*(L+G)}" y="${lin*(L+G)}" width="${L}" height="${L}" rx="2" fill="${CORES[t]}"><title>${p.data}: ${p.n}</title></rect>`;
 });
 return `<div style="margin-top:12px">
  <svg viewBox="0 0 ${COLS*(L+G)} ${7*(L+G)}" width="100%" style="max-height:86px" role="img" aria-label="uso por dia">${r}</svg>
  <div class="muted" style="font-size:11px;margin-top:6px">${c.dias_com_uso} de ${c.janela_dias} dias com uso · ${c.media_por_dia_ativo}/dia ativo · pico ${c.maior_dia}</div></div>`;
}
function gastos(g,falha,base){
 g=g||{};
 if(!Object.keys(g).length&&!falha) return '';
 if(!Object.keys(g).length)
  return `<div class="muted" style="font-size:12px">Não consegui somar os gastos de nenhuma das ${base} pessoas. Os dados estão no banco — é a soma que falhou.</div>`;
 const tot=Object.values(g).reduce((a,b)=>a+b,0);
 const rodape=falha?`parcial — falhei em ${falha} de ${base} pessoas`
                   :`3 meses · toda a base (${base} pessoas)`;
 const brl=v=>'R$ '+v.toFixed(2).replace('.',',');
 let r='';
 for(const [cat,v] of Object.entries(g)){
  r+=`<div style="margin:7px 0">
   <div style="display:flex;justify-content:space-between;font-size:12px">
    <span>${cat}</span><span class="muted">${brl(v)}</span></div>
   <div class="bar"><i style="width:${Math.max(2,v/tot*100)}%"></i></div></div>`;
 }
 return r+`<div class="muted" style="font-size:11px;margin-top:8px">total ${brl(tot)} · ${rodape}</div>`;
}
function kpi(l,v,u){return `<div class="kpi"><div class="l">${l}</div>
 <div class="v">${n(v)}</div>${u?`<div class="u">${u}</div>`:''}</div>`}
async function carrega(){
 let d;
 try{ const r=await fetch('/api/pulso?k='+encodeURIComponent(K),{cache:'no-store'});
      if(!r.ok){$('#app').innerHTML=card('Erro','Token invalido. Confira o link.');return}
      d=await r.json(); }
 catch(e){ $('#hora').textContent='sem conexao — tentando de novo…'; return }
 $('#hora').textContent=d.hora+' · atualiza sozinho';
 const m=d.metricas, e=d.envio, g=d.engajamento, s=d.serie;
 const hj=s[s.length-1]||{};
 const conn=d.conectado?'ok':'bad';
 const risco=(e.risco||'').includes('alto')?'bad':(e.risco||'').includes('aten')?'warn':'ok';
 const hab=(g.veredito||'').includes('🟢')?'ok':(g.veredito||'').includes('🟡')?'warn':'bad';
 const cron=d.cron_min==null?'bad':(d.cron_min<=3?'ok':'warn');
 let h='';
 // 1. esta no ar?
 h+=card('Está no ar?',
  `<div class="st"><span class="dot ${conn}"></span>WhatsApp: ${d.conectado?'conectado':d.whatsapp}</div>
   <div class="st" style="margin-top:9px"><span class="dot ${cron}"></span>
   Motor: ${d.cron_min==null?'nunca rodou':'última checagem há '+d.cron_min+' min'}</div>`);
 // 2. as pessoas estao usando?
 h+=card('As pessoas estão usando?',
  `<div class="st"><span class="dot ${hab}"></span>${g.veredito}</div>
   <div class="big" style="margin:10px 0 2px">${g.por_pessoa_dia}</div>
   <div class="muted" style="font-size:12px">demandas por pessoa por dia (7d) — abaixo de 1 não virou hábito</div>
   <div class="bar"><i style="width:${Math.min(100,g.por_pessoa_dia/3*100)}%"></i></div>
   ${heatmap(d.heatmap,d.constancia)}`);
 // 2b. no que as pessoas gastam
 // desenha tambem quando TUDO falhou: 'todo mundo falhou' e
 // 'ninguem tem despesa' sao indistinguiveis se o card some, e a
 // soma mais incompleta possivel era a unica que nao era dita.
 if((d.gastos&&Object.keys(d.gastos).length)||d.gastos_falharam)
  h+=card('Em que a base gasta',
          gastos(d.gastos,d.gastos_falharam,d.gastos_base));
 // 3. o numero esta em risco?
 h+=card('O número está em risco?',
  `<div class="st"><span class="dot ${risco}"></span>${e.risco}</div>
   <div class="grid3" style="margin-top:10px">
     ${kpi('Pico/min',e.pico_por_minuto,'limite ~6')}
     ${kpi('Proativas',e.proativas,'em 24h')}
     ${kpi('Razão',e.razao_proativa_por_recebida,'ideal &lt;1.5')}
   </div>
   <div class="muted" style="font-size:11px;margin-top:9px">
     freio: ${d.freio.ciclo}/ciclo · ${d.freio.intervalo} entre envios · ${d.freio.por_usuario_dia}/pessoa/dia</div>`);
 // 4. dinheiro — com o aviso do que e estimativa
 const f2=d.financeiro;
 let dec=(f2.decidem_ate_3_dias||[]).map(x=>
   `<tr><td>${x.nome}</td><td>${x.dias===0?'hoje':'em '+x.dias+'d'}</td></tr>`).join('');
 const R=v=>'R$ '+Number(v||0).toFixed(2).replace('.',',');
 const cs=f2.custos||{};
 h+=card('Dinheiro',
  `<div class="grid">
     ${kpi('Bruto',R(f2.bruto),f2.assinantes+' × '+R(f2.preco))}
     `+`<div class="kpi"><div class="l">Líquido — seu bolso</div>
        <div class="v" style="color:${f2.liquido>=0?'#22c55e':'#ef4444'}">${R(f2.liquido)}</div>
        <div class="u">− ${R(f2.custo_total)} de custo</div></div>`+`
   </div>
   <table style="margin-top:11px">
     ${(f2.fixos_detalhe||[]).map(x=>
       `<tr><td class="muted">${x.nome}</td><td>− ${R(x.valor)}</td></tr>`).join('')}
     <tr><td class="muted">IA (${f2.msgs_30d.recebidas} msgs/30d)</td><td>− ${R(cs.llm)}</td></tr>
     ${cs.envio>0?`<tr><td class="muted">Envio (${f2.msgs_30d.enviadas} msgs/30d)</td><td>− ${R(cs.envio)}</td></tr>`:''}
     ${cs.taxa_pagamento>0?`<tr><td class="muted">Taxa de pagamento</td><td>− ${R(cs.taxa_pagamento)}</td></tr>`:''}
     ${cs.imposto>0?`<tr><td class="muted">Imposto</td><td>− ${R(cs.imposto)}</td></tr>`:''}
     <tr><td><b>Custo total</b></td><td><b>− ${R(f2.custo_total)}</b></td></tr>
   </table>
   ${f2.breakeven_assinantes!=null?`<div class="muted" style="font-size:12px;margin-top:9px">
     Empata com <b style="color:#e6edf7">${f2.breakeven_assinantes}</b> assinante(s)</div>`:''}
   <div class="grid" style="margin-top:12px">
     ${kpi('Em teste',f2.em_teste,'ainda decidindo')}
     ${kpi('Conversão',f2.conversao_pct==null?'—':f2.conversao_pct+'%',
           f2.ja_decidiram+' já decidiram')}
   </div>
   <div class="grid" style="margin-top:10px">
     ${kpi('Saiu sem assinar',f2.saiu_sem_assinar,'trial expirou')}
     ${kpi('Cancelados',f2.cancelados)}
   </div>
   ${dec?`<div style="margin-top:12px"><div class="l" style="font-size:11px;color:#8296b3;margin-bottom:5px">DECIDEM ATÉ 3 DIAS</div><table>${dec}</table></div>`:''}
   <div class="muted" style="font-size:11px;margin-top:11px;
     border-top:1px solid #1c2740;padding-top:9px">
     ⚠️ ${f2.aviso}. Assinatura é marcada na mão pelo comando <b>ativar</b>.</div>`);
 // 5. margem por cliente
 const mg=f2.margem||{};
 h+=card('Margem por cliente',
  `<div class="st"><span class="dot ${mg.margem_contribuicao>0?(mg.margem_liquida_cliente>=0?'ok':'warn'):'bad'}"></span>${mg.leitura||''}</div>
   <table style="margin-top:11px">
     <tr><td class="muted">Preço</td><td>${R(mg.preco)}</td></tr>
     <tr><td class="muted">− taxa e imposto</td><td>${R(mg.receita_liquida_unit)}</td></tr>
     <tr><td class="muted">− custo variável dele</td><td>− ${R(mg.custo_variavel_cliente)}</td></tr>
     <tr><td><b>= Margem de contribuição</b></td>
         <td><b style="color:${mg.margem_contribuicao>0?'#22c55e':'#ef4444'}">${R(mg.margem_contribuicao)}</b>
         <span class="muted">(${mg.margem_contribuicao_pct}%)</span></td></tr>
     ${mg.fixo_rateado!=null?`<tr><td class="muted">− fixo rateado</td><td>− ${R(mg.fixo_rateado)}</td></tr>
     <tr><td><b>= Sobra por cliente</b></td>
         <td><b style="color:${mg.margem_liquida_cliente>=0?'#22c55e':'#ef4444'}">${R(mg.margem_liquida_cliente)}</b></td></tr>`
     :'<tr><td class="muted" colspan="2">sem assinante ainda — rateio do fixo indisponível</td></tr>'}
   </table>
   <div class="muted" style="font-size:11px;margin-top:9px">
     <b>Contribuição</b> = o que cada cliente novo acrescenta.
     <b>Sobra</b> = o que fica depois de dividir o fixo. Negativa no começo é
     falta de volume, não produto ruim — desde que a contribuição seja positiva.</div>`);
 // numeros de hoje
 h+=`<div class="grid">
   ${kpi('Usuários',m.total_users,`${m.trial} em teste · ${m.ativos} pagando`)}
   ${kpi('Novos hoje',hj.novos||0)}
   ${kpi('Demandas hoje',hj.recebidas||0,'mensagens que entraram')}
   ${kpi('Itens hoje',hj.itens||0,'guardados no banco')}
 </div>`;
 const f=hj.falhas||0;
 h+=`<div class="grid">
   ${kpi('Avisos enviados',hj.disparos||0,'hoje')}
   `+`<div class="kpi"><div class="l">Falhas de envio</div>
     <div class="v ${f?'err':''}">${f}</div><div class="u">hoje</div></div>`+`
 </div>`;
 // 7 dias
 let linhas=s.map((r,i)=>`<tr class="${i===s.length-1?'hoje':''}">
   <td>${r.rotulo}</td><td>${r.novos}</td><td>${r.ativos}</td>
   <td>${r.recebidas}</td><td>${r.itens}</td>
   <td class="${r.falhas?'err':''}">${r.falhas}</td></tr>`).join('');
 h+=card('Últimos 7 dias',
  `<table><tr><th>Dia</th><th>Novos</th><th>Ativos</th><th>Msgs</th>
   <th>Itens</th><th>Falhas</th></tr>${linhas}</table>`);
 // quem usa
 if(g.top&&g.top.length){
   h+=card('Quem mais usa (7d)', '<table>'+g.top.map(t=>
     `<tr><td>${t.nome}</td><td>${t.n} msgs</td></tr>`).join('')+'</table>');
 }
 // base
 const us=(d.usuarios||[]).slice(0,20).map(u=>`<tr><td>${u.nome}</td>
   <td><span class="tag">${u.status||'trial'}</span></td>
   <td>${u.n_pendentes}/${u.n_itens}</td>
   <td>${u.dias_trial_restantes}d</td></tr>`).join('');
 h+=card('Base',`<table><tr><th>Nome</th><th>Status</th><th>Pend/Total</th>
   <th>Trial</th></tr>${us||'<tr><td class="muted">ninguém ainda</td></tr>'}</table>`);
 $('#app').innerHTML=h;
 $('#rodape').textContent=d.build;
}
carrega(); setInterval(carrega,20000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)carrega()});
</script></body></html>"""
        return HTMLResponse(html)

    @app.get("/api/pulso")
    def api_pulso(request: Request):
        """Todos os números do dashboard num JSON só.

        Separar dados de tela existe por um motivo prático: o painel antigo
        montava HTML no servidor, então cada atualização baixava a página
        inteira. No celular, em 4G, isso é lento e gasta dado. Aqui a tela
        carrega uma vez e só busca números a cada 20s.
        """
        from fastapi.responses import JSONResponse
        if not _painel_autorizado(request):
            return _negado(request)
        m = db.painel_metricas()
        wa = _instance_state()
        ultimo = db.ultimo_cron_ping()
        cron_min = None
        if ultimo:
            try:
                cron_min = int((tempo.agora() - datetime.fromisoformat(
                    ultimo)).total_seconds() // 60)
            except Exception:
                cron_min = None
        return JSONResponse({
            "build": BUILD,
            "hora": tempo.agora().strftime("%d/%m %H:%M"),
            "whatsapp": wa,
            "conectado": wa == "open",
            "cron_min": cron_min,
            "trial_days": TRIAL_DAYS,
            "metricas": m,
            "serie": db.serie_diaria(7),
            "envio": db.pulso_envio(),
            "engajamento": db.engajamento(excluir_telefones=[ADMIN_PHONE, MASTER_PHONE]),
            # M2.3 — o que é conta mora em `_dados_do_painel`, testável; a
            # rota só serializa. Campo montado dentro de handler async é
            # campo que ninguém consegue testar sem subir servidor.
            **_dados_do_painel(),
            "financeiro": db.financeiro(TRIAL_DAYS),
            "usuarios": db.admin_list_users(),
            "freio": {"ciclo": DISPATCH_MAX_PER_CYCLE,
                      "intervalo": f"{ENVIO_INTERVALO_MIN:.0f}-"
                                   f"{ENVIO_INTERVALO_MAX:.0f}s",
                      "por_usuario_dia": MAX_PROATIVAS_POR_USUARIO_DIA},
        })

    @app.post("/painel/resgatar")
    async def painel_resgatar(request: Request):
        """Reprocessa uma mensagem que o webhook PERDEU.

        Em 04/08 a sessão do WhatsApp ficou fora por 3h. Três pessoas entraram
        pela landing nesse intervalo: as mensagens chegaram no aparelho, mas o
        webhook nunca disparou — então elas não existem no banco, não têm
        trial, e ficaram sem resposta nenhuma. Do lado delas, o produto
        simplesmente não funciona.

        Aqui a mensagem original é injetada como se o webhook tivesse rodado
        na hora: mesmo parser, mesmo onboarding, mesmo trial. Não é um "enviar
        mensagem avulsa" — é reexecutar o fluxo que faltou, o que também evita
        que a pessoa fique num estado meio-criado.

        Uso: {"telefone": "5511...", "texto": "...", "nome_push": "..."}
        """
        from fastapi.responses import JSONResponse
        if not _painel_autorizado(request):
            return _negado(request)
        try:
            body = await request.json()
            tel = re.sub(r"\D", "", str(body.get("telefone") or ""))
            texto = (body.get("texto") or "").strip()
            if not tel or not texto:
                return JSONResponse({"ok": False,
                                     "erro": "telefone e texto são obrigatórios"},
                                    status_code=400)
            payload = {"data": {"messages": {
                "key": {"remoteJid": f"{tel}@s.whatsapp.net",
                        "fromMe": False, "id": f"resgate-{tel}"},
                "pushName": body.get("nome_push") or "",
                "message": {"conversation": texto}}}}
            try:
                # tipo "resgate_painel", NAO "texto" (auditoria M2.0, P1-2):
                # aqui quem escreve e o dono, pelo painel, no lugar da
                # pessoa. Se isso contasse como entrada, a janela de 24h
                # abriria no NOSSO banco sem a pessoa ter falado — e a Meta,
                # que nao conhece o nosso msg_log, recusaria o texto livre.
                # Seria o guardrail da janela furado pelo caminho que o dono
                # usa justamente com quem sumiu.
                db.log_message(None, tel, "in", "resgate_painel", texto)
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[resgate] falha ao logar a mensagem no painel",
                    exc_info=True)
            reply = handle_incoming(payload)
            if not reply or not (reply.get("text") or "").strip():
                return JSONResponse({"ok": False, "enviado": False,
                                     "motivo": "motor não gerou resposta"})
            reply["text"] = motor_v8.tirar_enchimento(reply["text"])
            enviado = send_whatsapp(reply["number"], reply["text"])
            try:
                db.log_message(None, reply["number"],
                               "out" if enviado else "out_falhou",
                               "texto", reply["text"])
            except Exception:
                pass
            return JSONResponse({"ok": True, "enviado": bool(enviado),
                                 "resposta": reply["text"]})
        except Exception as e:
            import logging
            logging.getLogger("resolveai").warning("[resgate] falhou",
                                                   exc_info=True)
            return JSONResponse({"ok": False, "erro": str(e)},
                                status_code=400)

    @app.post("/painel/acao")
    async def painel_acao(request: Request):
        """Ações de admin do painel: estender trial, bloquear, ativar, etc.
        AÇÕES DESTRUTIVAS (apagar usuário/item) — exige token."""
        from fastapi.responses import JSONResponse
        if not _painel_autorizado(request):
            return _negado(request)
        try:
            body = await request.json()
            uid = int(body.get("user_id"))
            acao = body.get("acao")
            ok = False
            if acao == "estender":
                ok = db.admin_extend_trial(uid, int(body.get("dias", 7)))
            elif acao == "bloquear":
                ok = db.admin_set_status(uid, "bloqueado")
            elif acao == "ativar":
                ok = db.admin_set_status(uid, "ativo")
            elif acao == "liberar":  # desbloqueia -> volta pra trial
                ok = db.admin_set_status(uid, "trial")
            elif acao == "apagar":
                db.delete_user(uid); ok = True
            elif acao == "listar_itens":
                return JSONResponse({"ok": True,
                                     "itens": db.itens_abertos(uid, limite=100)})
            elif acao == "listar_concluidos":
                # Ver o que foi concluido. Precisou existir porque um bug de
                # 05/08 deu baixa em item que o usuario nao pediu, e nao havia
                # como saber o que tinha sumido.
                with db.get_conn() as _c:
                    _r = _c.execute(
                        "SELECT id, descricao, status FROM items "
                        "WHERE user_id=? AND status!='pendente' "
                        "ORDER BY id DESC LIMIT 50", (uid,)).fetchall()
                return JSONResponse({"ok": True, "itens": [
                    {"id": x[0], "descricao": x[1], "status": x[2]} for x in _r]})
            elif acao == "reabrir_item":
                # Desfaz baixa indevida. O item volta a existir pro usuario sem
                # nenhuma mensagem — so reaparece no "Ver tudo". Reparar dado que
                # a gente destruiu nao deve custar constrangimento a quem foi
                # lesado, nem revelar que alguem olhou a conversa dele.
                with db.get_conn() as _c:
                    _cur = _c.execute(
                        "UPDATE items SET status='pendente' "
                        "WHERE id=? AND user_id=?",
                        (int(body.get("item_id")), uid))
                    ok = _cur.rowcount == 1
                return JSONResponse({"ok": ok})
            elif acao == "apagar_item":
                # remove item específico (lixo de teste, duplicata).
                ok = db.apagar_item(int(body.get("item_id")), uid)
            return JSONResponse({"ok": ok})
        except Exception as e:
            return JSONResponse({"ok": False, "erro": str(e)}, status_code=400)

except ImportError:
    app = None  # permite importar handle_incoming em testes sem fastapi
