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
from datetime import datetime, date
import tempo
from typing import Any, Optional

import db
import textos
import ai_engine
import scheduler
import whapi  # camada Whapi.Cloud (substitui envio/webhook/mídia da Evolution)

db.init_db()

EVOLUTION_URL = os.environ.get("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
EVOLUTION_APIKEY = os.environ.get("EVOLUTION_APIKEY", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "resolveai")

# Link de pagamento (Kirvano, Mercado Pago Assinaturas, Stripe Payment Link…)
PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "https://SEU-LINK-DE-PAGAMENTO")
PAYMENT_LINK_ANUAL = os.environ.get("PAYMENT_LINK_ANUAL", "")
TRIAL_DAYS = int(os.environ.get("TRIAL_DAYS", "7"))
# v6: teto de duração de áudio (custo Whisper) e link dos Termos/Privacidade
AUDIO_MAX_SECONDS = int(os.environ.get("AUDIO_MAX_SECONDS", "120"))
# v6.5: teto de envios por ciclo do cron (o resto vai no próximo, sem perda)
DISPATCH_MAX_PER_CYCLE = int(os.environ.get("DISPATCH_MAX_PER_CYCLE", "60"))
TERMS_URL = os.environ.get(
    "TERMS_URL",
    "https://kevinromaoribeiro.github.io/resolveai-site/termos.html")
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
    chosen = keys or ["contas", "mercado", "datas"]
    exemplos = "\n".join(textos.USE_CASE_EXAMPLES[k] for k in chosen[:4])
    return (textos.SUGESTOES_ABERTURA.format(nome=first_name, trial_days=TRIAL_DAYS)
            + exemplos + textos.SUGESTOES_RODAPE)


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
_MASTER_RESET_RE = re.compile(
    r"^(reset|resetar|zerar|/reset|novo teste|reiniciar teste|sou novo)\b",
    re.IGNORECASE)


def _get_or_create_user(phone: str, push_name: str = "") -> tuple[dict, bool]:
    """Retorna (user, is_new)."""
    for u in db.list_users():
        if re.sub(r"\D", "", u["telefone"]) == phone:
            return u, False
    uid = db.create_user(nome=push_name or f"Usuário {phone[-4:]}",
                         telefone=phone)
    db.update_user_fields(uid, onboarding_step="nome", status="trial")
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
    if low in ("privacidade", "termos", "lgpd", "meus dados"):
        return ("🔒 *Privacidade em 4 linhas:*\n"
                "• Suas mensagens, fotos e áudios são processados por IA "
                "(OpenAI, servidores no exterior) só para te atender.\n"
                "• Nunca vendemos nem compartilhamos seus dados.\n"
                "• Eu *lembro* você de pagar — nunca pago, compro ou "
                "transfiro nada.\n"
                "• *apagar meus dados* remove tudo, na hora (LGPD).\n\n"
                f"Termos completos: {TERMS_URL}")
    if low in ("ajuda", "menu", "comandos"):
        return ("Eu entendo linguagem natural — manda texto, áudio ou foto "
                "do seu jeito. Comandos úteis:\n"
                "*assinar* · *cancelar* · *apagar meus dados* · "
                "*privacidade* · *ajuda*")

    # --- admin: "ativar 5511999990000" -------------------------------------
    if ADMIN_PHONE and phone == ADMIN_PHONE and low.startswith("ativar"):
        target = re.sub(r"\D", "", text)
        for u in db.list_users():
            if re.sub(r"\D", "", u["telefone"]) == target:
                db.set_status(u["id"], "ativo")
                return f"✅ Assinatura de {u['nome']} ({target}) ativada."
        return f"Número {target} não encontrado."

    return None


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
_NAO_NOME_PALAVRAS = {"quero", "preciso", "pode", "queria", "gostaria",
                      "me", "sim", "não", "nao", "ok", "legal", "bora",
                      "vamos", "legal", "help", "ajuda", "menu", "start", "começar"}


def _is_not_a_name(text: str) -> bool:
    """True se o texto claramente NÃO é um nome (saudação, pergunta, comando,
    frase longa, ou palavra funcional)."""
    t = text.strip()
    low = t.lower().strip("!?.,;")
    if low in _SAUDACOES or low in _NAO_NOME_PALAVRAS:
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
    kinds: texto | audio | imagem_silenciosa | imagem_com_texto | video |
           figurinha | reacao | desconhecido
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


