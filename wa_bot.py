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
import documento  # M3.5: imagem que NAO e boleto — propoe e a pessoa confirma
import podcast  # M4.2: mini-podcast semanal, um nicho por pessoa
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
BUILD = "v30.3-um-painel-so-e-a-jogada-que-nao-enxerguei-2026-09-04"

# LOGGER NO MODULO, nao so dentro de cada funcao.
#
# `log` era definido localmente em umas cinco funcoes, e todo bloco novo
# que escrevia `log.warning` fora delas estourava NameError — tres vezes
# so nesta semana, sempre num caminho de ERRO, que e onde ninguem olha
# ate quebrar. As definicoes locais continuam la e apenas sombreiam esta;
# o que muda e que agora existe uma pra quem esquecer.
import logging
log = logging.getLogger("resolveai")

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

# POR QUE A REATIVACAO NAO SAIU (M6.3).
#
# O `canal.falar` devolve `motivo` em toda recusa, e isso ia so pro log — que
# so se le com acesso a VPS. Sem isso aqui, a unica saida era adivinhar, e
# adivinhar em cima de mensagem que vai pra gente de verdade e o jeito errado.
#
# SO A STRING DO MOTIVO E A HORA. Nenhum telefone, nome ou id.
ULTIMA_RECUSA_REATIVACAO: dict = {}

# O MOTOR PROATIVO ESTA VIVO? (M6.4)
#
# A pergunta anterior a todas as outras. Sem ela eu investiguei duas
# hipoteses erradas sobre POR QUE a fila nao andava, quando a possibilidade
# de o ciclo nem estar rodando nunca foi descartada — e a unica prova disso
# era um log que exige acesso a VPS.
#
# So contagens e hora.
ULTIMO_CICLO: dict = {}

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
# OS COMANDOS QUE DEVOLVEM O LINK DE PAGAMENTO, num lugar so.
#
# Vira constante porque o template de fim de trial (MARKETING, 27/08/2026)
# manda a pessoa responder um deles. Corpo de template e contrato com a Meta:
# se alguem renomear o comando aqui, o template continua mandando responder
# uma palavra que o codigo nao atende mais — e a mensagem que existe pra
# converter passa a abrir a janela de 24h e entregar silencio.
# `test_fim_de_trial_pede_uma_acao_que_existe_no_codigo` deriva desta tupla.
COMANDOS_ASSINATURA = ("assinar", "planos", "quero assinar", "pagar")

# LINKS DE ASSINATURA DO MERCADO PAGO (Kevin, 28/08/2026).
#
# Ficam como DEFAULT no codigo, nao so em env var, de proposito: o default
# antigo era "https://SEU-LINK-DE-PAGAMENTO", e uma VPS sem a variavel
# configurada entregava esse placeholder literal a quem respondesse
# "assinar" — a pessoa mais valiosa que este bot encontra, no unico momento
# de conversao que o produto tem. Link de cobranca e publico por natureza
# (o cliente clica nele), entao versiona-lo nao expoe segredo nenhum. A env
# var continua valendo como override, pra trocar de link sem deploy.
#
# Sao ASSINATURAS recorrentes no Mercado Pago: o cartao e cobrado sozinho
# todo mes/ano. O bot nao lembra ninguem de pagar mensalidade e nao toca em
# cobranca — quem cobra e o Mercado Pago.
PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "https://mpago.la/2oashdp")
PAYMENT_LINK_ANUAL = os.environ.get("PAYMENT_LINK_ANUAL",
                                    "https://mpago.la/2n5pEVS")
PRECO_MENSAL = float(os.environ.get("PRECO_MENSAL", "19.90"))
PRECO_ANUAL = float(os.environ.get("PRECO_ANUAL", "149.00"))


def _brl(v: float) -> str:
    return ("R$ %.2f" % v).replace(".", ",")
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
# O NÚMERO DO PRÓPRIO BOT, com default no código (28/08/2026).
#
# Ele monta o convite no recado que a pessoa manda pra um terceiro ("quem me
# lembrou disso foi o Resolve AI — wa.me/…"). Sem ele, a frase sai sem link e
# o único canal de indicação orgânica do produto morre calado: quem recebe o
# recado não tem como virar usuário.
#
# Ficou vazio na VPS até 28/08, junto com o mesmo número faltando na landing
# (que apontava pro placeholder 5511000000000 e não convertia ninguém). Número
# de atendimento é público — versionar não expõe nada, e tira a chance de a
# variável ser esquecida de novo. A env var segue valendo como override.
BOT_PHONE = re.sub(r"\D", "",
                   os.environ.get("BOT_PHONE", "") or "5511988215902")
_MASTER_RESET_RE = re.compile(
    r"^(reset|resetar|zerar|/reset|novo teste|reiniciar teste|sou novo)\b",
    re.IGNORECASE)


def _master_reset_pega(texto: str) -> bool:
    """O modo teste captura esta mensagem? (M3.1)

    COMANDO ESPECÍFICO GANHA DO GENÉRICO — e essa regra nasceu de um
    estrago real, em 28/08/2026.

    `_MASTER_RESET_RE` casa com QUALQUER coisa começando em "resetar", e é
    avaliado antes do `_RESET_TRIAL_RE`. Como o número do dono é MASTER e
    ADMIN ao mesmo tempo, `resetar trial de todos` sempre caía no modo teste:
    apagava o cadastro DELE (6 itens perdidos) e não tocava em nenhum dos 10
    clientes. O comando de reset de trial era inalcançável — nenhuma frase
    funcionaria, e o bot ainda respondia com sucesso, de outra coisa.

    Não basta reordenar os `if`: eles vivem em funções diferentes e a próxima
    pessoa a mexer não veria a dependência. A exclusão fica escrita aqui.
    """
    t = (texto or "").strip()
    if not t:
        return False
    if _RESET_TRIAL_RE.match(t) or _RESET_TRIAL_ANTIGO_RE.match(t):
        return False
    return bool(_MASTER_RESET_RE.match(t))


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
            # SO ATE A PRIMEIRA QUEBRA DE LINHA.
            #
            # O payload e a primeira linha; depois dela vem a mensagem que a
            # pessoa manda ("Oi! Quero comecar..."). Sem este corte, o
            # ULTIMO interesse vinha grudado no texto inteiro
            # ("saude\n\noi! quero comecar...") e nao casava a lista de
            # validos — quem marcava dois perdia todos menos o primeiro, em
            # silencio.
            _cru = partes[2].split("\n")[0]
            ints = [i.strip().lower() for i in _cru.split(",")]
            interesses = ",".join(i for i in ints if i in valid)
        # O ASSUNTO DO AUDIO VEM NA MESMA MENSAGEM (M7.5).
        #
        # A landing acrescenta "E quero o resumo semanal de X." depois do
        # payload. A extracao disso morava so no `_handle_commands`, e o
        # cadastro novo devolve antes de chegar la — a escolha da pessoa
        # sumia e o bot perguntava de novo. Quem le o payload le tudo.
        _nicho = ""
        try:
            import podcast as _pod
            _m = _NICHO_DA_LANDING_RE.search(text or "")
            _nicho = (",".join(_pod.nichos_do_texto(_m.group(1)))
                      if _m else "")
        except Exception:
            log.warning("[landing] nao consegui ler o assunto do audio",
                        exc_info=True)
        return {"nome": nome, "idade": idade, "interesses": interesses,
                "podcast_nicho": _nicho}
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
    if not _master_reset_pega(text):
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
# O COMANDO NAO COMECA MAIS COM "RESETAR" (M3.1, pedido do Kevin).
#
# Dois motivos, os dois vindos do estrago de 28/08/2026:
#
# 1. Qualquer coisa iniciada em "reset"/"resetar"/"zerar" e capturada pelo
#    MODO TESTE (`_MASTER_RESET_RE`), que apaga o cadastro do dono. Comecar
#    o comando com outra palavra tira ele desse campo minado de vez.
# 2. "resetar trial de todos" e facil demais de digitar sem querer pra uma
#    acao que vale a base inteira. O Kevin: "eu nunca mais acho que vou
#    precisar resetar todos, entao deixe um comando menos comum".
#
# A frase antiga continua RECONHECIDA de proposito — nao pra funcionar, mas
# pra ser barrada antes do modo teste. Sem isso, quem digitasse o comando
# velho zeraria os proprios dados de novo, que foi exatamente o acidente.
_RESET_TRIAL_RE = re.compile(
    r"^\s*liberar\s+14\s+dias\s+(?:para|pra)\s+todos"
    r"(?:\s+os\s+clientes)?\s*[.!]?\s*$",
    re.IGNORECASE)

