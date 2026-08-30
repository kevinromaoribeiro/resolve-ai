# -*- coding: utf-8 -*-
"""A reativacao dos testers (M5.9): ordem do dono, cumprida uma vez.

O `KIND_TEMPLATE` diz que `reativacao` NAO tem checagem automatica de
proposito — "automatizar isso seria o bot decidindo sozinho reabrir janela
com a base inteira". A regra continua valendo. O que existe aqui e a ordem
explicita do Kevin em 30/08/2026 ("reative todos com o trial de 14 dias e
mande a novidade dos audios... resolva isso sem mim"), cumprida uma vez e
esgotada — nao uma politica nova.

Este arquivo mede as quatro coisas que fazem a diferenca entre ordem e
politica: sai UMA vez por pessoa, nao ressuscita quem saiu, respeita o
ritmo do freio, e pode ser cancelada sem deploy.
"""
import datetime as _dt

import pytest

import db
import scheduler
import templates as _cat
import tempo
import wa_bot
from conftest import TELEFONE, responder


@pytest.fixture
def ligada(monkeypatch):
    monkeypatch.setattr(scheduler, "REATIVAR_TESTERS", True)
    monkeypatch.setattr(scheduler, "ADMIN_PHONE", "5511999990000")
    return True


@pytest.fixture
def extras():
    """Testers de apoio, APAGADOS no fim.

    A linha de `users` sobrevive entre os arquivos (e o que a fixture
    `usuario` do conftest documenta em cinco comentarios). Sem apagar, os
    oito testers de um teste viravam base do seguinte — e do resto da suite.
    """
    criados: list[int] = []

    def criar(nome, telefone, status="trial"):
        u = db.get_user_by_phone(telefone)
        uid = u["id"] if u else db.create_user(nome=nome, telefone=telefone)
        db.update_user_fields(uid, status=status, trial_base=None)
        criados.append(uid)
        return uid

    yield criar
    for uid in criados:
        try:
            db.delete_user(uid)
        except Exception:
            pass


def _ate_achar(user_id, ciclos=40):
    """O disparo desta pessoa, ou None.

    A fila anda 2 por ciclo e a suite inteira deixa usuarios de outros
    arquivos em trial. Sem varrer os ciclos, estes testes mediriam a POSICAO
    da pessoa na fila em vez de medir se ela entra nela.
    """
    vistos = set()
    for _ in range(ciclos):
        d = scheduler.check_reativacao()
        if not d:
            return None
        for x in d:
            if x["user_id"] == user_id:
                return x
            if x["user_id"] in vistos:
                return None          # a fila parou de andar
            vistos.add(x["user_id"])
            db.log_dispatch(x["user_id"], "reativacao")
    return None


# ---------------------------------------------------------------------------
# 1. a ordem sai
# ---------------------------------------------------------------------------

def test_quem_esta_em_trial_entra_na_fila(usuario, ligada):
    d = _ate_achar(usuario["id"])
    assert d, "quem esta em trial ficou de fora da fila"
    assert d["kind"] == "reativacao"


def test_o_disparo_tem_texto(usuario, ligada):
    """`message` vazia e tratada pelo `dispatch_proactive` como registro de
    dedup de grupo e NAO SAI. O envio falharia em silencio."""
    d = scheduler.check_reativacao()[0]
    assert d["message"].strip(), "disparo sem texto nao sai"
    assert "quero o áudio" in d["message"].lower()


def test_o_template_aprovado_monta_as_variaveis(usuario, ligada):
    """Sem ramo no `para_disparo`, o proprio catalogo recusa o template e a
    mensagem nunca sai fora da janela — fail-closed da casa."""
    d = scheduler.check_reativacao()[0]
    tpl, variaveis = _cat.para_disparo(d)
    assert tpl == "reativar_boas_vindas"
    assert variaveis and all(str(v).strip() for v in variaveis)


def test_o_trial_volta_antes_de_a_mensagem_sair(usuario, ligada):
    """O template diz "seus 14 dias gratis estao intactos, valendo a partir
    de agora". Mandar antes de resetar tornaria a mensagem mentira."""
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=40)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    assert db.trial_days_left(db.get_user(usuario["id"])) == 0

    scheduler.check_reativacao()
    assert db.trial_days_left(db.get_user(usuario["id"])) >= 13


# ---------------------------------------------------------------------------
# 2. UMA vez na vida — o numero ja foi restringido duas vezes
# ---------------------------------------------------------------------------

