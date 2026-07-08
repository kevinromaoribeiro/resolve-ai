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
from typing import Any, Optional

import db
import ai_engine
import scheduler

db.init_db()

EVOLUTION_URL = os.environ.get("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
EVOLUTION_APIKEY = os.environ.get("EVOLUTION_APIKEY", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "resolveai")

# Link de pagamento (Kirvano, Mercado Pago Assinaturas, Stripe Payment Link…)
PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "https://SEU-LINK-DE-PAGAMENTO")
PAYMENT_LINK_ANUAL = os.environ.get("PAYMENT_LINK_ANUAL", "")
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))
# Número do dono (só ele pode ativar assinaturas manualmente no MVP)
ADMIN_PHONE = re.sub(r"\D", "", os.environ.get("ADMIN_PHONE", ""))


# ---------------------------------------------------------------------------
# Núcleo TESTÁVEL (sem FastAPI/HTTP): payload Evolution -> resposta
# ---------------------------------------------------------------------------

# Decisões pendentes por telefone (Regra de Ouro da imagem silenciosa)
PENDING: dict[str, dict] = {}
# Confirmações pendentes de comandos destrutivos: phone -> 'cancelar'|'apagar'
CONFIRM: dict[str, str] = {}

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
USE_CASE_EXAMPLES = {
    "contas": "📷 manda o *print do boleto* → eu leio valor e vencimento e te lembro um dia antes",
    "mercado": "🎤 _\"comprei arroz, óleo e café hoje\"_ → registro e aviso quando for hora de repor",
    "carro": "💬 _\"troquei o óleo hoje, 74.200 km\"_ → calculo e aviso da próxima troca",
    "saude": "💬 _\"consulta cardiologista dia 15/08 às 14h\"_ → lembrete um dia antes e no dia",
    "datas": "💬 _\"aniversário da minha mãe é 03/09\"_ → nunca mais passa em branco",
    "encomendas": "💬 _\"encomenda chega até sexta\"_ → eu cobro o prazo por você",
    "pet": "🎤 _\"comprei ração premier hoje\"_ → aviso quando estiver acabando, com reposição em 1 clique",
    "burocracia": "💬 _\"IPVA vence dia 15/01\"_ → lembrete com antecedência, sem multa",
}

WELCOME_MSG = (
    "Oi! Eu sou o *Resolve AI* 🟢 — o assistente que tira da sua cabeça "
    "contas, lembretes, manutenções e compras.\n\n"
    f"Você ganhou *{TRIAL_DAYS} dias grátis* para testar, sem cartão.\n\n"
    "Pra começar: *como você quer ser chamado?*"
)


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
    chosen = keys or ["contas", "mercado"]
    exemplos = "\n".join(f"• {USE_CASE_EXAMPLES[k]}" for k in chosen[:4])
    return (f"Perfeito, {first_name}! Seus *{TRIAL_DAYS} dias grátis* "
            f"começaram agora. ✅\n\n"
            f"*Experimente mandar:*\n{exemplos}\n\n"
            f"A qualquer momento: *assinar* (planos), *cancelar* ou "
            f"*apagar meus dados* (saio da sua vida sem deixar rastro).")


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


def _get_or_create_user(phone: str, push_name: str = "") -> tuple[dict, bool]:
    """Retorna (user, is_new)."""
    for u in db.list_users():
        if re.sub(r"\D", "", u["telefone"]) == phone:
            return u, False
    uid = db.create_user(nome=push_name or f"Usuário {phone[-4:]}",
                         telefone=phone)
    db.update_user_fields(uid, onboarding_step="nome", status="trial")
    return db.get_user(uid), True


def _handle_commands(user: dict, phone: str, text: str) -> Optional[str]:
    """Comandos globais (LGPD, assinatura, admin). Retorna resposta ou None."""
    low = text.strip().lower()
    first_name = user["nome"].split()[0]

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
            return ("Feito. Todos os seus dados foram apagados "
                    "permanentemente — registros, lembretes, tudo. "
                    "Se um dia quiser voltar, é só mandar um oi. 👋")
        return "Ok, não fiz nada. Seguimos normal. 🙂"

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
    if low in ("ajuda", "menu", "comandos"):
        return ("Eu entendo linguagem natural — manda texto, áudio ou foto "
                "do seu jeito. Comandos úteis:\n"
                "*assinar* · *cancelar* · *apagar meus dados* · *ajuda*")

    # --- admin: "ativar 5511999990000" -------------------------------------
    if ADMIN_PHONE and phone == ADMIN_PHONE and low.startswith("ativar"):
        target = re.sub(r"\D", "", text)
        for u in db.list_users():
            if re.sub(r"\D", "", u["telefone"]) == target:
                db.set_status(u["id"], "ativo")
                return f"✅ Assinatura de {u['nome']} ({target}) ativada."
        return f"Número {target} não encontrado."

    return None