# So pra proteger do modo teste. NAO executa nada.
_RESET_TRIAL_ANTIGO_RE = re.compile(
    r"^\s*(?:resetar|zerar)\s+(?:o\s+)?trial\s+(?:de\s+)?todos\s*[.!]?\s*$",
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
    if low in COMANDOS_ASSINATURA:
        # O "/mês" do anual é DERIVADO do valor, nunca escrito à mão: os dois
        # já estiveram fora de sincronia aqui (R$ 149 anunciado como
        # "R$ 12,40/mês", quando dá 12,42). Preço errado numa mensagem de
        # venda custa a confiança inteira por um detalhe de digitação.
        # A ECONOMIA TAMBEM E DERIVADA, pelo mesmo motivo do "/mês": o
        # desconto ficava pro cliente calcular, e quem calcula desiste.
        # `_meses_gratis` so aparece a partir de 1 mes cheio — "0 meses
        # grátis" seria pior que nao dizer nada.
        _economia = PRECO_MENSAL * 12 - PRECO_ANUAL
        _meses_gratis = int(_economia / PRECO_MENSAL) if PRECO_MENSAL else 0
        _vantagem = ""
        if _economia > 0:
            _vantagem = f" — economiza {_brl(_economia)}"
            if _meses_gratis >= 1:
                _vantagem += (f", quase {_meses_gratis} "
                              f"{'meses' if _meses_gratis > 1 else 'mês'} "
                              f"de graça")
        anual = (f"\n📅 Anual — {_brl(PRECO_ANUAL)} "
                 f"({_brl(PRECO_ANUAL / 12)}/mês){_vantagem}: "
                 f"{PAYMENT_LINK_ANUAL}"
                 if PAYMENT_LINK_ANUAL else "")
        # A FILA DE APROVAÇÃO COMEÇA AQUI (M2.9).
        #
        # Pedir o link e pagar são eventos diferentes, e o bot não tem como
        # saber se o cartão passou — quem sabe é o Kevin, olhando o Mercado
        # Pago. Sem este registro o pedido se perde no meio das conversas e
        # o cliente fica esperando uma ativação que ninguém lembrou de dar.
        try:
            db.log_dispatch(user["id"], "link-pagamento")
        except Exception:
            log.warning("[assinatura] pedido de link do user %s NAO entrou na "
                        "fila de aprovacao — ative pelo telefone",
                        user.get("id"), exc_info=True)
        # O QUE ELA GANHOU, com numero de verdade. Sem contagem, a linha
        # some: "guardei 0 compromissos" e argumento CONTRA a assinatura, e
        # numero inventado numa mensagem de venda custa a confianca inteira.
        _prova = ""
        try:
            _n = len(db.list_items(user["id"]))
            if _n:
                _prova = (f"Até agora eu guardei *{_n}* compromisso"
                          f"{'s' if _n > 1 else ''} seu"
                          f"{'s' if _n > 1 else ''} e te avisei antes de "
                          f"vencer.\n\n")
        except Exception:
            log.warning("[assinatura] nao consegui contar os itens do user %s",
                        user.get("id"), exc_info=True)
        return (f"Bora, {first_name}! 🚀\n\n"
                f"{_prova}"
                f"💳 Mensal — {_brl(PRECO_MENSAL)}: {PAYMENT_LINK}{anual}\n\n"
                f"É assinatura: renova sozinho, você não precisa pagar na mão "
                f"todo mês. Pra sair, é só me mandar *cancelar*.\n\n"
                f"Pagou? Me avisa aqui que eu ativo na hora.")
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
    # pelo bot que só o dono consegue cumprir é promessa quebrada. Uma vez
    # por usuário, registrado no log de disparos.
    #
    # `TRIAL_EXTENSAO_DIAS` = 2 desde 31/08/2026, e o número tem motivo:
    # dois dias bastam pra quem só precisava de um empurrão, e são curtos
    # demais pra quem estava adiando a decisão.
    if _MAIS_TEMPO_RE.match(text.strip()):
        if (user.get("status") or "trial") != "trial":
            return None            # assinante não precisa; deixa o motor falar
        if db.dispatched_ever("extensao-trial", user["id"]):
            faltam = db.trial_days_left(user, TRIAL_DAYS)
            # SEM O NOME DO DONO. Regra dele, textual: "nunca cite o meu
            # nome pra nenhum cliente JAMAIS" — o bot fala pela empresa.
            return (f"Já te dei uma extensão, {user['nome'].split()[0]} — "
                    f"restam *{faltam} dia(s)*. Se precisar de mais, me fala "
                    f"que eu levo pro time. 🙂")
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

    # --- o nicho que a pessoa escolheu na landing -------------------------
    #
    # NÃO CONSOME A MENSAGEM: ela também está se apresentando, e o resto do
    # fluxo precisa continuar normalmente. Isto aqui só guarda a escolha de
    # lado, uma vez.
    if not user.get("podcast_nicho"):
        _m_nicho = _NICHO_DA_LANDING_RE.search(text or "")
        if _m_nicho:
            _n = ",".join(podcast.nichos_do_texto(_m_nicho.group(1)))
            if _n:
                db.update_user_fields(user["id"], podcast_nicho=_n)
                user["podcast_nicho"] = _n
                import logging as _lg
                _lg.getLogger("resolveai").info(
                    "[podcast] nicho %r guardado pra user %s", _n, user["id"])

    # --- amostra do dono: um episódio de cada nicho ----------------------
    #
    # Pedido do Kevin (29/08/2026): "me manda um áudio de cada categoria pra
    # eu ver se tá bom". É o teste manual que decide se a voz e o roteiro
    # prestam antes de isso chegar em cliente.
    #
    # SÓ DO NÚMERO DO DONO, e com frase própria: são cinco áudios seguidos,
    # que é exatamente o padrão de ritmo que a gente evita com cliente.
    if (ADMIN_PHONE and phone == ADMIN_PHONE
            and _AMOSTRA_PODCAST_RE.match(text)):
        return _amostra_de_podcast(user, phone)

    # --- O DONO PEDE AUDIO QUANDO QUISER (M10) ---------------------------
    #
    # Ele valida o produto na mao, entao precisa gerar na hora, do tema que
    # ele escolher, sem esbarrar no teto de uma vez por janela — que existe
    # pra proteger CLIENTE de rajada, nao pra impedir inspecao.
    #
    # VEM ANTES do caminho do cliente de proposito: senao o "quero ouvir"
    # dele cairia no fluxo normal e ele levaria "voce ja ouviu o episodio
    # deste periodo" justamente quando quer conferir.
    if ADMIN_PHONE and phone == ADMIN_PHONE:
        # DECISAO VIVA MATA A PERGUNTA, NA RESPOSTA (auditoria M10, P0).
        #
        # Eu tinha posto a guarda so no ramo de ARMAR — e e na resposta que
        # ela importa. `_handle_commands` so roda com `kind == "texto"`, entao
        # foto e audio nao passam por aqui: nao popam o slot, e ainda assim
        # armam decisao. Sem esta linha, "quero audio" -> foto ambigua -> "1"
        # gerava a amostra, deixava o PENDING pendurado e o item da pessoa
        # sumia. E a baixa de 30/08 outra vez, com outro slot.
        if _pergunta_de_amostra_viva(phone) and _decisao_de_conversa_viva(phone):
            PODCAST_AMOSTRA_PERGUNTA.pop(phone, None)
        if _pergunta_de_amostra_viva(phone):
            _k = _assunto_por_numero(text) or podcast.nicho_valido(text)
            if _k:
                PODCAST_AMOSTRA_PERGUNTA.pop(phone, None)
                return _amostra_de_podcast(user, phone, nicho=_k)
            # DIGITO FORA DA FAIXA REPERGUNTA, nao desiste. "17" num menu
            # de 16 e engano de dedo, nao mudanca de assunto — e desistir
            # ali jogava a frase no motor de anotacao, que respondia "nao
            # identifiquei conta, data nem valor" pra quem so errou o numero.
            if text.strip().rstrip(".)").isdigit():
                return ("Esse número não está na lista — é de *1* a *%d*."
                        % len(podcast.NICHOS))
            # Fora a escolha e o engano de dedo, ele esta falando de outra
            # coisa: nao insiste.
            PODCAST_AMOSTRA_PERGUNTA.pop(phone, None)
        # DUAS PORTAS, NAO UMA (auditoria M10, P2). O `elif` engolia tambem
        # "quero ouvir" — que e o TITULO DO BOTAO do convite semanal — e com
        # isso o dono perdia o caminho do cliente: `_mandar_podcast` (com
        # multi-assunto, legenda "N de M", pergunta de frequencia e o farol)
        # virava inalcancavel pra ele, que e justamente quem precisa validar
        # esse caminho. Agora "quero audio" e a amostra dele e "quero ouvir"
        # e o produto de verdade.
        elif _AMOSTRA_PEDIDO_RE.match(text):
            if _decisao_de_conversa_viva(phone):
                return ("Só um instante — me responde a de cima primeiro. 🙂"
                        "\n\nDepois é só mandar *quero áudio* de novo.")
            PODCAST_AMOSTRA_PERGUNTA[phone] = tempo.agora()
            return ("🎧 De qual tema? Responde o número — eu gero na hora, "
                    "com as notícias dos últimos %d dias.\n\n%s"
                    % (AMOSTRA_JANELA_DIAS,
                       "\n".join("*%d* — %s %s" % (i, d["emoji"], d["rotulo"])
                                 for i, d in enumerate(
                                     podcast.NICHOS.values(), 1))))

    # --- MINI-PODCAST: as tres respostas do convite -----------------------
    #
    # O audio SO sai daqui, como resposta a um toque. Nunca proativo: audio de
    # 3 min chegando sozinho e a mensagem mais intrusiva que existe no
    # WhatsApp, e este numero ja foi restringido duas vezes.
    # "QUERO EXPERIMENTAR", o botao do template de novidade (M11).
    #
    # Ele existia so no `entende_comando` — que nao tem chamador em producao —
    # entao o botao principal do lancamento era decorativo: a pessoa clicava e
    # levava "nao identifiquei conta, data nem valor". Botao que o bot nao
    # atende e a regra que ja custou um P0 nesta base.
    #
    # Cai no mesmo aceite da oferta, que e a lista de assuntos: hoje a unica
    # novidade anunciada por esse template e o podcast, e a lista e o proximo
    # passo da jornada que o dono desenhou.
    if _NOVIDADE_ACEITE_RE.match(text):
        if _decisao_de_conversa_viva(phone):
            return ("Só um instante — me responde a de cima primeiro. 🙂"
                    "\n\nDepois é só mandar *quero o áudio* de novo.")
        PODCAST_PERGUNTA[phone] = tempo.agora()
        return _pergunta_do_nicho()

    if _PODCAST_NAO_QUERO_RE.match(text):
        # CARIMBA A RECUSA (auditoria M5.4, P1-5). Zerar o nicho deixava
        # "disse nao" identico a "nunca escolheu", e o convite voltava. A
        # data fica registrada: recusa nao e banimento, se ELA pedir o bot
        # atende — mas o bot nunca mais oferece sozinho.
        # A PERGUNTA PENDENTE MORRE JUNTO (auditoria M5.5, P1-3). Sem isto
        # ela seguia viva por 20 min: um "games" solto logo depois re-assinava
        # quem tinha acabado de cancelar E apagava o carimbo da recusa.
        PODCAST_PERGUNTA.pop(phone, None)
        # CARIMBA A NOVIDADE JUNTO (auditoria M11). "Nunca mais" e titulo
        # de botao em DOIS lugares: na oferta do podcast e no template de
        # novidade. Como o texto e o mesmo, o bot nao sabe de qual veio — e
        # entre recusar de menos e recusar de mais, recusar de mais e o lado
        # seguro: quem pediu pra nunca mais receber um aviso de audio nao
        # quer o audio nem o proximo anuncio. A justificativa submetida a
        # Meta promete exatamente isso.
        db.update_user_fields(user["id"], podcast_nicho=None,
                              podcast_dia=None,
                              podcast_recusado_em=tempo.agora().isoformat(),
                              novidade_recusada_em=tempo.agora().isoformat())
        return ("Beleza, cancelei o mini podcast. 👍\n\n"
                "Seus lembretes continuam normais — isso aqui era só um "
                "extra.\n\nSe mudar de ideia um dia, é só me mandar "
                "*quero os áudios*.")

    # SO PRA QUEM TEM PODCAST (auditoria M4.2, P2-6). "depois" e "mais
    # tarde" sao palavras de qualquer conversa; sem esta guarda, quem nunca
    # ouviu falar do recurso recebia resposta sobre podcast.
    # "AGORA NAO" VALE PRA TUDO (M8.0).
    #
    # Era tratado so quando a pessoa tinha podcast — em qualquer outra
    # oferta o botao nao fazia nada, e botao decorativo e a regra que ja
    # custou um P0 aqui. Agora ele SEMPRE responde, e o carimbo de cortesia
    # dá a ela 7 dias de silencio de TODA oferta nossa: pedir espaco uma vez
    # e ser atendido em tudo.
    if _PODCAST_DEPOIS_RE.match(text) and not user.get("podcast_nicho"):
        try:
            db.log_dispatch(user["id"], "reativacao")   # carimba a cortesia
        except Exception:
            log.warning("[cortesia] nao consegui carimbar a pausa do user %s",
                        user.get("id"), exc_info=True)
        return ("Tranquilo! 👍 Não te ofereço isso de novo essa semana.\n\n"
                "Seus lembretes continuam normais — é só me mandar o que "
                "você não pode esquecer.")

    if user.get("podcast_nicho") and _PODCAST_DEPOIS_RE.match(text):
        # Nao mexe em `podcast_ultimo`: ela nao ouviu nada. O convite volta
        # no ciclo semanal, sem insistencia hoje.
        return ("Tranquilo! 👍 Deixo pra próxima semana.\n\n"
                "Se quiser antes, é só me mandar *quero os áudios*.")

    if _PODCAST_QUERO_RE.match(text):
        # SEM ASSUNTO ESCOLHIDO, PERGUNTA — nao adivinha.
        #
        # Os 11 testers vieram antes da landing ter selecao de nicho, entao
        # ninguem tem assunto guardado. Escolher por eles seria mandar audio
        # de um tema que a pessoa nao pediu, que e o oposto da regra da casa.
        if not podcast.nichos_da_pessoa(user):
            # NAO PERGUNTA O QUE NAO VAI PODER OUVIR (auditoria M5.6, P1-1).
            #
            # A guarda de decisao viva estava so na RESPOSTA. Efeito medido:
            # com um menu de baixa aberto, o bot perguntava o assunto, a
            # pessoa respondia "futebol" e levava "nao identifiquei conta,
            # data nem valor" — de novo e de novo, por ate 10 min. Perguntar
            # e nao poder ouvir e a mesma jaula com outro nome.
            if _decisao_de_conversa_viva(phone):
                return ("Só um instante — me responde a de cima primeiro. 🙂"
                        "\n\nDepois é só mandar *quero o áudio* de novo.")
            # SLOT PROPRIO, FORA DO `PENDING` (auditoria M5.4, P0-2).
            #
            # Escrever em `PENDING` atropelava decisao viva: quem tinha
            # fotografado um boleto e tocado "Quero ouvir" perdia o `doc`
            # inteiro, calado. O podcast e um extra — nao pode passar por
            # cima da conta que a pessoa acabou de mandar.
            PODCAST_PERGUNTA[phone] = tempo.agora()
            return _pergunta_do_nicho()
        return _mandar_podcast(user, phone)

    # A resposta da pergunta acima.
    #
    # SEM CATCH-ALL (auditoria M5.4, P0-1). A versao anterior respondia "Nao
    # peguei o assunto" a QUALQUER coisa e mantinha a pergunta de pe por 24h:
    # medido pelo auditor, "luz 187 vence dia 20" sumia calado, "paguei a luz"
    # nao dava baixa, e ate o botao "Quero comecar" ficava inalcancavel. Um
    # extra opcional nao pode virar jaula em cima do produto.
    #
    # Duas saidas e mais nada: o nome exato de um assunto, ou uma recusa
    # curta. Todo o resto CAI FORA daqui e segue pro motor normal.
    # O EXTRA NUNCA PASSA NA FRENTE DA DECISAO (auditoria M5.5, P0-1/P1-2).
    #
    # Este bloco roda em `_handle_commands`, que vem ANTES do menu de baixa e
    # dos blocos de confirmacao. Duas coisas quebraram por causa disso:
    #
    #   1. numero: "2" respondendo "qual deles eu dou baixa?" virava
    #      assinatura de podcast e a baixa sumia. (O caminho numerico saiu.)
    #   2. recusa: "Esquece" e "Nao precisa" sao titulos de botao do
    #      documento e do retorno. Com a pergunta de pe, o toque virava
    #      "cancelei o podcast", o `PENDING` ficava intacto, e 20 min depois
    #      o resgate criava justamente o lembrete que a pessoa descartou.
    #
    # A regra que resolve os dois: se existe decisao viva, a pergunta do
    # assunto espera. Ela e conveniencia; a decisao e o produto.
    # P1-2 (M5.6): se uma decisao apareceu DEPOIS da pergunta (a pessoa
    # fotografou um boleto no meio), a pergunta e descartada em vez de ficar
    # de pe. Deixa-la viva era pior: a resposta dela caia fora deste bloco,
    # chegava no resgate de pendencia e matava a decisao do documento.
    if _pergunta_de_nicho_viva(phone) and _decisao_de_conversa_viva(phone):
        PODCAST_PERGUNTA.pop(phone, None)

    # A REGULARIDADE (M9.9) — mesma precedencia da pergunta do assunto: uma
    # decisao viva (baixa, documento) manda mais que uma preferencia.
    if _pergunta_de_freq_viva(phone) and _decisao_de_conversa_viva(phone):
        PODCAST_FREQ_PERGUNTA.pop(phone, None)
    if _pergunta_de_freq_viva(phone):
        _f = _frequencia_por_numero(text)
        if _f:
            PODCAST_FREQ_PERGUNTA.pop(phone, None)
            db.update_user_fields(user["id"], podcast_frequencia=str(_f))
            user["podcast_frequencia"] = str(_f)
            return (f"Combinado! 🎧 Vou te mandar *{_como_recebe(_f)}*.\n\n"
                    f"Cada episódio cobre as notícias desse período — nada "
                    f"repetido, nada que você já ouviu.\n\n"
                    f"_Pra mudar depois, é só dizer_ *muda a frequência*.")
        # NAO INSISTE: quem respondeu outra coisa esta falando de outra
        # coisa, e o padrao semanal ja serve. Perguntar duas vezes uma
        # preferencia e o "encher o saco" que o Kevin vetou em 30/08 — e
        # deixar o slot vivo faria a proxima mensagem dela cair aqui de
        # novo, que e a mesma jaula do menu numerico.
        PODCAST_FREQ_PERGUNTA.pop(phone, None)

    # O PASSO A PASSO — responde mesmo pra quem nao assinou: e ele que
    # explica como assinar.
    if _PODCAST_AJUDA_RE.match(text):
        return _passo_a_passo_do_podcast(user)

    # "MUDA OS ASSUNTOS" — a porta que faltava. Sem ela, quem cansou do
    # assunto que escolheu tinha duas saidas: aguentar ou cancelar o podcast
    # inteiro. Preferencia sem troca vira motivo de cancelamento.
    if _PODCAST_ASSUNTOS_RE.match(text) and podcast.nichos_da_pessoa(user):
        if _decisao_de_conversa_viva(phone):
            return ("Só um instante — me responde a de cima primeiro. 🙂"
                    "\n\nDepois é só mandar *muda os assuntos* de novo.")
        PODCAST_PERGUNTA[phone] = tempo.agora()
        return _lista_de_nichos()

    # "MUDA A FREQUENCIA" — o fecho promete isso, entao tem que existir.
    if _PODCAST_FREQ_RE.match(text) and podcast.nichos_da_pessoa(user):
        PODCAST_FREQ_PERGUNTA[phone] = tempo.agora()
        return _pergunta_da_regularidade()
    if _pergunta_de_nicho_viva(phone):
        _ns = _assuntos_da_resposta(text)
        if _ns:
            PODCAST_PERGUNTA.pop(phone, None)
            db.update_user_fields(user["id"],
                                  podcast_nicho=podcast.guardar_nichos(_ns),
                                  podcast_recusado_em=None)
            user["podcast_nicho"] = podcast.guardar_nichos(_ns)
            _nomes = [podcast.rotulo(k).lower() for k in _ns]
            _lista = (_nomes[0] if len(_nomes) == 1
                      else ", ".join(_nomes[:-1]) + " e " + _nomes[-1])
            _quantos = ("um episódio" if len(_ns) == 1
                        else "%d episódios, um de cada" % len(_ns))
            return (f"Fechado! 🎧 *{_lista}*.\n\n"
                    f"Você vai receber {_quantos}.\n\n"
                    f"Responde *quero ouvir* que eu mando agora.")
        # "cancela o lembrete da luz" casa `_e_recusa` por causa do "cancela".
        # Sem a guarda de outro assunto, o bot respondia "seus lembretes
        # continuam normais" e NAO cancelava nada.
        if _e_recusa(text) and not _e_outro_assunto(text) \
                and len(text.split()) <= 4:
            PODCAST_PERGUNTA.pop(phone, None)
            db.update_user_fields(
                user["id"], podcast_recusado_em=tempo.agora().isoformat())
            return ("Tranquilo! 👍 Seus lembretes continuam normais — o "
                    "áudio era só um extra.")

    # A ESCOLHA DO DIA — e ela agora VALE (M4.7).
    #
    # So com a pergunta pendente: "segunda" numa frase qualquer nao pode
    # virar assinatura de audio.
    if (user.get("podcast_nicho") and not user.get("podcast_dia")
            and user.get("podcast_dia_perguntado")):
        _dia = _DIA_DA_SEMANA_RE.match(text)
        if _dia:
            _nome_dia = _NORMALIZA_DIA.get(
                _sem_acento_simples(_dia.group(1)), _dia.group(1).capitalize())
            db.update_user_fields(user["id"], podcast_dia=_nome_dia)
            return (f"Fechado! 🎧 Toda *{_nome_dia.lower()}* eu te aviso que "
                    f"o resumo de "
                    f"*{podcast.rotulos_da_pessoa(user)}* "
                    f"está pronto.\n\n"
                    f"O áudio só vai quando você tocar em *Quero ouvir* — "
                    f"nunca sozinho. Pra parar, é só dizer _não quero mais o "
                    f"podcast_.")

    # --- "Copiar código": reenvia o código sozinho -------------------------
    #
    # O WhatsApp não tem botão que copia fora de template de autenticação
    # (categoria de OTP, que não pode ser usada pra cobrança). O que dá pra
    # fazer é isto: um toque traz o código pro fim da conversa, sozinho numa
    # mensagem, onde o toque-e-segura → Copiar entrega exatamente o que o app
    # do banco aceita. Serve principalmente quando o lembrete já rolou pra
    # cima e procurar a mensagem do código dá trabalho.
    if _COPIAR_CODIGO_RE.match(text):
        # O ITEM É O DO ÚLTIMO LEMBRETE, não "o que vence primeiro"
        # (auditoria M3.9, P1-3). O botão vai junto com UM lembrete
        # específico, e com D-60/D-30 e IPVA em D-30 o aviso de hoje pode ser
        # de um boleto que vence daqui a dois meses. Devolver o código de
        # outro item é entregar o boleto errado pra quem está pagando agora.
        _item = None
        _lembrado = ULTIMO_COBRADO.get(phone)
        if _lembrado:
            _cand = db.get_item(_lembrado)
            if (_cand and _cand.get("user_id") == user["id"]
                    and _cand.get("status") == "pendente"
                    and (_cand.get("codigo_pagamento") or "").strip()):
                _item = _cand
        if not _item:
            _item = db.item_com_codigo_mais_recente(user["id"])

        _cod = ({"tipo": _item.get("codigo_tipo"),
                 "colavel": _item.get("codigo_pagamento")} if _item else None)
        _so = boleto.mensagem_so_do_codigo(_cod)
        if not _so:
            return ("Não tenho código de pagamento guardado agora. 🤔\n\n"
                    "Me manda a foto ou o PDF do boleto que eu leio o código "
                    "e te devolvo pronto pra colar.")

        # O CÓDIGO SAI POR AQUI, NÃO PELO `return` (auditoria M3.9, P0-2).
        #
        # O que o `_handle_commands` devolve ainda passa por pós-processadores
        # que COLAM texto na resposta — a oferta de kit (que dispara pra quem
        # tem 1 item, ou seja, justamente quem acabou de mandar o primeiro
        # boleto) e o aviso de reconsentimento da base antiga. Qualquer um
        # deles desfazia o motivo desta mensagem existir: a pessoa segurava,
        # copiava, e colava "🧠 Esse foi o primeiro..." junto no campo do
        # banco — e o banco recusa.
        #
        # Mandando daqui, o código vai puro. O `return` leva só o fecho, que
        # pode ser decorado à vontade.
        _nome = boleto.nome_do_codigo(_cod)
        if not send_whatsapp(
                phone, f"Aqui vai o *{_nome}* de *{_item['descricao']}* 👇"):
            # EXPLICAÇÃO NÃO SAIU: o código também não vai (P1-4). Quarenta e
            # quatro dígitos chegando sem uma palavra antes parece invasão,
            # não serviço.
            return ("Não consegui te mandar o código agora. 😕 Tenta de novo "
                    "daqui a pouco.")
        if not send_whatsapp(phone, _so):
            return ("Mandei a mensagem mas o código não foi. 😕 Responde "
                    "*copiar código* que eu tento de novo.")
        return ("Pronto! 👆 É só segurar na mensagem do código e tocar em "
                "*Copiar*.")

    # --- a pessoa está DIZENDO a data que o bot pediu -----------------------
    #
    # Auditoria M3.5 (P1-4): estes dois estados eram becos sem saída. O bot
    # gravava "ajustar_retorno"/"ajustar_documento" no PENDING, pedia a data —
    # e ninguém lia a resposta. A pessoa respondia direitinho e não acontecia
    # nada; pior, o resgate de pendência transformava a oferta num lembrete
    # sem data que ela nunca pediu.
    #
    # Entra ANTES dos blocos de confirmação porque é o estado mais específico:
    # aqui já houve pergunta e a próxima mensagem é a resposta dela.
    _pend_aj = PENDING.get(phone) or {}
    if (_pend_aj.get("tipo") in ("ajustar_retorno", "ajustar_documento")
            and _proposta_viva(_pend_aj, ttl=AJUSTE_TTL_S)):
        # TRÊS PORTAS DE SAÍDA ANTES DE LER A DATA (auditoria M3.6, P1-3).
        #
        # O bloco original pegava QUALQUER frase em que o parser achasse um
        # número de data. Medido pelo auditor: "paguei a luz dia 20" virava a
        # data do documento — e a baixa da luz nunca acontecia. "hoje não
        # precisa" virava um item pra hoje. A janela era de 24h.
        #
        # Recusa e outro assunto saem daqui SEM criar nada; a mensagem segue
        # pro motor normal, que é quem sabe tratar as duas coisas.
        if _e_recusa(text):
            PENDING.pop(phone, None)
            return ("Beleza, não guardei nada. 👍\n\n"
                    "Se mudar de ideia, é só me mandar de novo.")
        # Outro assunto: solta a pendência e NÃO responde — a mensagem segue
        # pro resto do `_handle_commands` e pro motor, que é quem sabe dar
        # baixa, listar e registrar.
        _quando = None if _e_outro_assunto(text) else _data_do_texto(text)
        if _quando:
            PENDING.pop(phone, None)
            # O QUE A PESSOA ESCREVEU GANHA DO QUE O OCR ACHOU (M3.6, P1-2).
            # O bot pediu "me diz o que é e quando vence" e usava só a data,
            # mantendo justamente a descrição que ela tocou em Ajustar pra
            # corrigir. `_descricao_do_texto` devolve None quando ela só
            # mandou a data — aí a antiga continua valendo.
            _dito = _descricao_do_texto(text)
            _hora = _hora_do_texto(text)
            if _pend_aj["tipo"] == "ajustar_retorno":
                _desc = _dito or _pend_aj.get("descricao") or "seu compromisso"
                _cat = ai_engine.classify_category(_desc)
                _avisos = None
            else:
                _doc = _pend_aj.get("doc") or {}
                _desc = _dito or _doc.get("descricao") or "documento"
                _cat = _CATEGORIA_DE_DOC.get(_doc.get("tipo"), "Outros")
                _avisos = ",".join(
                    str(d) for d in documento.avisos(_doc.get("tipo")))
            try:
                db.add_item(user_id=user["id"], tipo="lembrete",
                            categoria=_cat, descricao=_desc[:120],
                            valor_reais=None, data_vencimento=_quando,
                            hora_alvo=_hora,
                            status="pendente", avisar_dias=_avisos or None)
            except Exception:
                logging.getLogger("resolveai").warning(
                    "[ajuste] falha ao guardar", exc_info=True)
                return ("Não consegui guardar agora. 😕 Tenta de novo daqui "
                        "a pouco.")
            return (f"Anotado! ✅ *{_desc}* — "
                    f"{_quando[8:10]}/{_quando[5:7]}/{_quando[0:4]}"
                    f"{(' às ' + _hora) if _hora else ''}.\n\n"
                    f"Te aviso antes. Pode esquecer que eu lembro.")
        # NÃO ENTENDI A DATA: solta a pendência e deixa a mensagem seguir pro
        # motor normal, que sabe ler frase solta. Insistir aqui prenderia a
        # pessoa num loop de "me diz a data" — e ela pode ter mudado de
        # assunto no meio do caminho, que é direito dela.
        PENDING.pop(phone, None)

    # --- resposta à oferta de remarcar: Confirmar / Outra data / Não precisa
    #
    # O bot ofereceu guardar o próximo serviço (unha, dentista) horas depois
    # da baixa. Aqui ele honra a resposta. Só entra com oferta pendente:
    # "confirmar" solto não pode virar item do nada.
    _pend_ret = PENDING.get(phone) or {}
    if (_pend_ret.get("tipo") == "confirmar_retorno"
            and _proposta_viva(_pend_ret)
            and _CONFIRMA_DOC_RE.match(text)):
        _sug = _pend_ret.get("sugestao") or {}
        _desc = _pend_ret.get("descricao") or "seu compromisso"
        _low = text.strip().lower()
        PENDING.pop(phone, None)

        if _low.startswith(("não precisa", "nao precisa", "esquec",
                            "descart", "deixa")):
            return "Tranquilo! 👍 Se quiser marcar depois, é só me dizer."

        if _low.startswith(("outra data", "ajust", "corrig")):
            # CARIMBA JUNTO (auditoria M10, P1): sem `PENDING_EM`, o
            # `_pending_vencido` trata a decisao como ja vencida e
            # `_decisao_de_conversa_viva` devolve False pra uma decisao
            # recem-nascida — que e o que deixa um slot de menu
            # atropela-la.
            PENDING_EM[phone] = tempo.agora()
            PENDING[phone] = {"tipo": "ajustar_retorno", "descricao": _desc,
                              "quando": tempo.agora()}
            return (f"Beleza! Me diz a data que você prefere pra *{_desc}* — "
                    f"pode ser _\"dia 12/10\"_ ou _\"daqui um mês\"_.")

        _prox = _sug.get("proxima")
        if not _prox:
            return "Me diz a data que eu guardo. 📅"
        try:
            db.add_item(user_id=user["id"], tipo="lembrete",
                        categoria=ai_engine.classify_category(_desc),
                        descricao=_desc[:120], valor_reais=None,
                        data_vencimento=_prox, status="pendente")
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[retorno] falha ao guardar", exc_info=True)
            return "Não consegui guardar agora. 😕 Tenta de novo daqui a pouco."
        return (f"Guardado! ✅ *{_desc}* pra {_prox[8:10]}/{_prox[5:7]}.\n\n"
                f"Te aviso antes. Pode esquecer que eu lembro.")

    # --- resposta à foto de documento: Confirmar / Ajustar / Esquece -------
    #
    # O bot propôs o que entendeu de uma imagem que não é boleto. Aqui ele
    # honra a resposta. Só entra se HOUVER proposta pendente pra este
    # telefone: "confirmar" solto, sem contexto, não pode virar item do nada.
    _pend_doc = PENDING.get(phone) or {}
    if (_pend_doc.get("tipo") == "confirmar_documento"
            and _proposta_viva(_pend_doc)
            and _CONFIRMA_DOC_RE.match(text)):
        _doc = _pend_doc.get("doc") or {}
        _low = text.strip().lower()
        PENDING.pop(phone, None)

        if _low.startswith(("esquec", "descart", "deixa")):
            return ("Beleza, não guardei nada. 👍\n\n"
                    "Se mudar de ideia, é só mandar a foto de novo.")

        if _low.startswith(("ajust", "corrig")):
            # NÃO tenta adivinhar a correção. Devolve a bola pra pessoa, que é
            # quem tem o documento na mão — chutar de novo depois de ela dizer
            # que está errado é o jeito mais rápido de perder a confiança.
            # CARIMBA JUNTO (auditoria M10, P1): sem `PENDING_EM`, o
            # `_pending_vencido` trata a decisao como ja vencida e
            # `_decisao_de_conversa_viva` devolve False pra uma decisao
            # recem-nascida — que e o que deixa um slot de menu
            # atropela-la.
            PENDING_EM[phone] = tempo.agora()
            PENDING[phone] = {"tipo": "ajustar_documento", "doc": _doc,
                              "quando": tempo.agora()}
            return (f"Beleza! Me diz do seu jeito o que é e quando vence.\n\n"
                    f"Tipo: _\"garantia da geladeira até 15/08/2027\"_ ou "
                    f"_\"CNH vence 12/03/2027\"_.")

        # --- Confirmar ---
        if not _doc.get("data"):
            # CARIMBA JUNTO (auditoria M10, P1): sem `PENDING_EM`, o
            # `_pending_vencido` trata a decisao como ja vencida e
            # `_decisao_de_conversa_viva` devolve False pra uma decisao
            # recem-nascida — que e o que deixa um slot de menu
            # atropela-la.
            PENDING_EM[phone] = tempo.agora()
            PENDING[phone] = {"tipo": "ajustar_documento", "doc": _doc,
                              "quando": tempo.agora()}
            return ("Só falta a data. 📅\n\n"
                    "Me diz quando vence que eu guardo.")
        # A DATA GRAVADA É A QUE IMPORTA, NÃO A QUE ESTÁ IMPRESSA (P1-3).
        #
        # A âncora da nota fiscal e da receita é a EMISSÃO, que é sempre
        # passado. Gravando ela em `data_vencimento`, o item nascia vencido e
        # o ciclo seguinte cobrava a pessoa por uma nota que ela tinha
        # acabado de mandar. `documento.vencimento` faz a conversão (emissão
        # + garantia de 1 ano, por exemplo) e devolve a validade intacta pros
        # tipos em que ela já é a data certa.
        _venc = documento.vencimento(_doc)
        if not _venc:
            # CARIMBA JUNTO (auditoria M10, P1): sem `PENDING_EM`, o
            # `_pending_vencido` trata a decisao como ja vencida e
            # `_decisao_de_conversa_viva` devolve False pra uma decisao
            # recem-nascida — que e o que deixa um slot de menu
            # atropela-la.
            PENDING_EM[phone] = tempo.agora()
            PENDING[phone] = {"tipo": "ajustar_documento", "doc": _doc,
                              "quando": tempo.agora()}
            return ("Só falta a data. 📅\n\nMe diz quando vence que eu guardo.")
        try:
            db.add_item(user_id=user["id"], tipo="lembrete",
                        categoria=_CATEGORIA_DE_DOC.get(_doc.get("tipo"),
                                                        "Outros"),
                        descricao=(_doc.get("descricao") or "documento")[:120],
                        valor_reais=None,
                        data_vencimento=_venc,
                        status="pendente",
                        # A antecedência viaja com o item: a promessa dizia
                        # "60 e 30 dias antes" e o motor só sabia avisar na
                        # véspera. Agora ela é verdade.
                        avisar_dias=",".join(
                            str(d) for d in documento.avisos(_doc.get("tipo"))
                        ) or None)
        except Exception:
            logging.getLogger("resolveai").warning(
                "[documento] falha ao guardar", exc_info=True)
            return ("Não consegui guardar agora. 😕 Manda de novo daqui a "
                    "pouco que eu tento outra vez.")
        _d = _venc
        return (f"Guardado! ✅ *{_doc.get('descricao')}* — "
                f"{_d[8:10]}/{_d[5:7]}/{_d[0:4]}.\n\n"
                f"Eu te aviso antes. Pode esquecer que eu lembro.")

    # --- "Quero começar": o botão do template de reativação (M3.2) ---------
    #
    # A pessoa clicou depois de semanas sem falar com o bot. A resposta tem
    # que ENSINAR com exemplo concreto, não dizer "manda aí" — o template já
    # disse isso, e repetir sem exemplo é onde ela desiste.
    if _COMECAR_RE.match(text):
        # DIZ O PRAZO. NAO DIZ QUE RENOVOU (auditoria M5.5, P1-4).
        #
        # O Kevin pediu "pra renovar, mande que renovou" — e o problema real
        # dele e verdadeiro: quem ganha dias e nao sabe segue achando que o
        # teste acabou. Mas quem renova e `db.resetar_trial`, disparado pelo
        # painel, em outro momento. Este caminho so LE o prazo.
        #
        # E `_COMECAR_RE` nao e so o botao do template: casa "bora" e
        # "comecar" soltos. Afirmar "renovei seu teste" aqui era mentir pra
        # qualquer um que digitasse "bora" no dia 10 do trial — e pior,
        # ensinar que a renovacao tinha sido de 4 dias.
        #
        # Entao a mensagem diz o que E VERDADE em qualquer um dos casos: ate
        # quando o acesso vale. Depois do reset no painel, esse numero ja
        # aparece maior — sem nenhuma frase precisar mudar.
        _dias_teste = 0
        try:
            # `TRIAL_DAYS`, nao o default 14 da funcao: prometer uma data
            # que o gate de acesso nao honra e o mesmo defeito de data
            # errada, com outro nome.
            _dias_teste = int(db.trial_days_left(user, TRIAL_DAYS) or 0)
        except Exception:
            log.warning("[reativacao] nao consegui ler os dias de trial",
                        exc_info=True)
        # QUEM JA DISSE NAO NAO OUVE A OFERTA DE NOVO (M5.4, P1-5).
        # SO OFERECE PRA QUEM TEM ACESSO (auditoria M5.6, P1-3).
        #
        # Medido: bloqueado, cancelado e vencido recebiam o convite, escolhiam
        # nicho, e o `_mandar_podcast` buscava noticia, escrevia roteiro e
        # chamava o TTS PAGO antes de o envio falhar — com a resposta
        # "responde *quero ouvir* que eu tento de novo", que convida a
        # queimar de novo. E `podcast_ultimo` nao e carimbado quando o envio
        # falha, entao o teto de 1x por semana nao segura o laco.
        _novidade = ""
        if not user.get("podcast_recusado_em") \
                and db.user_can_receive(user, TRIAL_DAYS):
            _novidade = (
                "🎧 *Novidade:* toda semana eu posso te mandar um resumo em "
                "áudio das notícias do assunto que você escolher — duas "
                "pessoas conversando, poucos minutos. É só responder "
                "_quero o áudio_.")
        _linha_trial = ""
        if _dias_teste > 0 and (user.get("status") or "") == "trial":
            _ate = (tempo.hoje() + timedelta(days=_dias_teste)).strftime(
                "%d/%m/%Y")
            _linha_trial = ("Seu teste está valendo até *%s* "
                            "(%d dia%s).\n\n"
                            % (_ate, _dias_teste,
                               "" if _dias_teste == 1 else "s"))
        return (f"Boa, {first_name}! 🎯\n\n"
                f"{_linha_trial}"
                f"Me manda do jeito que você falaria, tudo numa linha só:\n\n"
                f"• _luz 187 vence dia 20_\n"
                f"• _dentista dia 15 às 14h_\n"
                f"• _IPVA em março_\n\n"
                f"Eu guardo e te aviso *antes* de vencer. Pode mandar áudio "
                f"também, se for mais fácil."
                # A NOVIDADE VIAJA NA MENSAGEM QUE JA IA SAIR.
                #
                # Os 11 testers vieram antes de existir escolha de nicho, e
                # alcanca-los fora da janela exigiria template novo — que
                # levaria dias de aprovacao. Aqui a noticia chega na hora em
                # que eles voltam a falar com o bot, sem gastar mensagem
                # nem esperar a Meta.
                + (f"\n\n{_novidade}" if _novidade else ""))

    # --- admin: reset de trial da base inteira (M2.5) -----------------------
    #
    # PORTA ESTREITA, e nao por preciosismo: este comando escreve em TODA a
    # base de uma vez. "me lembra de resetar o trial amanha" e um LEMBRETE,
    # e um `startswith("resetar")` transformaria essa frase numa acao de
    # banco — o mesmo modo de falha do menu 1/2 que custou a FASE 1 inteira.
    # Por isso: frase exata, e so do numero do dono.
    # A FRASE ANTIGA ENSINA A NOVA, em vez de cair no vazio. Ela chega aqui
    # porque `_master_reset_pega` a barra do modo teste; sem esta resposta o
    # dono digitaria o comando velho, veria o bot conversar sobre outra
    # coisa, e concluiria que resetou.
    if (ADMIN_PHONE and phone == ADMIN_PHONE
            and _RESET_TRIAL_ANTIGO_RE.match(text)):
        return ("Esse comando mudou pra ser mais difícil de disparar sem "
                "querer.\n\nAgora é: *liberar 14 dias para todos*\n\n"
                "_(nada foi alterado)_")

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
# DOIS DIAS, NAO SETE. Decisao do Kevin em 31/08/2026, com clientes reais
# entrando: "daremos so mais 2 dias no maximo, isso nao e uma ONG,
# precisamos faturar".
#
# E o numero certo pelo funil, nao so pelo caixa: dois dias sao
# suficientes pra quem so precisava de um empurrao, e curtos demais pra
# quem estava adiando a decisao. Sete dias transformavam o trial de 14
# em 21 pra qualquer um que pedisse — e quem pede mais tempo duas
# semanas depois raramente fecha na terceira.
TRIAL_EXTENSAO_DIAS = int(os.environ.get("TRIAL_EXTENSAO_DIAS", "2"))

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
    # DECISAO NOVA MATA PERGUNTA VELHA — a mesma regra do `_armar_pending`.
    #
    # Os kits tambem respondem por digito solto, e o `_escolha_de_baixa` roda
    # ANTES deles no fluxo. Sem esta linha, quem tinha uma pergunta de baixa
    # armada, pedia os kits e respondia "1" via a conta de luz ser fechada em
    # vez do kit ser escolhido — a pergunta VELHA ganhando da NOVA, que e o
    # inverso da regra da casa. Ficou de fora quando o BAIXA_ESCOLHA nasceu.
    BAIXA_ESCOLHA.pop(phone, None)
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
    BAIXA_ESCOLHA.pop(phone, None)  # decisao nova mata pergunta velha
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


def entende_comando(texto: str) -> bool:
    """O bot reconhece este texto como comando? (M3.0)

    Existe por causa dos BOTÕES. O webhook converte o clique no TÍTULO do
    botão, como se a pessoa tivesse digitado aquilo — então um título que o
    parser não reconhece transforma o caminho mais fácil da interface na
    pior resposta possível: a pessoa clica e leva "não entendi".

    O teste que varre `BOTOES_POR_KIND` deriva daqui: botão novo com título
    inventado quebra a suíte, e não o cliente.
    """
    t = (texto or "").strip()
    if not t:
        return False
    baixo = t.lower()
    return bool(
        _BAIXA_RE.match(t)
        or _ADIAR_RE.match(t)
        or _COMECAR_RE.match(t)
        or _CONFIRMA_DOC_RE.match(t)
        or baixo in LISTA_COMANDOS
        or baixo in COMANDOS_ASSINATURA
        or baixo in _COMANDOS_DO_BOT
        # Os tres botoes do podcast. Sem isto, o clique no botao do
        # TEMPLATE aprovado cairia no LLM e podia virar "nao entendi" —
        # logo depois de a pessoa ter feito exatamente o que o bot
        # pediu. O teste que varre os botoes dos templates cobra isto.
        or _PODCAST_QUERO_RE.match(t)
        or _PODCAST_DEPOIS_RE.match(t)
        or _PODCAST_NAO_QUERO_RE.match(t)
        # "Quero experimentar", do template de novidade (M11). Mesmo motivo:
        # e botao de template APROVADO, entao o clique chega fora da janela e
        # nao pode cair no LLM.
        or _NOVIDADE_ACEITE_RE.match(t))


# OS TRÊS BOTÕES DA FOTO DE DOCUMENTO (M3.5).
#
# "Confirmar" / "Ajustar" / "Esquece" aparecem quando o bot lê uma imagem que
# não é boleto e PROPÕE o que entendeu. Sem tratamento aqui, o clique cairia
# no LLM e podia virar "não entendi" — logo depois de a pessoa ter feito
# exatamente o que o bot pediu.
# QUANTO TEMPO UMA PROPOSTA FICA DE PE (M3.5).
#
# Mais longo que o `PENDING_TTL_S` (20 min, feito pra menu 1/2 na tela) porque
# "quer marcar a proxima unha?" e "isso e uma nota fiscal?" sao perguntas que
# a pessoa responde quando puder, nao na hora. Mas NAO e eterno: sem prazo, um
# "confirmar" digitado dias depois, por outro motivo, criava um item de data
# velha — contexto zumbi virando dado errado na lista.
PROPOSTA_TTL_S = 24 * 3600


# JANELA CURTA PRA "ME DIZ A DATA" (auditoria M3.6, P1-3).
#
# A proposta com botao vive 24h: a pessoa pode abrir o WhatsApp so a noite e
# tocar em Confirmar, e o contexto tem que estar la. Ja o estado de AJUSTE e
# outra coisa — o bot acabou de fazer uma pergunta aberta, e quem responde a
# uma pergunta responde na hora. Vinte minutos depois, a proxima mensagem
# quase certamente e outro assunto, e trata-la como "a data que eu pedi" e
# como o bot sequestrava a conversa.
AJUSTE_TTL_S = 20 * 60


def _proposta_viva(pend: dict, ttl: Optional[int] = None) -> bool:
    """A proposta ainda vale? Sem carimbo, nao vale — fail-closed."""
    quando = (pend or {}).get("quando")
    if not quando:
        return False
    try:
        return ((tempo.agora() - quando).total_seconds()
                <= (ttl or PROPOSTA_TTL_S))
    except Exception:
        return False


# Em que categoria cada documento entra. "Outros" e o fallback honesto:
# categoria errada polui o resumo de gastos e a leitura do painel.
# So categorias que EXISTEM em db.VALID_CATEGORIES. `add_item` faz fallback
# silencioso pra "Outros" quando a categoria e invalida — e fallback silencioso
# e como um item de saude acaba contado como "outros" no resumo sem ninguem
# perceber. Ha teste cobrando que todas aqui sejam validas.
_CATEGORIA_DE_DOC = {
    "nota_fiscal": "Casa",
    "documento": "Outros",
    "receita": "Saúde",
    "vacina": "Pet",
}

_CONFIRMA_DOC_RE = re.compile(
    r"^\s*(confirmar|confirmo|confirma|isso mesmo|"
    r"ajustar|ajusta|corrigir|corrige|outra data|"
    r"esquece|esquecer|descarta|descartar|deixa pra la|"
    r"n[ãa]o precisa|nao precisa)\s*[.!]?\s*$",
    re.IGNORECASE)


# "QUERO COMECAR" — o botão do template de reativação (M3.2).
#
# Aceita com e sem acento porque o título do botão na Meta está sem ("Quero
# comecar"), mas quem digita escreve com. Os dois têm que cair no mesmo
# lugar: é a primeira coisa que a pessoa faz depois de semanas sumida.
_COMECAR_RE = re.compile(
    r"^\s*(quero\s+come[çc]ar|vamos\s+come[çc]ar|come[çc]ar|bora)"
    r"\s*[.!]?\s*$", re.IGNORECASE)


# OS BOTÕES DE CADA AVISO (M3.0).
#
# Títulos curtos (a Meta corta em 20 chars) e, acima de tudo, títulos que
# `entende_comando` reconhece. A ordem importa: a ação mais provável primeiro,
# porque no celular o primeiro botão é o que o polegar alcança.
# O NICHO VEM NA PRIMEIRA MENSAGEM, escolhido na landing. Ela monta
# "...(e o resumo semanal de Futebol)" e o link do WhatsApp já abre com esse
# texto — a pessoa só aperta enviar.
#
# Capturar aqui (e não perguntar depois) é o que faz a escolha dela valer:
# quem clicou no nicho já decidiu, e perguntar de novo no chat é o bot
# mostrando que não prestou atenção.
_NICHO_DA_LANDING_RE = re.compile(
    r"resumo\s+semanal\s+de\s+([^)\n.]{3,40})", re.I)

# ---------------------------------------------------------------------------
# MINI-PODCAST — as frases que o botão devolve (M4.2)
# ---------------------------------------------------------------------------
# O clique num botão do WhatsApp chega como TEXTO (`button_reply.title`),
# então quem atende é a mesma regra que atende quem digita. Por isso cada uma
# aceita também o jeito que a pessoa escreveria sem o botão.
#
# Ancoradas na frase inteira de propósito: "quero ouvir a música que você
# mandou" não pode virar um episódio de podcast.
# O ACEITE DO TEMPLATE DE NOVIDADE (M11).
#
# O corpo e generico ("novidade: {{2}}") pra servir a qualquer lancamento,
# entao o botao tambem e generico. Hoje a unica novidade que sai por ele e o
# podcast, e por isso ele cai no mesmo aceite da oferta — quem clica recebe a
# lista de assuntos, que e o proximo passo da jornada.
#
# QUANDO HOUVER UMA SEGUNDA NOVIDADE, este roteamento precisa saber qual foi
# anunciada. Enquanto so ha uma, apontar pra ela e honesto; inventar um
# registro de "ultima novidade anunciada" agora seria estrutura sem uso.
_NOVIDADE_ACEITE_RE = re.compile(
    r"^\s*(quero\s+experimentar|vou\s+experimentar|quero\s+testar)"
    r"\s*[.!?]?\s*$", re.I)

# A FRASE DA AMOSTRA DO DONO (M10), separada da do cliente de proposito.
#
# "quero ouvir" e o titulo do botao do convite semanal: se ela abrisse a
# amostra, o dono perderia o unico jeito de validar o caminho que o cliente
# percorre — que e exatamente o que ele pediu pra conferir.
_AMOSTRA_PEDIDO_RE = re.compile(
    r"^\s*(quero\s+[áa]udio|quero\s+um\s+[áa]udio|"
    r"me\s+manda\s+um\s+[áa]udio)\s*[.!?]?\s*$", re.I)

_PODCAST_QUERO_RE = re.compile(
    r"^\s*(quero\s+ouvir|manda\s+o\s+(mini\s+)?podcast|"
    r"quero\s+o\s+(mini\s+)?podcast|pode\s+mandar\s+o\s+[áa]udio|"
    # O ARTIGO E OPCIONAL (M10): o dono digita "quero audio" pra colher a
    # amostra dele, e "quero o audio" nao casava com isso — a frase caía no
    # motor de anotacao e voltava "nao identifiquei conta, data nem valor".
    r"quero\s+(o\s+)?[áa]udio|"
    # A FRASE DE VOLTA (M7.7). Toda saida do recurso termina dizendo
    # "quero os audios" — entao ela TEM que ser reconhecida aqui. Prometer
    # uma palavra que o Python nao entende e a regra que custou um P0.
    r"quero\s+os\s+[áa]udios|quero\s+as\s+not[íi]cias|"
    r"quero\s+o\s+resumo(\s+semanal)?)"
    r"\s*[.!?]?\s*$", re.I)

_PODCAST_ASSUNTOS_RE = re.compile(
    r"^\s*(muda[r]?\s+(os\s+)?(assunto|tema)s?|"
    r"troca[r]?\s+(os\s+)?(assunto|tema)s?|"
    r"quero\s+outros?\s+(assunto|tema)s?|"
    r"mudar\s+o\s+que\s+eu\s+receb[oe])\s*[.!?]?\s*$", re.I)

_PODCAST_AJUDA_RE = re.compile(
    r"^\s*(como\s+funciona\s+o\s+(mini\s+)?podcast|"
    r"como\s+funcionam?\s+os\s+[áa]udios|"
    r"passo\s+a\s+passo(\s+do\s+podcast)?|"
    r"ajuda\s+(do\s+)?podcast)\s*[.!?]?\s*$", re.I)

_PODCAST_FREQ_RE = re.compile(
    r"^\s*(muda[r]?\s+a\s+frequ[êe]ncia|mudar?\s+a\s+regularidade|"
    r"trocar?\s+a\s+frequ[êe]ncia|quero\s+mudar\s+a\s+frequ[êe]ncia|"
    r"frequ[êe]ncia)\s*[.!?]?\s*$", re.I)

_PODCAST_DEPOIS_RE = re.compile(
    r"^\s*(agora\s+n[ãa]o|mais\s+tarde|depois|hoje\s+n[ãa]o)"
    r"\s*[.!?]?\s*$", re.I)

# A saída tem que ser fácil de achar: sem ela, a única saída da pessoa é
# bloquear o número — e bloqueio conta contra a qualidade na Meta.
_PODCAST_NAO_QUERO_RE = re.compile(
    r"^\s*(n[ãa]o\s+quero\s+mais(\s+o\s+podcast)?|"
    r"cancela(r)?\s+o\s+(mini\s+)?podcast|"
    r"para(r)?\s+(o\s+)?podcast|sem\s+podcast|"
    # "NUNCA MAIS" E O TITULO DO BOTAO, e ele nao era reconhecido.
    #
    # A oferta do podcast sai com [Quero ouvir | Agora nao | Nunca mais]
    # desde o M5.4, e quem tocasse no terceiro caía no LLM e podia levar
    # "nao entendi" — logo depois de pedir pra nunca mais receber, que e a
    # pior hora possivel pra parecer que o bot ignorou. O teste que varria
    # botao de template nao pegava: a oferta e mensagem livre, nao template.
    r"nunca\s+mais)\s*[.!?]?\s*$", re.I)

# A RESPOSTA DA PERGUNTA DO DIA (M4.7).
#
# Ela tinha saido quando o dia deixou de valer; volta agora que o template
# faz o dia escolhido ser cumprido de verdade. Aceita as tres do botao e
# qualquer dia digitado — quem responde "quarta" tambem esta respondendo.
_DIA_DA_SEMANA_RE = re.compile(
    r"^\s*(segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|sabado|domingo)"
    r"(\s*-?\s*feira)?\s*[.!?]?\s*$", re.I)

# Comando do dono pra ouvir uma amostra de cada nicho antes de soltar.
_AMOSTRA_PODCAST_RE = re.compile(
    r"^\s*(amostra\s+do\s+podcast|testar\s+o\s+podcast|"
    r"podcast\s+de\s+teste)\s*[.!?]?\s*$", re.I)



# O botão que reenvia o código sozinho. Título curto de propósito: a Meta
# corta em 20 caracteres, e botão cortado no meio parece defeito.
BOTAO_COPIAR = "Copiar código"

# QUAL ITEM O ULTIMO LEMBRETE COBROU, por telefone (auditoria M3.9, P1-3).
#
# O botao do WhatsApp devolve so o TITULO, entao o clique nao diz de qual
# lembrete veio. Sem esta memoria, "Copiar codigo" caia no "o que vence
# primeiro" — e com IPVA em D-30 e documento em D-60 o lembrete de hoje pode
# ser de um boleto de dois meses adiante. A pessoa pediria um e receberia
# outro.
#
# Estado de processo, como o PENDING: some no restart, e ai o codigo volta a
# ser o do proximo vencimento, que e um palpite razoavel.
ULTIMO_COBRADO: dict[str, int] = {}

# O clique no botão chega como TEXTO (`button_reply.title`), então quem
# atende é a mesma regra que atende quem digita. Aceita as duas grafias e o
# jeito que a pessoa escreveria sem o botão.
# "me manda o codigo" e "codigo de barras" SAIRAM (auditoria M3.9, P2-8):
# codigo pode ser de rastreio, de cupom, do portao, do medico — e responder
# com a linha digitavel de um boleto pra quem perguntou outra coisa e o bot
# mostrando que nao entendeu. Fica so o que e inequivoco: o titulo do botao,
# suas variacoes diretas, e "copia e cola", que no Brasil so significa PIX.
_COPIAR_CODIGO_RE = re.compile(
    r"^\s*(copiar\s+(o\s+)?c[óo]digo(\s+de\s+barras)?|"
    r"copiar\s+pix|copia\s+e\s+cola(\s+do\s+pix)?)"
    r"\s*[.!?]?\s*$", re.I)

BOTOES_POR_KIND = {
    "vencimento": ["Paguei", "Adiar", "Ver tudo"],
    "vencido": ["Paguei", "Adiar", "Ver tudo"],
    "hora": ["Feito", "Adiar"],
    "resumo": ["Ver tudo"],
    "trial-ending": ["Assinar", "Ver tudo"],
    "winback": ["Ver tudo", "Assinar"],
    "reengajamento": ["Ver tudo", "Feito"],
    # M3.5 — oferta de remarcar servico. Os titulos vem de
    # `recorrencia.BOTOES` e sao os mesmos que `_CONFIRMA_DOC_RE` entende.
    "retorno": ["Confirmar", "Outra data", "Não precisa"],
}

def _botoes_do_disparo(d: dict):
    """Botões deste disparo, ou None. Kind desconhecido não quebra nada.

    Um disparo de GRUPO (vários itens numa mensagem só) não leva "Paguei":
    o clique viraria baixa de UM item, e qual deles seria adivinhação — o
    mesmo erro que em 14/08 deu baixa no item errado. Nesses, só "Ver tudo".
    """
    kind = (d or {}).get("kind") or ""
    # BOTOES QUE VIAJAM NO PROPRIO DISPARO ganham do mapa por kind. O convite
    # do podcast tem botao proprio (o nicho muda o texto, nao os botoes), e
    # duplicar a lista aqui e no `podcast.py` e como as duas versoes
    # divergem — foi exatamente assim que a promessa de D-60 descolou do
    # motor no M3.5.
    if (d or {}).get("botoes"):
        return list(d["botoes"])[:3]
    botoes = BOTOES_POR_KIND.get(kind)
    if not botoes:
        return None
    if not d.get("item_id") and kind in ("vencimento", "vencido", "hora"):
        return ["Ver tudo"]
    # BOLETO COM CÓDIGO TROCA "Ver tudo" POR "Copiar código".
    #
    # São no máximo 3 botões (limite da Meta), então entrar custa sair. Na
    # hora de pagar, "Ver tudo" é o menos útil dos três: a pessoa está com o
    # app do banco aberto, não querendo revisar a lista. E o botão resolve o
    # caso que o toque-e-copia não resolve — quando o lembrete já rolou pra
    # cima e achar a mensagem do código dá trabalho.
    if d.get("tem_codigo") and kind in ("vencimento", "vencido"):
        return [b for b in botoes if b != "Ver tudo"] + [BOTAO_COPIAR]
    return list(botoes)


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


def _resgatar_comprovante_sem_resposta(user: dict, phone: str,
                                       estado: dict) -> bool:
    """Guarda o comprovante cuja pergunta morreu sem resposta.

    So age quando o slot carrega `novo` — isto e, quando quem armou foi a
    foto de um comprovante ambiguo. A pergunta de baixa por texto nao tem
    nada pra resgatar: ali a pessoa nao mandou documento nenhum.

    Nao fecha conta nenhuma: as que estavam em duvida continuam pendentes.
    O comprovante entra como gasto ja pago, que e o unico fato que o papel
    afirma.
    """
    novo = (estado or {}).get("novo") or {}
    if not novo.get("descricao"):
        return False
    try:
        if _conta_ja_guardada(user["id"], novo["descricao"],
                              novo.get("valor_reais"),
                              novo.get("data_vencimento")):
            return False
        db.add_item(
            user_id=user["id"],
            tipo="despesa",
            categoria=ai_engine.classify_category(novo["descricao"]),
            descricao=novo["descricao"],
            valor_reais=novo.get("valor_reais"),
            data_vencimento=novo.get("data_vencimento"),
            status="concluido")
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[baixa] falha ao resgatar comprovante sem resposta",
            exc_info=True)
        return False
    _enviar_avulsa(
        phone,
        f"_(O comprovante de *{novo['descricao']}*"
        f"{_fmt_dinheiro(novo.get('valor_reais'))} ficou sem resposta, "
        f"então guardei como conta nova pra não perder. As outras continuam "
        f"pendentes.)_",
        user.get("id"))
    return True


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
        # PERGUNTA SEM RESPOSTA NAO PODE COMER O COMPROVANTE (P1-1).
        #
        # O bot ja disse "Recebi o comprovante — R$ X". Se a pessoa nao
        # responde e o slot morre calado, ela fica com a confirmacao de
        # recebimento e ZERO registro. Antes do conserto o comprovante virava
        # um item errado, mas aparecia no gasto do mes; sumir e pior.
        #
        # Mesma escolha do `_resgatar_pendencia`: guardar o que havia, avisar
        # que guardou, e nao afirmar nada que a pessoa nao disse — as contas
        # que estavam na duvida continuam pendentes.
        _resgatar_comprovante_sem_resposta(user, phone, estado)
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

    # "NENHUMA DESSAS" — a saida que impede o comprovante de se perder.
    #
    # So a pergunta do comprovante arma essa opcao (id None + `novo`). A
    # chave valor+vencimento SELECIONA mas nao IDENTIFICA: o comprovante
    # pode ser de uma conta que nem esta na lista. Sem esta saida, escolher
    # errado era a unica opcao — ou a pessoa quitava a conta errada, ou o
    # registro sumia.
    #
    # Grava CONCLUIDO porque comprovante e pagamento feito; e sem codigo de
    # pagamento, pela mesma razao do fluxo normal: conta ja paga nao precisa
    # de codigo e guardar isso seria carregar dado sensivel a toa.
    if item_id is None:
        novo = estado.get("novo") or {}
        if not novo.get("descricao"):
            return None
        # O DEDUP TAMBEM VALE AQUI (P1-2 da auditoria).
        #
        # Este ramo chamava `db.add_item` direto, e o caminho ambiguo retorna
        # antes do `_conta_ja_guardada` la em cima — entao mandar a mesma foto
        # de novo depois de responder "nenhuma dessas" gravava o comprovante
        # duas vezes e dobrava o gasto do mes. E exatamente o estrago que esta
        # correcao existe pra evitar, entrando por outra porta.
        _ja = _conta_ja_guardada(user["id"], novo["descricao"],
                                 novo.get("valor_reais"),
                                 novo.get("data_vencimento"))
        if _ja:
            return (f"Essa eu já tenho: *{_ja['descricao']}*"
                    f"{_fmt_dinheiro(_ja['valor_reais'])}.\n\n"
                    f"As outras continuam pendentes.")
        try:
            db.add_item(
                user_id=user["id"],
                tipo="despesa",
                categoria=ai_engine.classify_category(novo["descricao"]),
                descricao=novo["descricao"],
                valor_reais=novo.get("valor_reais"),
                data_vencimento=novo.get("data_vencimento"),
                status="concluido")
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[baixa] falha ao guardar o comprovante como conta nova",
                exc_info=True)
            # NUNCA dizer "guardei" sobre o que nao foi gravado.
            return ("Falhei em guardar aqui. 😕 Me manda a foto de novo, "
                    "por favor?")
        return (f"Guardei como conta nova ✅\n"
                f"*{novo['descricao']}*"
                f"{_fmt_dinheiro(novo.get('valor_reais'))}.\n\n"
                f"Entra no seu gasto do mês. As outras continuam pendentes.")

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