def _read_image(b64: str) -> Optional[str]:
    """Extrai texto da imagem via visão (Anthropic ou OpenAI). Loga erro real."""
    import logging
    log = logging.getLogger("resolveai")
    prompt = ("Extraia desta imagem, em uma linha: descrição do documento, "
              "valor em R$ e data de vencimento se houver. Responda só o texto.")
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
    first_name = user["nome"].split()[0]

    media_b64 = ""
    # Whapi manda a mídia como LINK (não base64). Baixa e converte.
    if kind in ("audio", "imagem_silenciosa", "imagem_com_texto"):
        link = data.get("_whapi_media_link") or ""
        if link:
            media_b64 = whapi.fetch_media_base64(link)
        else:
            import logging
            logging.getLogger("resolveai").info(
                "[media] %s sem link no payload Whapi", kind)

    # --- 0. boas-vindas: primeiro contato inicia o onboarding --------------
    if is_new:
        return {"number": phone, "text": textos.WELCOME_MSG.format(trial_days=TRIAL_DAYS, terms_url=TERMS_URL)}

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
    if status == "bloqueado":
        return None  # usuário bloqueado pelo admin: ignora em silêncio
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
        # v6: teto de duração — áudio longo custa ~20x um texto no Whisper
        secs = int((msg.get("audioMessage") or {}).get("seconds") or 0)
        if secs > AUDIO_MAX_SECONDS:
            return {"number": phone, "text": textos.AUDIO_LONGO.format(
                audio_max_min=AUDIO_MAX_SECONDS // 60)}
        transcript = _transcribe_audio(media_b64) if media_b64 else None
        if transcript is None:
            return {"number": phone, "text": textos.AUDIO_INDISPONIVEL}
        kind, content = "audio", transcript

    elif kind in ("imagem_silenciosa", "imagem_com_texto"):
        ocr = _read_image(media_b64) if media_b64 else None
        if ocr is None:
            return {"number": phone, "text": textos.IMAGEM_PEDIR_CONTEXTO}
        instruction = content
        content = ocr
        kind = "imagem_com_texto" if instruction.strip() else "imagem_silenciosa"
        result = ai_engine.converse(
            user["id"], first_name, kind, content, instruction=instruction
        )
        if result["needs_decision"]:
            PENDING[phone] = result["pending_payload"]
        return {"number": phone, "text": result["reply"]}

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

    # Texto e áudio passam pela camada de interpretação (intenção + banco)
    result = ai_engine.converse(user["id"], first_name, kind, content)
    if result["needs_decision"]:
        PENDING[phone] = result["pending_payload"]
    return {"number": phone, "text": result["reply"]}


# ---------------------------------------------------------------------------
# Envio via Evolution API
# ---------------------------------------------------------------------------

def send_whatsapp(number: str, text: str) -> bool:
    """Envia texto via Whapi.Cloud (antes era Evolution)."""
    return whapi.send_text(number, text)


def _instance_state() -> str:
    """Consulta o estado da sessão WhatsApp no Whapi ('open' = conectada)."""
    return whapi.instance_state()


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

    # 2 falhas seguidas (~2 min): no Whapi não dá pra "reiniciar" a sessão via
    # API — se caiu, precisa reescanear o QR no painel do Whapi. Só avisa.
    if falhas >= 2:
        resultado["acao"] = "aviso ao admin"
        db.set_setting("wa_falhas_seguidas", "0")
        if ADMIN_PHONE:
            aviso = ("⚠️ *Resolve AI* — a conexão do WhatsApp (Whapi) caiu "
                     f"(estado: {wa}). Reescaneie o QR no painel do Whapi: "
                     "panel.whapi.cloud")
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
    if send_whatsapp(re.sub(r"\D", "", ADMIN_PHONE), msg):
        db.log_dispatch(admin_id, "admin-report")
        return True
    return False


def dispatch_proactive() -> int:
    """Roda o motor proativo, envia e REGISTRA cada disparo (dedup)."""
    import logging
    log = logging.getLogger("resolveai")
    result = scheduler.run_proactive_engine()
    sent = 0
    all_dispatches = (result.get("alarm_dispatches", [])
                      + result.get("overdue_dispatches", [])
                      + result["due_dispatches"]
                      + result["churn_dispatches"]
                      + result.get("trial_dispatches", [])
                      + result.get("guided_dispatches", []))
    n_alarm = len(result.get("alarm_dispatches", []))
    log.info("[cron] motor rodou: %d alarme(s) de hora, %d total pra enviar",
             n_alarm, len(all_dispatches))
    for d in all_dispatches[:DISPATCH_MAX_PER_CYCLE]:
        number = re.sub(r"\D", "", d["telefone"])
        if not number:
            log.warning("[cron] disparo sem número: %s", d.get("message", "")[:40])
            continue
        ok = send_whatsapp(number, d["message"])
        log.info("[cron] envio p/ …%s (%s): %s", number[-4:],
                 d.get("kind", "?"), "OK" if ok else "FALHOU")
        if ok:
            sent += 1
            try:
                db.log_dispatch(d["user_id"], d.get("kind", "outro"),
                                d.get("item_id"))
            except Exception:
                pass  # log falhar não pode derrubar o envio
    return sent


# ---------------------------------------------------------------------------
# App FastAPI (camada fina)
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI, Request

    app = FastAPI(title="Resolve AI · WhatsApp Gateway")

    @app.get("/health")
    async def health():
        """Vigia: 500 só quando a sessão está claramente CAÍDA. Estados
        ambíguos (open/connected/connecting) contam como ok pra não gerar
        falso alarme no monitor."""
        wa = _instance_state()
        conectado = wa in ("open", "connected", "online", "connecting", "unknown")
        body = {"status": "ok" if conectado else "degraded",
                "whatsapp": wa, "instance": EVOLUTION_INSTANCE,
                "llm": "on" if ai_engine.LLM_AVAILABLE else "mock"}
        if wa in ("close", "closed", "disconnected", "removed"):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content=body)
        return body

    @app.post("/webhook")
    async def webhook(request: Request):
        raw = await request.json()
        # Traduz o payload do Whapi para o formato que handle_incoming entende.
        payload = whapi.to_evolution_shape(raw)
        if not payload:
            return {"ignored": True}
        # log da mensagem recebida (para o painel)
        try:
            data = payload.get("data") or {}
            key = data.get("key") or {}
            num = (key.get("remoteJid") or "").split("@")[0] or None
            msgobj = data.get("message", {}) if isinstance(data, dict) else {}
            kind, content = _classify_message(msgobj)
            db.log_message(None, num, "in", kind, content)
        except Exception:
            pass
        reply = handle_incoming(payload)
        if reply:
            send_whatsapp(reply["number"], reply["text"])
            try:
                db.log_message(None, reply["number"], "out", "texto", reply["text"])
            except Exception:
                pass
        return {"ok": True}

    @app.post("/cron/proactive")
    async def cron_proactive():
        """Chame a cada 15 min (cron-job.org gratuito). Dedup garante zero spam;
        alarmes com hora tocam no minuto; o resto respeita 8h-21h."""
        db.registrar_cron_ping()
        sent = dispatch_proactive()
        maybe_admin_report()
        return {"sent": sent}

    @app.post("/watchdog")
    @app.get("/watchdog")
    async def watchdog():
        """Vigia de auto-recuperação. Chame a cada 1-2 min no cron-job.org.
        Se a sessão do WhatsApp travar, reinicia sozinho e avisa o admin."""
        return watchdog_check()

    @app.get("/painel")
    async def painel():
        """Dashboard em tempo real — abra http://SEU-IP:8000/painel no navegador."""
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
async function acao(uid, tipo, extra) {{
  let body = {{user_id: uid, acao: tipo}};
  if (tipo === 'estender') {{
    let d = prompt('Quantos dias extras de trial?', '7');
    if (!d) return;
    body.dias = parseInt(d);
  }}
  const r = await fetch('/painel/acao', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}});
  if (r.ok) {{ location.reload(); }} else {{ alert('Falhou. Tente de novo.'); }}
}}
async function testarMotor() {{
  const r = await fetch('/cron/proactive', {{method:'POST'}});
  const j = await r.json();
  alert('Motor executado! Lembretes disparados agora: ' + (j.sent||0) +
        '\\n\\nSe você tinha um lembrete na hora, ele foi enviado. ' +
        'Recarregando o painel...');
  location.reload();
}}
</script>
</body></html>"""
        return HTMLResponse(html)

    @app.post("/painel/acao")
    async def painel_acao(request: Request):
        """Ações de admin do painel: estender trial, bloquear, ativar, etc."""
        from fastapi.responses import JSONResponse
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
            return JSONResponse({"ok": ok})
        except Exception as e:
            return JSONResponse({"ok": False, "erro": str(e)}, status_code=400)

except ImportError:
    app = None  # permite importar handle_incoming em testes sem fastapi