def test_quem_ja_recebeu_nunca_mais_entra(usuario, ligada):
    assert _ate_achar(usuario["id"])
    db.log_dispatch(usuario["id"], "reativacao")
    assert _ate_achar(usuario["id"]) is None


def test_nao_e_uma_vez_por_dia_e_sim_uma_vez_na_vida(usuario, ligada,
                                                     monkeypatch):
    db.log_dispatch(usuario["id"], "reativacao")
    depois = tempo.agora() + _dt.timedelta(days=9)
    monkeypatch.setattr(tempo, "agora", lambda a=depois: a)
    monkeypatch.setattr(tempo, "hoje", lambda a=depois: a.date())
    assert _ate_achar(usuario["id"]) is None


def test_o_carimbo_e_do_envio_e_nao_da_checagem(usuario, ligada):
    """A primeira versao gravava em `admin_acoes` na propria checagem: com o
    freio adiando o envio, a pessoa ficava marcada e nunca recebia nada.
    Rodar a checagem dez vezes sem enviar nao pode consumir a chance dela."""
    for _ in range(10):
        assert _ate_achar(usuario["id"]), "a checagem se auto-consumiu"


# ---------------------------------------------------------------------------
# 3. nao ressuscita ninguem
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["cancelado", "bloqueado", "ativo"])
def test_quem_saiu_ou_paga_fica_de_fora(usuario, ligada, status):
    db.update_user_fields(usuario["id"], status=status)
    assert _ate_achar(usuario["id"]) is None


def test_o_dono_nao_recebe_a_propria_ordem(usuario, ligada, monkeypatch):
    monkeypatch.setattr(scheduler, "ADMIN_PHONE", TELEFONE)
    assert _ate_achar(usuario["id"]) is None


def test_cancelado_nao_tem_o_trial_devolvido(usuario, ligada):
    """`resetar_trial` recusa quem saiu — reabrir o trial de quem pediu pra
    sair e voltar a mandar mensagem pra quem pediu silencio."""
    db.update_user_fields(usuario["id"], status="cancelado")
    with db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=40)
                    ).strftime("%Y-%m-%d %H:%M:%S"), usuario["id"]))
    scheduler.check_reativacao()
    assert db.trial_days_left(db.get_user(usuario["id"])) == 0


# ---------------------------------------------------------------------------
# 4. no ritmo do freio, e desligavel sem deploy
# ---------------------------------------------------------------------------

def test_nao_manda_a_base_inteira_de_uma_vez(usuario, ligada, extras):
    """Onze templates de uma vez num numero morno e o padrao que a Meta
    pune — e este numero ja foi restringido duas vezes."""
    for i in range(8):
        extras("Tester%d" % i, "551190000%04d" % i)
    d = scheduler.check_reativacao()
    assert len(d) <= scheduler.REATIVACAO_POR_CICLO, len(d)


def test_a_fila_anda_conforme_os_envios_saem(usuario, ligada, extras):
    """Quem ja recebeu sai da fila e os proximos entram — a base inteira e
    coberta em ciclos, sem rajada e sem ninguem duas vezes."""
    ids = {usuario["id"]}
    for i in range(4):
        ids.add(extras("Tester%d" % i, "551190000%04d" % i))

    alcancados: list[int] = []
    for _ in range(30):
        d = scheduler.check_reativacao()
        if not d:
            break
        for x in d:
            alcancados.append(x["user_id"])
            db.log_dispatch(x["user_id"], "reativacao")

    assert ids <= set(alcancados), ids - set(alcancados)
    assert len(alcancados) == len(set(alcancados)), "alguem entrou duas vezes"
    assert scheduler.check_reativacao() == [], "a fila nao esvaziou"


def test_desligar_a_chave_cancela_a_ordem(usuario, monkeypatch):
    monkeypatch.setattr(scheduler, "REATIVAR_TESTERS", False)
    assert scheduler.check_reativacao() == []
    assert _ate_achar(usuario["id"]) is None


def test_valor_estranho_na_chave_desliga_em_vez_de_ligar(monkeypatch):
    import importlib
    for valor, esperado in (("", True), ("sim", True), ("0", False),
                            ("nao", False), ("desligado", False)):
        monkeypatch.setenv("REATIVAR_TESTERS", valor)
        assert importlib.reload(scheduler).REATIVAR_TESTERS is esperado, valor
    monkeypatch.delenv("REATIVAR_TESTERS", raising=False)
    importlib.reload(scheduler)


# ---------------------------------------------------------------------------
# 5. o motor nao pode quebrar por causa disto
# ---------------------------------------------------------------------------