# ---------------------------------------------------------------------------
# DATA ESCRITA À MÃO, LIDA EM PYTHON
# ---------------------------------------------------------------------------
# Usada só depois que o bot PERGUNTOU a data ("me diz quando vence"). Nesse
# ponto a conversa tem uma pergunta e uma resposta, e mandar a frase pro LLM
# só pra virar "2027-03-12" é gastar chamada e aceitar variação onde não pode
# haver — regra 2: se dá pra decidir em Python, decide em Python.
#
# Ela é DELIBERADAMENTE curta. Só entende o que gente escreve respondendo
# "quando?", e devolve None em tudo o mais — e `None` aqui não perde nada: a
# mensagem segue pro motor normal, que é quem sabe interpretar frase solta.
#
# A ORDEM DAS REGRAS É A REGRA (auditoria M3.6, P1-4). A primeira versão
# testava as palavras relativas ("hoje", "mês que vem") ANTES do número, e
# por isso errava calada nas duas construções mais comuns:
#   "não é hoje, é 12/03"        -> devolvia hoje
#   "vence no dia 5 do mês que vem" -> devolvia o dia 29 do mês seguinte
# Devolver `None` é seguro (a mensagem segue pro motor normal). Devolver a
# data ERRADA não é: o item nasce com a data errada e ninguém confere.
#
# Data com ANO explícito vence tudo. Depois "dia N" com o mês dito. Só então
# as palavras relativas — e nenhuma delas vale se a frase tem negação.

# ISO: "2027-03-12". Aparece quando a pessoa copia de outro sistema.
_DATA_ISO_LIVRE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

# Com ano: aceita ponto ("05.12.2026"), porque o ano remove a ambiguidade.
_DATA_COM_ANO_RE = re.compile(
    r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")

# SEM ano: só barra e hífen. O PONTO SAIU de propósito — "R$ 30.12",
# "processo 05.12" e "1.200,00" viravam data. Sem o ano não há nada que
# desempate número de dinheiro de número de calendário.
_DATA_SEM_ANO_RE = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})\b")

_DAQUI_RE = re.compile(
    r"daqui\s+(?:a\s+)?(?:(\d{1,3})|um|uma|1)\s*(dias?|semanas?|m[êe]s(?:es)?|anos?)",
    re.I)
_DIA_SOLTO_RE = re.compile(r"\bdia\s+(\d{1,2})\b", re.I)

_MESES_NOME = ("janeiro", "fevereiro", "mar[çc]o", "abril", "maio", "junho",
               "julho", "agosto", "setembro", "outubro", "novembro",
               "dezembro")
_MES_POR_NOME_RE = re.compile(
    r"\bde\s+(" + "|".join(_MESES_NOME) + r")\b", re.I)

# NEGAÇÃO DESLIGA A PALAVRA RELATIVA. "hoje não precisa" e "amanhã não" são
# recusas, e viravam a data de hoje/amanhã — o item nascia do "não".
_NEGACAO_RE = re.compile(r"\b(n[ãa]o|nem|nunca|jamais)\b", re.I)

# RECORRÊNCIA NÃO É DATA. "todo dia 10" é uma conta que se repete; virar um
# item único pra setembro perde a repetição inteira sem avisar.
_RECORRENTE_RE = re.compile(
    r"\b(todo|todos|toda|todas|sempre|mensalmente|semanalmente)\b", re.I)

# Frases que são RECUSA, não resposta. O bot perguntou a data; isto é a
# pessoa dizendo que não vai responder — e insistir é o que faz ela parar de
# responder de vez.
#
# BUSCA NA FRASE INTEIRA, não só no começo: "hoje não precisa" e "isso aí
# esquece" são recusas que não começam com a palavra da recusa — e a primeira
# versão, ancorada em `^`, deixava "hoje não precisa" virar um item chamado
# *não precisa*, com data de hoje.
_RECUSA_RE = re.compile(
    r"\b(n[ãa]o\s+(precisa|quero|sei|lembro|tenho|vale)|deixa\s+(pra\s+)?l[áa]|"
    r"esquec[ea]|tanto\s+faz|sei\s+l[áa]|cancela)\b", re.I)

# Marcas de OUTRO ASSUNTO. Quem escreve isso não está respondendo "quando
# vence?" — está dando baixa, pedindo a lista, registrando outra coisa.
# Sem esta guarda, "paguei a luz dia 20" virava a data do documento e a
# baixa da luz nunca acontecia (auditoria M3.6, P1-3).
_OUTRO_ASSUNTO_RE = re.compile(
    r"\b(paguei|pago|quitei|resolvi|feito|fiz|comprei|gastei|"
    r"ver\s+tudo|minha\s+lista|meus\s+itens|quanto\s+(eu\s+)?gastei|"
    r"cancela|apagar|assinar|ajuda)\b", re.I)


def _e_recusa(texto: str) -> bool:
    return bool(_RECUSA_RE.search((texto or "").strip()))


def _e_outro_assunto(texto: str) -> bool:
    return bool(_OUTRO_ASSUNTO_RE.search(texto or ""))


