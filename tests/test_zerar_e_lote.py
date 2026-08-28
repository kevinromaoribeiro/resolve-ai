# -*- coding: utf-8 -*-
"""ZERAR UM CLIENTE E MANDAR TEMPLATE PRA UMA LISTA.

Dois pedidos do Kevin (28/08/2026):

1. "o cliente DAVI nao estava conseguindo mandar mensagens, zere
   completamente ele da base de tudo" — reset total, pra ele voltar pela
   landing page como usuario novo.
2. "todos os templates do meta, eu preciso ter o nome do que ele faz e um
   botao pra ativar, por cliente, ou por lista de clientes, por exemplo
   desengajados".
"""
import datetime as _dt

import pytest

import db
import tempo
import wa_bot


@pytest.fixture
def base_limpa():
    def _zera():
        with db.get_conn() as c:
            c.execute("DELETE FROM dispatches")
            c.execute("DELETE FROM items")
            c.execute("DELETE FROM msg_log")
            c.execute("DELETE FROM users")
    _zera()
    yield
    _zera()


def _pessoa(nome, tel, visto_ha=1, itens=2):
    uid = db.create_user(nome=nome, telefone=tel)
    for k in range(itens):
        db.add_item(user_id=uid, tipo="despesa", categoria="Contas",
                    descricao="conta %d" % k, valor_reais=50.0,
                    status="pendente")
    with db.get_conn() as c:
        c.execute("UPDATE users SET ultima_interacao=?, onboarding_step='done' "
                  "WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=visto_ha)
                    ).strftime("%Y-%m-%d %H:%M:%S"), uid))
    db.log_message(None, tel, "in", "texto", "oi tudo bem")
    return uid


# ---------------------------------------------------------------------------
# zerar
# ---------------------------------------------------------------------------

def test_zerar_apaga_tudo_do_cliente(base_limpa):
    """Nao pode sobrar rastro: ele volta pela landing como usuario novo."""
    uid = _pessoa("Davi", "5511977776666")
    outro = _pessoa("Fica", "5511977775555")
    db.log_dispatch(uid, "vencimento")
    assert db.zerar_cliente(uid, por="painel") is True
    assert db.get_user(uid) is None
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) c FROM items WHERE user_id=?",
                         (uid,)).fetchone()["c"] == 0
        assert c.execute("SELECT COUNT(*) c FROM dispatches WHERE user_id=?",
                         (uid,)).fetchone()["c"] == 0
        assert c.execute("SELECT COUNT(*) c FROM msg_log WHERE telefone=?",
                         ("5511977776666",)).fetchone()["c"] == 0
    # e o vizinho continua inteiro
    assert db.get_user(outro) is not None
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) c FROM items WHERE user_id=?",
                         (outro,)).fetchone()["c"] == 2


def test_zerar_fica_no_log_de_admin(base_limpa):
    uid = _pessoa("Davi", "5511977776666")
    db.zerar_cliente(uid, por="kevin")
    acoes = db.acoes_administrativas("zerar_cliente")
    assert acoes and acoes[0]["por"] == "kevin", acoes


def test_zerar_quem_nao_existe_devolve_false(base_limpa):
    assert db.zerar_cliente(99999) is False


def test_painel_zera_com_confirmacao(base_limpa, monkeypatch):
    from fastapi.testclient import TestClient
    uid = _pessoa("Davi", "5511977776666")
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    c = TestClient(wa_bot.app)
    # sem a confirmacao explicita, NAO apaga
    r = c.post("/painel/acao?k=tok", json={"user_id": uid, "acao": "zerar"})
    assert not r.json().get("ok"), r.text
    assert db.get_user(uid) is not None, "apagou sem confirmacao"
    r = c.post("/painel/acao?k=tok",
               json={"user_id": uid, "acao": "zerar", "confirmo": True})
    assert r.json().get("ok"), r.text
    assert db.get_user(uid) is None


# ---------------------------------------------------------------------------
# envio em lote por segmento
# ---------------------------------------------------------------------------

def test_segmentos_separam_quem_e_quem(base_limpa):
    _pessoa("Ativa", "5511977770001", visto_ha=1)
    _pessoa("Sumida", "5511977770002", visto_ha=20)
    _pessoa("SemItem", "5511977770003", visto_ha=2, itens=0)
    segs = db.segmentos()
    nomes = {s: [p["nome"] for p in gente] for s, gente in segs.items()}
    assert nomes["desengajados"] == ["Sumida"], nomes
    assert "SemItem" in nomes["sem_itens"], nomes
    assert len(nomes["todos"]) == 3, nomes


def test_lote_manda_pra_todo_mundo_do_segmento(base_limpa, monkeypatch):
    from fastapi.testclient import TestClient
    _pessoa("Sumida1", "5511977770004", visto_ha=20)
    _pessoa("Sumida2", "5511977770005", visto_ha=30)
    _pessoa("Ativa", "5511977770006", visto_ha=1)
    enviados = []
    monkeypatch.setattr(
        wa_bot.wasender, "falar",
        lambda tel, txt, **kw: enviados.append(tel) or
        {"enviado": True, "via": "template", "motivo": ""})
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    c = TestClient(wa_bot.app)
    r = c.post("/painel/lote?k=tok",
               json={"segmento": "desengajados",
                     "template": "resolveai_reengajamento_pendentes",
                     "confirmo": True})
    j = r.json()
    assert j.get("ok"), j
    assert j["enviados"] == 2 and j["falharam"] == 0, j
    assert len(enviados) == 2, enviados