def test_o_kind_esta_declarado():
    """A casa exige que todo kind emitido esteja declarado num lugar so."""
    assert "reativacao" in scheduler.KINDS_PROATIVOS


def test_a_madrugada_nao_derruba_o_motor(usuario, ligada, monkeypatch):
    """`podcast_conv` atribuido so no `else` ja derrubou o motor inteiro das
    21h as 8h, alarmes com hora inclusos (auditoria M4.4)."""
    madrugada = _dt.datetime(2026, 9, 1, 3, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda a=madrugada: a)
    monkeypatch.setattr(tempo, "hoje", lambda a=madrugada: a.date())
    out = scheduler.run_proactive_engine()
    assert out["reativacao_dispatches"] == []
    assert isinstance(out["total"], int)


def test_banco_fora_nao_derruba_a_checagem(usuario, ligada, monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "resetar_trial", explode)
    assert scheduler.check_reativacao() == []


def test_a_reativacao_entra_no_total_do_motor(usuario, ligada, monkeypatch):
    meio_dia = _dt.datetime(2026, 9, 1, 12, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda a=meio_dia: a)
    monkeypatch.setattr(tempo, "hoje", lambda a=meio_dia: a.date())
    out = scheduler.run_proactive_engine()
    assert out["reativacao_dispatches"], out
    assert out["total"] >= len(out["reativacao_dispatches"])


# ---------------------------------------------------------------------------
# 6. e a novidade do audio chega de verdade
# ---------------------------------------------------------------------------

def test_quem_responde_o_texto_consegue_assinar_o_audio(usuario, ligada,
                                                        monkeypatch):
    import voz
    monkeypatch.setattr(voz, "disponivel", lambda: True)
    monkeypatch.setattr(scheduler, "PODCAST_ATIVO", True)
    d = scheduler.check_reativacao()[0]
    assert "quero o áudio" in d["message"].lower()

    r = responder("quero o áudio")
    assert "futebol" in r.lower(), r
    responder("games")
    assert db.get_user(usuario["id"])["podcast_nicho"] == "games"


# ---------------------------------------------------------------------------
# 7. as guardas que o patch reverso pegou sem teste (M6.0)
# ---------------------------------------------------------------------------

def test_a_ordem_se_esgota_e_para_de_varrer_a_base(usuario, ligada,
                                                   monkeypatch):
    """`resetar_trial` rodava a cada 60s PARA SEMPRE, muito depois de o
    ultimo tester ja ter recebido. Idempotente nao e o mesmo que terminado —
    e ordem que nunca termina virou a politica que o `KIND_TEMPLATE` proibe.
    """
    db.log_dispatch(usuario["id"], "reativacao")
    for u in db.list_users():
        db.log_dispatch(u["id"], "reativacao")

    tocou = []
    monkeypatch.setattr(db, "resetar_trial",
                        lambda *a, **k: tocou.append(1) or [])
    assert scheduler.check_reativacao() == []
    assert not tocou, "varreu a base com a ordem ja cumprida"


def test_a_cortesia_espera_um_dia_depois_de_outra_proativa(usuario, ligada):
    """Reativacao e pra quem ESFRIOU. Quem recebeu lembrete hoje nao
    esfriou — e duas vibracoes do mesmo numero e o que a Meta pune."""
    assert _ate_achar(usuario["id"], ciclos=1) or True   # so pra aquecer
    db.log_dispatch(usuario["id"], "vencimento")
    assert _ate_achar(usuario["id"]) is None