def _handle_onboarding(user: dict, text: str) -> Optional[str]:
    """Fluxo conversacional de cadastro. Retorna resposta ou None se concluído."""
    step = user.get("onboarding_step")
    if not step:
        return None
    if step == "nome":
        nome = text.strip().split("\n")[0][:60]
        if len(nome) < 2:
            return "Não peguei — como você quer ser chamado?"
        db.update_user_fields(user["id"], nome=nome,
                              onboarding_step="interesses")
        return _interesses_menu(nome.split()[0])
    if step == "interesses":
        keys = [] if text.strip().lower() in ("pular", "depois") \
            else _parse_interesses(text)
        db.update_user_fields(user["id"], interesses=",".join(keys) or None,
                              onboarding_step=None)
        return _onboarding_done_msg(user["nome"].split()[0], keys)
    return None


def _classify_message(msg: dict) -> tuple[str, str]:
    """
    Mapeia a mensagem da Evolution para (kind, content) do ai_engine.
    kinds: texto | audio | imagem_silenciosa | imagem_com_texto | video | desconhecido
    """
    if "conversation" in msg and msg["conversation"]:
        return "texto", msg["conversation"]
    ext = msg.get("extendedTextMessage") or {}
    if ext.get("text"):
        return "texto", ext["text"]
    if "audioMessage" in msg:
        return "audio", ""          # base64 chega em data.message.base64 (webhook_base64)
    if "imageMessage" in msg:
        caption = (msg["imageMessage"] or {}).get("caption", "") or ""
        return ("imagem_com_texto" if caption.strip() else "imagem_silenciosa"), caption
    if "videoMessage" in msg:
        return "video", ""
    return "desconhecido", ""


