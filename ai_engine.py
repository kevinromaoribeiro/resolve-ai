"""
ai_engine.py — Motor de Ingestão AI do RESOLVE AI.

Dois modos de operação:
1. MODO LLM: se houver OPENAI_API_KEY ou ANTHROPIC_API_KEY no .env / ambiente,
   usa gpt-4o-mini ou claude-3-haiku via litellm para extrair entidades em JSON.
2. MODO MOCK (Simulação Inteligente): sem chave de API, usa Regex e regras
   determinísticas. A interface funciona 100% sem travar.

Contrato de saída (dict):
{
    "reply": str,              # mensagem do bot para o chat
    "items": [                 # 0..N itens estruturados p/ salvar no SQLite
        {"tipo": ..., "categoria": ..., "descricao": ...,
         "valor_reais": ..., "data_vencimento": ..., "status": ...,
         "link_afiliado": ...}
    ],
    "needs_decision": bool,    # True quando devolvemos menu numerado (regra de ouro)
    "pending_payload": dict|None,  # item aguardando decisão do usuário (1/2)
    "mode": "llm" | "mock",
}
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Configuração de modo
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Loader minimalista de .env (evita dependência extra)."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_AVAILABLE = bool(OPENAI_KEY or ANTHROPIC_KEY)
LLM_MODEL = "gpt-4o-mini" if OPENAI_KEY else "claude-3-haiku-20240307"

AFFILIATE_TAG = "resolveai-20"

# Palavras que indicam item físico de reposição -> aciona 1-Click Buy
REPLENISHABLE_KEYWORDS = {
    "ração": "racao",
    "racao": "racao",
    "areia": "areia+gato",
    "óleo": "oleo+motor",
    "oleo": "oleo+motor",
    "filtro": "filtro",
    "refil": "refil+filtro",
    "shampoo": "shampoo",
    "fralda": "fralda",
    "café": "cafe",
    "cafe": "cafe",
    "gás": "botijao+gas",
    "gas": "botijao+gas",
}

CATEGORY_KEYWORDS = {
    "Pet": ["ração", "racao", "areia", "petz", "veterinário", "veterinario",
            "vacina", "gato", "cachorro", "pet", "antipulgas"],
    "Veículo": ["óleo", "oleo", "carro", "km", "revisão", "revisao", "pneu",
                "ipva", "licenciamento", "mecânico", "mecanico", "gasolina",
                "combustível", "combustivel", "troca de óleo", "alinhamento"],
    "Contas": ["luz", "energia", "água", "agua", "internet", "boleto",
               "fatura", "conta", "aluguel", "condomínio", "condominio",
               "cartão", "cartao", "iptu", "telefone", "celular"],
    "Alimentação": ["mercado", "supermercado", "feira", "comida", "almoço",
                    "almoco", "jantar", "ifood", "padaria", "café", "cafe",
                    "restaurante", "pizza", "lanche"],
    "Lazer": ["cinema", "show", "viagem", "streaming", "netflix", "spotify",
              "jogo", "playstation", "xbox", "bar", "parque"],
}

MONTHS_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

FILLER_RE = re.compile(
    r"\b(ééé+|eee+|hmm+|hum+|né|ne\b|tipo assim|então,?|entao,?|aí,?|ai,?|"
    r"bom,?|olha só|olha so|deixa eu ver)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Utilitários de extração (Mock)
# ---------------------------------------------------------------------------

def clean_audio_transcript(text: str) -> str:
    """Remove cacoetes de fala de uma transcrição informal."""
    cleaned = FILLER_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ,.")


def extract_money(text: str) -> Optional[float]:
    """Extrai o primeiro valor monetário (R$ 145,00 | 145 reais | 89.90)."""
    patterns = [
        r"r\$\s*([\d.]+,\d{2})",          # R$ 1.234,56
        r"r\$\s*(\d+(?:[.,]\d{1,2})?)",   # R$ 145 / R$ 145,5
        r"(\d+(?:[.,]\d{1,2})?)\s*(?:reais|conto|pila)",
        # "paguei 250 no mercado" — número logo após verbo de pagamento
        r"(?:paguei|gastei|comprei\s+por|custou|foi|deu)\s+(\d+(?:[.,]\d{1,2})?)\b",
        # "50 de luz" / "30 na farmácia" — número + preposição + substantivo,
        # exceto datas ("dia 20 de julho")
        r"(?<!dia\s)\b(\d+(?:[.,]\d{1,2})?)\s+(?:de|do|da|em|no|na)\s+"
        r"(?!janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|"
        r"setembro|outubro|novembro|dezembro)[a-zà-ú]",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = m.group(1)
            if "," in raw and "." in raw:      # 1.234,56
                raw = raw.replace(".", "").replace(",", ".")
            elif "," in raw:                   # 145,00
                raw = raw.replace(",", ".")
            try:
                return round(float(raw), 2)
            except ValueError:
                continue
    return None


def extract_due_date(text: str, ref: Optional[date] = None) -> Optional[str]:
    """Extrai data de vencimento explícita ou implícita. Retorna ISO ou None."""
    ref = ref or date.today()
    low = text.lower()

    # dd/mm/yyyy ou dd/mm
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", low)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = m.group(3)
        year = int(y) + 2000 if y and len(y) == 2 else int(y) if y else ref.year
        try:
            parsed = date(year, mo, d)
            if not y and parsed < ref:  # sem ano e já passou -> próximo ano
                parsed = date(year + 1, mo, d)
            return parsed.isoformat()
        except ValueError:
            pass

    # "dia 20 de julho" / "dia 20"
    m = re.search(r"dia\s+(\d{1,2})(?:\s+de\s+([a-zç]+))?", low)
    if m:
        d = int(m.group(1))
        mo = MONTHS_PT.get(m.group(2) or "", ref.month)
        try:
            parsed = date(ref.year, mo, d)
            if parsed < ref:
                parsed = date(ref.year + (1 if mo == 12 else 0),
                              mo % 12 + 1 if not m.group(2) else mo, d) \
                    if not m.group(2) else date(ref.year + 1, mo, d)
            return parsed.isoformat()
        except ValueError:
            pass

    # Relativos
    if "amanhã" in low or "amanha" in low:
        return (ref + timedelta(days=1)).isoformat()
    if "depois de amanhã" in low or "depois de amanha" in low:
        return (ref + timedelta(days=2)).isoformat()
    m = re.search(r"(?:em|daqui a?)\s+(\d+)\s+dias?", low)
    if m:
        return (ref + timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(?:em|daqui a?)\s+(\d+)\s+semanas?", low)
    if m:
        return (ref + timedelta(weeks=int(m.group(1)))).isoformat()
    if "semana que vem" in low or "próxima semana" in low or "proxima semana" in low:
        return (ref + timedelta(days=7)).isoformat()
    if "mês que vem" in low or "mes que vem" in low:
        return (ref + timedelta(days=30)).isoformat()
    return None


def classify_category(text: str) -> str:
    low = text.lower()
    scores = {cat: sum(1 for kw in kws if kw in low)
              for cat, kws in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else "Outros"


def affiliate_link_for(text: str) -> Optional[str]:
    low = text.lower()
    for kw, query in REPLENISHABLE_KEYWORDS.items():
        if kw in low:
            return (f"https://www.mercadolivre.com.br/jm/search?"
                    f"as_word={query}&tag={AFFILIATE_TAG}")
    return None


def infer_tipo(text: str, has_value: bool, has_date: bool) -> str:
    low = text.lower()
    paid_markers = ["paguei", "comprei", "gastei", "pago"]
    remind_markers = ["lembra", "lembrar", "lembrete", "agendar", "agenda",
                      "vence", "vencimento", "não esquecer", "nao esquecer",
                      "marcar", "avisa", "avise"]
    if any(w in low for w in remind_markers) or (has_date and not any(w in low for w in paid_markers)):
        return "lembrete"
    if any(w in low for w in paid_markers) or has_value:
        return "despesa"
    return "lembrete"


def _fmt_br(iso: Optional[str]) -> str:
    if not iso:
        return "sem data"
    y, m, d = iso.split("-")
    return f"{d}/{m}"


_CMD_PREFIX_RE = re.compile(
    r"^(?:me\s+)?(?:lembra(?:r)?\s+(?:de\s+|da\s+|do\s+)?|lembrete\s+(?:de\s+|da\s+|do\s+)?|"
    r"agenda(?:r)?\s+|anota(?:r)?\s+(?:que\s+)?|não\s+(?:me\s+)?deixa\s+esquecer\s+(?:de\s+|da\s+|do\s+)?|"
    r"nao\s+(?:me\s+)?deixa\s+esquecer\s+(?:de\s+|da\s+|do\s+)?)",
    re.IGNORECASE,
)
_DATE_PHRASE_RE = re.compile(
    r"\s*(?:no\s+|em\s+|para\s+o?\s*)?"
    r"(?:dia\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?"      # dia 10/11[/2026]
    r"|dia\s+\d{1,2}(?:\s+de\s+[a-zç]+)?"          # dia 20 [de julho]
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?"               # 10/11[/2026]
    r"|amanhã|amanha|semana\s+que\s+vem|m[eê]s\s+que\s+vem"
    r"|(?:em|daqui\s+a?)\s+\d+\s+(?:dias?|semanas?))\s*",
    re.IGNORECASE,
)


_MONEY_PHRASE_RE = re.compile(
    r"\s*(?:r\$\s*[\d.,]+|\d+(?:[.,]\d{1,2})?\s*(?:reais|conto|pila))\s*",
    re.IGNORECASE,
)
_DANGLING_RE = re.compile(r"\b(vence(?:ndo)?|vencimento|venc)\b\s*$",
                          re.IGNORECASE)


_PAY_PREFIX_RE = re.compile(
    r"^(?:paguei|gastei|comprei|quitei)\s+(?:r\$\s*)?\d*(?:[.,]\d{1,2})?"
    r"\s*(?:de|do|da|em|no|na)?\s*",
    re.IGNORECASE,
)


def _summarize(text: str, limit: int = 60) -> str:
    """Limpa verbos de comando/pagamento, datas e valores da descrição."""
    t = _CMD_PREFIX_RE.sub("", text.strip())
    t = _PAY_PREFIX_RE.sub("", t)
    t = _DATE_PHRASE_RE.sub(" ", t)
    t = _MONEY_PHRASE_RE.sub(" ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,.-")
    t = _DANGLING_RE.sub("", t).strip(" ,.-")
    if not t:
        t = text.strip().rstrip(".")
    return t if len(t) <= limit else t[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Handlers por tipo de entrada (MOCK)
# ---------------------------------------------------------------------------

def _base_result(mode: str = "mock") -> dict:
    return {"reply": "", "items": [], "needs_decision": False,
            "pending_payload": None, "mode": mode}


def _build_item(text: str) -> dict:
    valor = extract_money(text)
    venc = extract_due_date(text)
    categoria = classify_category(text)
    tipo = infer_tipo(text, valor is not None, venc is not None)
    status = "concluido" if (tipo == "despesa" and re.search(
        r"\b(paguei|pago|comprei|gastei)\b", text, re.IGNORECASE)) else "pendente"
    return {
        "tipo": tipo,
        "categoria": categoria,
        "descricao": _summarize(text),
        "valor_reais": valor,
        "data_vencimento": venc,
        "status": status,
        "link_afiliado": affiliate_link_for(text),
    }


def handle_text(text: str, user_name: str = "Kevin") -> dict:
    res = _base_result()
    item = _build_item(text)
    res["items"].append(item)
    if item["tipo"] == "lembrete":
        res["reply"] = (f"Anotado, {user_name}. Lembrete de "
                        f"{item['descricao'].lower()} programado para "
                        f"{_fmt_br(item['data_vencimento'])}.")
    else:
        valor_txt = (f"R$ {item['valor_reais']:.2f}".replace(".", ",")
                     if item["valor_reais"] else "valor não informado")
        res["reply"] = (f"Registrado, {user_name}. Despesa de {valor_txt} "
                        f"em {item['categoria']}.")
    return res


def handle_audio(transcript: str, user_name: str = "Kevin") -> dict:
    cleaned = clean_audio_transcript(transcript)
    res = handle_text(cleaned, user_name)
    item = res["items"][0]
    res["reply"] += (f"\n\n_Áudio processado e categorizado em "
                     f"{item['tipo'].capitalize()}s/{item['categoria']}._")
    return res


def handle_image_with_text(ocr_content: str, instruction: str,
                           user_name: str = "Kevin") -> dict:
    """Imagem + instrução: extrai dados do OCR e executa o comando."""
    res = _base_result()
    combined = f"{ocr_content}. {instruction}"
    item = _build_item(combined)

    low_instr = instruction.lower()
    if re.search(r"\b(pago|paguei|arquiv)", low_instr):
        item["tipo"], item["status"] = "despesa", "concluido"
    elif re.search(r"\b(agend|lembr|avis)", low_instr):
        item["tipo"], item["status"] = "lembrete", "pendente"

    res["items"].append(item)
    valor_txt = (f"R$ {item['valor_reais']:.2f}".replace(".", ",")
                 if item["valor_reais"] else "sem valor identificado")
    if item["tipo"] == "despesa":
        res["reply"] = (f"Documento lido, {user_name}. {valor_txt} arquivado "
                        f"como despesa paga em {item['categoria']}.")
    else:
        res["reply"] = (f"Documento lido, {user_name}. Lembrete de {valor_txt} "
                        f"agendado para {_fmt_br(item['data_vencimento'])}.")
    return res


def handle_silent_image(ocr_content: str, user_name: str = "Kevin") -> dict:
    """Regra de Ouro: imagem sem contexto -> menu numerado + micro-educação."""
    res = _base_result()
    item = _build_item(ocr_content)
    valor_txt = (f"R$ {item['valor_reais']:.2f}".replace(".", ",")
                 if item["valor_reais"] else "valor não identificado")
    venc_iso = item["data_vencimento"]
    venc_txt = _fmt_br(venc_iso)

    res["needs_decision"] = True
    res["pending_payload"] = item
    res["reply"] = (
        f"Identifiquei um documento financeiro no valor de {valor_txt} "
        f"com vencimento em {venc_txt}. Como procedo?\n\n"
        f"1️⃣ Salvar como **Despesa Paga**.\n"
        f"2️⃣ Agendar **Lembrete de Cobrança** para um dia antes do vencimento.\n\n"
        f"_(💡 Dica: na próxima, mande a foto junto com um áudio de 2 segundos "
        f"dizendo 'pago' ou 'agendar' que eu resolvo direto.)_"
    )
    return res


def resolve_pending_decision(choice: str, pending: dict) -> dict:
    """Processa a resposta 1/2 do menu de decisão."""
    res = _base_result()
    c = choice.strip().lower()
    item = dict(pending)
    if c.startswith("1") or "pag" in c:
        item["tipo"], item["status"] = "despesa", "concluido"
        res["items"].append(item)
        res["reply"] = "Feito. Arquivado como **Despesa Paga**."
    elif c.startswith("2") or "agend" in c or "lembr" in c:
        item["tipo"], item["status"] = "lembrete", "pendente"
        if item.get("data_vencimento"):
            y, m, d = map(int, item["data_vencimento"].split("-"))
            remind = date(y, m, d) - timedelta(days=1)
            item["data_vencimento"] = remind.isoformat()
        res["items"].append(item)
        res["reply"] = (f"Agendado. Lembrete de cobrança em "
                        f"{_fmt_br(item.get('data_vencimento'))}.")
    else:
        res["needs_decision"] = True
        res["pending_payload"] = pending
        res["reply"] = "Não entendi. Responda **1** (despesa paga) ou **2** (agendar lembrete)."
    return res


def handle_video() -> dict:
    res = _base_result()
    res["reply"] = (
        "Para garantir precisão e velocidade máxima, processamos apenas fotos, "
        "áudios e textos rápidos. Envie um print ou áudio resumindo o vídeo "
        "que eu executo na hora."
    )
    return res


# ---------------------------------------------------------------------------
# Camada LLM (opcional)
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """Você é o motor de ingestão do RESOLVE AI, um Life Operating System via WhatsApp.
Analise a mensagem do usuário e responda APENAS com JSON válido, sem markdown, sem preâmbulo, no formato:
{
  "reply": "resposta executiva, concisa, máximo 2 linhas, em pt-BR",
  "items": [{
      "tipo": "lembrete|despesa|documento",
      "categoria": "Alimentação|Pet|Veículo|Contas|Lazer|Outros",
      "descricao": "resumo curto",
      "valor_reais": 0.0 ou null,
      "data_vencimento": "YYYY-MM-DD" ou null,
      "status": "pendente|concluido"
  }],
  "needs_decision": false
}
Além de registrar, interprete INTENÇÕES conversacionais: se a mensagem for
saudação, agradecimento, pergunta sobre capacidades, consulta ("quanto gastei",
"o que tem pendente"), ou baixa de item ("paguei a conta de luz"), responda
naturalmente no campo "reply" e retorne items=[] — o sistema resolve consultas
e baixas em outra camada. Nunca invente dados.
Regras:
- Data de referência (hoje): {today}
- Se a entrada for imagem SEM instrução, retorne needs_decision=true, items vazio, e um reply
  com menu numerado (1 = despesa paga, 2 = lembrete) + dica educacional.