def _data_do_texto(texto: str, base=None):
    """Frase -> 'YYYY-MM-DD'. None quando não dá pra ter certeza."""
    t = (texto or "").strip().lower()
    if not t or _RECORRENTE_RE.search(t):
        return None
    hoje = base or tempo.hoje()

    def _valida(a, mes, d):
        # JANELA DE SANIDADE, o mesmo raciocínio do `boleto.py`: "12/03/2126"
        # é dedo escorregando, não um lembrete pra daqui a cem anos. Item com
        # data absurda nunca dispara e fica na lista pra sempre — a pessoa
        # não perde nada por ele ter sido recusado, e perde a confiança na
        # lista se ele ficar lá.
        try:
            alvo = date(a, mes, d)
        except ValueError:
            return None
        hoje_ = base or tempo.hoje()
        if not (hoje_.year - 1 <= alvo.year <= hoje_.year + 10):
            return None
        return alvo.isoformat()

    # 1. ISO — sem ambiguidade nenhuma.
    m = _DATA_ISO_LIVRE_RE.search(t)
    if m:
        return _valida(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # 2. dd/mm/aaaa — o ano dito manda em qualquer palavra da frase.
    m = _DATA_COM_ANO_RE.search(t)
    if m:
        ano = int(m.group(3))
        if ano < 100:
            ano += 2000
        return _valida(ano, int(m.group(2)), int(m.group(1)))

    # 3. "dia 5 de setembro" / "dia 5 do mês que vem" — o dia com o mês dito.
    #    Vem ANTES das palavras relativas: era aqui que "dia 5 do mês que vem"
    #    virava "dia 29 do mês que vem", porque o "mês que vem" casava
    #    primeiro e o 5 era ignorado.
    md = _DIA_SOLTO_RE.search(t)
    if md:
        d = int(md.group(1))
        mm = _MES_POR_NOME_RE.search(t)
        if mm:
            alvo_mes = _MESES_NOME.index(
                next(n for n in _MESES_NOME
                     if re.fullmatch(n, mm.group(1), re.I))) + 1
            ano = hoje.year
            iso = _valida(ano, alvo_mes, d)
            if iso and iso < hoje.isoformat():
                iso = _valida(ano + 1, alvo_mes, d)
            return iso
        if re.search(r"\b(m[êe]s\s+que\s+vem|pr[óo]ximo\s+m[êe]s)\b", t):
            base_mes = _somar_meses(hoje.replace(day=1), 1)
            return _valida(base_mes.year, base_mes.month, d)

    # 4. dd/mm sem ano — sempre pra frente.
    m = _DATA_SEM_ANO_RE.search(t)
    if m:
        d, mes = int(m.group(1)), int(m.group(2))
        iso = _valida(hoje.year, mes, d)
        # SEM ANO, A DATA É SEMPRE PRA FRENTE. "vence 12/03" dito em agosto
        # é março do ano que vem — gravar 12/03 deste ano faria o item nascer
        # vencido e o bot cobrar na hora.
        if iso and iso < hoje.isoformat():
            iso = _valida(hoje.year + 1, mes, d)
        if iso:
            return iso

    # 5. Palavras relativas — e só sem negação na frase.
    if not _NEGACAO_RE.search(t):
        # "FIM DO MÊS" É O ÚLTIMO DIA, não "hoje + 30". Sem esta regra,
        # "fim do mês que vem" caía no ramo genérico de "mês que vem" e
        # devolvia o dia 29 — data errada, calada, com cara de certa.
        _fim = re.search(r"\b(fim|final)\s+d[oe]\s+m[êe]s\b", t)
        if _fim:
            salto = 1 if re.search(r"\b(que\s+vem|seguinte)\b",
                                   t[_fim.end():]) else 0
            primeiro = _somar_meses(hoje.replace(day=1), salto + 1)
            return (primeiro - timedelta(days=1)).isoformat()
        if re.search(r"\bdepois\s+de\s+amanh", t):
            return (hoje + timedelta(days=2)).isoformat()
        if re.search(r"\bamanh[ãa]\b", t):
            return (hoje + timedelta(days=1)).isoformat()
        if re.search(r"\bhoje\b", t):
            return hoje.isoformat()
        if re.search(r"\bsemana\s+que\s+vem\b", t):
            return (hoje + timedelta(days=7)).isoformat()
        if re.search(r"\b(m[êe]s\s+que\s+vem|pr[óo]ximo\s+m[êe]s)\b", t):
            return _somar_meses(hoje, 1).isoformat()
        if re.search(r"\bano\s+que\s+vem\b", t):
            return _somar_meses(hoje, 12).isoformat()

        m = _DAQUI_RE.search(t)
        if m:
            n = int(m.group(1)) if m.group(1) else 1
            unidade = m.group(2)
            if unidade.startswith("dia"):
                return (hoje + timedelta(days=n)).isoformat()
            if unidade.startswith("semana"):
                return (hoje + timedelta(days=7 * n)).isoformat()
            if unidade.startswith("ano"):
                return _somar_meses(hoje, 12 * n).isoformat()
            return _somar_meses(hoje, n).isoformat()

    # 6. "dia 15" sozinho — a próxima ocorrência desse dia.
    if md and not _NEGACAO_RE.search(t):
        d = int(md.group(1))
        if not 1 <= d <= 31:
            return None
        for salto in (0, 1, 2):
            base_mes = _somar_meses(hoje.replace(day=1), salto)
            iso = _valida(base_mes.year, base_mes.month, d)
            if iso and iso >= hoje.isoformat():
                return iso
    return None


# COM DOIS-PONTOS, OS MINUTOS SÃO OBRIGATÓRIOS. Com o `?` valendo pros dois
# separadores, "9:5" virava 09:00 — hora inventada a partir de um número
# truncado, e `hora_alvo` é o que dispara o alarme. Mensagem na hora errada é
# pior que mensagem nenhuma: ensina a pessoa a ignorar o alarme.
_HORA_RE = re.compile(r"\b([01]?\d|2[0-3])\s*(?:h\s*([0-5]\d)?|:\s*([0-5]\d))\b",
                      re.I)


def _hora_do_texto(texto: str):
    """"às 14h", "14:30" -> 'HH:MM'. None quando não há hora na frase.

    Existe porque o ajuste pedia "o que é e quando" e jogava a hora fora:
    "dentista dia 15 às 14h" guardava o dia e perdia o horário, que é
    justamente o dado que faz o lembrete servir.
    """
    m = _HORA_RE.search(texto or "")
    if not m:
        return None
    return "%02d:%s" % (int(m.group(1)), m.group(2) or m.group(3) or "00")


# Conectivo que abre frase e não descreve nada. Só o COMEÇO é limpo — tirar
# preposição do meio transformava "passaporte da minha filha" em "passaporte
# minha filha", que é pior que não ter mexido.
#
# A ALTERNATIVA LONGA VEM PRIMEIRO: o `re` casa a primeira que der certo, não
# a maior. Com "na" listado antes de "na verdade", a frase "na verdade é a
# CNH do meu pai" perdia só o "na" e o item saía chamado "verdade e a CNH do
# meu pai".
_ABERTURA_RE = re.compile(
    r"^(?:\W|\b(?:na\s+verdade|acho\s+que|isso\s+é|isso\s+eh|"
    r"vencem|vence|venc[ei]|ent[ãa]o|at[ée]|"
    r"é|eh|e|o|a|os|as|um|uma|no|na|em|de|do|da|pra|para)\b)+", re.I)

# Sobra de "às 14h" depois que a hora sai: "dentista as" na lista é descuido
# visível.
_SOBRA_FINAL_RE = re.compile(
    r"(\s+\b(a|as|às|ate|até|de|do|da|em|no|na|pra|para|"
    r"vence|vencem|venc[ei])\b)+\s*[,;]?\s*$", re.I)

# CPF, CNPJ e telefone: nunca viram descrição de item.
_IDENTIFICACAO_RE = re.compile(
    r"(\b\d{3}\.\d{3}\.\d{3}-?\d{2}\b|"        # CPF
    r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-?\d{2}\b|"   # CNPJ
    r"\b\d{2}\s?9?\d{4}[- ]?\d{4}\b|"          # telefone
    r"\bcpf\b|\bcnpj\b|\brg\b)", re.I)


def _descricao_do_texto(texto: str):
    """O que sobra da frase depois de tirar data, hora e abertura.

    A pergunta do ajuste é "me diz **o que é** e quando vence" — e a primeira
    versão usava só a data, mantendo a descrição do OCR que a pessoa tinha
    acabado de dizer que estava errada (auditoria M3.6, P1-2). Quem responde
    "não é minha CNH, é o passaporte da minha filha, vence 15/06/2028" tem
    que ver *passaporte da minha filha* na lista.

    Lê a ÚLTIMA oração com conteúdo, não a frase inteira: a correção vem
    depois da negação ("não é X, é Y"), e é o Y que interessa. Oração com
    negação é descartada — ela diz o que a coisa NÃO é.

    None quando não sobra nome nenhum. Aí a descrição antiga continua
    valendo, que é melhor que um item chamado "15/06/2028".
    """
    t = " " + (texto or "") + " "
    for rx in (_DATA_ISO_LIVRE_RE, _DATA_COM_ANO_RE, _DATA_SEM_ANO_RE,
               _DAQUI_RE, _DIA_SOLTO_RE, _HORA_RE, _MES_POR_NOME_RE):
        t = rx.sub(" ", t)
    t = re.sub(r"\b(hoje|amanh[ãa]|semana que vem|m[êe]s que vem|"
               r"pr[óo]ximo m[êe]s|ano que vem)\b", " ", t, flags=re.I)

    # A VÍRGULA DECIMAL NÃO SEPARA ORAÇÃO. Partindo em toda vírgula,
    # "custou R$ 1,50, vence 05/09" virava a descrição "custou R$ 1" — um
    # valor cortado ao meio na lista da pessoa.
    for oracao in reversed(re.split(r"(?<!\d)[,;](?!\d)", t)):
        if _NEGACAO_RE.search(oracao):
            continue
        # DADO DE IDENTIFICAÇÃO NÃO VIRA NOME DE ITEM. O `documento.py` já
        # tem essa regra pro OCR; aqui vale igual, porque a pessoa às vezes
        # responde com o telefone ou o CPF na mesma frase — e esse número
        # ficaria na lista dela, visível, pra sempre.
        if _IDENTIFICACAO_RE.search(oracao):
            continue
        limpo = _ABERTURA_RE.sub("", oracao.strip())
        limpo = re.sub(r"\s{2,}", " ", limpo).strip(" -–—:.")
        limpo = _SOBRA_FINAL_RE.sub("", limpo).strip(" ,;-–—:.")
        if len(re.sub(r"[^A-Za-zÀ-ÿ]", "", limpo)) >= 4:
            return limpo[:120]
    return None


def _somar_meses(d, meses: int):
    ano = d.year + (d.month - 1 + meses) // 12
    mes = (d.month - 1 + meses) % 12 + 1
    dia = min(d.day, [31, 29 if ano % 4 == 0 and (ano % 100 or not ano % 400)
                      else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1])
    return date(ano, mes, dia)


# ESTADOS DE CONVERSA — nunca viram item sozinhos (auditoria M3.5, P1-5).
#
# `_resgatar_pendencia` salva como lembrete qualquer pendência que tenha
# `descricao`, e está certo: aquilo é um item que a pessoa mandou e que ficou
# preso esperando confirmação. Só que as pendências novas (M3.5) NÃO são
# itens — são o bot esperando uma resposta a uma PERGUNTA DELE. Resgatar a
# oferta de remarcar a unha criaria um lembrete de unha sem data que ninguém
# pediu, e "o bot inventa item" é exatamente o que custou a confiança em
# 14/08. Aqui a pendência é descartada; a pergunta some, e nada é criado.
_PENDENCIAS_DE_CONVERSA = frozenset({
    "confirmar_retorno", "ajustar_retorno",
    "confirmar_documento", "ajustar_documento",
})


# Quantos assuntos a `/amostra` manda quando o dono nao pede um especifico.
# Cinco era o catalogo inteiro quando o comando nasceu; com 16 assuntos, varrer
# tudo custa 16 sinteses TTS pagas e 32 mensagens por toque.
AMOSTRA_MAX_NICHOS = 5


def _amostra_de_podcast(user: dict, phone: str, nicho: str = "",
                        provedor: str = "") -> str:
    """Uma amostra de episódios, pro dono julgar antes de soltar.

    Roda o caminho REAL — feed, roteiro, locução, voz, envio. Um dublê aqui
    não serviria pra nada: o que ele quer ouvir é o que o cliente ouviria.

    SEM NICHO, MANDA UM LOTE, NÃO O CATÁLOGO INTEIRO (auditoria M9, P2). O
    catálogo foi de 5 pra 16 assuntos e este comando varria todos: 16 sínteses
    TTS **pagas**, 32 mensagens e uns 3 min de `sleep` segurando a thread, por
    toque. O número nunca foi escolhido — subiu de carona com o catálogo. Pra
    julgar voz e formato, um punhado basta; pra comparar dois provedores, o
    caminho certo é pedir UM nicho, que é o único que compara o mesmo texto.

    Não mexe em `podcast_ultimo` nem carimba nada: isto é inspeção, não
    entrega. Se marcasse, o dono ficaria uma semana sem receber o episódio
    dele por ter testado.
    """
    import random
    import time

    import noticias
    import voz

    if not voz.disponivel():
        return ("Não tem provedor de voz configurado. 🎙️\n\n"
                "Falta a chave da OpenAI (ou `VOZ_PROVEDOR`) no ambiente — "
                "sem ela o podcast nem é oferecido pros clientes.")

    # UM NICHO SO, quando pedido: serve pra comparar dois provedores de voz
    # no MESMO conteudo, que e a unica comparacao que diz alguma coisa.
    alvos = ([podcast.nicho_valido(nicho)] if podcast.nicho_valido(nicho)
             else list(podcast.NICHOS)[:AMOSTRA_MAX_NICHOS])
    linhas, ok, enviados_ate_agora = [], 0, 0
    for chave in alvos:
        rotulo = podcast.rotulo(chave)
        try:
            itens = noticias.buscar(chave, dias=AMOSTRA_JANELA_DIAS)
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[amostra] feed de %s falhou", chave, exc_info=True)
            itens = []
        # SEM NOME, IGUAL AO CLIENTE (auditoria M16, P2). O roteiro do
        # cliente perdeu o nome no M12; se a amostra mantivesse, ela deixaria
        # de ser "o que o cliente ouviria" — que e a unica razao dela existir.
        roteiro = podcast.locucao(chave, itens)
        if not roteiro:
            linhas.append(f"• {rotulo}: sem notícia da semana")
            continue
        audio = voz.sintetizar(roteiro, provedor=provedor or None)
        if not audio:
            linhas.append(f"• {rotulo}: a voz falhou")
            continue
        # ESPACAMENTO, como em todo envio em lote desta casa. Cinco nichos
        # sao ate dez mensagens; manda-las de enfiada e a assinatura de
        # ritmo que ja rendeu 3h de restricao neste numero (ver o comentario
        # do FREIO 2). O `sleep` aqui e seguro: isto roda no handler, que ja
        # esta em thread propria.
        if enviados_ate_agora:
            time.sleep(random.uniform(ENVIO_INTERVALO_MIN,
                                      ENVIO_INTERVALO_MAX))
        enviados_ate_agora += 1
        send_whatsapp(phone, f"🎧 *{rotulo}* — amostra")
        res = wasender.falar_audio(phone, audio, user_id=user["id"])
        if res.get("enviado"):
            ok += 1
            linhas.append(f"• {rotulo}: {podcast.duracao_estimada_s(roteiro)}s"
                          f" · {len(roteiro.split())} palavras")
        else:
            linhas.append(f"• {rotulo}: não saiu ({res.get('motivo')})")

    # SO O AUDIO, SEM RELATORIO (pedido do dono, 02/09/2026: "mande apenas
    # o audio e nao esse texto que veio abaixo"). Ele ja ve a legenda com o
    # nome do tema antes da faixa; o resto era ruido de bastidor na conversa
    # dele. Quando algo NAO sai, ai sim o texto tem serventia — e so ai.
    if ok == len(alvos):
        # O audio e a legenda ja sairam; nao ha texto a acrescentar. Mas
        # devolver "" faria a mensagem seguir pro motor de IA como nao
        # tratada — ver `SEM_RESPOSTA`.
        return SEM_RESPOSTA
    return ("Nem tudo saiu:\n\n" + "\n".join(linhas))


# O dia gravado tem que bater com o que o `scheduler` compara. Sem
# normalizar, "terca" digitado e "Terça" da tabela nunca casariam e a pessoa
# ficaria esperando um lembrete que nunca vem.
_NORMALIZA_DIA = {
    "segunda": "Segunda", "terca": "Terça", "quarta": "Quarta",
    "quinta": "Quinta", "sexta": "Sexta", "sabado": "Sábado",
    "domingo": "Domingo",
}


def _sem_acento_simples(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn")


# A PERGUNTA DO NICHO MORA AQUI, NAO NO `PENDING` (auditoria M5.4, P0-2).
#
# `PENDING` guarda DECISAO — confirmar um boleto, escolher item pra baixa.
# Escrever aqui atropelava essa decisao e o dado da pessoa evaporava. Este
# slot e so uma pergunta de conveniencia: se for atropelado, nao se perde
# nada. TTL curto pelo mesmo motivo do `AJUSTE_TTL_S`: pergunta aberta que
# dura 24h e jaula.
PODCAST_PERGUNTA: dict = {}

# A PERGUNTA DA REGULARIDADE, no mesmo desenho da do assunto: slot proprio,
# fora do `PENDING`, com prazo. Sai depois do primeiro episodio ("gostou?
# escolha quando quer receber") e so entao os numeros 1 a 4 valem.
PODCAST_FREQ_PERGUNTA: dict = {}

# "Qual tema?" esperando resposta do DONO (M10). Slot proprio, como os outros
# dois: numero so vira escolha enquanto a pergunta esta viva.
PODCAST_AMOSTRA_PERGUNTA: dict = {}

# "TRATEI, E NAO HA O QUE DIZER" — precisa ser dizivel (M15).
#
# String vazia nao diz isso: os chamadores testam `if resposta:`, e vazio e
# indistinguivel de "nao tratei". Foi assim que a amostra do dono, que passou
# a nao mandar relatorio, deixou o "13" seguir pro motor de IA — e ele
# respondeu por conta propria com uma lista de noticias INVENTADA, de outro
# assunto, logo abaixo do audio certo.
SEM_RESPOSTA = "\x00sem-resposta"

# A janela da amostra do dono e FIXA em 7 dias. A do cliente varia (5, 7, 15
# ou 30) conforme a escolha de cada um; amostra com janela variavel nao
# compara com nada de uma semana pra outra.
AMOSTRA_JANELA_DIAS = 7


def _decisao_de_conversa_viva(phone: str) -> bool:
    """Tem alguma coisa esperando resposta desta pessoa AGORA?

    O podcast e um extra. Ele nao pergunta, nao ouve e nao aceita recusa
    enquanto existir uma decisao de verdade na mesa — porque "esquece",
    "nao precisa" e "deixa la" pertencem a ELA, nao a nos. Sequestrar uma
    dessas custa o dado da pessoa E carimba um opt-out permanente.

    VIVA, NAO SO PRESENTE (autoauditoria M5.7). A primeira versao lia
    presenca crua nos dicionarios. Os dois estados aqui sobrevivem entre
    mensagens de proposito e os dois tem prazo; estado VENCIDO nao e decisao
    viva, e tratar como se fosse trancava a pessoa fora do recurso por ate
    20 min depois de a decisao ja ter morrido. Cada um e lido pelo MESMO
    criterio que o dono dele usa pra aceitar resposta.

    `CONFERIR_FILA` e `AUDIO_ESPERADO` ficaram DE FORA, e nao por esquecimento:
    os dois sao armados depois do `_handle_commands` e esvaziados no mesmo
    ciclo, entao nunca estao vivos na hora em que esta funcao le. Nao
    protegiam nada — e como nenhum dos dois tem TTL, uma entrada orfa
    (excecao entre armar e esvaziar) bloquearia o pedido de audio pra sempre.
    """
    if PENDING.get(phone) and not _pending_vencido(phone):
        return True
    _baixa = BAIXA_ESCOLHA.get(phone)
    if _baixa:
        try:
            if (tempo.agora() - _baixa["quando"]).total_seconds() \
                    <= BAIXA_ESCOLHA_TTL_S:
                return True
        except Exception:
            # Carimbo ilegivel: o `_escolha_de_baixa` trata isso como estado
            # morto. Aqui vale o mesmo — na duvida a pessoa NAO fica presa.
            log.warning("[baixa] carimbo ilegivel ao medir decisao viva",
                        exc_info=True)
    return False


def _pergunta_de_nicho_viva(phone: str) -> bool:
    quando = PODCAST_PERGUNTA.get(phone)
    if not quando:
        return False
    if (tempo.agora() - quando).total_seconds() > AJUSTE_TTL_S:
        PODCAST_PERGUNTA.pop(phone, None)
        return False
    return True


def _pergunta_de_amostra_viva(phone: str) -> bool:
    """Mesma regra dos outros dois slots, e pelo mesmo motivo: fora da
    pergunta, digito e resposta de menu de OUTRO."""
    quando = PODCAST_AMOSTRA_PERGUNTA.get(phone)
    if not quando:
        return False
    if (tempo.agora() - quando).total_seconds() > AJUSTE_TTL_S:
        PODCAST_AMOSTRA_PERGUNTA.pop(phone, None)
        return False
    return True


def _pergunta_de_freq_viva(phone: str) -> bool:
    """Mesma regra da pergunta do assunto, e pelo mesmo motivo.

    Sem TTL, um "2" digitado dias depois — respondendo qualquer outra coisa —
    viraria "de 7 em 7 dias" calado. Foi assim que o menu numerico derrubou a
    baixa de conta em 30/08.
    """
    quando = PODCAST_FREQ_PERGUNTA.get(phone)
    if not quando:
        return False
    if (tempo.agora() - quando).total_seconds() > AJUSTE_TTL_S:
        PODCAST_FREQ_PERGUNTA.pop(phone, None)
        return False
    return True


def _como_recebe(dias: int) -> str:
    """"a cada 5 dias", "uma vez por semana"... — pra escrever no fecho."""
    return {5: "a cada 5 dias", 7: "uma vez por semana",
            15: "a cada 15 dias", 30: "uma vez por mês"}.get(
                dias, "uma vez por semana")


def _ordem_dos_assuntos() -> list:
    """A ordem em que os assuntos aparecem na lista — e o que o numero
    significa. Vem do `NICHOS`, entao lista e resposta nunca divergem."""
    return list(podcast.NICHOS.keys())


def _assunto_por_numero(texto: str):
    """"3" -> a chave do terceiro assunto da lista. None se nao for numero.

    So e chamada com a pergunta VIVA — fora dela, digito nao vira assunto.
    """
    t = (texto or "").strip().rstrip(".)")
    if not t.isdigit():
        return None
    ordem = _ordem_dos_assuntos()
    n = int(t)
    return ordem[n - 1] if 1 <= n <= len(ordem) else None


def _assuntos_da_resposta(texto: str) -> list:
    """Ate tres assuntos de uma resposta como "1, 5, 9" ou "futebol e moda".

    Aceita numero e nome misturados, ignora o que nao existe e nao repete.
    Devolve [] quando nada casou — e ai a mensagem segue pro motor normal em
    vez de ficar presa aqui.
    """
    import re as _re
    achados: list = []
    bruto = (texto or "").strip()
    # "1 5 9": espaco so vira separador quando a resposta INTEIRA e numero.
    # Separar por espaco sempre quebraria "varejo online" em duas palavras
    # que nao existem, e a pessoa perderia a escolha sem entender por que.
    sep = (r"[,;/\s]+" if _re.fullmatch(r"[0-9\s]+", bruto)
           else r"[,;/]|\s+e\s+|\n")
    for pedaco in _re.split(sep, bruto):
        pedaco = pedaco.strip()
        if not pedaco:
            continue
        k = _assunto_por_numero(pedaco) or podcast.nicho_valido(pedaco)
        if k and k not in achados:
            achados.append(k)
        if len(achados) >= podcast.MAX_ASSUNTOS:
            break
    return achados


def _passo_a_passo_do_podcast(user: dict) -> str:
    """O guia do mini podcast em uma mensagem.

    CADA LINHA E UMA COISA QUE DA PRA DIGITAR. Guia que explica conceito nao
    ajuda ninguem no celular; ajuda o que a pessoa consegue copiar e mandar.
    E ele diz o estado ATUAL dela: um passo a passo generico faz a pessoa
    perguntar "mas eu estou em qual mesmo?".
    """
    _ks = podcast.nichos_da_pessoa(user)
    if _ks:
        agora = (f"Hoje você recebe *{podcast.rotulos_da_pessoa(user)}*, "
                 f"{_como_recebe(db.frequencia_do_podcast(user))}.")
    else:
        agora = "Você ainda não assinou o mini podcast."
    return (f"🎧 *Como funciona o mini podcast*\n\n"
            f"{agora}\n\n"
            # SEM MINUTAGEM, pelo mesmo motivo do convite: o audio sai
            # entre 40s e 3min conforme a semana e conforme o roteiro venha
            # da locucao ou do fallback. Prometer numero e errar de graca.
            f"São duas pessoas conversando sobre as notícias dos assuntos "
            f"que você escolher, direto aqui no WhatsApp.\n\n"
            f"*Pra usar, é só me mandar:*\n\n"
            f"• *quero ouvir* — manda o episódio agora\n"
            f"• *muda os assuntos* — escolhe outros (até "
            f"{podcast.MAX_ASSUNTOS})\n"
            f"• *muda a frequência* — de 5 em 5 dias, semanal, quinzenal "
            f"ou mensal\n"
            f"• *não quero mais o podcast* — para de vez\n\n"
            # "NA FRENTE" VIROU MENTIRA quando a legenda passou pra depois
            # do audio (auditoria M9 2a passada, P1-A) — ela so pode sair
            # depois porque antes nao da pra saber se o envio vai.
            f"_Com mais de um assunto, você recebe um áudio de cada, no "
            f"mesmo dia, cada um com o nome logo abaixo._")


def _pergunta_da_regularidade(primeiro: str = "") -> str:
    """Sai depois do primeiro episodio. Numerada porque a resposta e numero."""
    ola = ("%s, g" % primeiro) if primeiro else "G"
    return (f"{ola}ostou? 🎧\n\n"
            f"Escolhe de quanto em quanto tempo você quer receber — é só "
            f"responder o número:\n\n"
            f"*1* — a cada 5 dias\n"
            f"*2* — 1x por semana\n"
            f"*3* — a cada 15 dias\n"
            f"*4* — 1x por mês")


# AS RESPOSTAS DO MENU DE REGULARIDADE, por extenso.
#
# CASAMENTO EXATO, NUNCA SUBSTRING (auditoria M9, P1-1/P1-2). Duas coisas
# quebraram enquanto isto usava `in`:
#
#   1. "5 dias" esta dentro de "a cada 15 dias", e o menu escreve exatamente
#      "a cada 15 dias" — quem respondia com as palavras levava 5 dias, tres
#      vezes a taxa que pediu.
#   2. com o slot vivo por 20 min, "me lembra do IPTU semana que vem" virava
#      resposta do menu e a funcao dava `return`: o lembrete nunca chegava ao
#      motor. E a mesma jaula do menu numerico de 30/08, com outra porta.
#
# A pergunta do assunto nunca teve esse problema porque compara a mensagem
# INTEIRA. Aqui e igual: a resposta tem que ser a resposta.
_FREQ_POR_EXTENSO = {
    # SEM O "5" SOLTO (auditoria M9 2a passada, P2): ele aceitava "5" como
    # cinco dias, mas "7"/"15"/"30" nao valiam nada — e o menu numera 1 a 4,
    # entao "5" nem e opcao. Assimetria gratuita num parser estrito.
    5: ("5 dias", "a cada 5 dias", "a cada 5", "cinco dias",
        "de 5 em 5 dias"),
    7: ("semanal", "toda semana", "1x por semana", "uma vez por semana",
        "por semana", "7 dias", "sete dias", "cada semana"),
    15: ("quinzenal", "15 dias", "a cada 15 dias", "quinze dias",
         "de 15 em 15 dias", "a cada 15"),
    30: ("mensal", "1x por mes", "uma vez por mes", "por mes", "30 dias",
         "trinta dias", "a cada 30 dias", "todo mes", "cada mes"),
}


def _frequencia_por_numero(texto: str):
    """"2" -> 7 dias. Aceita tambem "quinzenal", "a cada 15 dias" etc.

    So responde quando a mensagem INTEIRA e a escolha. Frase com a palavra
    dentro nao conta — ver o bloco acima.
    """
    t = (texto or "").strip().rstrip(".!?)").strip().lower()
    porordem = {"1": 5, "2": 7, "3": 15, "4": 30}
    if t in porordem:
        return porordem[t]
    import unicodedata as _u
    limpo = "".join(c for c in _u.normalize("NFD", t)
                    if _u.category(c) != "Mn")
    limpo = " ".join(limpo.split())
    for dias, termos in _FREQ_POR_EXTENSO.items():
        if limpo in termos:
            return dias
    return None


def _lista_de_nichos() -> str:
    """Os assuntos, NUMERADOS — porque a resposta esperada e o numero.

    A numeracao so e segura porque `_assunto_por_numero` roda unicamente com
    a pergunta viva: em 30/08 um catch-all que lia qualquer digito fez "2"
    respondendo "qual deles eu dou baixa?" virar assinatura de podcast, e a
    baixa sumiu calada.
    """
    linhas = ["*%d* — %s %s" % (i, d["emoji"], d["rotulo"])
              for i, d in enumerate(podcast.NICHOS.values(), 1)]
    return ("Quais assuntos você mais gosta? Responde os números — "
            "*até %d*:\n\n" % podcast.MAX_ASSUNTOS
            + "\n".join(linhas)
            + "\n\n_Exemplo: *1, 6, 12*_")


def _pergunta_do_nicho() -> str:
    return ("Boa! 🎧 Toda semana eu te mando um resumo em áudio das notícias "
            "do assunto que você escolher — duas pessoas conversando, uns "
            "poucos minutos.\n\n" + _lista_de_nichos())


def _mandar_podcast(user: dict, phone: str) -> str:
    """Gera e manda o episódio. Devolve o texto que fecha a conversa.

    A ORDEM IMPORTA e cada passo pode falhar honestamente:
      1. busca a notícia nos feeds  -> sem notícia, não há episódio
      2. escreve o roteiro          -> LLM reprovado cai no determinístico
      3. sintetiza a voz            -> sem áudio, não manda nada
      4. manda pelo `canal`         -> respeita a janela de 24h como tudo

    Em NENHUM ponto o bot inventa conteúdo pra ter o que mandar. "Esta semana
    não teve novidade no seu assunto" é uma resposta honesta; um episódio
    fabricado pra cumprir agenda é como se perde a confiança de alguém de uma
    vez só.
    """
    # A CHAVE DE EMERGENCIA VALE AQUI TAMBEM (auditoria M5.4, P1-3).
    #
    # Este e o caminho reativo ("quero ouvir") e ele gera TTS pago. Uma chave
    # que desliga o proativo e deixa o reativo de pe nao desliga a feature —
    # so esconde metade dela.
    #
    # `_amostra_de_podcast` DE PROPOSITO fica de fora: e o comando do dono,
    # e e exatamente com a feature desligada que ele precisa reouvir uma
    # amostra pra decidir se religa.
    import scheduler as _sched
    if not _sched.PODCAST_ATIVO:
        return ("Esse recurso está fora do ar por uns instantes. 🙏\n\n"
                "Seus lembretes seguem normais — te aviso quando voltar.")

    # QUEM NAO TEM ACESSO NAO GASTA TTS (auditoria M5.6, P1-3).
    #
    # A checagem tem que ser AQUI, antes de buscar noticia e sintetizar: o
    # envio ja falharia no `canal`, mas so depois de a conta paga ter sido
    # gasta — e a resposta de falha convida a tentar de novo.
    if not db.user_can_receive(user, TRIAL_DAYS):
        return ("Seu período de teste terminou. 🙏\n\n"
                "Reativando o acesso, o áudio da semana volta junto.")

    # `time`/`random` sao por funcao neste arquivo (ver `_amostra_de_podcast`),
    # e o espacamento entre assuntos precisa dos dois.
    import noticias
    import random
    import time
    import voz

    nicho = user.get("podcast_nicho")
    # O TETO DE 1X POR SEMANA VALE AQUI TAMBEM (auditoria M4.2, P1-4).
    #
    # `pode_enviar` so era consultado no `check_podcast`, entao dez toques em
    # "quero ouvir" viravam dez audios e dez chamadas pagas de TTS. Dez notas
    # de voz em segundos e exatamente o padrao que a Meta pune — e este
    # numero ja foi restringido duas vezes.
    # DATA ILEGIVEL NAO PODE PRENDER A PESSOA (auditoria M4.3). No caminho
    # proativo, `pode_enviar` trata data podre como "acabou de enviar" — e
    # esta certo, o erro seguro la e mandar de menos. Aqui e o contrario:
    # ela PEDIU. Sem isto, um valor corrompido no banco tirava a unica saida
    # manual que existia, pra sempre e em silencio.
    _ultimo = user.get("podcast_ultimo")
    if _ultimo and not podcast.data_legivel(_ultimo):
        import logging
        logging.getLogger("resolveai").warning(
            "[podcast] podcast_ultimo ilegivel (%r) no user %s — ignorado",
            _ultimo, user["id"])
        # IGNORA AQUI, NAO APAGA NO BANCO (auditoria M4.5, P2-6).
        #
        # Apagar antes de tentar enviar tirava a pessoa das DUAS filas se
        # o envio falhasse depois: a semanal exige `podcast_ultimo`
        # preenchido, a de primeira vez exige `podcast_convite_em` vazio.
        # Ela ficava sem convite nenhum, pra sempre.
        #
        # O valor podre e so ignorado NESTA decisao. Quem o substitui e o
        # `podcast_marcar_envio` la embaixo — e ele so roda quando o audio
        # comprovadamente saiu.
        _ultimo = None
    _assinou = podcast.nichos_da_pessoa(user)
    if _assinou and not podcast.pode_enviar(
            _ultimo, dias=db.frequencia_do_podcast(user)):
        return (f"Você já ouviu o episódio deste período. 🎧\n\n"
                f"Você recebe *{_como_recebe(db.frequencia_do_podcast(user))}*"
                f" — mando um toque quando o próximo estiver pronto.\n\n"
                f"_Pra mudar, é só dizer_ *muda a frequência*.")
    if not _assinou:
        return ("Você ainda não escolheu um assunto pro mini podcast. 🎧\n\n"
                "Pode ser futebol, games, inteligência artificial, moda ou "
                "varejo online — é só me dizer qual.")

    import logging as _lg
    _log = _lg.getLogger("resolveai")

    # UM EPISODIO POR ASSUNTO (M9.7). A pessoa escolheu ate tres porque gosta
    # das tres — juntar num audio so daria assunto trocado no meio.
    # `_assinou` E A MESMA LISTA, ja calculada no portao logo acima. O `or`
    # que existia aqui era rede morta: o portao devolve cedo quando ela esta
    # vazia, entao o outro ramo nunca rodava — e rede morta e pior que
    # nenhuma, porque parece que alguem cuidou do caso.
    _assuntos = _assinou
    # A JANELA E A FREQUENCIA DELA: quem ouve de mes em mes precisa do mes.
    _janela = db.frequencia_do_podcast(user)

    # O QUE JA CHEGOU NUM LOTE QUE O CANAL INTERROMPEU. Vazio no caminho
    # normal — so tem conteudo depois de uma falha de envio recente.
    # USA `_ultimo`, NAO O CAMPO CRU (auditoria M9, 4a passada). Ele e a
    # mesma leitura ja sanitizada tres linhas acima — data ilegivel vira
    # None de proposito, pra nao prender a pessoa. Ler o campo cru aqui
    # seriam duas interpretacoes diferentes do mesmo valor, lado a lado.
    _ja_chegou = db.podcast_lote_interrompido(user["id"], ultimo=_ultimo)

    _saiu = 0
    # QUEM QUEBROU POR FALHA DE CANAL (auditoria M9 2a passada, P1-A). E
    # diferente de `_vazios`: la nao havia noticia, aqui havia episodio pronto
    # e o envio caiu. O fecho precisa dos dois pra nao esconder metade dos
    # motivos de um audio nao ter chegado.
    _falhou_envio: list = []
    # O QUE REALMENTE SAIU (auditoria M9, P1-5). O fecho era montado a partir
    # do que ela ASSINA, entao um assunto sem noticia virava "seu resumo de
    # moda e futebol esta ai em cima" com um audio so, citando as fontes de um
    # episodio que nao existe.
    _entregues: list = []
    _vazios: list = []
    for _k in _assuntos:
        # JA CHEGOU NO LOTE INTERROMPIDO? (auditoria M9 2a passada, P1-A.)
        # So acontece depois de uma falha de envio recente: como a gente nao
        # carimba nesse caso, o "quero ouvir" seguinte passaria por aqui de
        # novo e remandaria o que ela ja ouviu.
        if _k in _ja_chegou:
            _entregues.append(_k)
            # CONTA COMO SAIDO pra numeracao: senao a retomada de um lote
            # interrompido diria "1 de 3" pro segundo assunto, e a pessoa
            # procuraria um primeiro que ela ja tem.
            _saiu += 1
            continue

        # O EPISODIO DO DIA VEM ANTES DE TUDO (auditoria M16, P2). A leitura
        # do cache estava DEPOIS do "sem noticia, continue": se o feed da
        # pessoa voltasse vazio naquele instante, ela ficava sem audio embora
        # existisse episodio valido do tema+janela dela guardado hoje. E, no
        # acerto, a busca de RSS e a chamada paga de LLM ja tinham rodado pra
        # ser jogadas fora — o M12 economizava TTS e desperdicava o resto.
        # UM BLOCO DE ENVIO SO (auditoria M16, 3a rodada). Eu tinha
        # duplicado o envio inteiro aqui e esqueci o freio anti-rajada
        # na copia — justamente no caminho que a maioria percorre,
        # porque o M12 existe pra maioria acertar o cache. Duas copias
        # significam lembrar da proxima correcao duas vezes, e foi isso
        # que falhou. Agora o cache so preenche `audio`; quem envia e um
        # trecho unico, la embaixo.
        audio = db.podcast_episodio_do_dia(_k, _janela)
        roteiro = ""

        # SO PAGA RSS, LLM E TTS QUEM NAO TEM EPISODIO PRONTO.
        if not audio:
            _diag: dict = {}
            try:
                itens = noticias.buscar(_k, dias=_janela, relatorio=_diag)
            except Exception:
                _log.warning("[podcast] falha ao buscar noticia de %s", _k,
                             exc_info=True)
                itens = []
                _diag = {"fontes": 3, "falharam": 3}
            # AS TRES FONTES CAIRAM E "NAO TEVE NOTICIA" SAO COISAS DIFERENTES.
            # A primeira e o assunto mudo e tem que acender o farol; a segunda e
            # o produto se comportando ("prefiro nao te mandar audio so pra
            # cumprir tabela") e nao pode acender nada.
            _fontes_mudas = (_diag.get("falharam", 0) >= _diag.get("fontes", 3)
                             and _diag.get("fontes", 0) > 0)

            # SEM O NOME DELA NO ROTEIRO (M12). Ele era o unico pedaco que
            # diferia entre duas pessoas do mesmo tema — e era ele que obrigava
            # a pagar uma sintese por pessoa. O nome continua na mensagem de
            # texto que acompanha; o audio virou o mesmo pra todo mundo.
            roteiro = podcast.locucao(_k, itens)
            if not roteiro:
                _vazios.append(podcast.rotulo(_k).lower())
                # SEMANA QUIETA NAO E FALHA (auditoria M9, P2) — mas fonte
                # caida e. Contando as duas como falha, o farol acenderia laranja
                # em rotina ate o dono aprender a ignora-lo, que e o oposto do
                # motivo dele existir; contando as duas como sucesso, ele nunca
                # avisaria que um assunto ficou mudo.
                db.podcast_registrar_episodio(
                    user["id"], _k, 0, not _fontes_mudas,
                    "fontes fora do ar" if _fontes_mudas
                    else "sem noticia na janela")
                continue

            # O EPISODIO DO DIA E DE TODO MUNDO (M12). Mesma chave = mesmo
            # tema, mesma janela, mesmo dia — entao e o mesmo audio, e pagar
            # sintese de novo seria pagar duas vezes pelo mesmo arquivo.
            audio = voz.sintetizar(roteiro)
            if audio:
                db.podcast_guardar_episodio_do_dia(_k, _janela, audio)
            if not audio:
                db.podcast_registrar_episodio(user["id"], _k, 0, False,
                                              "voz nao sintetizou")
                continue

        # ESPACAMENTO, como em todo envio em lote desta casa (auditoria M9,
        # P1-4). Tres assuntos sao ate sete mensagens; manda-las de enfiada e
        # a assinatura de ritmo que ja rendeu 3h de restricao neste numero
        # (ver `_amostra_de_podcast` e o FREIO 2 do cron). Este era o unico
        # caminho de lote sem freio — e o unico que roda pra cliente.
        # So ENTRE assuntos: fazer a pessoa esperar pelo primeiro audio depois
        # de ela tocar "quero ouvir" seria pagar o preco sem o motivo.
        if _saiu:
            time.sleep(random.uniform(ENVIO_INTERVALO_MIN,
                                      ENVIO_INTERVALO_MAX))

        res = wasender.falar_audio(phone, audio, user_id=user["id"])
        if not res.get("enviado"):
            _log.warning("[podcast] audio de %s nao saiu p/ user %s: %s",
                         _k, user["id"], res.get("motivo"))
            db.podcast_registrar_episodio(user["id"], _k, 0, False,
                                          str(res.get("motivo") or "")[:80])
            _falhou_envio.append(podcast.rotulo(_k).lower())
            # PARA AQUI. `continue` mandava a legenda seguinte com "1 de 3"
            # outra vez, porque `_saiu` nao tinha andado. E falha de envio e
            # quase sempre do CANAL: insistir nos outros dois assuntos so
            # acrescenta mensagem num numero que a Meta ja restringiu.
            break

        _saiu += 1
        _entregues.append(_k)

        # A LEGENDA VEM DEPOIS DO AUDIO (auditoria M9 2a passada, P1-A).
        #
        # Ela vinha antes, e o `break` da rodada anterior matou a
        # MULTIPLICACAO das legendas orfas, nao a causa: antes de mandar a
        # gente nao sabe se o audio vai. Uma legenda sozinha, sem nada atras,
        # continuava possivel — e o teste que eu tinha escrito pra isso
        # permitia uma, em vez de proibir todas.
        #
        # Depois funciona igual: o WhatsApp mostra a mensagem logo abaixo da
        # nota de voz, que e onde a pessoa procura o rotulo.
        if len(_assuntos) > 1:
            wasender.falar(
                phone,
                "%s *%s* — %d de %d" % (podcast.NICHOS[_k]["emoji"],
                                        podcast.rotulo(_k), _saiu,
                                        len(_assuntos)),
                user_id=user["id"])
        # Opus a 32 kbps: bytes * 8 / 32000 = segundos. E estimativa, e esta
        # rotulada como tal no dash — mas e medida do arquivo, nao chute.
        db.podcast_registrar_episodio(
            user["id"], _k, round(len(audio) * 8 / 32000.0, 1), True)

    if not _saiu:
        # FALHA DE ENVIO NAO E FALHA DE GERACAO. Dizer "nao consegui gerar"
        # quando o episodio existia e so nao atravessou culpa a coisa errada
        # e esconde da pessoa que tentar de novo AGORA resolve.
        if _falhou_envio:
            return ("Preparei seu episódio mas não consegui te mandar "
                    "agora. 😕\n\nResponde *quero ouvir* que eu tento de "
                    "novo — não perdi nada.")
        if _vazios:
            return (f"Dessa vez não achei novidade que valesse o áudio em "
                    f"*{podcast._lista(_vazios)}*. 🤷\n\n"
                    f"Na próxima eu tento de novo — prefiro não te mandar "
                    f"áudio só pra cumprir tabela.")
        return ("Não consegui gerar o áudio agora. 😕 Tenta de novo daqui a "
                "pouco que eu mando.")
    res = {"enviado": True}

    # O CARIMBO NAO PODE DERRUBAR O FECHO (auditoria M9, P2). Aqui os
    # audios JA SAIRAM. Se o UPDATE estourar ("database is locked" e cenario
    # real, e o `scheduler` embrulha os carimbos dele por isso), a pessoa
    # receberia excecao no lugar do fecho e `podcast_ultimo` ficaria sem
    # carimbo — o proximo toque reenviaria tudo e requeimaria TTS pago.
    # ENTREGA PARCIAL POR FALHA DE CANAL NAO CARIMBA (auditoria M9 2a
    # passada, P1-A). Carimbar aqui trancaria a pessoa ate a proxima janela —
    # ate 30 dias, pra quem escolheu mensal — por um erro que foi nosso. Sem
    # carimbo ela pode tocar "quero ouvir" de novo, e o laco la em cima pula
    # o que ja chegou, entao vem so o que falta.
    #
    # Sem noticia NAO conta como entrega parcial: ali nao ha o que reenviar,
    # e nao carimbar faria o convite renascer todo dia por causa de uma
    # semana quieta.
    if _falhou_envio:
        _log.info("[podcast] entrega parcial p/ user %s (%d de %d) — sem "
                  "carimbo, ela pode pedir o resto",
                  user["id"], _saiu, len(_assuntos))
    else:
        try:
            db.podcast_marcar_envio(user["id"])
        except Exception:
            _log.warning("[podcast] nao consegui carimbar o envio do "
                         "user %s", user["id"], exc_info=True)
    # O convite fica carimbado junto: quem ouviu não precisa ser convidado
    # de novo pelo caminho de "primeira vez".
    if not user.get("podcast_convite_em"):
        db.podcast_marcar_convite(user["id"])
    # O COMBINADO VAI NO FECHO, sem gastar mensagem nova.
    #
    # Aqui era o lugar da pergunta "que dia você prefere?". Ela saiu quando o
    # dia deixou de ser regra (o dia fixo fazia a pessoa perder a semana
    # inteira se não abrisse o WhatsApp naquele dia). Perguntar um dia que a
    # gente não honra é pior que não perguntar.
    #
    # O que sobrou é o que importa: a frequência e a saída. E vai de carona
    # numa mensagem que já ia sair — a resposta ao toque é reativa, não gasta
    # o teto proativo dela nem entra na razão de ritmo.
    # AS FONTES SAO AS DOS ASSUNTOS QUE SAIRAM, sem repetir: com tres
    # assuntos a lista dobrava de tamanho e vinha com nome repetido.
    _fontes: list = []
    for _k in _entregues:
        for _f in podcast.fontes(_k):
            if _f[0] not in _fontes:
                _fontes.append(_f[0])

    # SO O QUE SAIU (auditoria M9, P1-5).
    _cabeca = (f"Pronto! 🎧 Seu resumo de "
               f"*{podcast.rotulos_da_pessoa(','.join(_entregues))}* "
               f"está aí em cima.\n\n"
               f"_Fontes: {', '.join(_fontes[:6])}._\n\n")
    # E O QUE NAO SAIU E DITO, nao escondido: semana quieta num assunto e
    # comportamento correto ("prefiro nao te mandar audio so pra cumprir
    # tabela"), mas a pessoa contou os audios e viu que faltou um.
    if _vazios:
        _cabeca += (f"_Em {podcast._lista(_vazios)} não achei novidade que "
                    f"valesse o áudio dessa vez._\n\n")
    # E O QUE QUEBROU NA ENTREGA TAMBEM (auditoria M9 2a passada, P1-A). Ela
    # contou os audios e viu que faltou; nao reconhecer parece defeito calado.
    if _falhou_envio:
        _cabeca += (f"_Não consegui te mandar "
                    f"{podcast._lista(_falhou_envio)} agora. Responde "
                    f"*quero ouvir* que eu tento de novo._\n\n")

    # A PERGUNTA DA REGULARIDADE SO NA PRIMEIRA VEZ, e de carona: mensagem
    # separada custaria uma proativa pra perguntar uma preferencia.
    if not (user.get("podcast_frequencia") or "").strip():
        PODCAST_FREQ_PERGUNTA[phone] = tempo.agora()
        _primeiro = (user.get("nome") or "").split()[0] if user.get("nome") else ""
        # A SAIDA VAI JUNTO DA PERGUNTA (auditoria M9.9). Perguntando a
        # regularidade sem oferecer a saida, o primeiro episodio — que e
        # justamente onde a pessoa mais decide se quer isso — ficava sem
        # nenhuma porta, e a unica que sobra e bloquear o numero.
        return (_cabeca + _pergunta_da_regularidade(_primeiro)
                + "\n\n_Se não quiser mais, é só dizer_ "
                  "*não quero mais o podcast*.")

    return (_cabeca
            + f"Mando um desses *{_como_recebe(db.frequencia_do_podcast(user))}*"
            f". Pra parar, é só dizer _não quero mais o podcast_.")


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
    if pend.get("tipo") in _PENDENCIAS_DE_CONVERSA:
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
            if resposta == SEM_RESPOSTA:
                continue          # tratado, e sem texto a mandar
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


def _pendentes_equivalentes(user_id: int, dados: dict) -> list:
    """Os pendentes que casam com este comprovante por VALOR + VENCIMENTO.

    Uma consulta só, usada pelas duas pontas: quem decide a baixa e quem
    monta a pergunta quando há mais de um. Duas cópias da mesma busca
    divergiriam na próxima correção — foi assim que o envio do podcast
    perdeu o freio anti-rajada numa das cópias.

    Teto de 5 porque a pergunta é numerada de 1 a 9 e ainda precisa de uma
    linha pro "nenhuma dessas". Cinco contas com valor E vencimento
    idênticos já é caso de laboratório; nove é impossível na vida real.
    """
    venc_titulo = dados.get("vencimento_titulo")
    valor = dados.get("valor_reais")
    if not venc_titulo or not valor:
        return []
    try:
        # Descricao vazia fica de fora: o menu e numerado e imprimiria
        # "*1* — None", que nao ajuda ninguem a escolher.
        return [i for i in db.list_items(user_id, status="pendente")
                if i.get("valor_reais") == valor
                and i.get("data_vencimento") == venc_titulo
                and (i.get("descricao") or "").strip()][:5]
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[boleto] falha ao procurar pendente equivalente", exc_info=True)
        return []


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
    casados = _pendentes_equivalentes(user_id, dados)
    if len(casados) > 1:
        # AMBIGUIDADE NÃO É AUSÊNCIA — é pergunta.
        #
        # Devolver None aqui era o defeito: quem chama entende "não é baixa
        # de nada", cai no fluxo de conta nova e GRAVA UM ITEM. O resultado
        # são os dois estragos que o próprio bloco do chamador diz existir
        # pra evitar: o gasto do mês contado duas vezes e o lembrete da
        # conta JÁ PAGA disparando no vencimento.
        #
        # E o erro era invisível dos dois lados: `_pendente_de_mesmo_valor`
        # também devolve None quando há mais de um, então nem a dica de
        # correção aparecia. A pessoa só descobria pelo lembrete errado.
        #
        # Mesma sentinela do `_alvo_da_baixa`, pela mesma razão: continua
        # valendo que o bot NÃO decide sozinho. Ele pergunta, numerado, e a
        # escolha fica guardada no BAIXA_ESCOLHA.
        import logging
        logging.getLogger("resolveai").info(
            "[boleto] %d pendentes com mesmo valor e vencimento — pergunto "
            "em vez de dar baixa no escuro", len(casados))
        return AMBIGUO
    if not casados:
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
        if _pendente is AMBIGUO:
            # PERGUNTAR, E NÃO GRAVAR NADA ENQUANTO NÃO SOUBER.
            #
            # Duas contas com o mesmo valor E o mesmo vencimento acontecem
            # (mensalidade, condomínio, seguro, parcela). O comprovante é de
            # UMA delas — gravar um terceiro item seria inventar uma despesa
            # que não existe e deixar a paga cobrando no vencimento.
            #
            # A última opção existe porque a chave SELECIONA mas não
            # identifica: o comprovante pode ser de uma conta que nem está
            # na lista, e sem essa saída o registro dela se perderia. Por
            # isso o payload do documento viaja junto no slot.
            #
            # Slot próprio (BAIXA_ESCOLHA), nunca o PENDING: escrever no
            # PENDING atropelaria uma confirmação de boleto em curso, que é
            # o P0-2 do M5.4. `_decisao_de_conversa_viva` já conhece este
            # slot, então nada de podcast atropela a pergunta.
            _op = _pendentes_equivalentes(user["id"], dados)
            # `> 1`, e nao `if _op` (P2-1 da auditoria): entre a primeira
            # leitura e esta a lista pode ter encolhido pra uma. Com `if _op`
            # o bot montava um menu de uma opcao so, pulando o veto por nome,
            # e ainda escrevia "Tenho 1 contas com esse mesmo valor".
            if len(_op) > 1:
                BAIXA_ESCOLHA[phone] = {
                    "ids": [i["id"] for i in _op] + [None],
                    "quando": tempo.agora(),
                    "novo": {"descricao": desc,
                             "valor_reais": dados["valor_reais"],
                             "data_vencimento": dados["data_vencimento"]},
                }
                _linhas = "\n".join(f"*{n}* — {i['descricao']}"
                                    for n, i in enumerate(_op, 1))
                return (f"Recebi o comprovante"
                        f"{_fmt_dinheiro(dados['valor_reais'])}.\n\n"
                        f"Tenho {len(_op)} contas com esse mesmo valor e "
                        f"vencimento. Qual delas ele pagou?\n\n"
                        f"{_linhas}\n"
                        f"*{len(_op) + 1}* — Nenhuma dessas, é conta nova\n\n"
                        f"_Responde o número._")
            # Sem opções pra listar (a lista mudou entre uma leitura e
            # outra): segue o fluxo normal em vez de travar a pessoa.
            _pendente = None
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
        # GUARDA O CÓDIGO DE PAGAMENTO (M3.5), em coluna própria.
        #
        # `desc` continua vindo de `boleto.descricao_de`, que não carrega
        # código nenhum — a proteção antiga segue de pé. O código fica no
        # banco e volta pra pessoa só no aviso de vencimento, formatado pra
        # colar no app do banco.
        #
        # Só em conta que ainda vai ser paga: comprovante já pago não precisa
        # de código, e guardá-lo seria carregar dado sensível à toa.
        _cod = None if concluido else boleto.codigo_de_pagamento(texto_lido)
        db.add_item(
            user_id=user["id"],
            tipo="despesa",
            categoria=ai_engine.classify_category(desc),
            descricao=desc,
            valor_reais=dados["valor_reais"],
            data_vencimento=dados["data_vencimento"],
            status="concluido" if concluido else "pendente",
            codigo_pagamento=(_cod or {}).get("colavel"),
            codigo_tipo=(_cod or {}).get("tipo"))
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
    if landing and (landing.get("nome") or landing.get("interesses")
                    or landing.get("podcast_nicho")) and (
            is_new or user.get("onboarding_step") in ("nome", "interesses")):
        fn = ((landing["nome"] or "").split() or [""])[0] or first_name
        db.update_user_fields(
            user["id"],
            nome=landing["nome"] or user["nome"],
            idade=landing.get("idade"),
            interesses=landing.get("interesses") or None,
            # `or None` NAO: sem assunto escolhido o campo fica como estava,
            # e quem ja tinha um nao perde por preencher o formulario de novo.
            **({"podcast_nicho": landing["podcast_nicho"]}
               if landing.get("podcast_nicho") else {}),
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
        if cmd_pre == SEM_RESPOSTA:
            return None           # tratado, e sem texto a mandar
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
        if cmd_reply == SEM_RESPOSTA:
            return None           # tratado, e sem texto a mandar
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

        # M3.5 — NÃO É BOLETO, MAS PODE SER DOCUMENTO QUE VENCE.
        #
        # Nota fiscal, CNH, receita, carteirinha de vacina: o bot reconhece,
        # PROPÕE o que entendeu e deixa a pessoa confirmar. Diferente do
        # boleto (que tem âncora dura de valor e código), aqui é
        # interpretação — e item errado na lista é pior que item nenhum.
        _prop = documento.pergunta_de_confirmacao(documento.reconhecer(ocr))
        if _prop:
            # CARIMBA JUNTO (auditoria M10, P1): sem `PENDING_EM`, o
            # `_pending_vencido` trata a decisao como ja vencida e
            # `_decisao_de_conversa_viva` devolve False pra uma decisao
            # recem-nascida — que e o que deixa um slot de menu
            # atropela-la.
            PENDING_EM[phone] = tempo.agora()
            PENDING[phone] = {"tipo": "confirmar_documento",
                              "doc": _prop["doc"],
                              "quando": tempo.agora()}
            return {"number": phone, "text": _prop["texto"],
                    "botoes": _prop["botoes"]}

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


def _avisar_trial_estendido(user_id: int, dias: int) -> str:
    """Conta pra pessoa que o teste dela ganhou dias. Devolve "" se deu certo.

    Sai por template (`resolveai_trial_estendido`) porque quem levou mais
    dias normalmente é justamente quem sumiu — e quem sumiu está fora da
    janela de 24h. Texto livre aqui morreria calado.
    """
    u = db.get_user(user_id)
    if not u:
        return "usuário não encontrado"
    restam = db.trial_days_left(u)
    nova = (tempo.hoje() + timedelta(days=max(0, restam))).strftime("%d/%m/%Y")
    res = wasender.falar(
        re.sub(r"\D", "", u["telefone"] or ""),
        (f"Oi {(u.get('nome') or '').split()[0] or 'tudo bem'}, liberei mais "
         f"*{dias}* dia(s) de teste pra você.\n\nSeu acesso vale até "
         f"*{nova}*. Continuo te avisando dos seus compromissos até lá."),
        user_id=user_id, template="resolveai_trial_estendido",
        variaveis=[(u.get("nome") or "?").split()[0] or "Oi", str(dias), nova],
        botoes=["Ver tudo"])
    if res.get("enviado"):
        return ""
    if res.get("motivo") == "template_nao_aprovado":
        return ("dias liberados, mas o aviso não saiu: "
                "`resolveai_trial_estendido` ainda não está em "
                "TEMPLATES_APROVADOS")
    return f"dias liberados, mas o aviso não saiu ({res.get('motivo')})"


VARIAVEIS_QUE_SEI_PREENCHER = {
    "primeiro_nome", "dias", "quantidade_itens", "descricao", "item",
    "desde", "hora", "quando", "dias_extras", "nova_data",
}

# VARIAVEIS QUE SO O DONO SABE — ele digita no painel, na hora do envio.
#
# O `resolveai_novidade` existe pra servir a TODO lancamento sem nova
# submissao a Meta, e o preco disso e que o nome da novidade e a explicacao
# sao texto livre. Nenhum dado do banco responde "o que voce lancou hoje".
#
# Sem isto o template ficava num limbo: aprovado na Meta, liberado na
# allowlist, e invisivel no painel — porque `_templates_manuais` so oferece o
# que sabe preencher sozinho. Nao havia botao nenhum pra dispara-lo.
VARIAVEIS_LIVRES = {"nome_da_novidade", "o_que_ela_faz"}

# Teto de cada campo livre. O corpo inteiro do template tem limite na Meta, e
# texto colado sem querer (um artigo, um log) viraria recusa no envio — ou,
# pior, uma mensagem gigante pra base inteira.
LIMITE_VARIAVEL_LIVRE = 220


def _variaveis_do_template(nome_template: str, u: dict, extras: dict = None):
    """Monta os valores das variáveis a partir dos dados REAIS da pessoa.

    Devolve (True, [valores]) ou (False, "motivo").

    Existe separado do envio porque `_templates_manuais` precisa saber, ANTES
    de oferecer o botão, se o template é preenchível — botão que só falha
    depois de clicado é pior que botão ausente.

    `extras` traz o que só o dono sabe (o nome da novidade e a explicação),
    digitado no painel na hora do envio. Vazio é RECUSA, não string vazia:
    mandar "novidade no Resolve AI: **." pra base inteira é pior do que não
    mandar nada.
    """
    import templates as _cat
    if nome_template not in _cat.CATALOGO:
        return False, f"template {nome_template!r} não existe no catálogo"
    extras = extras or {}
    user_id = u.get("id")
    primeiro = (u.get("nome") or "?").split()[0] or "Oi"
    pendentes = db.list_items(user_id, status="pendente") or []
    valores = []
    for var in _cat.CATALOGO[nome_template].variaveis:
        if var == "primeiro_nome":
            valores.append(primeiro)
        elif var == "dias":
            valores.append(str(max(0, db.trial_days_left(u))))
        elif var == "dias_extras":
            # QUANTOS DIAS A PESSOA TEM AGORA. Depois de um reset são os 14
            # cheios; depois de um "+7" é o que sobrou mais os 7.
            #
            # Zero é recusa, não "0": "liberei mais 0 dia(s) de teste pra
            # você" é piada de mau gosto com quem está com o trial vencido.
            _d = db.trial_days_left(u)
            if _d <= 0:
                return False, ("essa pessoa está com o trial vencido — o "
                               "template prometeria 0 dia(s)")
            valores.append(str(_d))
        elif var == "nova_data":
            _d = db.trial_days_left(u)
            if _d <= 0:
                return False, "trial vencido: não há data futura pra anunciar"
            valores.append((tempo.hoje()
                            + timedelta(days=_d)).strftime("%d/%m/%Y"))
        elif var == "quantidade_itens":
            valores.append(str(len(pendentes)))
        elif var in ("descricao", "item"):
            if not pendentes:
                return False, ("essa pessoa não tem item pendente — o "
                               "template ficaria com variável vazia")
            valores.append((pendentes[0].get("descricao") or "").strip()
                           or "seu compromisso")
        elif var == "desde":
            alvo = pendentes[0] if pendentes else {}
            valores.append(_cat._dia_e_mes(alvo.get("data_criacao")))
        elif var in ("hora", "quando"):
            alvo = pendentes[0] if pendentes else {}
            valores.append(alvo.get("hora_alvo")
                           or _fmt_br(alvo.get("data_vencimento")) or "hoje")
        elif var in VARIAVEIS_LIVRES:
            _txt = str(extras.get(var) or "").strip()
            if not _txt:
                return False, f"falta escrever {var.replace('_', ' ')!r}"
            if len(_txt) > LIMITE_VARIAVEL_LIVRE:
                return False, (f"{var.replace('_', ' ')!r} passou de "
                               f"{LIMITE_VARIAVEL_LIVRE} caracteres")
            # QUEBRA DE LINHA NAO PASSA. A Meta recusa variavel com \n, e a
            # recusa viria depois do lote ja ter comecado — metade da base
            # recebendo e metade nao.
            valores.append(" ".join(_txt.split()))
        else:
            return False, f"não sei preencher a variável {var!r}"
        if not str(valores[-1]).strip():
            return False, f"variável {var!r} ficaria vazia"
    return True, valores


def _enviar_template_manual(user_id: int, nome_template: str,
                            extras: dict = None):
    """Manda um template aprovado pra UMA pessoa, por ordem do dono (M2.9).

    Devolve (ok, motivo). Três travas, todas fail-closed:

    1. O template tem que estar no catálogo do repo — nome inventado não vira
       chamada à Meta.
    2. As variáveis são montadas dos dados REAIS da pessoa. Template com
       variável vazia é recusado pela Meta e queima reputação do número.
    3. O envio passa por `canal.falar` como qualquer proativa. O painel não
       ganha porta própria: fora da janela, só template aprovado.

    Fica no log de admin porque é o dono falando com um cliente específico —
    e daqui a um mês ninguém lembra quem recebeu o quê.
    """
    import templates as _cat
    u = db.get_user(user_id)
    if not u:
        return False, "usuário não encontrado"
    if nome_template not in _cat.CATALOGO:
        return False, f"template {nome_template!r} não existe no catálogo"

    ok, valores = _variaveis_do_template(nome_template, u, extras)
    if not ok:
        return False, valores

    res = wasender.falar(re.sub(r"\D", "", u["telefone"] or ""), "",
                         user_id=user_id, template=nome_template,
                         variaveis=valores)
    try:
        db.registrar_acao_admin("enviar_template", alvo=user_id, por="painel",
                                detalhe=f"{nome_template} enviado={res.get('enviado')}")
    except Exception:
        log.warning("[painel] envio manual sem rastro no log", exc_info=True)
    if not res.get("enviado"):
        return False, res.get("motivo") or "não enviado"
    # RASTRO POR PESSOA, com o nome do template como kind.
    #
    # So o log de acoes gravava isto, e la o template vive DENTRO de um
    # texto — nao da pra perguntar "quem ja recebeu o aviso de novidade".
    # Sem essa pergunta o dono nao tem como evitar repetir um aviso, que e
    # o jeito mais rapido de virar spam aos olhos de quem recebe.
    #
    # Nao muda comportamento nenhum: o nome do template nao esta em
    # KINDS_DE_CORTESIA nem em KIND_TEMPLATE, entao nao come cota de
    # cortesia nem colide com o dedup do motor. E so o registro.
    try:
        db.log_dispatch(user_id, nome_template)
    except Exception:
        log.warning("[painel] envio manual sem carimbo de disparo",
                    exc_info=True)
    # E TAMBEM NO LOG DE MENSAGENS, senao o envio fica INVISIVEL.
    #
    # "Ultimas mensagens" e onde o dono olha pra saber se algo saiu. Envio
    # de template nao passava por `log_message`, entao o lote inteiro
    # sumia da lista: a ultima saida visivel era de meia hora antes. Sem
    # ver, ele clica de novo — foi exatamente o que aconteceu.
    #
    # Grava o NOME do template e as variaveis livres, nao o corpo inteiro:
    # o corpo vive na Meta e repeti-lo aqui so encheria a tela.
    try:
        _rot = _cat.CATALOGO[nome_template].rotulo or nome_template
        _extra = " · ".join(str(v) for v in (valores or [])[1:] if v)
        db.log_message(user_id, re.sub(r"\D", "", u["telefone"] or ""),
                       "out", "template",
                       f"[template] {_rot}" + (f" — {_extra}" if _extra else ""))
    except Exception:
        log.warning("[painel] envio manual fora do log de mensagens",
                    exc_info=True)
    return True, ""


def _diagnostico_de_audio() -> dict:
    """O episodio consegue sair como NOTA DE VOZ?

    So o OGG/Opus mostra os botoes de 1x / 1,5x / 2x no WhatsApp, e o Opus
    depende do ffmpeg estar na imagem. Quando ele falta, o episodio sai em
    MP3: toca, mas sem acelerador — que foi exatamente a pergunta do dono
    ("cade o acelerador de velocidade?").
    """
    try:
        import voz as _voz
        tem = _voz.tem_ffmpeg()
        falha = getattr(_voz, "_ULTIMA_FALHA_OPUS", "") or ""
        return {"ffmpeg": tem,
                "formato": _voz.formato_de_saida()[1],
                "acelerador": ("sim" if tem and not falha
                               else "nao (sai em mp3)"),
                # O MOTIVO, quando a colagem falhou. Sem ele o diagnostico
                # diz "tem ffmpeg: sim" e o audio chega sem acelerador, e
                # nao ha por onde comecar.
                "ultima_falha_opus": falha[:200]}
    except Exception:
        return {"ffmpeg": None, "formato": "?", "acelerador": "?"}


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
    # OS TRES FAROIS DO PODCAST (M9.10). `podcast_farois` ja nao levanta,
    # mas o `try` fica: painel que cai por causa de telemetria e um painel a
    # menos justamente no dia em que ele seria necessario.
    try:
        _pod = db.podcast_farois()
    except Exception:
        import logging
        logging.getLogger("resolveai").warning(
            "[painel] farois do podcast falharam", exc_info=True)
        _pod = {"estado": "sem dados", "ok": 0, "falhas": 0,
                "segundos_medio": 0, "na_semana": 0, "ultimo": ""}
    _seg = int(_pod.get("segundos_medio") or 0)
    return {
        "heatmap": serie,
        # M9.10 — "se estao conseguindo puxar das fontes e gerar
        # perfeitamente", quanto dura e quantos sairam na semana. O primeiro
        # e o que justifica os outros: o sintoma de fonte seca sempre foi
        # AUSENCIA de audio, e ausencia nao aparece em painel nenhum — quem
        # nao recebe nao reclama, cancela.
        "podcast": {
            "estado": _pod.get("estado") or "sem dados",
            "ok": _pod.get("ok") or 0,
            "falhas": _pod.get("falhas") or 0,
            "na_semana": _pod.get("na_semana") or 0,
            "segundos_medio": _seg,
            # MIN:SEG PRONTO. "125 segundos" obriga a fazer conta pra saber
            # se o episodio saiu do tamanho combinado (uns 2 minutos).
            "duracao": ("—" if not _seg
                        else "%d:%02d" % (_seg // 60, _seg % 60)),
            # O PORQUE DA COR VAI JUNTO: "com falhas" sozinho manda abrir o
            # log pra descobrir o que ja esta gravado aqui do lado.
            "nota": ("nenhum episódio ainda"
                     if (_pod.get("estado") or "sem dados") == "sem dados"
                     else "%d ok · %d falha%s em 7 dias"
                          % (_pod.get("ok") or 0, _pod.get("falhas") or 0,
                             "" if (_pod.get("falhas") or 0) == 1 else "s")),
        },
        # `serie` pronta: sem isso o painel varria a tabela DUAS vezes por
        # request, a cada 20 segundos.
        "constancia": db.constancia(dias=90, serie=serie),
        "gastos": dict(sorted(gastos.items(), key=lambda kv: kv[1],
                              reverse=True)),
        # Soma incompleta é dita, não maquiada de total.
        "gastos_falharam": falhou,
        "gastos_base": len(_usuarios),
        # M2.8 — as três perguntas que decidem se isto vira negócio.
        # `fora` é o mesmo do heatmap: o dono é o usuário mais ativo da base
        # e não é cliente. Deixá-lo entrar inflaria justamente os dois
        # numeradores que o Kevin usa pra decidir se sai prospectar.
        "validacao": db.validacao(TRIAL_DAYS, excluir_telefones=fora),
        # M2.9 — quem pediu o link e ainda não foi aprovado pelo dono. Fica
        # no topo do painel: é a única fila em que dinheiro está parado
        # esperando alguém clicar.
        "aprovacoes": db.aguardando_aprovacao(),
        # Os templates que o dono pode disparar na mão, com o RÓTULO do que
        # cada um faz — "resolveai_conta_a_vencer" não ajuda a decidir, "Avisa
        # que uma conta vence em breve" ajuda. A tela não inventa nomes:
        # escolhe desta lista.
        "templates": _templates_com_rotulo(),
        # Os recortes da base pro envio em lote, já com a contagem.
        "segmentos": {k: len(v) for k, v in
                      db.segmentos(excluir_telefones=fora).items()},
        # M3.4 — a lista de poderes, pro Kevin consultar sem depender de
        # mim nem da memória dele.
        "poderes": PODERES,
        "grupos_de_poder": list(GRUPOS_DE_PODER),
    }


# ---------------------------------------------------------------------------
# A ABA DE PODERES DO PAINEL (M3.4)
# ---------------------------------------------------------------------------
# Pedido do Kevin em 28/08/2026, e nasceu de um problema concreto: ele perdeu
# a conta do que o produto já faz. Perguntou se o "avisa minha esposa" existia
# — e existia há semanas. Recurso que o dono não lembra que tem não entra na
# landing, não é vendido e não é usado.
#
# REGRA PERMANENTE: feature nova entra aqui no MESMO commit. Não é
# documentação opcional — há teste cobrando que todo template do catálogo
# apareça nesta lista.
GRUPOS_DE_PODER = ("Entra dado", "Sai aviso", "A pessoa responde",
                   "Você controla", "Protege o número")

PODERES = [
    {"grupo": "Entra dado", "titulo": "Texto do jeito que a pessoa fala",
     "desc": "\"luz 187 vence dia 20\" vira item com valor, data e categoria. "
             "Sem formulário e sem palavra mágica."},
    {"grupo": "Entra dado", "titulo": "Áudio",
     "desc": "Transcreve sozinho, até 2 minutos. É como a maioria prefere "
             "mandar."},
    {"grupo": "Entra dado", "titulo": "Foto de boleto e PDF de conta",
     "desc": "Lê código de barras, valor e vencimento. O caso que mais "
             "impressiona na primeira vez."},
    {"grupo": "Entra dado", "titulo": "Código de barras e PIX guardados",
     "desc": "Do boleto, o bot guarda o código e devolve na hora de pagar — "
             "sem espaço e sem ponto, pronto pra colar no app do banco, "
             "dizendo se é código de barras ou PIX. No aviso de vencimento "
             "aparece o botão *Copiar código*: um toque e o código chega "
             "sozinho numa mensagem, onde segurar e tocar em Copiar pega só "
             "ele. (O WhatsApp só tem botão que copia de verdade em template "
             "de autenticação, que é pra código de acesso e não pode ser "
             "usado em cobrança.)"},
    {"grupo": "Entra dado", "titulo": "Foto de documento que vence",
     "desc": "Nota fiscal, CNH, receita, carteirinha de vacina: o bot "
             "reconhece, mostra o que entendeu e você toca em Confirmar, "
             "Ajustar ou Esquece. Nunca guarda por conta própria. Cada tipo "
             "tem a antecedência que faz sentido: CNH avisa 60 e 30 dias "
             "antes, nota fiscal 30 dias antes de a garantia de 1 ano "
             "acabar. Vale por FOTO ou por TEXTO — quem escreve \"minha CNH "
             "vence 12/03/2027\" ganha a mesma antecedência de quem manda a "
             "foto."},
    {"grupo": "Entra dado", "titulo": "Placa do carro",
     "desc": "Com o final da placa, calcula IPVA e licenciamento de SP — "
             "inclusive o pulo de fim de semana e feriado."},

    {"grupo": "Sai aviso", "titulo": "Conta a vencer",
     "desc": "Avisa na véspera. Para veículo, avisa em 30, 7 e 1 dia — IPVA "
             "não se resolve de um dia pro outro."},
    {"grupo": "Sai aviso", "titulo": "Item vencido",
     "desc": "Cobra uma vez, no dia seguinte. Vários vencidos viram UMA "
             "mensagem, não uma por item."},
    {"grupo": "Sai aviso", "titulo": "Lembrete de hora marcada",
     "desc": "Alarme no horário exato. É o único aviso que fura o silêncio "
             "da madrugada."},
    {"grupo": "Sai aviso", "titulo": "Resumo dos compromissos",
     "desc": "No dia da semana que a pessoa escolheu. Só sai se houver o que "
             "dizer."},
    {"grupo": "Sai aviso", "titulo": "Resumo de gastos da semana",
     "desc": "Quanto gastou, em quê, e a comparação com a semana anterior. "
             "Hoje só alcança quem falou nas últimas 24h."},
    {"grupo": "Sai aviso", "titulo": "Lembra de item parado há dias",
     "desc": "Reengajamento cita UM item com a data — não uma contagem "
             "genérica, que a Meta classifica como marketing."},
    {"grupo": "Sai aviso", "titulo": "Avisa que o teste está acabando",
     "desc": "Uma vez por pessoa, na vida inteira do trial. É a única "
             "mensagem que pede a assinatura."},
    {"grupo": "Sai aviso", "titulo": "Conta que você liberou mais dias",
     "desc": "Quando você estende o teste, a pessoa fica sabendo do prazo "
             "novo. Ganhar dias e não saber é o mesmo que não ganhar."},
    {"grupo": "Sai aviso", "titulo": "Cobra quem pediu o link e não pagou",
     "desc": "Só por ação sua, nunca sozinho — o bot não sabe se o cartão "
             "passou."},
    {"grupo": "Sai aviso", "titulo": "Pede desculpa e reativa quem esfriou",
     "desc": "Explica a falha, diz que os dias estão valendo e ensina a usar "
             "com exemplo concreto."},
    {"grupo": "Sai aviso", "titulo": "Oferece marcar o próximo serviço",
     "desc": "Horas depois de você dar baixa em unha, dentista ou "
             "sobrancelha, pergunta se já guarda o próximo — com a data "
             "calculada. Conta de luz não ganha essa pergunta."},
    {"grupo": "Sai aviso", "titulo": "Mini podcast em áudio",
     "desc": "Áudio de até 3 minutos por assunto. A pessoa escolhe ATÉ 3 "
             "assuntos entre 16 — futebol, games, IA, moda, varejo online, "
             "economia, Brasil, saúde, celebridades, carros, viagens, "
             "horóscopo, geopolítica, ciência, música e gastronomia — cada "
             "um com 3 fontes de RSS conferidas, citadas no fim do áudio. "
             "Com mais de um assunto, os episódios saem no mesmo dia, um de "
             "cada, com o nome de cada um logo abaixo do áudio. "
             "Ela também escolhe de quanto em quanto tempo recebe: a cada "
             "5, 7, 15 ou 30 dias — e essa escolha é também a janela de "
             "notícia (quem recebe de mês em mês ouve o mês inteiro). "
             "A notícia vem do feed, nunca da cabeça do "
             "modelo; se ele citar fonte de fora, o roteiro é recusado. "
             "O bot NUNCA manda sozinho: pergunta \"quer ouvir?\" com botão "
             "e manda só no toque — no primeiro dia em que a pessoa "
             "aparecer, não num dia fixo, senão quem não abre o WhatsApp "
             "naquele dia perde a rodada. Sem novidade no período, ele diz "
             "isso em vez de inventar episódio. "
             "Ela troca quando quiser: _muda os assuntos_, _muda a "
             "frequência_, ou _não quero mais o podcast_. "
             "Custa ~US$ 0,03 por áudio. Você testa com "
             "_amostra do podcast_ e acompanha nos 3 faróis do painel."},
    {"grupo": "Sai aviso", "titulo": "Aviso de novidade",
     "desc": "Template aprovado pra anunciar funcionalidade nova. O nome da "
             "novidade e a frase que explica são variáveis, então o mesmo "
             "template serve pro próximo lançamento sem nova submissão. "
             "MARKETING, assumido: anunciar feature é falar do produto, e a "
             "régua da Meta separa pelo motivo, não pelo tom. Sai UMA vez "
             "por lançamento, só quando VOCÊ dispara no botão de lote — não "
             "tem checagem automática de propósito. Traz \"Nunca mais\", que "
             "desliga o aviso pra sempre pra quem tocar."},
    {"grupo": "Sai aviso", "titulo": "Arquivamento com aviso",
     "desc": "Item parado 15 dias sai da lista, mas só DEPOIS que o aviso "
             "comprovadamente saiu. Nunca some calado."},

    {"grupo": "A pessoa responde", "titulo": "Botões em vez de digitar",
     "desc": "Paguei · Adiar · Ver tudo. Todo título de botão é um comando "
             "que o bot entende, garantido por teste."},
    {"grupo": "A pessoa responde", "titulo": "Baixa sem ambiguidade",
     "desc": "\"paguei\", \"feito\", \"já resolvi\" fecham o item. Se houver "
             "dúvida de qual, o bot PERGUNTA em vez de chutar."},
    {"grupo": "A pessoa responde", "titulo": "Ver tudo",
     "desc": "A lista do que está em aberto, com data e valor."},
    {"grupo": "A pessoa responde", "titulo": "Recado para outra pessoa",
     "desc": "\"avisa minha esposa\" → o bot escreve o recado e devolve um "
             "link: sai do WhatsApp da própria pessoa, num toque. Nunca "
             "escreve pra quem não autorizou."},
    {"grupo": "A pessoa responde", "titulo": "Apagar os dados de verdade",
     "desc": "\"apagar meus dados\" limpa itens, conversas e cadastro — "
             "verificado contra o banco, não contra a mensagem na tela."},

    {"grupo": "Você controla", "titulo": "Funil de validação",
     "desc": "Entraram → registram sozinhas → o bot já salvou → voltaram → "
             "pagam. O veredito aponta UM gargalo por vez."},
    {"grupo": "Você controla", "titulo": "Aprovar pagamento na mão",
     "desc": "Quem pede o link entra numa fila. Você confere no Mercado Pago "
             "e aprova como mensal ou anual — o dia da aprovação inicia o "
             "ciclo."},
    {"grupo": "Você controla", "titulo": "Dar dias, bloquear, zerar cliente",
     "desc": "Por cliente, com confirmação. Zerar exige digitar o nome."},
    {"grupo": "Você controla", "titulo": "Disparo em lote por segmento",
     "desc": "Desengajados, sem itens, trial, ativos. Espaça os envios e "
             "mostra quem NÃO recebeu e por quê."},
    {"grupo": "Você controla", "titulo": "Devolver 14 dias pra todo mundo",
     "desc": "Comando `liberar 14 dias para todos`. Você fica de fora, e "
             "clicar duas vezes no mesmo dia não dá 28."},

    {"grupo": "Protege o número", "titulo": "Porta única de saída",
     "desc": "Toda proativa passa por um lugar só. Fora da janela de 24h, "
             "sem template aprovado, NÃO SAI — nem por decisão do LLM."},
    {"grupo": "Protege o número", "titulo": "Freios de ritmo",
     "desc": "5 por ciclo, 6 por pessoa/dia, 60 a 120s entre envios. Em "
             "04/08 saíram 4 num minuto e a Meta restringiu o número."},
    {"grupo": "Protege o número", "titulo": "Silêncio das 21h às 8h",
     "desc": "Só alarme de hora marcada fura."},
    {"grupo": "Protege o número", "titulo": "LGPD com aceite antes do dado",
     "desc": "Nada é guardado antes do aceite, e recusa apaga de verdade."},
]


def _templates_com_rotulo() -> list:
    """[{nome, rotulo, categoria, automatico}] pro painel.

    `automatico` diz se algum kind do motor já dispara aquele template
    sozinho. O Kevin pediu o botão manual "caso eu queira", mas precisa ver
    de relance o que já roda sem ele — senão manda na mão o que o motor ia
    mandar de qualquer jeito, e a pessoa recebe duas vezes.
    """
    import templates as _cat
    auto = set(_cat.KIND_TEMPLATE.values())
    return [{"nome": n, "rotulo": _cat.CATALOGO[n].rotulo or n,
             "categoria": _cat.CATALOGO[n].categoria,
             "automatico": n in auto,
             # O painel usa isto pra abrir os campos de texto do lancamento
             # SO no template que precisa deles.
             "pede_texto": sorted(set(_cat.CATALOGO[n].variaveis or [])
                                  & VARIAVEIS_LIVRES),
             # O QUE JA SAIU FICA NA TELA. O `alert` do resultado some no
             # primeiro OK, e depois disso nao havia como saber se um aviso
             # tinha sido enviado — a duvida e o que faz clicar de novo.
             "envio": _resumo_seguro(n)}
            for n in sorted(_templates_manuais())]


# AS METAS DE LUCRO, EM REAIS LIQUIDOS NO BOLSO DO DONO.
#
# Ficam em `settings` e nao no codigo porque sao dele, nao do produto: ele
# muda quando quiser, pelo painel, sem deploy.
METAS_PADRAO = {"curto": 2000.0, "medio": 5000.0, "bom": 10000.0}
METAS_ROTULO = {"curto": "6 meses", "medio": "1 ano", "bom": "negócio bom"}


def _metas() -> dict:
    import json as _j
    try:
        bruto = db.get_setting("metas_lucro")
        if bruto:
            guardado = _j.loads(bruto)
            return {k: float(guardado.get(k, v))
                    for k, v in METAS_PADRAO.items()}
    except Exception:
        log.warning("[metas] nao consegui ler — uso o padrao", exc_info=True)
    return dict(METAS_PADRAO)


def _plano_das_metas() -> dict:
    """Quantos clientes pagantes cada meta exige. Conta, nao opiniao.

    A pergunta que o painel nao respondia: "2 mil por mes" e quanto em
    CLIENTE? Sem esse numero a meta e um desejo; com ele vira alvo.

        clientes = (meta + custo fixo) / margem de contribuicao

    A margem vem do mesmo lugar que o card "Margem por cliente" — se um dia
    o preco ou o custo mudar, os dois mudam juntos.
    """
    metas = _metas()
    try:
        fin = db.financeiro(TRIAL_DAYS)
        marg = (fin.get("margem") or {}).get("margem_contribuicao") or 0.0
        fixo = float((fin.get("custos") or {}).get("fixos") or 0.0)
        assin = int(fin.get("assinantes") or 0)
        liquido = float(fin.get("liquido") or 0.0)
    except Exception:
        log.warning("[metas] financeiro indisponivel", exc_info=True)
        marg, fixo, assin, liquido = 0.0, 0.0, 0, 0.0
    saida = {"assinantes_hoje": assin, "liquido_hoje": round(liquido, 2),
             "margem_por_cliente": round(marg, 2), "fixo": round(fixo, 2),
             "alvos": []}
    for chave, valor in sorted(metas.items(), key=lambda kv: kv[1]):
        # Margem zero ou negativa nao tem solucao em cliente: vender mais
        # afundaria mais. Dizer isso e melhor do que mostrar um numero
        # gigante que parece meta.
        precisa = None if marg <= 0 else int(-(-(valor + fixo) // marg))
        saida["alvos"].append({
            "chave": chave, "rotulo": METAS_ROTULO.get(chave, chave),
            "meta": valor, "clientes": precisa,
            "faltam": None if precisa is None else max(0, precisa - assin)})
    return saida


def _poderes_seguro() -> list:
    """O mesmo inventario que o card 'O que o Resolve AI faz' mostra."""
    try:
        return PODERES if isinstance(PODERES, list) else []
    except Exception:
        log.warning("[conselho] inventario indisponivel", exc_info=True)
        return []


def _conselhos_guardados() -> dict:
    """A ultima analise de cada conselheiro, pra tela nao nascer vazia."""
    try:
        import conselho as _c
        return {t: _c.guardado(db, t) for t in _c.CONSELHOS}
    except Exception:
        log.warning("[conselho] nao consegui ler os guardados", exc_info=True)
        return {}


def _seguro_dict(fn) -> dict:
    """Uma consulta quebrada vira dicionario vazio, nunca derruba o resto."""
    try:
        return fn() or {}
    except Exception:
        log.warning("[conselho] uma metrica falhou", exc_info=True)
        return {}


def _custo_seguro() -> dict:
    """O custo por pessoa nunca pode derrubar o painel inteiro."""
    try:
        return db.custo_medio_por_usuario(30)
    except Exception:
        log.warning("[custo] nao consegui medir", exc_info=True)
        return {"pessoas": 0, "medio": 0.0, "maior": 0.0,
                "conferido": False, "topo": []}


def _resumo_seguro(nome_template: str) -> dict:
    """Nunca derruba o painel por causa de uma contagem."""
    try:
        return db.resumo_de_envios(nome_template, 2)
    except Exception:
        return {"quantos": 0, "ultimo": ""}


def _templates_manuais() -> list:
    """Templates do catálogo que o painel pode oferecer pra envio manual.

    Só os que `_enviar_template_manual` sabe preencher. Oferecer um que ele
    não sabe preencher seria um botão que só falha depois de clicado.
    """
    import templates as _cat
    # As LIVRES contam como preenchiveis: o painel pede o texto na hora.
    # Sem isso o `resolveai_novidade` ficava aprovado na Meta, liberado na
    # allowlist e sem botao nenhum que o disparasse.
    _sei = VARIAVEIS_QUE_SEI_PREENCHER | VARIAVEIS_LIVRES
    return [n for n, t in _cat.CATALOGO.items()
            if set(t.variaveis or []) <= _sei]


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
    #
    # E TEM CAUDA, pelo mesmo motivo que tem cabeca. O teto e de
    # `DISPATCH_MAX_PER_CYCLE` por ciclo: o que entra na frente ocupa a vaga
    # do que vem atras. A reativacao e convite, nao promessa — se ela passar
    # na frente do "sua conta vence amanha", o produto deixou de cumprir o
    # que vendeu pra caber uma mensagem de cortesia. Descoberto assim: um
    # teste de 2025 (`test_lembrete_de_vencimento_sai_com_botao`) ficou
    # vermelho no minuto em que a reativacao entrou no motor.
    _por_ultimo = ("reativacao_dispatches",)
    _chaves = list(_ordem) + sorted(
        k for k in result
        if k.endswith("_dispatches") and k not in _ordem
        and k not in _por_ultimo) + [
        k for k in _por_ultimo if k in result]
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
    # POR QUE OS SLOTS DO CICLO FORAM GASTOS SEM ENVIAR NADA (M6.6).
    #
    # O teto e `DISPATCH_MAX_PER_CYCLE` e a fatia e tirada ANTES do laco:
    # todo `continue` la dentro queima uma vaga sem mandar mensagem. Com
    # `enviados: 0` e nenhum erro, esta era a unica pergunta que restava —
    # e ela nao tinha resposta visivel de fora.
    _pulados: dict = {}
    ULTIMO_CICLO.clear()
    ULTIMO_CICLO.update({
        "quando": tempo.agora().strftime("%d/%m %H:%M:%S"),
        "candidatos": len(all_dispatches),
        "reativacao": sum(1 for _d in all_dispatches
                          if (_d.get("kind") or "") == "reativacao")})
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
    # PODA DOS NATIMORTOS, ANTES do corte do ciclo.
    #
    # O corte e `all_dispatches[:MAX]` — os primeiros CANDIDATOS, nao os
    # primeiros ENVIADOS. Um disparo que nao tem como sair (kind sem template,
    # pessoa fora da janela de 24h) consumiria uma vaga em TODO ciclo, para
    # sempre, empurrando pra tras quem esta alcancavel. E os kinds sem
    # template ("arquivado", "gastos", nudges do trial) sao justamente os de
    # quem anda sumido — a fila encheria de gente inalcancavel.
    #
    # Nao e descarte: o disparo nao e carimbado e volta assim que a pessoa
    # responder qualquer coisa e reabrir a janela.
    import templates as _cat_poda
    _janela_cache: dict = {}

    def _tem_como_sair(_d) -> bool:
        if (_d.get("kind") or "") not in _cat_poda.KINDS_SEM_TEMPLATE:
            return True
        # A chave inclui o TELEFONE: disparo sem user_id existe (o webhook
        # grava entrada com user_id=None) e dois deles compartilhariam a
        # mesma entrada de cache, respondendo pela janela de outra pessoa.
        _chave = (_d.get("user_id"), _d.get("telefone") or "")
        if _chave not in _janela_cache:
            _janela_cache[_chave] = db.dentro_da_janela(
                user_id=_chave[0], telefone=_chave[1])
        return _janela_cache[_chave]

    # A CORTESIA CEDE A VEZ, DENTRO DO MESMO CICLO.
    #
    # `db.teve_proativa_hoje` ja segura isto ENTRE ciclos, mas nao dentro de
    # um: quando o `check_reativacao` roda, o lembrete de vencimento do mesmo
    # ciclo ainda nao foi carimbado, porque o carimbo e do envio. Aqui os
    # dois estao lado a lado e da pra ver.
    #
    # Duas razoes, e a segunda e a que importa: o teto e de
    # `DISPATCH_MAX_PER_CYCLE`, entao o convite ocuparia a vaga do "sua conta
    # vence amanha"; e reativacao existe pra quem ESFRIOU — quem esta
    # recebendo lembrete nao esfriou. Ela volta no proximo ciclo.
    # UMA CORTESIA POR SEMANA, SOMANDO TODAS ELAS (M8.0).
    #
    # Vale por PESSOA e por CONJUNTO: quem recebeu o convite do podcast na
    # segunda nao recebe o empurrao do trial na quarta. O que ela pediu
    # (lembrete, alarme, resumo, cobranca do link que ela mesma pediu) passa
    # por fora desta conta — e o produto, e nao tem teto.
    if all_dispatches:
        _antes_semana = len(all_dispatches)
        all_dispatches = [
            d for d in all_dispatches
            if (d.get("kind") or "") not in KINDS_DE_CORTESIA
            or not _cortesia_recente(d.get("user_id"))]
        _cortados = _antes_semana - len(all_dispatches)
        if _cortados:
            log.info("[cortesia] %d adiada(s): a pessoa ja recebeu algo "
                     "nosso nos ultimos %d dias",
                     _cortados, CORTESIA_INTERVALO_DIAS)
            try:
                ULTIMO_CICLO["cortesia_da_semana"] = _cortados
            except Exception:
                pass

    # PARA DE PUXAR ASSUNTO COM QUEM NAO RESPONDE (M7.4).
    #
    # NAO e freio de operacao: e por PESSOA. Quem conversa com o bot recebe
    # tudo, todo dia, sem teto — inclusive o podcast e o convite. So quem
    # ficou `SILENCIO_ATE_PARAR` mensagens seguidas sem responder deixa de
    # receber o que a gente PUXA; o que ela pediu (lembrete de conta) segue
    # saindo, porque e o produto e ela paga por ele.
    #
    # Volta sozinho: a pessoa responde qualquer coisa e a contagem zera.
    if all_dispatches:
        _antes_silencio = len(all_dispatches)
        all_dispatches = [
            d for d in all_dispatches
            if (d.get("kind") or "") not in KINDS_DE_CORTESIA
            or not _parou_de_ouvir(d.get("user_id"))]
        _cortados = _antes_silencio - len(all_dispatches)
        if _cortados:
            log.info("[engajamento] %d convite(s) adiado(s): a pessoa nao "
                     "responde ha %d mensagens. Lembretes seguem.",
                     _cortados, SILENCIO_ATE_PARAR)
            try:
                ULTIMO_CICLO["convite_adiado"] = _cortados
            except Exception:
                pass

    _antes_poda = len(all_dispatches)
    all_dispatches = [d for d in all_dispatches if _tem_como_sair(d)]
    if len(all_dispatches) != _antes_poda:
        log.info("[cron] %d disparo(s) sem template e fora da janela adiados "
                 "— voltam quando a pessoa responder",
                 _antes_poda - len(all_dispatches))

    # O `_servidos` SAIU (M6.9), e nao por descuido.
    #
    # Ele removia a reativacao de quem tivesse OUTRO disparo no mesmo ciclo.
    # Medido em producao: os disparos de `anti-churn` ficam na fila e sao
    # recusados so no `falar` ("fora_da_janela_sem_template"), mas ja tinham
    # marcado a pessoa como atendida — entao a reativacao era removida todo
    # ciclo, para sempre. Mesma inanicao da M6.8, por outra porta.
    #
    # O que protege o produto e a ORDEM, nao este filtro: a reativacao e a
    # ultima da fila, entao lembrete, alarme e resumo pegam as vagas antes.
    # E pra as duas nao chegarem coladas ja existem a janela de 4h do
    # `check_reativacao`, o espacamento de 60-120s entre envios e o teto de
    # 6 proativas por usuario por dia.

    # BLOQUEIO DE CABECA DE FILA (M6.7).
    #
    # Era `all_dispatches[:DISPATCH_MAX_PER_CYCLE]`: a fatia saia ANTES do
    # laco, sempre os mesmos primeiros. Como o carimbo (`log_dispatch`) so
    # acontece no SUCESSO, um disparo recusado volta identico no ciclo
    # seguinte — e ocupa a mesma vaga, para sempre. Dois disparos travados na
    # frente da fila param o motor proativo INTEIRO, em silencio: o /health
    # mostrava 17 candidatos, enviados 0, nenhuma excecao e nenhum descarte.
    #
    # Agora o teto e de ENVIOS, nao de tentativas: quem falha cede o lugar
    # pro proximo. O teto de tentativas existe pra que um ciclo nao vire uma
    # varredura da fila inteira quando tudo esta falhando.
    _tentativas = 0
    _max_tentativas = max(DISPATCH_MAX_PER_CYCLE * 3, 6)
    # QUEM JA RECEBEU NESTE CICLO — de verdade, nao na fila (M6.9).
    #
    # O filtro antigo olhava a FILA e por isso morria de fome: um
    # `anti-churn` que so seria recusado la no `falar` ja marcava a pessoa
    # como atendida, e a reativacao era removida todo ciclo, para sempre.
    # Aqui so entra quem o `falar` aceitou.
    _ja_falei_com: set = set()
    for d in all_dispatches:
        if sent >= DISPATCH_MAX_PER_CYCLE or _tentativas >= _max_tentativas:
            break
        _tentativas += 1
        # A CORTESIA NAO CHEGA COLADA NA MENSAGEM DE PRODUTO.
        # O espacamento e de 60-120s; duas vibracoes seguidas do mesmo
        # numero e o padrao que a Meta pune. A reativacao volta no proximo
        # ciclo — e agora ela VOLTA mesmo, porque so cede a vez pra quem
        # recebeu de verdade.
        if ((d.get("kind") or "") == "reativacao"
                and d.get("user_id") in _ja_falei_com):
            _pulados["cortesia_adiada"] = _pulados.get(
                "cortesia_adiada", 0) + 1
            continue
        number = re.sub(r"\D", "", d["telefone"])
        if not number:
            log.warning("[cron] disparo sem número: %s", d.get("message", "")[:40])
            _pulados["sem_numero"] = _pulados.get("sem_numero", 0) + 1
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
                _pulados["irmao_sem_cabeca"] = _pulados.get(
                    "irmao_sem_cabeca", 0) + 1
                continue
            try:
                db.log_dispatch(d["user_id"], d.get("kind", "outro"),
                                d.get("item_id"))
            except Exception:
                log.warning("[cron] falhei ao registrar dedup", exc_info=True)
            _pulados["so_dedup"] = _pulados.get("so_dedup", 0) + 1
            continue

        # FREIO 3: teto diário por usuário.
        # Uma pessoa com 12 lembretes no mesmo dia não pode virar 12 vibrações
        # — nem pra ela, nem pro número. O que não couber hoje sai amanhã: o
        # dedup é por item, então nada se perde.
        if _proativas_hoje(d["user_id"]) >= MAX_PROATIVAS_POR_USUARIO_DIA:
            log.info("[cron] teto diário atingido p/ user %s — adiado",
                     d["user_id"])
            _pulados["teto_diario"] = _pulados.get("teto_diario", 0) + 1
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
        # BOTÕES (M3.0): valem só dentro da janela — fora dela quem manda
        # botão é o template aprovado, e os dele são declarados na submissão.
        # `falar` ignora este parâmetro quando cai no caminho do template.
        res = wasender.falar(number, d["message"], user_id=d.get("user_id"),
                             template=_tpl, variaveis=_vars,
                             botoes=_botoes_do_disparo(d))
        ok = res.get("enviado")
        if ok and d.get("user_id"):
            _ja_falei_com.add(d["user_id"])
        if not ok:
            # QUALQUER kind, nao so a reativacao (M6.7). Foi justamente uma
            # recusa de OUTRO kind que travou a fila inteira, e ela era
            # invisivel porque este registro so olhava a reativacao.
            ULTIMA_RECUSA_REATIVACAO.clear()
            ULTIMA_RECUSA_REATIVACAO.update({
                "kind": str(d.get("kind") or "?")[:24],
                "motivo": str(res.get("motivo") or "sem motivo")[:60],
                "quando": tempo.agora().strftime("%d/%m %H:%M")})
        log.info("[cron] envio p/ ...%s (%s): %s", number[-4:],
                 d.get("kind", "?"),
                 ("OK via " + (res.get("via") or "?")) if ok
                 else f"NAO ENVIADO ({res.get('motivo')})")
        cabeca_ok[(d.get("user_id"), d.get("kind"))] = bool(ok)
        # SO QUANDO O LEMBRETE SAIU: se ele nao chegou, nao existe botao
        # esperando clique, e a memoria apontaria pra um item que a pessoa
        # nao viu.
        if ok and d.get("tem_codigo") and d.get("item_id"):
            ULTIMO_COBRADO[number] = d["item_id"]

        # OS CARIMBOS DO PODCAST SAO GRAVADOS PELO ENVIO (auditoria
        # M4.2). Eles existiam no `db.py` e ninguem chamava: sem eles o
        # convite e a pergunta do dia voltavam A CADA CICLO — de minuto
        # em minuto — ate estourar o teto diario da pessoa. E o teto e
        # COMPARTILHADO com o aviso de vencimento: seis convites de
        # podcast comiam a cota e o aviso do IPTU nao saia. O extra
        # roubando o lugar do que a pessoa pagou pra ter.
        #
        # Aqui e o lugar certo pelo mesmo motivo do `log_dispatch`:
        # marcado por QUEM ENVIA, nunca por quem gera. Convite que nao
        # saiu nao pode ficar carimbado.
        # EMBRULHADO como todas as vizinhas deste laco (auditoria M4.3).
        # Eram as unicas chamadas de banco desprotegidas aqui, e o cron
        # roda em thread paralela ao webhook: "database is locked" e
        # cenario real. A mensagem SAIU; se o carimbo estoura, ele leva
        # junto quem estava atras na fila — e o `_loop_proativo` engole
        # com "ciclo falhou", que foi o silencio que escondeu o P0-1.
        try:
            if (ok and d.get("user_id")
                    and d.get("kind") in ("podcast", "podcast-convite")):
                db.podcast_marcar_convite(d["user_id"])
            if (ok and d.get("user_id")
                    and d.get("kind") == "podcast-dia"):
                db.podcast_marcar_pergunta_do_dia(d["user_id"])
        except Exception:
            log.warning("[cron] falhei ao carimbar o podcast",
                        exc_info=True)
        # A OFERTA DE REMARCAR PRECISA DEIXAR CONTEXTO PRA RESPOSTA.
        #
        # Sem isto a feature nao funcionava de ponta a ponta: a pergunta saia,
        # a pessoa tocava em "Confirmar" e o handler nao achava contexto
        # nenhum — silencio total, logo depois de ela fazer o que o bot pediu.
        # Meus testes passavam porque setavam o PENDING na mao.
        #
        # SO QUANDO `ok`: se a pergunta nao saiu, nao pode existir contexto
        # esperando resposta, senao um "confirmar" de outro assunto criaria
        # item do nada.
        # NAO ATROPELA CONVERSA EM ANDAMENTO. Se ja ha algo esperando
        # resposta (menu 1/2, escolha de baixa), a oferta de remarcar nao
        # toma o lugar dele — a pessoa responderia o menu e cairia aqui.
        _ocupado = bool((PENDING.get(number) or {}) and
                        (PENDING[number].get("tipo") != "confirmar_retorno"))
        if (ok and d.get("kind") == "retorno" and d.get("sugestao")
                and not _ocupado):
            PENDING[number] = {"tipo": "confirmar_retorno",
                               "sugestao": d["sugestao"],
                               "descricao": d.get("descricao") or "",
                               "quando": tempo.agora()}
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
                    # O CÓDIGO DE PAGAMENTO NÃO VAI PRO LOG (auditoria M3.5,
                    # P1-6). A mensagem de vencimento passou a carregar a
                    # linha digitável, e os 200 primeiros chars alcançavam
                    # parte dela. `sem_codigo_de_pagamento` já existe pra
                    # isso; aqui ela volta a valer no caminho de falha, que é
                    # justamente onde o guardrail costuma ser esquecido.
                    _preview = boleto.sem_codigo_de_pagamento(
                        (d.get("message") or "").split("\n\nCódigo")[0]
                        .split("\n\nPIX copia")[0])
                    db.log_message(d.get("user_id"), number, "out_falhou",
                                   d.get("kind", "outro"),
                                   f"[{res.get('motivo')}] {_preview[:200]}")
            except Exception:
                log.warning("[cron] falha ao registrar a nao-entrega",
                            exc_info=True)
    try:
        ULTIMO_CICLO["enviados"] = sent
        ULTIMO_CICLO["tentativas"] = _tentativas
        if _pulados:
            ULTIMO_CICLO["pulados"] = dict(_pulados)
    except Exception:
        pass          # diagnostico nunca derruba o ciclo de verdade
    return sent


def _estado_dos_templates() -> dict:
    """Quais kinds proativos conseguem falar com quem esta FORA da janela.

    Fail-closed: sem o nome em `TEMPLATES_APROVADOS`, o `canal` recusa e a
    promessa daquele kind so vale pra quem respondeu nas ultimas 24h.
    """
    try:
        import templates as _cat
        aprovados = {p.strip() for p in
                     (os.environ.get("TEMPLATES_APROVADOS", "") or "").split(",")
                     if p.strip()}
        liberados, faltando = [], []
        for kind, nome in sorted(_cat.KIND_TEMPLATE.items()):
            (liberados if nome in aprovados else faltando).append(kind)
        return {"liberados": liberados, "faltando": faltando,
                "sem_template_por_decisao": sorted(_cat.KINDS_SEM_TEMPLATE)}
    except Exception:
        log.warning("[templates] nao consegui montar o estado", exc_info=True)
        return {"erro": True}


# CORTESIA: o que a gente manda porque QUER. Some no vermelho.
#
# O criterio nao e "e importante?", e "quem pediu?". Lembrete de conta a
# pessoa pediu — e o produto. Convite de podcast e reengajamento sao nossos.
KINDS_DE_CORTESIA = frozenset({
    "anti-churn", "winback", "reativacao",
    "podcast", "podcast-convite", "podcast-dia",
})

# UMA CORTESIA POR PESSOA A CADA 7 DIAS — a regra inteira, num numero so.
#
# Cada feature ja tinha seu teto (convite 1x, cobranca 2x, empurrao 1x,
# anti-churn 3x). Somados, a mesma pessoa recebia varias coisas nossas na
# mesma semana, cada uma "dentro do limite dela". Teto por feature nao e
# teto: e a soma que a pessoa sente, e e a soma que a Meta le.
CORTESIA_INTERVALO_DIAS = int(os.environ.get("CORTESIA_INTERVALO_DIAS", "7"))


# Depois de quantas proativas seguidas sem NENHUMA resposta a gente para de
# puxar assunto com uma pessoa. Cinco e generoso: quem le e nao responde
# ainda recebe cinco convites antes de a gente entender o recado.
SILENCIO_ATE_PARAR = int(os.environ.get("SILENCIO_ATE_PARAR", "5"))


def _cortesia_recente(user_id) -> bool:
    """Esta pessoa ja recebeu alguma cortesia NOSSA nos ultimos N dias?

    Na duvida devolve True: adiar um convite e recuperavel, mandar duas
    mensagens nossas na mesma semana e o que faz a pessoa bloquear.
    """
    if not user_id:
        return False
    try:
        return any(db.dispatched_within(k, user_id,
                                        days=CORTESIA_INTERVALO_DIAS)
                   for k in KINDS_DE_CORTESIA)
    except Exception:
        log.warning("[cortesia] nao consegui medir o intervalo do user %s",
                    user_id, exc_info=True)
        return True


def _parou_de_ouvir(user_id) -> bool:
    """Esta pessoa parou de responder ha muitas mensagens seguidas?

    NAO e sobre a base, e sobre ELA. Quem conversa com o bot recebe tudo o
    que a gente construiu, sem teto e sem espera — e e por isso que escalar
    com clientes engajados nao tem risco: cada resposta entra do outro lado
    da conta que a Meta faz.

    Na duvida devolve False: erro aqui nao pode calar ninguem.
    """
    if not user_id:
        return False
    try:
        return db.proativas_sem_resposta(user_id) >= SILENCIO_ATE_PARAR
    except Exception:
        log.warning("[engajamento] nao consegui medir o silencio do user %s",
                    user_id, exc_info=True)
        return False


def _quantos_pararam_de_ouvir() -> int:
    """Quantas pessoas da base pararam de responder. So contagem."""
    try:
        return sum(1 for u in db.list_users() if _parou_de_ouvir(u["id"]))
    except Exception:
        log.warning("[engajamento] nao consegui contar", exc_info=True)
        return -1


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

    # A LANDING E SERVIDA PELO PROPRIO APP (M5.8).
    #
    # Ela ja viajava na imagem (`COPY . .` no Dockerfile) e nunca teve rota:
    # "por no ar" dependia de um segundo servico que nunca existiu. Servir
    # daqui resolve hoje, no IP, e continua valendo no dia em que um dominio
    # apontar pro EasyPanel — nao muda nada no codigo.
    #
    # LIDA DO DISCO A CADA PEDIDO, de proposito: a landing muda mais que o
    # bot, e cache em memoria significaria subir a imagem toda pra trocar uma
    # frase. Sao 26 KB e o trafego e de dezenas por dia, nao milhares.
    @app.get("/")
    async def landing():
        from fastapi.responses import HTMLResponse, JSONResponse
        try:
            with open(os.path.join(os.path.dirname(
                    os.path.abspath(__file__)), "landing.html"),
                    encoding="utf-8") as f:
                return HTMLResponse(f.read())
        except Exception:
            # A landing sumir NAO pode derrubar o webhook: o bot atende 11
            # pessoas e a pagina e vitrine. Falha visivel, servico de pe.
            log.warning("[landing] nao consegui ler o arquivo", exc_info=True)
            return JSONResponse({"erro": "landing indisponivel"},
                                status_code=503)

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
                # SO CONTAGENS (M6.1). Nenhum nome, telefone ou id: serve pra
                # responder "por que a fila da reativacao esta vazia?" sem o
                # token do painel, que e segredo. O `/health` e publico, e
                # este bloco e estritamente menos sensivel do que o
                # `usuario_mais_notificado` que ja estava aqui.
                "reativacao": dict(scheduler.reativacao_diagnostico(),
                                   **({"ultima_recusa":
                                       ULTIMA_RECUSA_REATIVACAO}
                                      if ULTIMA_RECUSA_REATIVACAO else {})),
                "ciclo": dict(ULTIMO_CICLO) or "AINDA NAO RODOU",
                # Quantas pessoas pararam de responder (M7.4). Nao e
                # freio de operacao: e quanta gente sumiu.
                "sem_responder": _quantos_pararam_de_ouvir(),
                # O NOME DO TEMPLATE NAO E SEGREDO (M6.4). `TEMPLATES_APROVADOS`
                # e uma allowlist fail-closed: se o nome nao estiver la, o
                # `canal` recusa o envio e a fila fica parada pra sempre, sem
                # nada visivel de fora. Isto responde "ja posso mandar?" sem
                # precisar abrir o EasyPanel.
                # QUAIS TEMPLATES ESTAO LIBERADOS (M7.1).
                #
                # `TEMPLATES_APROVADOS` e allowlist fail-closed: kind cujo
                # template nao esta la simplesmente NAO ALCANCA quem esta
                # fora da janela de 24h — e isso e invisivel, porque a
                # mensagem apenas nao chega. Antes de escalar, esta e a
                # pergunta que decide quais promessas o produto consegue
                # cumprir com cliente que ficou quieto.
                #
                # Nome de template nao e segredo. O valor das credenciais
                # continua fora daqui.
                "templates": _estado_dos_templates(),
                # O BOTAO DE VELOCIDADE DEPENDE DO OPUS, e o Opus depende
                # do ffmpeg estar na imagem. Sem este campo, "cade o
                # acelerador?" so se responde abrindo o log do container —
                # que e canvas e nao da pra ler. Sinal que so existe no log
                # e sinal que ninguem le.
                "audio": _diagnostico_de_audio(),
                "painel": "protegido" if PAINEL_TOKEN else "SEM TOKEN",
                "alerta_dono": "armado" if ADMIN_PHONE else "SEM ADMIN_PHONE"}
        # o diagnóstico do v8 carrega trecho de mensagem de usuário —
        # só sai com token, senão /health vira vazamento de conversa.
        if _painel_autorizado(request):
            body["v8_ultima_falha"] = getattr(motor_v8, "ULTIMA_FALHA", "")
            # QUAL KIND ESTA MUDO FORA DA JANELA DE 24H.
            #
            # `TEMPLATES_APROVADOS` e uma env var, e o gate e fail-closed: o
            # que nao esta la NAO SAI, sem erro e sem log de negocio. Isso e
            # certo — template nao aprovado nunca pode virar envio silencioso
            # — mas cria o buraco oposto: template APROVADO na Meta e
            # esquecido na variavel tambem fica mudo, e do lado de ca nada
            # aparece. Ja perdemos aviso assim.
            #
            # Aqui o /health responde a pergunta direto, sem precisar abrir o
            # EasyPanel (onde o token da Meta esta na mesma tela).
            try:
                import templates as _tpls
                _apr = wasender._aprovados()
                _faltando = sorted(
                    {k: t for k, t in _tpls.KIND_TEMPLATE.items()
                     if t not in _apr}.items())
                body["templates"] = {
                    "aprovados": sorted(_apr),
                    "kind_sem_template_liberado": [
                        f"{k} -> {t}" for k, t in _faltando],
                }
            except Exception:
                # O /health existe pra dizer o que esta mudo; se ELE
                # emudecer, tem que sobrar rastro (auditoria M3.9, P2-7).
                #
                # `import logging` LOCAL (auditoria M4.0): o `wa_bot` nao tem
                # `logging` no namespace do modulo, entao a versao anterior
                # trocava um `except: pass` silencioso por um NameError que
                # devolvia 500 PELADO — justo na ferramenta que a gente usa
                # pra conferir se o deploy subiu.
                import logging
                logging.getLogger("resolveai").warning(
                    "[health] nao consegui ler os templates", exc_info=True)
                body["templates"] = "nao consegui ler"
            # MINI-PODCAST: sem provedor de voz a feature nem e oferecida, e
            # sem isso aqui so daria pra descobrir isso pelo silencio.
            try:
                import voz as _voz
                body["podcast"] = {
                    "voz": _voz.provedor_configurado() or "SEM PROVEDOR",
                    "formato": _voz.MIME,
                    "custo_mes_usd_por_1000":
                        round(_voz.custo_mensal_estimado_usd(1000), 2),
                }
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[health] nao consegui ler o estado da voz", exc_info=True)
                body["podcast"] = "nao consegui ler"
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
            # SCRUB TAMBEM NA ENTRADA (auditoria M4.0). A pessoa cola o
            # codigo de volta pra perguntar alguma coisa, e a linha
            # digitavel dela ficava em claro no msg_log — a frase "codigo de
            # pagamento nao entra no log" tem que valer nas duas direcoes.
            db.log_message(None, num, "in", kind,
                           boleto.sem_codigo_de_pagamento(content))
        except Exception:
            import logging
            logging.getLogger("resolveai").warning(
                "[webhook] falha ao logar entrada", exc_info=True)

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
            # `reply["botoes"]` quando quem montou a resposta ja sabe quais
            # sao (proposta de documento, oferta de remarcar). Sem passar
            # isto, a pergunta saia como texto puro e a pessoa tinha que
            # digitar "confirmar" — auditoria M3.5, P1-8.
            ok = botoes.enviar_resposta(reply["number"], reply["text"],
                                        send_whatsapp,
                                        botoes=reply.get("botoes"))
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
                # O CODIGO DE PAGAMENTO NAO ENTRA NO LOG (auditoria M3.9,
                # P0-1). O guardrail ja valia no caminho proativo; este
                # commit criou o PRIMEIRO caminho em que um codigo de
                # pagamento vira `reply["text"]` — e ele passava direto.
                # A linha digitavel ficaria em claro no banco, no painel e
                # em todo backup, sem a pessoa saber.
                db.log_message(None, reply["number"],
                               "out" if ok else "out_falhou", "texto",
                               boleto.sem_codigo_de_pagamento(reply["text"]))
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[webhook] falha ao logar resposta", exc_info=True)
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
            except Exception as e:
                # A EXCECAO PRECISA SER VISIVEL DE FORA (M6.5).
                #
                # Este `except` engole o ciclo inteiro e escreve num log que
                # so se le com acesso a VPS. Em 30/08 ele escondeu, por
                # horas, um motor proativo que quebrava TODO ciclo — e o
                # sintoma visivel era so "a fila nao anda".
                #
                # So o tipo e a mensagem da excecao, truncados. Nada de
                # telefone, nome ou conteudo de mensagem.
                try:
                    ULTIMO_CICLO["erro"] = "%s: %s" % (
                        type(e).__name__, str(e)[:120])
                    ULTIMO_CICLO["erro_em"] = tempo.agora().strftime(
                        "%d/%m %H:%M:%S")
                except Exception:
                    pass
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
        """O painel antigo. Hoje leva pro novo, que tem tudo o que ele tinha.

        Eram duas telas com dados diferentes sobre o mesmo negocio, e o dono
        se perdia entre elas — o link do relatorio diario ia pra uma, o
        atalho do celular pra outra. O /dash absorveu o que so existia aqui
        (as ultimas mensagens e o botao de testar o motor), entao manter as
        duas so multiplicaria o lugar onde procurar.

        `?antigo=1` ainda abre esta tela. E saida de emergencia, nao opcao:
        se faltar alguma coisa no novo, o dono nao fica sem painel enquanto
        eu conserto.
        """
        if not _painel_autorizado(request):
            return _negado(request)
        from fastapi.responses import HTMLResponse
        if not request.query_params.get("antigo"):
            from fastapi.responses import RedirectResponse
            _k = request.query_params.get("k") or ""
            import urllib.parse as _up
            return RedirectResponse(
                "/dash?k=" + _up.quote(_k, safe=""), status_code=302)
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

        # OS TRES FAROIS DO PODCAST (M9.10). Nunca levanta: painel que quebra
        # por causa de telemetria e um painel a menos num dia de incidente.
        try:
            _pod_f = db.podcast_farois()
        except Exception:
            log.warning("[dash] farois do podcast falharam", exc_info=True)
            _pod_f = {"estado": "sem dados", "ok": 0, "falhas": 0,
                      "segundos_medio": 0, "na_semana": 0, "ultimo": ""}
        _pod_cor = {"ok": "#22c55e", "atencao": "#f59e0b",
                    "quebrado": "#ef4444"}.get(_pod_f["estado"], "#94a3b8")
        _pod_luz = {"ok": "🟢 gerando", "atencao": "🟠 com falhas",
                    "quebrado": "🔴 quebrado"}.get(_pod_f["estado"],
                                                   "⚪ sem dados")
        # O RODAPE CONTA O PORQUE. "🟠 com falhas" sozinho manda o Kevin
        # abrir o log pra descobrir o que ja esta gravado aqui do lado.
        _pod_nota = ("nenhum episódio ainda"
                     if _pod_f["estado"] == "sem dados"
                     else "%d ok · %d falha%s nos últimos 7 dias"
                          % (_pod_f["ok"], _pod_f["falhas"],
                             "" if _pod_f["falhas"] == 1 else "s"))
        _pod_seg = int(_pod_f["segundos_medio"] or 0)
        _pod_dur = ("—" if not _pod_seg
                    else "%d:%02d" % (_pod_seg // 60, _pod_seg % 60))

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
<h1 style="font-size:15px">🎧 Mini podcast</h1>
<div class="grid">
<div class="card"><div class="n" style="color:{_pod_cor};font-size:19px">{_pod_luz}</div><div class="l">{_pod_nota}</div></div>
<div class="card"><div class="n">{_pod_dur}</div><div class="l">duração média do áudio (min:seg)</div></div>
<div class="card"><div class="n">{_pod_f['na_semana']}</div><div class="l">episódios enviados na semana</div></div>
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
/* ABAS. Rolam de lado no celular e cabem inteiras no desktop. */
#abas{display:flex;gap:6px;overflow-x:auto;margin:0 -14px 14px;
 padding:0 14px 8px;scrollbar-width:none;-ms-overflow-style:none;
 position:sticky;top:0;z-index:9;background:#0b1220}
#abas::-webkit-scrollbar{display:none}
.aba{flex:0 0 auto;padding:8px 13px;border-radius:999px;cursor:pointer;
 border:1px solid #1f2c47;background:#131d31;color:#8296b3;
 font-size:13px;font-weight:600;white-space:nowrap}
.aba.on{background:#1c4d3a;border-color:#2e7d5b;color:#d7f5e6}
/* DESKTOP NAO E CELULAR ESTICADO.
   Grade de verdade, e nao `column-count`: coluna preenche de cima pra
   baixo, entao o segundo card ia parar embaixo do primeiro e a ordem de
   leitura virava vertical. `auto-fill` acomoda 1, 2 ou 3 colunas conforme
   a tela, sem media query por largura. */
#painel{display:grid;gap:12px;align-items:start;
 grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr))}
#painel>.card{margin-bottom:0}
/* Cards que sao lista ou formulario ocupam a linha inteira: espremidos em
   340px eles ficam ilegiveis. */
#painel>.card.largo{grid-column:1/-1}
@media(min-width:900px){
 body{padding:22px calc((100vw - 1240px)/2 + 22px) 48px}
 #abas{margin:0 0 18px;padding:0 0 10px}
 h1{font-size:20px}
 .aba{font-size:13.5px;padding:9px 16px}
}
@media(min-width:1500px){
 body{padding:26px calc((100vw - 1480px)/2 + 26px) 56px}
}
/* Tabela de mensagens: rola sozinha em vez de esticar a pagina. */
.rolatab{overflow-x:auto;-webkit-overflow-scrolling:touch}
.rolatab table{width:100%;border-collapse:collapse;font-size:12px}
.rolatab td{padding:6px 8px;border-bottom:1px solid #1f2c47;
 vertical-align:top;text-align:left}
.rolatab .q{color:#8296b3;white-space:nowrap}
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
/* CONTROLES (M2.9). Alvo de toque de 34px: o painel é usado no celular, com
   uma mão, e botão que erra o dedo aqui aprova o plano errado. */
.b{display:inline-block;min-height:34px;padding:7px 11px;margin:2px 4px 2px 0;
 border:1px solid #2b3a5c;background:#1a2540;color:#dbe6f7;border-radius:9px;
 font-size:12px;font-weight:600;cursor:pointer}
.b:active{transform:scale(.97)}
.bok{border-color:#1d6b3f;background:#14301f;color:#7ee2a8}
.berr{border-color:#6b1d1d;background:#301414;color:#f0a3a3}
/* `min-width:0` é o que impede o <select> de empurrar o botão "enviar" pra
   fora da linha: por padrão um item flex não encolhe abaixo do conteúdo, e
   rótulo longo ("Avisa que o teste está acabando e oferece a assinatura")
   deixava o botão inalcançável no celular. */
.sel{flex:1;min-width:0;min-height:34px;background:#1a2540;color:#dbe6f7;
 font-size:12px;border:1px solid #2b3a5c;border-radius:9px;padding:6px}
.filtros{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:11px}
.fl{min-height:30px;padding:5px 10px;border-radius:99px;font-size:11px;
 border:1px solid #2b3a5c;background:transparent;color:#8296b3;cursor:pointer}
.fl.on{background:#1f2c47;color:#e6edf7;border-color:#3b82f6}
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
// A QUAL ABA CADA CARD PERTENCE.
//
// O mapa mora aqui, e nao espalhado em cada chamada, por dois motivos: as
// chamadas de `card` sao multilinha (mexer nelas e onde se quebra
// parentese), e assim da pra ler a organizacao inteira do painel de uma
// vez. Card fora do mapa cai em "negocio" e continua aparecendo — esquecer
// de mapear nao pode fazer informacao SUMIR da tela.
const ABA_DO_CARD={
 'Isto está virando negócio?':'negocio',
 'As pessoas estão usando?':'negocio',
 'Últimos 7 dias':'negocio',
 'Quem mais usa (7d)':'negocio',
 'Em que a base gasta':'negocio',
 'Dinheiro':'financeiro',
 'Margem por cliente':'financeiro',
 '💰 Pediram o link — conferir no Mercado Pago':'financeiro',
 'Clientes':'clientes',
 'Mandar pra uma lista':'clientes',
 'Sumiram — vale uma ligação':'clientes',
 'Devolver 14 dias pra todo mundo':'clientes',
 'O que o Resolve AI faz':'produto',
 '🎧 Mini podcast':'produto',
 'Conselheiro de crescimento':'crescimento',
 'Conselheiro de preço':'crescimento',
 'Conselheiro de marketing':'crescimento',
 'Conselheiro de experiência':'crescimento',
 'Ideias de produto':'crescimento',
 'Metas de lucro':'financeiro',
 'Custo real por cliente':'financeiro',
 'Está no ar?':'sistema',
 'Está no ar? (detalhe)':'sistema',
 'Testar o motor agora':'sistema',
 'Últimas mensagens':'sistema',
 'O número está em risco?':'sistema'
};
const ABAS={};
// A aba escolhida sobrevive ao redesenho de 20s e a proxima visita.
let ABA_ATIVA=(function(){
 try{ return localStorage.getItem('resolveai_aba')||'negocio' }
 catch(e){ return 'negocio' }
})();
async function testarMotorAgora(){
 const bt=document.getElementById('btMotor');
 if(bt){ bt.disabled=true; bt.textContent='Rodando...'; }
 try{
   const r=await fetch('/cron/proactive',
     {method:'POST',headers:{'Content-Type':'application/json',
                             'X-Painel-Token':K}});
   const j=await r.json();
   alert('Ciclo rodado. Disparos agora: '+(j.sent||0)+'.');
 }catch(e){ alert('Sem conexao com o servidor.'); }
 if(bt){ bt.disabled=false; bt.textContent='Rodar um ciclo agora'; }
 carrega();
}
async function pedirConselho(tipo){
 const bt=document.getElementById('bt_'+tipo);
 // O modelo leva alguns segundos. Sem travar o botao, o dono clica de novo
 // e paga duas analises — foi o que aconteceu com o disparo em lote.
 if(bt){ bt.disabled=true; bt.textContent='Analisando...'; }
 try{
   const r=await fetch('/painel/conselho?k='+encodeURIComponent(K),
     {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tipo:tipo,forcar:true})});
   const j=await r.json();
   if(!j.ok){ alert('Nao deu: '+(j.erro||'tente de novo')); }
 }catch(e){ alert('Sem conexao com o servidor.'); }
 if(bt){ bt.disabled=false; bt.textContent='Analisar de novo'; }
 carrega();
}
async function salvarMetas(){
 const v=id=>parseFloat((document.getElementById(id)||{}).value||'0')||0;
 const metas={curto:v('mt_curto'),medio:v('mt_medio'),bom:v('mt_bom')};
 if(!metas.curto||!metas.medio||!metas.bom){
   alert('Preencha as tres metas.'); return;
 }
 try{
   const r=await fetch('/painel/metas?k='+encodeURIComponent(K),
     {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(metas)});
   const j=await r.json();
   if(!j.ok){ alert('Nao deu: '+(j.erro||'tente de novo')); return; }
 }catch(e){ alert('Sem conexao com o servidor.'); return; }
 carrega();
}
function troca(k){
 ABA_ATIVA=k;
 try{ localStorage.setItem('resolveai_aba',k) }catch(e){}
 carrega();
}
// Cards que nao cabem numa coluna estreita: lista de gente, tabela de
// mensagens, formulario de envio e o texto do conselheiro.
const CARD_LARGO=new Set(['Clientes','Mandar pra uma lista',
 'Últimas mensagens','Conselheiro de crescimento','Conselheiro de preço',
 'Conselheiro de marketing','Conselheiro de experiência',
 'Ideias de produto','O que o Resolve AI faz']);
function card(t,c){
 const largo=CARD_LARGO.has(t)?' largo':'';
 const html=`<div class="card${largo}"><h2>${t}</h2>${c}</div>`;
 const a=ABA_DO_CARD[t]||'negocio';
 ABAS[a]=(ABAS[a]||'')+html;
 return html;
}
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
 // OS TRES FAROIS DO MINI PODCAST (M9.10)
 const P=d.podcast||{estado:'sem dados',duracao:'—',na_semana:0,
                     nota:'nenhum episódio ainda'};
 const pluz={ok:'ok',atencao:'warn',quebrado:'bad'}[P.estado]||'';
 const ptxt={ok:'Gerando',atencao:'Com falhas',
             quebrado:'Quebrado'}[P.estado]||'Sem dados';
 const conn=d.conectado?'ok':'bad';
 const risco=(e.risco||'').includes('alto')?'bad':(e.risco||'').includes('aten')?'warn':'ok';
 const hab=(g.veredito||'').includes('🟢')?'ok':(g.veredito||'').includes('🟡')?'warn':'bad';
 const cron=d.cron_min==null?'bad':(d.cron_min<=3?'ok':'warn');
 // ABA AO VIVO: o que so existia no painel antigo, em /painel.
 //
 // Eram duas telas separadas e o dono se perdia entre elas. As 30 ultimas
 // mensagens ja vinham no payload — nao precisou de nada novo no servidor.
 const ULT=(m.ultimas||[]);
 const linhasMsg=ULT.map(r=>{
   const ent=r.direcao==='in';
   const hora=(r.ts||'').slice(11,16);
   const tel=(r.telefone||'').slice(-4)||'----';
   const txt=(r.preview||'').replace(/</g,'&lt;').slice(0,90);
   const cor=ent?'#3b82f6':'#22c55e';
   return `<tr><td class="q">${hora}</td><td class="q">…${tel}</td>`
     +`<td class="q" style="color:${cor}">${ent?'recebida':'enviada'}</td>`
     +`<td>${txt}</td></tr>`;
 }).join('');
 // ZERA A CADA PINTURA. O painel se redesenha a cada 20s; sem isto os
 // cards se empilhariam e a tela cresceria pra sempre.
 Object.keys(ABAS).forEach(k=>delete ABAS[k]);
 let h='';
 // 0. O FUNIL DE VALIDACAO — vem PRIMEIRO de proposito.
 //
 // O topo da tela era "está no ar?", que é pergunta de plantão, não de
 // decisão: quando o bot está de pé (o normal) esse card não muda nada do
 // que o Kevin vai fazer no dia. A pergunta que ele precisa responder no
 // beta é "isto está virando negócio?", e ela se decide em quatro degraus.
 // Cada degrau é um gargalo diferente, e só o primeiro que estiver vazio
 // importa — por isso o veredito aponta um só.
 const V=d.validacao||{base:0,ativados:0,salvos:0,pagantes:0,retidos:0,pessoas:[]};
 const barra=(rot,n,tot,cor,dica)=>{
   const pct=tot?Math.round(n/tot*100):0;
   return `<div style="margin-bottom:11px">
     <div style="display:flex;justify-content:space-between;font-size:12px;
       margin-bottom:4px"><span>${rot}</span>
       <b style="font-variant-numeric:tabular-nums">${n}<span
        style="color:#8296b3;font-weight:400">/${tot} · ${pct}%</span></b></div>
     <div style="height:9px;background:#0b1220;border-radius:5px;overflow:hidden">
       <i style="display:block;height:100%;width:${pct}%;background:${cor}"></i></div>
     <div style="font-size:10px;color:#8296b3;margin-top:3px">${dica}</div></div>`;
 };
 h+=card('Isto está virando negócio?',
  `<div style="font-size:14px;line-height:1.5;margin-bottom:14px;
     padding:11px;background:#0b1220;border-radius:10px;
     border-left:3px solid #f59e0b">${V.veredito||'—'}</div>`+
  barra('Entraram',V.base,V.base||1,'#3b82f6','pessoas na base (sem contar você)')+
  barra('Registram sozinhas',V.ativados,V.base,'#8b5cf6','3+ itens cadastrados')+
  barra('O bot já salvou',V.salvos,V.base,'#22c55e','deram baixa depois de um lembrete')+
  barra('Voltaram essa semana',V.retidos,V.base,'#14b8a6','falaram nos últimos 7 dias')+
  barra('Pagam',V.pagantes,V.base,'#f59e0b','assinatura ativa'));
 // 0b. PRA QUEM LIGAR. Com 11 pessoas, a acao util nao e "melhorar a
 // metrica" — e falar com o fulano que sumiu. Por isso nome, e nao contagem.
 const sumidos=(V.pessoas||[]).filter(p=>p.sumido);
 if(sumidos.length){
   h+=card('Sumiram — vale uma ligação',
     sumidos.map(p=>`<div style="display:flex;justify-content:space-between;
       padding:7px 0;border-bottom:1px solid #1f2c47">
       <span><b>${p.nome}</b> <span class="muted" style="font-size:11px">
       ${p.itens} ${p.itens===1?'item':'itens'} · ${p.salvo?'já foi salvo pelo bot':'nunca deu baixa'}
       </span></span>
       <span class="muted" style="font-size:12px;white-space:nowrap">
       ${p.visto_ha}d atrás</span></div>`).join(''));
 }
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
 // ESPERANDO APROVACAO — a unica fila com dinheiro parado.
 // Vem antes da base porque exige acao HOJE: a pessoa pediu o link, o Kevin
 // confere no Mercado Pago e diz mensal ou anual. O bot nao adivinha isso.
 const ap=d.aprovacoes||[];
 if(ap.length){
   h+=card('💰 Pediram o link — conferir no Mercado Pago',
     ap.map(p=>`<div style="padding:9px 0;border-bottom:1px solid #1f2c47">
       <div style="display:flex;justify-content:space-between;
         align-items:center;margin-bottom:7px">
         <b>${p.nome}</b><span class="muted" style="font-size:11px">
         pediu ${p.pediu_ha_dias===0?'hoje':'há '+p.pediu_ha_dias+'d'}</span></div>
       <button class="b bok" onclick="acao(${p.id},'aprovar',{plano:'mensal'})"
         >✓ Pagou mensal</button>
       <button class="b bok" onclick="acao(${p.id},'aprovar',{plano:'anual'})"
         >✓ Pagou anual</button>
       <button class="b" onclick="cobrar(${p.id})">↻ Cobrar de novo</button>
     </div>`).join(''));
 }
 // BASE COM CONTROLE. Filtro por status + os botoes de admin em cada linha.
 const F=window.__filtro||'todos';
 window.__users=d.usuarios||[];   // `zerar` lê o nome daqui, não do onclick
 window.__dados=d;                // `poderes()` lê a lista daqui
 const cont={todos:(d.usuarios||[]).length};
 (d.usuarios||[]).forEach(u=>{const k=u.status||'trial';
   cont[k]=(cont[k]||0)+1});
 const abas=['todos','trial','ativo','bloqueado','cancelado'].map(k=>
   `<button class="fl ${F===k?'on':''}" onclick="filtro('${k}')">${k}
    <span class="muted">${cont[k]||0}</span></button>`).join('');
 // DISPAROS EM LOTE. O card fica ANTES da lista porque a decisao aqui e
 // "quero falar com um grupo", nao "quero mexer numa pessoa".
 const TPL=d.templates||[];
 const SEG=d.segmentos||{};
 const optSeg=Object.keys(SEG).map(k=>
   `<option value="${k}">${k} (${SEG[k]})</option>`).join('');
 const optTplLote=TPL.map(t=>
   `<option value="${t.nome}" data-pede="${(t.pede_texto||[]).join(',')}" data-enviados="${
     (t.envio||{}).quantos||0}" data-ultimo="${(t.envio||{}).ultimo||''}">${
     t.rotulo}${t.automatico?' · já automático':''}</option>`
 ).join('');
 // O QUE O RESOLVE AI FAZ (M3.4). Fica junto dos controles porque o Kevin
 // consulta isso pra decidir o que vender e o que ainda falta construir.
 h+=card('O que o Resolve AI faz',
  `<div class="muted" style="font-size:11px;margin-bottom:9px">
     ${(d.poderes||[]).length} recursos no ar — atualizado a cada entrega.</div>
   <button class="b" style="width:100%" onclick="poderes()">
     Ver a lista completa</button>`);
 // RESET DE TRIAL EM BOTÃO. O comando por WhatsApp exige a frase exata
 // ("resetar trial de todos") e falhou calado em 28/08 quando a frase saiu
 // diferente. Botão não erra a digitação.
 h+=card('Devolver 14 dias pra todo mundo',
  `<div class="muted" style="font-size:11px;margin-bottom:9px">
     Reinicia o teste de todos os clientes a partir de hoje. Não toca em
     item, lembrete nem histórico, e você fica de fora.</div>
   <button class="b" style="width:100%" onclick="resetarTrials()">
     Resetar trial de todos</button>`);
 h+=card('Mandar pra uma lista',
  `<div class="muted" style="font-size:11px;margin-bottom:9px">
     Os marcados como <b>já automático</b> o motor manda sozinho na hora
     certa — use aqui só se quiser antecipar.</div>
   <select id="segLote" class="sel" style="width:100%;margin-bottom:7px">${optSeg}</select>
   <select id="tplLote" class="sel" style="width:100%;margin-bottom:9px"
           onchange="camposDoLote()">${optTplLote}</select>
   <div id="jaFoi" class="muted" style="font-size:11px;margin-bottom:7px"></div>
   <div id="txtLote" style="display:none;margin-bottom:9px">
     <input id="novNome" class="sel" maxlength="220"
            style="width:100%;margin-bottom:6px"
            placeholder="Nome da novidade (ex: mini podcast em áudio)">
     <textarea id="novTexto" class="sel" rows="3" maxlength="220"
            style="width:100%;resize:vertical"
            placeholder="O que ela faz, em uma ou duas frases"></textarea>
     <div class="muted" style="font-size:11px;margin-top:4px">
       Isso entra na mensagem que TODO mundo do grupo vai ler.</div>
   </div>
   <button class="b bok" id="btLote" style="width:100%" onclick="lote()">Enviar pra lista</button>`);
 const us=(d.usuarios||[]).filter(u=>F==='todos'||(u.status||'trial')===F)
  .map(u=>{
   const a=u.assinatura||{};
   // A LINHA DO DINHEIRO: so quem tem plano mostra ciclo. Trial mostra dias
   // restantes; atrasado grita, porque e o que manda conferir o extrato.
   let ciclo='';
   if(a.plano){
     ciclo=a.atrasado
      ? `<span class="err">⚠ ${a.plano} venceu há ${a.dias_atraso}d
         (${a.vence_em})</span>`
      : `<span class="muted">${a.plano} · renova ${a.vence_em}
         (${a.dias_para_vencer}d)</span>`;
   } else {
     // "trial · 0d" parecia defeito. Trial que acabou é uma informação
     // diferente de trial que está acabando, e é a que pede ação.
     const dt=u.dias_trial_restantes;
     ciclo=(dt<=0)
       ? `<span class="err">trial acabou — não recebe mais avisos</span>`
       : `<span class="muted">trial · ${dt}d restantes</span>`;
   }
   const opts=TPL.map(t=>`<option value="${t.nome}">${t.rotulo}</option>`).join('');
   return `<div style="padding:11px 0;border-bottom:1px solid #1f2c47">
     <div style="display:flex;justify-content:space-between;align-items:center">
       <b>${u.nome}</b>
       <span class="tag">${u.status||'trial'}</span></div>
     <div style="font-size:11px;margin:4px 0 8px">${ciclo}
       <span class="muted">· ${u.n_pendentes}/${u.n_itens} itens</span></div>
     <div>
       <button class="b bok" onclick="acao(${u.id},'aprovar',{plano:'mensal'})">mensal</button>
       <button class="b bok" onclick="acao(${u.id},'aprovar',{plano:'anual'})">anual</button>
       <button class="b" onclick="dias(${u.id})">+dias</button>
       ${(u.status==='bloqueado')
         ? `<button class="b" onclick="acao(${u.id},'liberar')">desbloquear</button>`
         : `<button class="b berr" onclick="acao(${u.id},'bloquear')">bloquear</button>`}
       <button class="b berr" onclick="zerar(${u.id})">zerar</button>
     </div>
     <div style="margin-top:7px;display:flex;gap:6px">
       <select id="t${u.id}" class="sel">${opts}</select>
       <button class="b" onclick="mandar(${u.id})">enviar</button>
     </div></div>`;
  }).join('');
 h+=card('🎧 Mini podcast',
   `<div class="st"><span class="dot ${pluz}"></span>${ptxt}</div>
     <div class="sub" style="margin:6px 0 10px">${P.nota}</div>
     <div class="grid">
       <div class="kpi"><div class="l">Duração média</div>
         <div class="v">${P.duracao}</div><div class="u">min:seg</div></div>
       <div class="kpi"><div class="l">Enviados na semana</div>
         <div class="v">${P.na_semana}</div><div class="u">episódios</div></div>
     </div>`);
 h+=card('Clientes',
   `<div class="filtros">${abas}</div>`+
   (us||'<div class="muted">ninguém com esse status</div>'));
 // O PAINEL SE REDESENHA A CADA 20s E ISSO APAGAVA O QUE O DONO DIGITAVA.
 //
 // O card de lote virou um formulario de verdade quando ganhou os dois
 // campos de texto do lancamento. Redesenhar troca o innerHTML inteiro:
 // os seletores voltavam pro primeiro item, os campos sumiam e o texto ia
 // junto. Vinte segundos nao dao pra escolher o template e escrever duas
 // frases — na pratica era impossivel usar.
 //
 // Guardar e devolver os valores. E `_ocupado` cobre o resto: restaurar o
 // valor nao devolve o CURSOR, entao quem esta com o dedo no campo nao
 // pode ser redesenhado no meio da palavra.
 const _lote={seg:_valor('segLote'),tpl:_valor('tplLote'),
              nome:_valor('novNome'),txt:_valor('novTexto')};
 // METAS DE LUCRO — em cliente, nao so em reais.
 //
 // "2 mil por mes" e desejo ate virar "106 clientes". A conta e feita no
 // servidor com a mesma margem do card de margem, entao os dois nunca
 // divergem.
 const MT=d.metas||{alvos:[]};
 const reais=v=>'R$ '+(v||0).toLocaleString('pt-BR',{minimumFractionDigits:0,
                                                     maximumFractionDigits:0});
 const linhasMeta=(MT.alvos||[]).map(a=>{
   const falta=a.clientes==null?'—':a.faltam+' a mais';
   const pct=a.clientes?Math.min(100,Math.round(
     (MT.assinantes_hoje/a.clientes)*100)):0;
   return `<div style="margin-bottom:11px">
     <div style="display:flex;justify-content:space-between;font-size:12px">
       <span>${a.rotulo} — <b>${reais(a.meta)}</b>/mês</span>
       <b style="font-variant-numeric:tabular-nums">${
         a.clientes==null?'—':a.clientes+' clientes'}</b></div>
     <div style="height:8px;background:#0b1220;border-radius:5px;
       overflow:hidden;margin:4px 0 3px">
       <i style="display:block;height:100%;width:${pct}%;
         background:#22c55e"></i></div>
     <div style="font-size:10px;color:#8296b3">faltam ${falta}
       · hoje ${MT.assinantes_hoje} pagando</div></div>`;
 }).join('');
 card('Metas de lucro', linhasMeta
   + `<div style="border-top:1px solid #1f2c47;margin-top:10px;padding-top:10px">
       <div class="muted" style="font-size:11px;margin-bottom:7px">
         Margem de ${reais(MT.margem_por_cliente)} por cliente
         · fixo de ${reais(MT.fixo)}/mês. Mudar as metas:</div>
       <div style="display:flex;gap:6px;flex-wrap:wrap">
         <input id="mt_curto" class="sel" style="flex:1;min-width:90px"
           placeholder="6 meses" value="${(MT.alvos[0]||{}).meta||''}">
         <input id="mt_medio" class="sel" style="flex:1;min-width:90px"
           placeholder="1 ano" value="${(MT.alvos[1]||{}).meta||''}">
         <input id="mt_bom" class="sel" style="flex:1;min-width:90px"
           placeholder="bom" value="${(MT.alvos[2]||{}).meta||''}">
       </div>
       <button class="b bok" style="width:100%;margin-top:7px"
         onclick="salvarMetas()">Salvar metas</button></div>`);

 // CUSTO REAL POR PESSOA. A media esconde o cliente caro; os dois aparecem.
 const CU=d.custo_usuario||{pessoas:0,medio:0,maior:0,topo:[]};
 const dinheiro=v=>'R$ '+(v||0).toFixed(2).replace('.',',');
 const topo=(CU.topo||[]).map(x=>
   `<tr><td>${x.nome}</td>
     <td class="q">${x.texto_in}t ${x.audio_in}a ${x.imagem_in}f
       ${x.episodios}p</td>
     <td class="q" style="text-align:right"><b>${dinheiro(x.custo_total)}</b></td>
    </tr>`).join('');
 // O CUSTO CHEIO PRIMEIRO, e o variavel depois.
 //
 // So o variavel (centavos) faz o produto parecer de graca e leva a
 // concluir que da pra baixar o preco. O fixo existe e alguem paga: e o
 // cheio que decide se o preco fecha.
 const sobra=CU.sobra_por_cliente;
 const corSobra=sobra==null?'#8296b3':(sobra>0?'#22c55e':'#ef4444');
 card('Custo real por cliente',
   `<div class="grid">
     <div class="kpi"><div class="l">Custo CHEIO por pessoa</div>
       <div class="v">${dinheiro(CU.cheio_medio)}</div>
       <div class="u">variável + fatia do fixo</div></div>
     <div class="kpi"><div class="l">Sobra por cliente</div>
       <div class="v" style="color:${corSobra}">${dinheiro(sobra)}</div>
       <div class="u">preço − custo cheio</div></div>
    </div>
    <div style="font-size:11px;color:#8296b3;margin:9px 0 4px">
      Fixo de ${dinheiro(CU.fixo_mes)}/mês ÷ ${CU.pessoas} pessoa(s)
      = ${dinheiro(CU.fixo_rateado)} cada. Quanto mais gente, menor essa
      fatia — é aqui que o volume resolve.</div>
    <div class="grid">
     <div class="kpi"><div class="l">Variável médio / 30 dias</div>
       <div class="v">${dinheiro(CU.medio)}</div>
       <div class="u">${CU.pessoas} pessoa(s)</div></div>
     <div class="kpi"><div class="l">O variável mais caro</div>
       <div class="v">${dinheiro(CU.maior)}</div>
       <div class="u">total da base ${dinheiro(CU.total)}</div></div>
    </div>
    <div class="sub" style="margin:10px 0 4px">Quem mais custa
      <span class="muted">(t=texto a=áudio f=foto p=podcast)</span></div>
    <div class="rolatab"><table>${topo||''}</table></div>`
   + (CU.conferido ? ''
      : `<div class="muted" style="font-size:11px;margin-top:9px">
          ⚠️ Estimativa: os preços unitários vieram de tabela pública, não
          de fatura. Confira uma fatura e ajuste antes de decidir preço.
         </div>`));

 // ABA CRESCIMENTO: os dois conselheiros.
 //
 // So por botao. Analise que se regenera junto com o painel queimaria
 // dinheiro em silencio e mudaria de opiniao a cada leitura. A resposta
 // fica guardada e datada, pra tela nunca nascer vazia.
 const CO=d.conselhos||{};
 const escapa=t=>(t||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
 const quandoBr=q=>q?(q.slice(8,10)+'/'+q.slice(5,7)+' '+q.slice(11,16)):'';
 const conselheiro=(tipo,titulo,linha)=>{
   const g=CO[tipo]||{};
   const corpo=g.texto
     ? `<div style="white-space:pre-wrap;font-size:13px;line-height:1.55">${
         escapa(g.texto)}</div>
        <div class="muted" style="font-size:11px;margin-top:9px">
          Análise de ${quandoBr(g.quando)}</div>`
     : `<div class="muted" style="font-size:12px">Nenhuma análise ainda.</div>`;
   card(titulo,
     `<div class="sub" style="margin:-4px 0 10px">${linha}</div>`
     + corpo
     + `<button class="b bok" id="bt_${tipo}" style="width:100%;margin-top:10px"
         onclick="pedirConselho('${tipo}')">${
           g.texto?'Analisar de novo':'Pedir análise'}</button>`);
 };
 conselheiro('crescimento','Conselheiro de crescimento',
   'Onde está o gargalo e o que fazer esta semana.');
 conselheiro('preco','Conselheiro de preço',
   'Olha o custo cheio por cliente e opina se R$ 19,90 está certo.');
 conselheiro('marketing','Conselheiro de marketing',
   'Quem é o cliente, que frase usar e como conseguir gente sem verba.');
 conselheiro('cx','Conselheiro de experiência',
   'Onde a pessoa trava, como é o primeiro dia ideal e o que a afasta.');
 conselheiro('produto','Ideias de produto',
   'Features novas que aumentam o uso diário e ideias de negócio pra chegar nas metas.');

 card('Últimas mensagens', linhasMsg
     ? `<div class="rolatab"><table>${linhasMsg}</table></div>`
     : '<div class="muted">Nenhuma mensagem ainda.</div>');
 card('Testar o motor agora',
   `<div class="muted" style="font-size:12px;margin-bottom:9px">
      Roda um ciclo do motor proativo na hora, sem esperar o relógio. Serve
      pra conferir se um lembrete que deveria ter saído sai mesmo.</div>
    <button class="b bok" id="btMotor" style="width:100%"
      onclick="testarMotorAgora()">Rodar um ciclo agora</button>`);
 card('Está no ar? (detalhe)',
   `<div class="grid">
     <div class="kpi"><div class="l">Recebidas hoje</div>
       <div class="v">${m.msgs_in_hoje}</div></div>
     <div class="kpi"><div class="l">Enviadas hoje</div>
       <div class="v">${m.msgs_out_hoje}</div></div>
     <div class="kpi"><div class="l">Itens criados hoje</div>
       <div class="v">${m.itens_hoje}</div></div>
     <div class="kpi"><div class="l">Lembretes disparados</div>
       <div class="v">${m.disparos_hoje}</div></div>
    </div>`);

 // MONTAGEM POR ABAS.
 //
 // `ABA_ATIVA` e global de proposito: o painel se redesenha a cada 20s e,
 // sem guardar a escolha, ele jogaria o dono de volta na primeira aba no
 // meio da leitura. `localStorage` guarda entre visitas.
 const ORDEM=[['negocio','Negócio'],['financeiro','Financeiro'],
              ['crescimento','Crescimento'],['clientes','Clientes'],
              ['produto','Produto'],['sistema','Ao vivo']];
 if(!ABAS[ABA_ATIVA]&&ABA_ATIVA!=='crescimento') ABA_ATIVA='negocio';
 // `barraAbas`, e nao `barra`: ja existe uma `barra` neste escopo (a das
 // barras de progresso do funil) e redeclarar derruba o painel inteiro.
 const barraAbas=ORDEM.map(([k,rot])=>
   `<div class="aba ${k===ABA_ATIVA?'on':''}" onclick="troca('${k}')">${rot}</div>`
 ).join('');
 h=`<div id="abas">${barraAbas}</div><div id="painel">`
   +(ABAS[ABA_ATIVA]||'<div class="card">Nada aqui ainda.</div>')+'</div>';
 $('#app').innerHTML=h;
 _devolve('segLote',_lote.seg); _devolve('tplLote',_lote.tpl);
 _devolve('novNome',_lote.nome); _devolve('novTexto',_lote.txt);
 // O `onchange` cobre a troca; a PRIMEIRA pintura precisa desta
 // chamada, senao o template ja selecionado pede texto e os campos
 // nascem escondidos.
 camposDoLote();
 $('#rodape').textContent=d.build;
}
function _valor(id){const e=document.getElementById(id);return e?e.value:'';}
function _devolve(id,v){
  const e=document.getElementById(id);
  if(e&&v) e.value=v;
}
// Enquanto o dono escreve, o painel espera. Ele volta a atualizar sozinho
// assim que o campo perde o foco e o texto e enviado ou apagado.
function _ocupado(){
  const a=document.activeElement;
  if(a&&['segLote','tplLote','novNome','novTexto'].indexOf(a.id)>=0) return true;
  return !!(_valor('novNome')||_valor('novTexto'));
}
// ---- CONTROLE (M2.9) ----------------------------------------------------
// Toda ação passa pelo mesmo POST autenticado. `recarrega` logo depois pra
// tela nunca mostrar um estado que o servidor já mudou.
function filtro(k){ window.__filtro=k; carrega(); }
async function acao(uid,tipo,extra){
  const body=Object.assign({user_id:uid,acao:tipo},extra||{});
  // CONFIRMAÇÃO NO QUE MEXE EM DINHEIRO OU CORTA ACESSO. O dedo escorrega no
  // celular, e "aprovar anual" errado trava o ciclo da pessoa por 12 meses.
  const grave={aprovar:'Confirmar '+(extra&&extra.plano||'')+' pra este cliente?',
               bloquear:'Bloquear este cliente?'};
  if(grave[tipo] && !confirm(grave[tipo])) return;
  try{
    const r=await fetch('/painel/acao?k='+encodeURIComponent(K),
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify(body)});
    const j=await r.json();
    if(!j.ok){ alert('Não deu: '+(j.erro||'tente de novo')); }
  }catch(e){ alert('Sem conexão com o servidor.'); }
  carrega();
}
function dias(uid){
  const d=prompt('Quantos dias a mais de teste?','7');
  if(!d) return;
  const n=parseInt(d,10);
  if(!n||n<1||n>365){ alert('Informe um número de 1 a 365.'); return; }
  acao(uid,'estender',{dias:n});
}
function mandar(uid){
  const s=document.getElementById('t'+uid);
  if(!s||!s.value) return;
  if(!confirm('Enviar "'+s.value.replace('resolveai_','')+'" pra esta pessoa?'))
    return;
  acao(uid,'enviar_template',{template:s.value});
}
function cobrar(uid){ acao(uid,'reenviar_link',{}); }
// A LISTA DE PODERES. Abre sobre a tela, sem sair do painel — a ideia é
// consultar rápido, não navegar.
function poderes(){
  const d=window.__dados||{};
  const lista=d.poderes||[], grupos=d.grupos_de_poder||[];
  if(!lista.length){ alert('Lista ainda não carregou. Tenta de novo.'); return; }
  let h='';
  grupos.forEach(g=>{
    const doGrupo=lista.filter(p=>p.grupo===g);
    if(!doGrupo.length) return;
    h+=`<h3 style="font-size:11px;letter-spacing:.09em;text-transform:uppercase;
         color:#8296b3;margin:18px 0 9px;font-weight:700">${g}</h3>`;
    doGrupo.forEach(p=>{
      h+=`<div style="padding:9px 0;border-bottom:1px solid #1f2c47">
            <div style="font-weight:600;font-size:14px">${p.titulo}</div>
            <div class="muted" style="font-size:12px;margin-top:3px;
                 line-height:1.45">${p.desc}</div></div>`;
    });
  });
  const cx=document.createElement('div');
  cx.style.cssText='position:fixed;inset:0;z-index:99;background:#0b1220;'
    +'overflow-y:auto;padding:16px 14px calc(28px + env(safe-area-inset-bottom))';
  cx.innerHTML=`<div style="display:flex;justify-content:space-between;
      align-items:center;margin-bottom:6px">
      <h2 style="font-size:17px;margin:0;font-weight:700">O que o Resolve AI faz</h2>
      <button class="b" id="fechaPoderes">fechar</button></div>
    <div class="muted" style="font-size:11px;margin-bottom:4px">
      ${lista.length} recursos no ar.</div>${h}`;
  document.body.appendChild(cx);
  cx.querySelector('#fechaPoderes').onclick=()=>cx.remove();
}
async function resetarTrials(){
  if(!confirm('Devolver 14 dias de teste pra TODOS os clientes, contados de '
    +'hoje?')) return;
  try{
    const r=await fetch('/painel/acao?k='+encodeURIComponent(K),
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({acao:'resetar_trials',confirmo:true})});
    const j=await r.json();
    alert(j.ok ? (j.tocados+' pessoa(s) voltaram a ter 14 dias.'
                  +(j.tocados?'':' (ninguém pra resetar — já foi feito hoje)'))
               : ('Não deu: '+(j.erro||'tente de novo')));
  }catch(e){ alert('Sem conexão com o servidor.'); }
  carrega();
}
// ZERAR É IRREVERSÍVEL: digitar o nome, e não só confirmar. Um "OK" no
// celular é um toque; digitar o nome é uma decisão.
function zerar(uid){
  // O NOME VEM DOS DADOS, não interpolado no onclick. Um cliente chamado
  // D'Ávila fecharia a string do atributo e quebraria a tela INTEIRA — não
  // só o botão dele. Dado de usuário nunca entra em código gerado.
  const u=(window.__users||[]).find(x=>x.id===uid);
  const nome=(u&&u.nome)||'este cliente';
  const r=prompt('Isso APAGA '+nome+' por completo: itens, conversas, tudo. '
    +'Não tem desfazer. Digite o nome pra confirmar:');
  if(!r || r.trim().toLowerCase()!==(nome||'').trim().toLowerCase()){
    if(r!==null) alert('Nome não confere. Nada foi apagado.');
    return;
  }
  acao(uid,'zerar',{confirmo:true});
}
// Os campos de texto do lancamento so aparecem no template que pede.
// Deixar dois campos vazios em toda tela convida a mandar o que nao devia.
function camposDoLote(){
  const tpl=document.getElementById('tplLote'),
        box=document.getElementById('txtLote');
  if(!tpl||!box) return;
  const o=tpl.options[tpl.selectedIndex];
  const pede=((o&&o.getAttribute('data-pede'))||'').length>0;
  box.style.display = pede ? 'block' : 'none';
  // O RECIBO FICA NA TELA, e nao so no alerta que some.
  const av=document.getElementById('jaFoi');
  if(av){
    const n=parseInt((o&&o.getAttribute('data-enviados'))||'0',10)||0;
    const q=(o&&o.getAttribute('data-ultimo'))||'';
    if(n){
      av.textContent='Ja enviado nos ultimos 2 dias para '+n+' pessoa(s)'
        +(q?' — ultimo em '+q.slice(8,10)+'/'+q.slice(5,7)+' '+q.slice(11,16):'')
        +'. Quem ja recebeu sera pulado.';
    } else {
      av.textContent='Ainda nao enviei este aviso nos ultimos 2 dias.';
    }
  }
}
async function lote(){
  const seg=document.getElementById('segLote'),
        tpl=document.getElementById('tplLote');
  if(!seg||!tpl) return;
  const o=tpl.options[tpl.selectedIndex];
  const pede=((o&&o.getAttribute('data-pede'))||'').split(',').filter(Boolean);
  const extras={};
  if(pede.length){
    const nome=(document.getElementById('novNome')||{}).value||'';
    const txt=(document.getElementById('novTexto')||{}).value||'';
    if(!nome.trim()||!txt.trim()){
      alert('Escreve o nome da novidade e o que ela faz antes de enviar.');
      return;
    }
    extras.nome_da_novidade=nome.trim();
    extras.o_que_ela_faz=txt.trim();
  }
  const txtSeg=seg.options[seg.selectedIndex].text;
  const qtd=(txtSeg.match(/[(]([0-9]+)[)]/)||[])[1] || '?';
  // PERGUNTA ANTES QUEM JA RECEBEU. O aviso so serve no instante da
  // decisao: depois do envio a mensagem repetida ja saiu.
  let repetidos=0, nomes=[];
  try{
    const rc=await fetch('/painel/lote?k='+encodeURIComponent(K),
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({segmento:seg.value,template:tpl.value,
                            extras:extras,confirmo:true,conferir:true})});
    const jc=await rc.json();
    if(jc&&jc.ok){ repetidos=jc.repetidos||0; nomes=jc.nomes||[]; }
  }catch(e){}
  let alerta='';
  if(repetidos){
    alerta=' ATENCAO: '+repetidos+' ja receberam isto nos ultimos 2 dias ';
    alerta+='('+nomes.join(', ')+'). Vou PULAR essas pessoas.';
  }
  // A PREVIA VEM ANTES DO OK. Texto livre que vai pra base inteira tem que
  // ser lido uma vez fora do campo em que foi digitado.
  // SEM QUEBRA DE LINHA AQUI, nem escapada. Este JS mora dentro de uma
  // string Python normal: uma barra invertida seguida de n vira quebra de
  // verdade, a string JS fica aberta atravessando a linha e o painel
  // INTEIRO some numa tela branca.
  //
  // E vale pro COMENTARIO tambem — foi assim que o painel caiu: este aviso
  // trazia o escape escrito por extenso pra explicar o perigo, o Python o
  // converteu, o comentario quebrou no meio e a segunda metade da frase
  // virou codigo. Aqui so se descreve o escape com palavras.
  // A previa cabe numa linha so.
  let previa='';
  if(pede.length){
    previa=' — vai dizer: novidade no Resolve AI: ';
    previa+=extras.nome_da_novidade+' / '+extras.o_que_ela_faz;
  }
  if(!confirm('Enviar "'+tpl.options[tpl.selectedIndex].text+'" pra '+qtd
      +' pessoa(s) do grupo "'+seg.value+'"?'+previa+alerta)) return;
  // BOTAO TRAVADO ENQUANTO ENVIA. O lote leva minutos e a tela nao mudava
  // nada — o dono clicou OK tres vezes achando que nao tinha funcionado.
  const bt=document.getElementById('btLote');
  if(bt){ bt.disabled=true; bt.textContent='Enviando, pode levar minutos...'; }
  try{
    const r=await fetch('/painel/lote?k='+encodeURIComponent(K),
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({segmento:seg.value,template:tpl.value,
                            extras:extras,confirmo:true})});
    const j=await r.json();
    if(!j.ok){ alert('Não deu: '+(j.erro||'tente de novo')); }
    else {
      let m='Enviados: '+j.enviados+' de '+j.total+'.';
      if(j.pulados){ m+=' Pulei '+j.pulados+' que ja tinham recebido.'; }
      if(j.aviso){ m=j.aviso; }
      if(j.falharam){
        // Quem NAO recebeu tem que aparecer com o motivo: lote que diz so
        // "enviado" esconde a pessoa que ficou sem, e ela e a que importa.
        m+='\\n\\nNão saiu pra '+j.falharam+':\\n'
          +(j.detalhes||[]).map(f=>'· '+f.nome+': '+f.motivo).join('\\n');
      }
      alert(m);
      // LIMPA DEPOIS DE ENVIAR, e nao so por arrumacao: enquanto houver
      // texto nos campos o painel para de se atualizar (`_ocupado`). Sem
      // isto ele congelaria pra sempre depois do primeiro lote.
      const n=document.getElementById('novNome');
      const t=document.getElementById('novTexto');
      if(n) n.value='';
      if(t) t.value='';
    }
  }catch(e){ alert('Sem conexão com o servidor.'); }
  if(bt){ bt.disabled=false; bt.textContent='Enviar pra lista'; }
  carrega();
}
carrega(); setInterval(()=>{if(!_ocupado())carrega()},20000);
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden && !_ocupado()) carrega();
});
</script></body></html>"""
        return HTMLResponse(html)

    @app.post("/painel/conselho")
    async def painel_conselho(request: Request):
        """O conselheiro do painel. So por botao, e a resposta fica guardada.

        Analise que se regenera junto com o painel (a cada 20s) queimaria
        dinheiro em silencio e ainda mudaria de opiniao a cada leitura. Por
        isso: pedido explicito, resposta datada, e um pedido novo dentro da
        validade devolve o que ja existe em vez de gastar de novo.
        """
        from fastapi.responses import JSONResponse
        import conselho as _c
        if not _painel_autorizado(request):
            return _negado(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        tipo = str(body.get("tipo") or "crescimento")
        if tipo not in _c.CONSELHOS:
            return JSONResponse({"ok": False, "erro": "conselho desconhecido"})

        antigo = _c.guardado(db, tipo)
        if antigo["texto"] and not body.get("forcar"):
            try:
                idade = (tempo.agora()
                         - datetime.fromisoformat(antigo["quando"])
                         ).total_seconds() / 3600.0
            except Exception:
                idade = 999.0
            if idade < _c.VALIDADE_H:
                return JSONResponse({"ok": True, "texto": antigo["texto"],
                                     "quando": antigo["quando"],
                                     "reaproveitado": True})

        # O RETRATO VEM DO MESMO LUGAR QUE A TELA, e nao de consultas
        # proprias: se o painel e o conselheiro lessem fontes diferentes,
        # um diria uma coisa e o outro diria outra sobre o mesmo dia.
        dados = {
            "validacao": _seguro_dict(
                lambda: db.validacao(
                    TRIAL_DAYS,
                    excluir_telefones=[ADMIN_PHONE, MASTER_PHONE])),
            "engajamento": _seguro_dict(
                lambda: db.engajamento(
                    excluir_telefones=[ADMIN_PHONE, MASTER_PHONE])),
            "financeiro": _seguro_dict(lambda: db.financeiro(TRIAL_DAYS)),
            "custo_usuario": _custo_seguro(),
            "metas": _plano_das_metas(),
            "templates": _estado_dos_templates(),
            "envio": _seguro_dict(db.pulso_envio),
            "podcast": _seguro_dict(lambda: db.podcast_farois(7)),
            # O INVENTARIO DE CAPACIDADES vai junto: sem ele o conselheiro
            # opina no escuro e sugere construir o que ja esta pronto.
            "poderes": _poderes_seguro(),
        }
        ok, texto = _c.pedir(tipo, dados,
                             os.environ.get("LLM_MODEL_CONSELHO", "")
                             or getattr(ai_engine, "LLM_MODEL", ""))
        if not ok:
            return JSONResponse({"ok": False, "erro": texto})
        quando = tempo.agora().isoformat(timespec="seconds")
        _c.guardar(db, tipo, texto, quando)
        db.registrar_acao_admin("conselho", por="painel", detalhe=tipo)
        return JSONResponse({"ok": True, "texto": texto, "quando": quando})

    @app.post("/painel/metas")
    async def painel_metas(request: Request):
        """As metas de lucro sao do dono: ele muda pelo painel, sem deploy."""
        from fastapi.responses import JSONResponse
        import json as _j
        if not _painel_autorizado(request):
            return _negado(request)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "erro": "corpo inválido"})
        novas = {}
        for chave in METAS_PADRAO:
            try:
                valor = float(body.get(chave))
            except (TypeError, ValueError):
                return JSONResponse({"ok": False,
                                     "erro": f"meta {chave!r} inválida"})
            # Meta zero ou negativa nao e meta, e o painel dividiria por ela
            # pra achar quantos clientes faltam.
            if valor <= 0:
                return JSONResponse({"ok": False,
                                     "erro": "meta tem que ser maior que zero"})
            novas[chave] = valor
        db.set_setting("metas_lucro", _j.dumps(novas))
        db.registrar_acao_admin("metas", por="painel",
                                detalhe=_j.dumps(novas))
        return JSONResponse({"ok": True, "metas": novas})

    @app.post("/painel/lote")
    async def painel_lote(request: Request):
        """Manda um template aprovado pra um SEGMENTO inteiro (M3.0).

        Pedido do Kevin: "por lista de clientes, por exemplo desengajados, eu
        mando ideias de uso".

        Três cuidados, e nenhum é decoração:

        1. `confirmo` obrigatório. É a única rota do sistema que fala com
           várias pessoas de uma vez; um clique sem querer vira mensagem em
           massa, e é exatamente essa assinatura que a Meta lê como spam.
        2. Espaçamento entre envios, o mesmo do motor. Em 04/08 o número foi
           restringido por RITMO — 4 mensagens num minuto — e não por
           conteúdo. Um lote de 11 disparado de uma vez é pior que isso.
        3. Passa por `_enviar_template_manual`, que já valida catálogo e
           variáveis por pessoa. Quem não tiver dado pra preencher é PULADO
           com motivo, não recebe mensagem quebrada.
        """
        from fastapi.responses import JSONResponse
        if not _painel_autorizado(request):
            return _negado(request)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "erro": "corpo inválido"})
        if not body.get("confirmo"):
            return JSONResponse({"ok": False,
                                 "erro": "confirmação obrigatória pra envio "
                                         "em lote"})
        seg = str(body.get("segmento") or "")
        tpl = str(body.get("template") or "")
        grupos = db.segmentos(excluir_telefones=[ADMIN_PHONE, MASTER_PHONE])
        if seg not in grupos:
            return JSONResponse({"ok": False,
                                 "erro": f"segmento {seg!r} não existe"})
        import templates as _cat
        if tpl not in _cat.CATALOGO:
            return JSONResponse({"ok": False,
                                 "erro": f"template {tpl!r} não existe"})
        # OS CAMPOS LIVRES SAO VALIDADOS ANTES DO PRIMEIRO ENVIO.
        #
        # O lote espaca os disparos por minutos. Se a validacao morasse so
        # dentro do laco, um texto grande demais recusaria pessoa por pessoa
        # com o lote ja em andamento — metade da base recebendo e metade nao,
        # sem ninguem entender por que. Aqui ou o lote inteiro sai, ou nenhum.
        extras = body.get("extras") or {}
        if not isinstance(extras, dict):
            return JSONResponse({"ok": False, "erro": "extras inválidos"})
        extras = {k: v for k, v in extras.items() if k in VARIAVEIS_LIVRES}
        _faltando = [v for v in (_cat.CATALOGO[tpl].variaveis or [])
                     if v in VARIAVEIS_LIVRES
                     and not str(extras.get(v) or "").strip()]
        if _faltando:
            return JSONResponse(
                {"ok": False,
                 "erro": "falta preencher: "
                         + ", ".join(v.replace("_", " ") for v in _faltando)})
        _grande = [v for v, t in extras.items()
                   if len(str(t).strip()) > LIMITE_VARIAVEL_LIVRE]
        if _grande:
            return JSONResponse(
                {"ok": False,
                 "erro": (", ".join(v.replace("_", " ") for v in _grande)
                          + f": máximo {LIMITE_VARIAVEL_LIVRE} caracteres")})

        gente = grupos[seg]

        # MODO CONFERIR: responde quem ja recebeu e NAO manda nada.
        #
        # O aviso tem que chegar no instante em que o dono decide — dentro
        # da confirmacao, antes do OK. Depois do envio nao serve pra nada:
        # a mensagem repetida ja saiu.
        #
        # Fica DEPOIS das validacoes de texto de proposito, pra conferir
        # tambem se os campos estao preenchidos antes de o dono se
        # comprometer com a lista.
        _ja = db.recebeu_nos_ultimos_dias(tpl, int(body.get("dias") or 2))
        _repetidos = [p["nome"] for p in gente if p["id"] in _ja]
        if body.get("conferir"):
            return JSONResponse({"ok": True, "conferindo": True,
                                 "total": len(gente),
                                 "repetidos": len(_repetidos),
                                 "nomes": _repetidos[:8]})

        # A TRAVA E DO SERVIDOR, NAO DA ATENCAO DO DONO.
        #
        # Este lote leva de 2 a 4 minutos e a tela nao dava sinal nenhum
        # enquanto rodava. O dono clicou OK tres vezes achando que nao
        # tinha funcionado — e nada impedia tres lotes de sairem, tres
        # mensagens iguais pra cada pessoa, num numero ja restringido duas
        # vezes pela Meta. Avisar nao basta: quem clica de novo e
        # justamente quem NAO viu o aviso.
        #
        # Por isso pula por padrao, e a repeticao exige `repetir` explicito.
        # Fail-closed: na duvida a pessoa NAO recebe duas vezes.
        _pulados = 0
        if _repetidos and not body.get("repetir"):
            _antes = len(gente)
            gente = [p for p in gente if p["id"] not in _ja]
            _pulados = _antes - len(gente)
            if not gente:
                return JSONResponse(
                    {"ok": True, "enviados": 0, "falharam": 0,
                     "total": 0, "pulados": _pulados, "detalhes": [],
                     "aviso": ("Todo mundo desse grupo já recebeu este "
                               "aviso nos últimos dias. Não mandei de novo.")})

        enviados, falhas = 0, []
        import asyncio
        import random as _rnd
        # `await asyncio.sleep`, NUNCA `time.sleep` — esta linha derrubou o
        # bot em 28/08/2026.
        #
        # Este handler é `async def`. `time.sleep()` aqui não espaça envio
        # nenhum: congela o EVENT LOOP do FastAPI, e com ele o processo
        # inteiro. Durante os ~15 minutos do disparo o bot ficou surdo pra
        # TODA a base — os webhooks chegavam, respondiam 200 e morriam sem
        # processamento. O dono escreveu duas vezes e levou silêncio.
        #
        # `asyncio.sleep` cede o controle: o espaçamento continua idêntico
        # (é ele que evitou a restrição de 04/08) e o bot segue atendendo
        # quem escreve enquanto o lote corre.
        #
        # O envio em si é síncrono (httpx.post, ~1s). Aceitável: bloqueia por
        # 1 segundo, não por 15 minutos. Se um dia o lote passar de dezenas
        # de pessoas, ele vira `asyncio.to_thread`.
        for i, p in enumerate(gente):
            if i:
                await asyncio.sleep(_rnd.uniform(ENVIO_INTERVALO_MIN,
                                                ENVIO_INTERVALO_MAX))
            ok_um, motivo = _enviar_template_manual(p["id"], tpl, extras)
            if ok_um:
                enviados += 1
            else:
                falhas.append({"nome": p["nome"], "motivo": motivo})
        db.registrar_acao_admin(
            "lote", por="painel",
            detalhe=f"{tpl} -> {seg}: {enviados} de {len(gente)}")
        import logging as _lg
        _lg.getLogger("resolveai").info(
            "[lote] %s -> %s: %d enviados, %d falharam",
            tpl, seg, enviados, len(falhas))
        return JSONResponse({"ok": True, "enviados": enviados,
                             "falharam": len(falhas), "total": len(gente),
                             "pulados": _pulados,
                             "detalhes": falhas[:20]})

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
            "metas": _plano_das_metas(),
            "custo_usuario": _custo_seguro(),
            "conselhos": _conselhos_guardados(),
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
                               "texto",
                               boleto.sem_codigo_de_pagamento(reply["text"]))
            except Exception:
                import logging
                logging.getLogger("resolveai").warning(
                    "[painel] falha ao logar resposta", exc_info=True)
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
            # AÇÕES QUE VALEM PRA BASE INTEIRA vêm antes do `user_id`, que
            # elas não têm. Sem isto o handler estourava no `int(None)` e
            # devolvia o erro do Python como se fosse resposta de negócio.
            if body.get("acao") == "resetar_trials":
                # RESET DE TRIAL SEM DEPENDER DE DIGITAÇÃO.
                #
                # O comando por WhatsApp (`resetar trial de todos`) exige a
                # frase exata e falhou em produção em 28/08 — a mensagem não
                # casou o padrão e nada aconteceu, em silêncio. Um botão não
                # tem como errar a frase.
                if not body.get("confirmo"):
                    return JSONResponse({
                        "ok": False,
                        "erro": "confirmação obrigatória pra resetar todos"})
                _dono = re.sub(r"\D", "", ADMIN_PHONE or "")
                _alvos = [u["id"] for u in db.list_users()
                          if re.sub(r"\D", "", u.get("telefone") or "")
                          != _dono]
                _tocados = db.resetar_trial(_alvos, por="painel")
                return JSONResponse({"ok": True, "tocados": len(_tocados)})
            if body.get("acao") == "amostra_podcast":
                # AMOSTRA PELO PAINEL, alem do comando por WhatsApp.
                #
                # O comando (`amostra do podcast`) exige que o dono digite do
                # numero cadastrado como ADMIN_PHONE. Pelo painel ele escolhe
                # PRA QUEM vai — util quando o numero dele nao e o admin, e
                # util pra mandar a amostra pra alguem avaliar junto.
                #
                # Mesma trava do resto do painel: so com o token.
                _alvo = re.sub(r"\D", "", str(body.get("telefone") or ""))
                if not _alvo:
                    return JSONResponse({"ok": False,
                                         "erro": "telefone obrigatorio"})
                _u = db.get_user_by_phone(_alvo)
                if not _u:
                    return JSONResponse(
                        {"ok": False,
                         "erro": "numero nao cadastrado: %s" % _alvo})
                try:
                    _txt = _amostra_de_podcast(
                        _u, _alvo,
                        nicho=str(body.get("nicho") or ""),
                        provedor=str(body.get("provedor") or ""))
                except Exception as e:
                    log.warning("[painel] amostra do podcast falhou",
                                exc_info=True)
                    return JSONResponse({"ok": False, "erro": repr(e)[:200]})
                # O SENTINELA NAO E TEXTO (auditoria M16, P0). Aqui a
                # amostra e mandada pra OUTRA pessoa avaliar junto, entao um
                # "\x00sem-resposta" iria pro telefone de um terceiro.
                if _txt and _txt != SEM_RESPOSTA:
                    send_whatsapp(_alvo, _txt)
                return JSONResponse({"ok": True,
                                     "resumo": "" if _txt == SEM_RESPOSTA
                                     else _txt})
            uid = int(body.get("user_id"))
            acao = body.get("acao")
            ok = False
            if acao == "estender":
                _dias = int(body.get("dias", 7))
                ok = db.admin_extend_trial(uid, _dias)
                if ok:
                    # AVISA A PESSOA DO PRAZO NOVO (M3.0).
                    #
                    # Ganhar dias e não saber é o mesmo que não ganhar: a
                    # pessoa segue achando que o teste acabou e some. Melhor
                    # esforço, e a falha aparece pro dono em `aviso` — os
                    # dias NÃO são desfeitos se a mensagem não sair, senão
                    # ele clicaria de novo e daria o dobro sem perceber.
                    _av = _avisar_trial_estendido(uid, _dias)
                    if _av:
                        return JSONResponse({"ok": True, "aviso": _av})
            elif acao == "aprovar":
                # O DONO CONFERIU NO MERCADO PAGO E CONFIRMOU (M2.9).
                # Plano inválido não vira "ativo sem plano": isso deixaria a
                # pessoa pagante sem ciclo, e ela nunca apareceria como
                # vencida — um cliente que some da cobrança pra sempre.
                try:
                    ok = db.aprovar_pagamento(
                        uid, str(body.get("plano") or ""),
                        em=body.get("em") or None, por="painel")
                except ValueError as e:
                    return JSONResponse({"ok": False, "erro": str(e)})
            elif acao == "reenviar_link":
                # REENVIO DO LINK, na mão, quando o dono viu que não pagou.
                #
                # É texto livre: só sai DENTRO da janela de 24h. Não existe
                # template aprovado pra "você pediu o link e não pagou", e
                # inventar um seria linguagem promocional em template UTILITY
                # — o caminho mais curto pra terceira restrição do número. Se
                # a janela estiver fechada, a recusa aparece na tela com o
                # motivo, e o Kevin manda do WhatsApp dele.
                _u = db.get_user(uid)
                if not _u:
                    return JSONResponse({"ok": False, "erro": "sem usuário"})
                _res = wasender.falar(
                    re.sub(r"\D", "", _u["telefone"] or ""),
                    _handle_commands(_u, _u["telefone"], "assinar") or "",
                    user_id=uid)
                if not _res.get("enviado"):
                    return JSONResponse({
                        "ok": False,
                        "erro": ("fora da janela de 24h — peça pra pessoa "
                                 "mandar qualquer mensagem, ou cobre pelo seu "
                                 "WhatsApp")
                        if _res.get("motivo") == "fora_da_janela_sem_template"
                        else (_res.get("motivo") or "não enviado")})
                db.registrar_acao_admin("reenviar_link", alvo=uid,
                                        por="painel")
                ok = True
            elif acao == "enviar_template":
                # ENVIO MANUAL DE TEMPLATE, pela porta única de sempre.
                #
                # O painel não ganha caminho de envio próprio: passa por
                # `canal.falar`, que exige template aprovado fora da janela.
                # Sem isso o painel viraria o buraco por onde texto livre sai
                # fora da janela — exatamente o que rendeu duas restrições
                # neste número.
                ok, motivo = _enviar_template_manual(
                    uid, str(body.get("template") or ""))
                if not ok:
                    return JSONResponse({"ok": False, "erro": motivo})
            elif acao == "bloquear":
                ok = db.admin_set_status(uid, "bloqueado")
            elif acao == "ativar":
                ok = db.admin_set_status(uid, "ativo")
            elif acao == "liberar":  # desbloqueia -> volta pra trial
                ok = db.admin_set_status(uid, "trial")
            elif acao == "zerar":
                # IRREVERSÍVEL: exige `confirmo` no corpo, não só o clique.
                # O painel é usado no celular; um toque errado aqui apaga a
                # pessoa inteira, e não existe desfazer.
                if not body.get("confirmo"):
                    return JSONResponse({
                        "ok": False,
                        "erro": "confirmação obrigatória pra apagar tudo"})
                ok = db.zerar_cliente(uid, por="painel")
                if not ok:
                    return JSONResponse({"ok": False,
                                         "erro": "usuário não encontrado"})
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
