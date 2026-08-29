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
    #
    # `admin_acoes` (M2.5) entrou pelo mesmo motivo: e tabela que so cresce,
    # e um teste que conta "quantos resets aconteceram" mediria os resets
    # dos arquivos anteriores. A regra do CLAUDE.md manda medir DELTA; aqui
    # dá pra fazer melhor e zerar, porque nenhum teste legitimo herda isso.
    #
    # `dispatches` entrou pelo mesmo raciocinio (M2.5). O dedup do relatorio
    # do dono e gravado com `user_id=0` — o dono nem sempre tem linha em
    # `users` —, entao ele NAO era limpo pela fixture `usuario`, que so apaga
    # os disparos do usuario dela. Efeito: o primeiro teste que rodava o
    # relatorio passava e todos os seguintes recebiam string vazia, como se
    # a funcao estivesse quebrada.
    with db.get_conn() as _c:
        _c.execute("DELETE FROM msg_log")
        _c.execute("DELETE FROM admin_acoes")
        _c.execute("DELETE FROM dispatches")

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
    # `trial_base=None` (M2.5): o relogio do trial e alterado pelo reset
    # administrativo, e a linha de `users` sobrevive entre os testes. Sem
    # zerar aqui, um teste que reseta o trial deixa o proximo comecando com
    # 14 dias de credito — e o proximo mede o reset do anterior.
    # `dia_resumo` DE VOLTA AO DEFAULT (M2.5, rodada 2): ele decide em que
    # dia da semana saem os DOIS resumos, e a linha de `users` sobrevive
    # entre os arquivos. Um teste que troca o dia pra provar a regra deixava
    # todos os seguintes medindo o dia errado — e o sintoma era o motor
    # proativo "nao disparar", que parece defeito de producao.
    # `plano`/`pago_em` DE VOLTA A NADA (M2.9): a linha de `users` sobrevive
    # entre os arquivos, e um teste que aprova pagamento deixava o proximo
    # comecando como assinante ativo — inclusive os que medem "quem NAO e
    # pagante". E o quinto campo com esse mesmo sintoma; o padrao aqui e:
    # tudo que uma acao administrativa escreve, esta fixture zera.
    db.update_user_fields(uid, onboarding_step=None, status="trial",
                          trial_base=None, placa_final=None,
                          plano=None, pago_em=None,
                          dia_resumo="Segunda-feira",
                          lgpd_aceite_em=tempo.agora().isoformat())
    # `data_criacao` DE VOLTA PRA AGORA. A linha de `users` sobrevive entre
    # os arquivos, e um teste que envelhece o usuario pra simular fim de
    # trial deixava todos os seguintes com `user_can_receive` False — o
    # motor proativo inteiro parava de disparar, em testes que nao falam de
    # trial nenhum. A fixture promete "usuario dentro do produto"; usuario
    # com trial vencido nao e isso.
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET data_criacao=? WHERE id=?",
                     (tempo.agora().strftime("%Y-%m-%d %H:%M:%S"), uid))
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


@pytest.fixture
def horario_util(monkeypatch):
    """Congela o relogio num horario em que o motor PODE falar.

    `run_proactive_engine` silencia das 21h as 8h (`_in_quiet_hours`), entao
    qualquer teste que passe por `dispatch_proactive` sem congelar a hora
    PASSA DE DIA E FALHA A NOITE. Descoberto as 21h31 de 28/08/2026, com
    sete testes vermelhos de uma vez e nenhuma mudanca de codigo que os
    explicasse — o tipo de falha que faz a gente procurar bug onde nao tem.

    Terca, 10h: fora do quiet hours e fora do `dia_resumo` default (segunda),
    pra nao ligar o digest semanal sem querer.
    """
    import datetime as _dt
    agora = _dt.datetime(2026, 8, 18, 10, 0, 0)
    assert agora.weekday() == 1, "18/08/2026 tem que ser terca"
    monkeypatch.setattr(tempo, "agora", lambda: agora)
    monkeypatch.setattr(tempo, "hoje", lambda: agora.date())
    return agora