def test_lote_sem_confirmacao_nao_manda_nada(base_limpa, monkeypatch):
    from fastapi.testclient import TestClient
    _pessoa("Sumida", "5511977770007", visto_ha=20)
    chamou = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: chamou.append(1) or {"enviado": True})
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    c = TestClient(wa_bot.app)
    r = c.post("/painel/lote?k=tok",
               json={"segmento": "desengajados",
                     "template": "resolveai_reengajamento_pendentes"})
    assert not r.json().get("ok")
    assert not chamou, "disparo em lote sem confirmacao"


def test_lote_recusa_segmento_desconhecido(base_limpa, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    c = TestClient(wa_bot.app)
    r = c.post("/painel/lote?k=tok",
               json={"segmento": "os_que_eu_gosto", "confirmo": True,
                     "template": "resolveai_reengajamento_pendentes"})
    assert not r.json().get("ok"), r.text


def test_lote_exige_token(base_limpa):
    from fastapi.testclient import TestClient
    c = TestClient(wa_bot.app)
    r = c.post("/painel/lote?k=errado",
               json={"segmento": "todos", "confirmo": True,
                     "template": "resolveai_reengajamento_pendentes"})
    assert r.status_code != 200 or not r.json().get("ok")


# ---------------------------------------------------------------------------
# os motores automaticos
# ---------------------------------------------------------------------------

def test_toda_checagem_do_scheduler_esta_ligada_no_motor():
    """"garanta que tem ali os motores ativos que mandam sozinho" (Kevin).

    Uma `check_*` escrita, testada e NAO chamada pelo motor e um recurso que
    existe no repo e nao existe pro cliente. Ja aconteceu duas vezes neste
    projeto: `gastos_por_categoria` (M2.3) e `gastos_dispatches` (M2.5, P0-1),
    as duas descobertas por auditoria e nao por teste.
    """
    import inspect
    import scheduler
    fonte = inspect.getsource(scheduler.run_proactive_engine)
    orfas = [n for n in dir(scheduler)
             if n.startswith("check_") and (n + "(") not in fonte]
    assert not orfas, (
        "checagem existe mas o motor nunca chama: %s. Ligue no "
        "`run_proactive_engine` ou explique por que fica fora." % orfas)


def test_todo_kind_proativo_sai_ou_e_excecao_declarada():
    """Kind sem template e sem estar na lista de excecoes some calado."""
    import scheduler
    import templates as _cat
    for kind in scheduler.KINDS_PROATIVOS:
        assert (kind in _cat.KIND_TEMPLATE
                or kind in _cat.KINDS_SEM_TEMPLATE), (
            "kind %r nao tem template nem esta em KINDS_SEM_TEMPLATE: fora "
            "da janela ele desaparece sem log" % kind)


# ---------------------------------------------------------------------------
# reset de trial pelo painel
# ---------------------------------------------------------------------------

def test_painel_reseta_todos_os_trials(base_limpa, monkeypatch):
    """O comando por WhatsApp exige a frase exata e falhou em producao
    (28/08). O painel precisa de um caminho que nao dependa de digitacao.
    """
    from fastapi.testclient import TestClient
    import db as _db
    a = _pessoa("Ana", "5511900000021")
    b = _pessoa("Bruno", "5511900000022")
    dono = _pessoa("Kevin", "5511945230459")
    for uid in (a, b, dono):
        with _db.get_conn() as c:
            c.execute("UPDATE users SET trial_base=? WHERE id=?",
                      ((tempo.agora() - _dt.timedelta(days=40)
                        ).strftime("%Y-%m-%d %H:%M:%S"), uid))
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511945230459")
    c = TestClient(wa_bot.app)
    r = c.post("/painel/acao?k=tok",
               json={"acao": "resetar_trials", "confirmo": True})
    j = r.json()
    assert j.get("ok"), j
    assert j["tocados"] == 2, j
    assert db.trial_days_left(db.get_user(a)) == 14
    assert db.trial_days_left(db.get_user(b)) == 14
    # O DONO FICA DE FORA: ele nao e cliente, e resetar o trial dele mexe
    # nos numeros que ele usa pra decidir.
    assert db.trial_days_left(db.get_user(dono)) == 0, "resetou o dono"


def test_reset_pelo_painel_exige_confirmacao(base_limpa, monkeypatch):
    from fastapi.testclient import TestClient
    uid = _pessoa("Ana", "5511900000023")
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    c = TestClient(wa_bot.app)
    r = c.post("/painel/acao?k=tok", json={"acao": "resetar_trials"})
    assert not r.json().get("ok"), r.text


def test_reset_pelo_painel_e_idempotente_no_dia(base_limpa, monkeypatch):
    """Clicar duas vezes nao da 28 dias."""
    from fastapi.testclient import TestClient
    import db as _db
    uid = _pessoa("Ana", "5511900000024")
    with _db.get_conn() as c:
        c.execute("UPDATE users SET trial_base=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(days=40)
                    ).strftime("%Y-%m-%d %H:%M:%S"), uid))
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    c = TestClient(wa_bot.app)
    corpo = {"acao": "resetar_trials", "confirmo": True}
    c.post("/painel/acao?k=tok", json=corpo)
    r2 = c.post("/painel/acao?k=tok", json=corpo)
    assert db.trial_days_left(db.get_user(uid)) == 14, "deu dias em dobro"
    assert r2.json()["tocados"] == 0, r2.text