- Despesas já pagas ("paguei", "comprei") -> status "concluido".
- Tom executivo e conciso. Nunca resposta genérica."""


def _call_llm(user_content: str) -> Optional[dict]:
    """Chama o LLM via litellm. Retorna dict ou None em caso de falha."""
    try:
        from litellm import completion  # import tardio: só se instalado
        resp = completion(
            model=LLM_MODEL,
            max_tokens=600,
            messages=[
                {"role": "system",
                 "content": LLM_SYSTEM_PROMPT.replace("{today}", date.today().isoformat())},
                {"role": "user", "content": user_content},
            ],
        )
        raw = resp.choices[0].message.content
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        data = json.loads(raw)
        result = _base_result(mode="llm")
        result["reply"] = data.get("reply", "")
        result["items"] = data.get("items", []) or []
        result["needs_decision"] = bool(data.get("needs_decision", False))
        for it in result["items"]:
            it.setdefault("link_afiliado", affiliate_link_for(it.get("descricao", "")))
            it.setdefault("status", "pendente")
        return result
    except Exception:
        return None  # fallback silencioso para o mock


# ---------------------------------------------------------------------------
# Camada de INTERPRETAÇÃO: intenção -> ação (funciona no Mock, sem LLM)
# ---------------------------------------------------------------------------

INTENT_PATTERNS = [
    ("saudacao", r"^(oi+|ol[aá]|bom dia|boa tarde|boa noite|e a[ií]|eai|opa|hey|salve|fala)\b"),
    ("agradecimento", r"^(obrigad[oa]+|valeu|vlw|show|top|perfeito|massa|boa)\W*$"),
    ("capacidades", r"o que (voc[eê]|vc|tu) faz|como (voc[eê]|vc|isso) funciona|pra que (voc[eê]|vc|isso) serve|me ajuda com o qu|quais.*(comandos|fun[cç][oõ]es)"),
    ("consulta_gastos", r"quanto (j[aá] )?gastei|meus gastos|total gasto|gastei quanto|resumo.*gasto"),
    ("consulta_agenda", r"o que (tem|vence|t[aá] pendente)|meus lembretes|minha agenda|o que (voc[eê]|vc) anotou|pr[oó]ximos (vencimentos|lembretes)|(lista|mostra).*(pendente|lembrete)|o que falta"),
]

CONCLUSAO_RE = re.compile(
    r"^(j[aá]\s+)?(paguei|resolvi|feito|conclu[ií]|quitei)\b", re.IGNORECASE)


def detect_intent(text: str) -> str:
    low = text.strip().lower()
    for intent, pat in INTENT_PATTERNS:
        if re.search(pat, low):
            return intent
    if CONCLUSAO_RE.search(low) and extract_money(low) is None:
        return "conclusao"          # "paguei a conta de luz" (sem valor novo)
    if len(low) <= 3 or re.fullmatch(
            r"[\W_]*(hmm+|hum+|kkk+|rs+|ok|t[aá]|sei l[aá]|\?+)[\W_]*", low):
        return "vago"
    return "registro"


def _match_pending_item(user_id: int, text: str) -> Optional[dict]:
    """Encontra o lembrete pendente que melhor casa com o texto."""
    import db
    stop = {"paguei", "resolvi", "feito", "conclui", "concluí", "quitei",
            "ja", "já", "a", "o", "de", "da", "do", "no", "na", "hoje",
            "agora", "essa", "esse", "aquela", "aquele"}
    words = {w for w in re.findall(r"\w+", text.lower())
             if w not in stop and len(w) > 2}
    best, best_score = None, 0
    for item in db.list_items(user_id, status="pendente"):
        iw = set(re.findall(r"\w+", item["descricao"].lower()))
        score = len(words & iw)
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= 1 else None


def _split_multi(text: str) -> list[str]:
    """'paguei 50 de luz e 30 de água' -> partes, quando há ≥2 valores."""
    raw_parts = [p.strip() for p in re.split(r"\s+e\s+|,(?!\d)\s*", text)]
    parts = [p for p in raw_parts if extract_money(p) is not None]
    if len(parts) < 2:
        return [text]
    # propaga o verbo de pagamento da frase para as partes sem verbo
    verb = re.match(r"^\s*(paguei|gastei|comprei)\b", text, re.IGNORECASE)
    if verb:
        v = verb.group(1)
        parts = [p if re.match(r"^\s*(paguei|gastei|comprei)\b", p,
                               re.IGNORECASE) else f"{v} {p}"
                 for p in parts]
    return parts


def _fmt_valor(v: Optional[float]) -> str:
    return f"R$ {v:.2f}".replace(".", ",") if v is not None else ""


def converse(
    user_id: int,
    user_name: str,
    kind: str,
    content: str,
    instruction: str = "",
    pending: Optional[dict] = None,
) -> dict:
    """
    Ponto de entrada CONVERSACIONAL: interpreta a intenção, consulta e
    atualiza o banco quando preciso, persiste itens e devolve resposta pronta.
    Mesmo contrato de process_input.
    """
    import db

    def _persist(result: dict) -> None:
        for item in result.get("items", []):
            db.add_item(user_id=user_id, tipo=item["tipo"],
                        categoria=item.get("categoria", "Outros"),
                        descricao=(item.get("descricao") or "item")[:120],
                        valor_reais=item.get("valor_reais"),
                        data_vencimento=item.get("data_vencimento"),
                        status=item.get("status", "pendente"),
                        link_afiliado=item.get("link_afiliado"))
        if not result.get("items"):
            db.touch_user(user_id)

    # Mídia e decisões seguem o fluxo original (Regra de Ouro intacta)
    if kind in ("decisao", "video", "imagem_com_texto", "imagem_silenciosa"):
        result = process_input(kind, content, instruction=instruction,
                               user_name=user_name, pending=pending)
        _persist(result)
        return result

    text = clean_audio_transcript(content) if kind == "audio" else content
    intent = detect_intent(text)
    base = _base_result()

    if intent == "saudacao":
        base["reply"] = (f"Oi, {user_name}! 👋 Pode mandar: uma conta pra eu "
                         f"lembrar, um gasto pra registrar, ou pergunte "
                         f"_\"o que tem pendente?\"_. Estou ouvindo.")
    elif intent == "agradecimento":
        base["reply"] = "Tamo junto! 🤝 Qualquer coisa, é só mandar."
    elif intent == "capacidades":
        base["reply"] = (
            "Eu tiro coisas da sua cabeça. Pode mandar do seu jeito:\n"
            "• _\"conta de luz vence dia 20, R$ 187\"_ → eu lembro antes\n"
            "• _\"paguei 250 no mercado\"_ → registro o gasto\n"
            "• _\"paguei a conta de luz\"_ → dou baixa no lembrete\n"
            "• _\"quanto gastei esse mês?\"_ · _\"o que tem pendente?\"_\n"
            "• 📷 foto de boleto ou 🎤 áudio enrolado mesmo\n"
            "Comandos: *assinar* · *cancelar* · *apagar meus dados*")
    elif intent == "consulta_gastos":
        total = db.month_spend(user_id)
        if total <= 0:
            base["reply"] = ("Ainda não registrei gastos seus este mês. "
                             "Manda tipo _\"paguei 250 no mercado\"_ que eu "
                             "começo a somar.")
        else:
            cats = db.spend_by_category(user_id)
            top = "\n".join(f"• {c}: {_fmt_valor(v)}"
                            for c, v in list(cats.items())[:4] if v > 0)
            base["reply"] = (f"📊 Você gastou *{_fmt_valor(total)}* este "
                             f"mês:\n{top}")
    elif intent == "consulta_agenda":
        pendentes = db.list_items(user_id, status="pendente")
        if not pendentes:
            base["reply"] = ("Nada pendente — sua cabeça está oficialmente "
                             "leve. 🧘 Quando surgir algo, manda que eu "
                             "seguro.")
        else:
            linhas = "\n".join(
                f"• {i['descricao']}"
                + (f" — vence {i['data_vencimento'][8:10]}/"
                   f"{i['data_vencimento'][5:7]}"
                   if i["data_vencimento"] else "")
                + (f" ({_fmt_valor(i['valor_reais'])})"
                   if i["valor_reais"] else "")
                for i in pendentes[:8])
            base["reply"] = (f"📋 Você tem *{len(pendentes)} pendente(s)*:\n"
                             f"{linhas}\n\nResolveu algum? Me fala "
                             f"(_\"paguei a conta de luz\"_) que eu dou "
                             f"baixa.")
    elif intent == "conclusao":
        match = _match_pending_item(user_id, text)
        if match:
            db.update_item_status(match["id"], "concluido")
            rest = len(db.list_items(user_id, status="pendente"))
            base["reply"] = (f"Baixa dada! ✅ *{match['descricao']}* "
                             f"concluído. "
                             + (f"Sobraram {rest} pendente(s)."
                                if rest else "Zerou a lista. 🧘"))
        else:
            item = _build_item(text)
            item["tipo"], item["status"] = "despesa", "concluido"
            base["items"].append(item)
            base["reply"] = ("Não achei lembrete pendente disso, então "
                             "registrei como despesa concluída. Quer que "
                             "eu some no mês? Me diz o valor "
                             "(ex.: _\"foi 89 reais\"_).")
    elif intent == "vago":
        base["reply"] = ("Me dá um pouco mais de contexto? 🙂 Exemplos: "
                         "_\"IPVA vence dia 15/01\"_, _\"paguei 250 no "
                         "mercado\"_, _\"o que tem pendente?\"_")
    else:
        # --- registro, com suporte a múltiplos itens numa frase -----------
        parts = _split_multi(text)
        if len(parts) > 1:
            for p in parts:
                base["items"].extend(handle_text(p, user_name)["items"])
            resumo = "\n".join(
                f"• {i['descricao']}"
                + (f" — {_fmt_valor(i['valor_reais'])}"
                   if i["valor_reais"] else "")
                for i in base["items"])
            base["reply"] = f"Registrei {len(base['items'])} itens:\n{resumo}"
        else:
            result = process_input(kind, content, instruction=instruction,
                                   user_name=user_name, pending=pending)
            _persist(result)
            return result

    _persist(base)
    return base


# ---------------------------------------------------------------------------
# API pública do motor
# ---------------------------------------------------------------------------

def process_input(
    kind: str,
    content: str,
    instruction: str = "",
    user_name: str = "Kevin",
    pending: Optional[dict] = None,
) -> dict:
    """
    Ponto de entrada único.
    kind: 'texto' | 'audio' | 'imagem_com_texto' | 'imagem_silenciosa'
          | 'video' | 'decisao'
    """
    if kind == "decisao" and pending:
        return resolve_pending_decision(content, pending)

    if kind == "video":
        return handle_video()

    if LLM_AVAILABLE:
        prompt_map = {
            "texto": f"[TEXTO] {content}",
            "audio": f"[TRANSCRIÇÃO DE ÁUDIO INFORMAL] {content}",
            "imagem_com_texto": f"[OCR DA IMAGEM] {content}\n[INSTRUÇÃO DO USUÁRIO] {instruction}",
            "imagem_silenciosa": f"[OCR DA IMAGEM, SEM INSTRUÇÃO] {content}",
        }
        llm_result = _call_llm(prompt_map.get(kind, content))
        if llm_result:
            if llm_result["needs_decision"]:
                llm_result["pending_payload"] = _build_item(content)
            return llm_result

    # Fallback / modo padrão: Mock inteligente
    handlers = {
        "texto": lambda: handle_text(content, user_name),
        "audio": lambda: handle_audio(content, user_name),
        "imagem_com_texto": lambda: handle_image_with_text(content, instruction, user_name),
        "imagem_silenciosa": lambda: handle_silent_image(content, user_name),
    }
    handler = handlers.get(kind)
    if handler is None:
        res = _base_result()
        res["reply"] = "Formato não reconhecido. Envie texto, áudio ou foto."
        return res
    return handler()
