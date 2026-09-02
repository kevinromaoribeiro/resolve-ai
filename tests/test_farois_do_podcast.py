# -*- coding: utf-8 -*-
"""Os tres farois do podcast (M9.10).

O Kevin, 31/08/2026: "crie no meu dash 3 faróis, 1 se está ativo o recurso de
áudios podcast ou seja se estão conseguindo puxar das fontes e gerar
perfeitamente, um de tempo médio dos áudios e o último pra mostrar quantos já
foram enviados por semana".

O farol 1 e o que justifica os outros dois: o sintoma de fonte seca sempre foi
AUSENCIA de audio, e ausencia nao aparece em painel nenhum. Quem nao recebe
nao reclama — cancela.

SAO DUAS TELAS e elas se testam diferente. O `/dash` (mobile, o que o Kevin
abre) e desenhado por JS a partir do `/api/pulso`: o HTML so prova que o card
existe, os NUMEROS se conferem no JSON. O `/painel` (tela grande) e montado no
servidor, entao ali o proprio HTML carrega os valores.
"""
import datetime as _dt

import pytest
from fastapi.testclient import TestClient

import db
import tempo
import wa_bot


@pytest.fixture(autouse=True)
def farol_limpo():
    def zera():
        try:
            with db.get_conn() as c:
                c.execute("DELETE FROM podcast_log")
        except Exception:
            pass          # a tabela so nasce no primeiro registro
    zera()
    yield
    zera()


def _dash(monkeypatch) -> str:
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    return TestClient(wa_bot.app).get("/dash?k=tok").text


def _painel(monkeypatch) -> str:
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    return TestClient(wa_bot.app).get("/painel?k=tok").text


def _pulso() -> dict:
    """Os numeros como o `/dash` os recebe."""
    return wa_bot._dados_do_painel()["podcast"]


# ---------------------------------------------------------------------------
# 1. o card existe na tela que o Kevin abre
# ---------------------------------------------------------------------------

def test_o_card_esta_no_dash(monkeypatch):
    html = _dash(monkeypatch)
    assert "Mini podcast" in html, "o farol sumiu do painel mobile"


def test_os_tres_farois_estao_desenhados(monkeypatch):
    html = _dash(monkeypatch)
    assert "${ptxt}" in html                # 1: esta funcionando?
    assert "Duração média" in html          # 2: quanto dura?
    assert "Enviados na semana" in html     # 3: quantos sairam?


def test_o_card_vem_antes_da_lista_de_clientes(monkeypatch):
    """Farol que exige rolar uma tabela de clientes inteira nao e farol."""
    html = _dash(monkeypatch)
    assert html.index("Mini podcast") < html.index("card('Clientes'")


def test_o_dash_sobrevive_a_um_pulso_sem_o_campo(monkeypatch):
    """Deploy pela metade (JS novo, API velha) nao pode deixar a tela em
    branco — o card cai no default em vez de estourar no `undefined`."""
    html = _dash(monkeypatch)
    assert "d.podcast||{estado:'sem dados'" in html


# ---------------------------------------------------------------------------
# 2. os numeros — no JSON, que e de onde a tela os le
# ---------------------------------------------------------------------------

def test_tudo_saindo_acende_verde(usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    assert _pulso()["estado"] == "ok"


def test_uma_falha_tira_do_verde(usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    db.podcast_registrar_episodio(usuario["id"], "moda", 0, False, "403")
    assert _pulso()["estado"] == "atencao"


def test_maioria_falhando_acende_vermelho(usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    for _ in range(3):
        db.podcast_registrar_episodio(usuario["id"], "moda", 0, False, "403")
    assert _pulso()["estado"] == "quebrado"


def test_sem_episodio_o_painel_nao_grita():
    """No primeiro dia depois do deploy o farol nao pode acender vermelho
    sem motivo — "sem dados" e outra coisa que "quebrado"."""
    p = _pulso()
    assert p["estado"] == "sem dados"
    assert p["nota"] == "nenhum episódio ainda"


def test_o_farol_diz_o_porque_da_cor(usuario):
    """"Com falhas" sozinho manda o Kevin abrir o log pra descobrir o que ja
    esta gravado ali do lado."""
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    db.podcast_registrar_episodio(usuario["id"], "moda", 0, False, "403")
    assert _pulso()["nota"] == "1 ok · 1 falha em 7 dias"


def test_a_nota_concorda_no_plural(usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    for _ in range(2):
        db.podcast_registrar_episodio(usuario["id"], "moda", 0, False, "403")
    assert "2 falhas em 7 dias" in _pulso()["nota"]


def test_a_duracao_sai_em_minutos_e_segundos(usuario):
    """"125 segundos" obriga a fazer conta pra saber se o episodio ficou do
    tamanho combinado (uns 2 minutos)."""
    db.podcast_registrar_episodio(usuario["id"], "futebol", 125.0, True)
    assert _pulso()["duracao"] == "2:05"


def test_sem_duracao_mostra_travessao():
    assert _pulso()["duracao"] == "—"


def test_a_contagem_da_semana_bate(usuario):
    for _ in range(3):
        db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    assert _pulso()["na_semana"] == 3


def test_episodio_velho_sai_da_conta_da_semana(usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    with db.get_conn() as c:
        c.execute("UPDATE podcast_log SET quando=?",
                  ((tempo.agora() - _dt.timedelta(days=20)
                    ).strftime("%Y-%m-%d %H:%M:%S"),))
    assert _pulso()["na_semana"] == 0


def test_o_painel_nao_cai_se_a_telemetria_cair(monkeypatch, usuario):
    """Painel que quebra por causa de telemetria e um painel a menos
    justamente no dia em que ele seria necessario."""
    monkeypatch.setattr(db, "podcast_farois",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("banco fora")))
    assert _pulso()["estado"] == "sem dados"
    assert "Mini podcast" in _dash(monkeypatch)


def test_o_farol_nao_expoe_ninguem(usuario):
    """Farol e agregado por decisao: numero de cliente em cartao de metrica
    nao tem razao de existir. O `/dash` e interno, mas o `/health` ja foi
    auditado por isso e a regra vale pros dois."""
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    for chave, valor in _pulso().items():
        assert isinstance(valor, (int, float, str)), (chave, valor)
        assert usuario["telefone"] not in str(valor), chave


# ---------------------------------------------------------------------------
# 3. a tela grande mostra o mesmo
# ---------------------------------------------------------------------------

def test_o_painel_grande_tambem_tem_os_farois(monkeypatch, usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 125.0, True)
    html = _painel(monkeypatch)
    assert "Mini podcast" in html
    assert "🟢 gerando" in html
    assert "2:05" in html
    assert "episódios enviados na semana" in html


def test_o_painel_grande_pinta_a_falha(monkeypatch, usuario):
    db.podcast_registrar_episodio(usuario["id"], "futebol", 100.0, True)
    db.podcast_registrar_episodio(usuario["id"], "moda", 0, False, "403")
    html = _painel(monkeypatch)
    assert "🟠 com falhas" in html
    assert "#f59e0b" in html
