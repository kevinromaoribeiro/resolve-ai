"""Harness dos testes da FASE 1.

Regra do projeto: teste que nao EXECUTA o fluxo nao conta. Entao aqui nada
e mock de logica — o que roda e o `handle_incoming` de verdade, contra um
SQLite de verdade. So o transporte (WhatsApp) e o LLM sao interceptados,
porque um cobra dinheiro e o outro nao e deterministico.

Armadilha ja paga em auditoria anterior: estado global (PENDING, CONFIRM,
caches) vazando entre casos faz o teste seguinte passar por engano. Por isso
a fixture `limpo` e autouse e varre TODOS os dicionarios de processo.
"""
import os
import pathlib
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Antes de importar db: o modulo le DB_PATH no import.
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="resolveai_t"),
                                     "teste.db")
os.environ.setdefault("PAINEL_TOKEN", "teste")
os.environ.setdefault("ADMIN_PHONE", "5511900000000")
os.environ.setdefault("MASTER_PHONE", "5511900000001")
os.environ.setdefault("OPENAI_API_KEY", "")

import pytest        # noqa: E402
import db            # noqa: E402
import tempo         # noqa: E402
import wa_bot        # noqa: E402
import wasender      # noqa: E402
import canal         # noqa: E402
import calendario    # noqa: E402
import meta_cloud    # noqa: E402
import motor_v8      # noqa: E402

db.init_db()

TELEFONE = "5511988887777"


@pytest.fixture(autouse=True)
def limpo(monkeypatch):
    """Zera estado de processo e corta qualquer saida pro mundo real."""
    for nome in ("PENDING", "PENDING_EM", "PENDING_ERROS", "BAIXA_ESCOLHA",
                 "CONFIRM", "TRIAL_VENCIDO", "RECONSENTIR",
                 "RECEM_APAGADOS", "CONFERIR_FILA", "AUDIO_ESPERADO",
                 "SILENCIOU_AGORA", "PRE_ACEITE", "KIT_ETAPA",
                 "KIT_JA_OFERECIDO", "KIT_CONVITE", "ULTIMO_AVISO_LGPD",
                 "CANCELADO_AVISO", "PASSADO_AVISADO", "_ALERTAS_ENVIADOS"):
        alvo = getattr(wa_bot, nome, None)
        if isinstance(alvo, dict):
            alvo.clear()
    for nome in ("FALHA_JA_LOGADA",):
        alvo = getattr(wa_bot, nome, None)
        if isinstance(alvo, set):
            alvo.clear()

    # msg_log é estado GLOBAL, então some aqui e não na fixture `usuario` —
    # teste que não pede usuário via linha de mensagem do caso anterior e
    # mede constância de outro teste.
    with db.get_conn() as _c:
        _c.execute("DELETE FROM msg_log")

    enviadas: list[tuple[str, str]] = []

    def _send_text(number, text, *a, **kw):
        enviadas.append((number, text))
        return True

    # ATENCAO: wa_bot faz `import canal as wasender`, e canal.py amarra as
    # funcoes no import (`send_text = _mod.send_text`). Patch no modulo
    # `wasender` NAO chega no wa_bot — e um teste que acha que cortou a rede
    # e nao cortou manda mensagem de verdade. O alvo certo e `canal`.
    for _mod in (canal, wasender, meta_cloud):
        monkeypatch.setattr(_mod, "send_text", _send_text, raising=False)
        monkeypatch.setattr(_mod, "baixar_midia",
                            lambda **kw: "", raising=False)
        monkeypatch.setattr(_mod, "instance_state",
                            lambda: "open", raising=False)
    monkeypatch.setattr(wa_bot, "send_whatsapp",
                        lambda number, text: _send_text(number, text),
                        raising=False)
    # LLM fora do ar por padrao: o teste que quiser resposta do motor
    # sobrescreve `motor_v8.route`. Assim nenhum teste vira chamada paga.
    monkeypatch.setattr(motor_v8, "route",
                        lambda *a, **kw: None, raising=False)
    # NADA A CORTAR AQUI (M2.2, rodada 2): o `calendario` deixou de ter
    # fonte externa. A defesa contra rede em teste virou um teste que
    # instrumenta o `httpx` inteiro e roda os caminhos de produção — vigiar
    # o alvo em vez do dublê. Ver test_nenhum_caminho_de_producao_vai_na_rede.
    assert not hasattr(calendario, "_buscar_feriados_online"), (
        "voltou fonte externa ao calendario: reative o corte de rede aqui")
    yield enviadas


@pytest.fixture
def usuario():
    """Usuario ja dentro do produto: cadastro fechado e LGPD aceita."""
    u = db.get_user_by_phone(TELEFONE)
    if not u:
        uid = db.create_user(nome="Kevin", telefone=TELEFONE)
    else:
        uid = u["id"]
        with db.get_conn() as conn:
            conn.execute("DELETE FROM items WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM dispatches WHERE user_id=?", (uid,))
            # (msg_log é limpo na fixture `limpo`, que é autouse — ele é
            # estado global e vaza pra teste que nem pede usuário.)
    db.update_user_fields(uid, onboarding_step=None, status="trial",
                          lgpd_aceite_em=tempo.agora().isoformat())
    return db.get_user(uid)


def texto(msg: str, telefone: str = TELEFONE) -> dict:
    """Webhook de mensagem de texto, no formato que o wa_bot recebe."""
    return {"data": {"key": {"remoteJid": f"{telefone}@s.whatsapp.net",
                             "fromMe": False, "id": f"MSG{abs(hash(msg))}"},
                     "pushName": "Kevin",
                     "message": {"conversation": msg}}}


def responder(msg: str, telefone: str = TELEFONE) -> str:
    """Manda a mensagem e devolve o texto que o bot responderia ('' se nada)."""
    out = wa_bot.handle_incoming(texto(msg, telefone))
    return (out or {}).get("text", "") or ""