def _transcribe_audio(b64: str) -> Optional[str]:
    """Transcreve áudio via OpenAI Whisper, se houver chave. Senão, None."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        import io
        from openai import OpenAI
        audio_bytes = base64.b64decode(b64)
        buf = io.BytesIO(audio_bytes)
        buf.name = "audio.ogg"
        client = OpenAI()
        result = client.audio.transcriptions.create(
            model="whisper-1", file=buf, language="pt"
        )
        return result.text
    except Exception:
        return None


def _read_image(b64: str) -> Optional[str]:
    """Extrai texto da imagem via visão (Anthropic ou OpenAI). Senão, None."""
    prompt = ("Extraia desta imagem, em uma linha: descrição do documento, "
              "valor em R$ e data de vencimento se houver. Responda só o texto.")
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
            return resp.content[0].text
        if os.environ.get("OPENAI_API_KEY"):
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=200,
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url":
                     {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt}]}])
            return resp.choices[0].message.content
    except Exception:
        pass
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
    user, is_new = _get_or_create_user(phone, push_name)
    first_name = user["nome"].split()[0]

    msg = data.get("message") or {}
    kind, content = _classify_message(msg)
    media_b64 = data.get("base64") or msg.get("base64") or ""

    # --- 0. boas-vindas: primeiro contato inicia o onboarding --------------
    if is_new:
        return {"number": phone, "text": WELCOME_MSG}

    # --- 1. comandos globais e onboarding (só em texto) --------------------
    if kind == "texto":
        cmd_reply = _handle_commands(user, phone, content)
        if cmd_reply:
            return {"number": phone, "text": cmd_reply}
        onb_reply = _handle_onboarding(user, content)
        if onb_reply:
            return {"number": phone, "text": onb_reply}

    # --- 2. gates de acesso -------------------------------------------------
    status = user.get("status") or "trial"
    if status == "cancelado":
        return {"number": phone, "text":
                (f"{first_name}, sua assinatura está cancelada. Quer voltar? "
                 f"Mande *assinar* que eu reativo tudo — seus dados estão "
                 f"guardados. 🙂")}
    if status == "trial" and db.trial_days_left(user, TRIAL_DAYS) <= 0:
        return {"number": phone, "text": _payment_msg(first_name)}

    # --- decisão pendente (menu 1/2) tem prioridade -----------------------
    if kind == "texto" and phone in PENDING:
        result = ai_engine.converse(
            user["id"], first_name, "decisao", content,
            pending=PENDING[phone],
        )
        if not result["needs_decision"]:
            PENDING.pop(phone, None)
        else:
            PENDING[phone] = result["pending_payload"]
        return {"number": phone, "text": result["reply"]}

    # --- roteamento por tipo ----------------------------------------------
    if kind == "audio":
        transcript = _transcribe_audio(media_b64) if media_b64 else None
        if transcript is None:
            return {"number": phone, "text":
                    ("Recebi seu áudio! 🎤 Neste beta a transcrição ainda está "
                     "sendo ativada — me manda em texto rapidinho que eu "
                     "resolvo na hora.")}
        kind, content = "audio", transcript

    elif kind in ("imagem_silenciosa", "imagem_com_texto"):
        ocr = _read_image(media_b64) if media_b64 else None
        if ocr is None:
            return {"number": phone, "text":
                    ("Recebi sua imagem! 📷 Neste beta a leitura automática "
                     "está sendo ativada — me diz em uma linha o que é "
                     "(ex.: 'boleto Enel R$ 187,40 vence 20/07') que eu "
                     "registro agora.")}
        instruction = content
        content = ocr
        kind = "imagem_com_texto" if instruction.strip() else "imagem_silenciosa"
        result = ai_engine.converse(
            user["id"], first_name, kind, content, instruction=instruction
        )
        if result["needs_decision"]:
            PENDING[phone] = result["pending_payload"]
        return {"number": phone, "text": result["reply"]}

    elif kind == "desconhecido":
        return {"number": phone, "text":
                "Formato não suportado. Me manda texto, áudio ou foto. 🙂"}

    # Texto e áudio passam pela camada de interpretação (intenção + banco)
    result = ai_engine.converse(user["id"], first_name, kind, content)
    if result["needs_decision"]:
        PENDING[phone] = result["pending_payload"]
    return {"number": phone, "text": result["reply"]}


# ---------------------------------------------------------------------------
# Envio via Evolution API
# ---------------------------------------------------------------------------

def send_whatsapp(number: str, text: str) -> bool:
    """POST /message/sendText/{instance} na Evolution API (v2)."""
    try:
        import httpx
        resp = httpx.post(
            f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}",
            headers={"apikey": EVOLUTION_APIKEY,
                     "Content-Type": "application/json"},
            json={"number": number, "text": text},
            timeout=15,
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def dispatch_proactive() -> int:
    """Roda o motor proativo e envia os disparos reais. Retorna nº enviados."""
    result = scheduler.run_proactive_engine()
    sent = 0
    for d in result["due_dispatches"] + result["churn_dispatches"] + result.get("trial_dispatches", []):
        number = re.sub(r"\D", "", d["telefone"])
        if number and send_whatsapp(number, d["message"]):
            sent += 1
    return sent


# ---------------------------------------------------------------------------
# App FastAPI (camada fina)
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI, Request

    app = FastAPI(title="Resolve AI · WhatsApp Gateway")

    @app.get("/health")
    async def health():
        return {"status": "ok", "instance": EVOLUTION_INSTANCE,
                "llm": "on" if ai_engine.LLM_AVAILABLE else "mock"}

    @app.post("/webhook")
    async def webhook(request: Request):
        payload = await request.json()
        event = payload.get("event", "")
        if event not in ("messages.upsert", "MESSAGES_UPSERT"):
            return {"ignored": event}
        reply = handle_incoming(payload)
        if reply:
            send_whatsapp(reply["number"], reply["text"])
        return {"ok": True}

    @app.post("/cron/proactive")
    async def cron_proactive():
        """Chame 1x/dia (cron-job.org gratuito) para os disparos proativos."""
        return {"sent": dispatch_proactive()}

except ImportError:
    app = None  # permite importar handle_incoming em testes sem fastapi