def test_o_convite_nunca_toma_a_vaga_do_lembrete(usuario, ligada,
                                                 monkeypatch):
    """O teto e de `DISPATCH_MAX_PER_CYCLE` por ciclo. Se o convite passa na
    frente do "sua conta vence amanha", o produto deixou de cumprir o que
    vendeu pra caber uma cortesia."""
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(kw.get("template") or "livre")
        or {"enviado": True, "via": "t", "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    monkeypatch.setattr(wa_bot, "DISPATCH_MAX_PER_CYCLE", 1)

    outro = db.create_user(nome="Fria", telefone="5511977770000")
    db.update_user_fields(outro, status="trial", trial_base=None)
    try:
        d_lembrete = {"user_id": usuario["id"], "user_nome": "Kevin",
                      "telefone": TELEFONE, "item_id": None,
                      "kind": "vencimento", "message": "sua conta vence",
                      "quando": "31/08"}
        d_convite = {"user_id": outro, "user_nome": "Fria",
                     "telefone": "5511977770000", "item_id": None,
                     "kind": "reativacao", "message": "oi, volta",
                     "quando": "31/08"}
        monkeypatch.setattr(
            scheduler, "run_proactive_engine",
            lambda **k: {"executed_at": "x",
                         "reativacao_dispatches": [d_convite],
                         "due_dispatches": [d_lembrete], "total": 2})
        db.log_message(None, TELEFONE, "in", "texto", "oi")
        wa_bot.dispatch_proactive()
        assert enviados and "reativar_boas_vindas" not in enviados, enviados
    finally:
        db.delete_user(outro)


def test_o_convite_vai_depois_das_chaves_nao_ordenadas(usuario, ligada,
                                                       monkeypatch):
    """A cauda explicita.

    `due`/`overdue`/`alarm` ja estao na CABECA da ordem, entao o convite
    perderia pra eles de qualquer jeito. O que a cauda muda e a disputa com
    as chaves NAO ordenadas, que entram por ordem alfabetica: sem ela,
    `reativacao_dispatches` passa na frente de `retorno_dispatches` — e
    "seu retorno do dentista e amanha" e produto, o convite e cortesia.
    """
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(kw.get("template") or "livre")
        or {"enviado": True, "via": "t", "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    monkeypatch.setattr(wa_bot, "DISPATCH_MAX_PER_CYCLE", 1)

    fria = db.create_user(nome="Fria", telefone="5511977770001")
    db.update_user_fields(fria, status="trial", trial_base=None)
    try:
        monkeypatch.setattr(
            scheduler, "run_proactive_engine",
            lambda **k: {
                "executed_at": "x", "total": 2,
                "reativacao_dispatches": [
                    {"user_id": fria, "user_nome": "Fria",
                     "telefone": "5511977770001", "item_id": None,
                     "kind": "reativacao", "message": "oi, volta",
                     "quando": "31/08"}],
                "retorno_dispatches": [
                    {"user_id": usuario["id"], "user_nome": "Kevin",
                     "telefone": TELEFONE, "item_id": None,
                     "kind": "retorno", "message": "seu retorno é amanhã",
                     "quando": "31/08"}]})
        db.log_message(None, TELEFONE, "in", "texto", "oi")
        wa_bot.dispatch_proactive()
        assert enviados and "reativar_boas_vindas" not in enviados, enviados
    finally:
        db.delete_user(fria)


# ---------------------------------------------------------------------------
# 8. o diagnostico (M6.1)
# ---------------------------------------------------------------------------

def test_o_diagnostico_conta_todo_mundo_uma_vez_so(usuario, ligada, extras):
    for i in range(3):
        extras("T%d" % i, "551190000%04d" % i)
    d = scheduler.reativacao_diagnostico()
    partes = ("dono", "fora_do_trial", "ja_receberam", "sem_acesso",
              "adiados_hoje", "na_fila")
    assert sum(d[k] for k in partes) == d["total"], d


def test_o_diagnostico_explica_a_fila_vazia(usuario, ligada):
    assert scheduler.reativacao_diagnostico()["na_fila"] >= 1
    db.log_dispatch(usuario["id"], "reativacao")
    d = scheduler.reativacao_diagnostico()
    assert d["ja_receberam"] >= 1


def test_o_diagnostico_nao_vaza_dado_pessoal(usuario, ligada):
    """O `/health` e publico. Contagem e diagnostico; nome e telefone
    seriam vazamento."""
    d = scheduler.reativacao_diagnostico()
    for chave, valor in d.items():
        assert isinstance(valor, (int, bool)), (chave, valor)


def test_o_diagnostico_nao_derruba_o_health(usuario, ligada, monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "list_users", explode)
    d = scheduler.reativacao_diagnostico()
    assert d.get("erro") is True


def test_disparo_que_nao_vai_sair_nao_toma_a_vez_da_reativacao(usuario,
                                                               ligada,
                                                               monkeypatch):
    """O BUG QUE CUSTOU UM DIA DE ENVIO (M6.2).

    `_servidos` marcava a pessoa como atendida ANTES de o `_tem_como_sair`
    descartar o disparo que a atenderia. Como os testers estao TODOS fora da
    janela de 24h — e por isso que a reativacao existe —, todo ciclo a fila
    inteira era zerada por mensagens que nunca sairiam. Em silencio: o
    diagnostico dizia "10 na fila" e o contador de envios nao mexia.
    """
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(kw.get("template") or "livre")
        or {"enviado": True, "via": "t", "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)

    # a pessoa esta FORA da janela: nenhuma entrada dela nas ultimas 24h
    with db.get_conn() as c:
        c.execute("DELETE FROM msg_log")

    # um disparo de kind SEM template — ele nao tem como sair fora da janela
    sem_template = sorted(_cat.KINDS_SEM_TEMPLATE)[0]
    d_fantasma = {"user_id": usuario["id"], "user_nome": "Kevin",
                  "telefone": TELEFONE, "item_id": None,
                  "kind": sem_template, "message": "isso nao vai sair",
                  "quando": "31/08"}
    d_convite = {"user_id": usuario["id"], "user_nome": "Kevin",
                 "telefone": TELEFONE, "item_id": None,
                 "kind": "reativacao", "message": "oi, volta",
                 "quando": "31/08"}
    monkeypatch.setattr(
        scheduler, "run_proactive_engine",
        lambda **k: {"executed_at": "x", "total": 2,
                     "churn_dispatches": [d_fantasma],
                     "reativacao_dispatches": [d_convite]})

    wa_bot.dispatch_proactive()
    assert "reativar_boas_vindas" in enviados, (
        "o disparo descartado tomou a vez da reativacao: %s" % enviados)


def test_a_recusa_do_envio_aparece_no_diagnostico(usuario, ligada,
                                                  monkeypatch):
    """Sem isto, a unica saida era adivinhar — e eu adivinhei errado duas
    vezes seguidas em cima de mensagem que vai pra gente de verdade."""
    wa_bot.ULTIMA_RECUSA_REATIVACAO.clear()
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: {"enviado": False, "via": "",
                                "motivo": "template_nao_aprovado"})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    d = {"user_id": usuario["id"], "user_nome": "Kevin",
         "telefone": TELEFONE, "item_id": None, "kind": "reativacao",
         "message": "oi, volta", "quando": "31/08"}
    monkeypatch.setattr(scheduler, "run_proactive_engine",
                        lambda **k: {"executed_at": "x", "total": 1,
                                     "reativacao_dispatches": [d]})
    wa_bot.dispatch_proactive()
    assert wa_bot.ULTIMA_RECUSA_REATIVACAO.get("motivo") == \
        "template_nao_aprovado", wa_bot.ULTIMA_RECUSA_REATIVACAO


def test_a_recusa_registrada_nao_leva_dado_pessoal(usuario, ligada,
                                                   monkeypatch):
    """O `/health` e publico."""
    wa_bot.ULTIMA_RECUSA_REATIVACAO.clear()
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: {"enviado": False, "motivo": "x", "via": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    d = {"user_id": usuario["id"], "user_nome": "Kevin",
         "telefone": TELEFONE, "item_id": None, "kind": "reativacao",
         "message": "oi", "quando": "31/08"}
    monkeypatch.setattr(scheduler, "run_proactive_engine",
                        lambda **k: {"executed_at": "x", "total": 1,
                                     "reativacao_dispatches": [d]})
    wa_bot.dispatch_proactive()
    texto = str(wa_bot.ULTIMA_RECUSA_REATIVACAO)
    assert TELEFONE not in texto and "Kevin" not in texto, texto


def test_o_health_diz_se_o_template_esta_liberado(usuario, monkeypatch):
    """`TEMPLATES_APROVADOS` e allowlist fail-closed: sem o nome la, o canal
    recusa e a fila fica parada pra sempre, sem nada visivel de fora. Foi
    exatamente o que aconteceu em producao em 30/08."""
    from fastapi.testclient import TestClient
    c = TestClient(wa_bot.app)

    monkeypatch.setenv("TEMPLATES_APROVADOS", "outro_qualquer")
    assert c.get("/health").json()["template_reativacao_liberado"] is False

    monkeypatch.setenv("TEMPLATES_APROVADOS",
                       "outro_qualquer, reativar_boas_vindas")
    assert c.get("/health").json()["template_reativacao_liberado"] is True


def test_o_ciclo_proativo_aparece_no_health(usuario, ligada, monkeypatch):
    """A pergunta anterior a todas: o motor esta rodando? Sem isto eu
    investiguei duas hipoteses erradas sem nunca descartar a possibilidade
    de o ciclo nem estar acontecendo."""
    from fastapi.testclient import TestClient
    wa_bot.ULTIMO_CICLO.clear()
    c = TestClient(wa_bot.app)
    assert c.get("/health").json()["ciclo"] == "AINDA NAO RODOU"

    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.dispatch_proactive()
    ciclo = c.get("/health").json()["ciclo"]
    assert "quando" in ciclo and "candidatos" in ciclo, ciclo
    assert isinstance(ciclo.get("enviados"), int), ciclo
