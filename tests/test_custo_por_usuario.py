# -*- coding: utf-8 -*-
"""A margem real precisa do custo REAL, e ele e por pessoa.

O modelo antigo cobrava uma taxa unica por mensagem recebida e zero por
mensagem enviada. Duas consequencias, as duas erram pra cima:

  - um "ok" de texto custava o mesmo que um audio de 40s, que passa por
    transcricao E LLM;
  - a locucao do podcast, a leitura de boleto por foto e a conversa paga da
    Meta nao entravam na conta de jeito nenhum.

Numa base pequena a media esconde isso: uma pessoa que manda audio todo dia
e recebe podcast custa varias vezes o que custa quem manda dois textos por
semana. Decidir preco pela media e decidir pelo cliente que menos usa.
"""
import datetime as _dt

import pytest

import db
import tempo


@pytest.fixture
def base_limpa():
    """A conta de custo le a base inteira; sobra de outro teste vira ruido."""
    def _zera():
        with db.get_conn() as c:
            for t in ("dispatches", "items", "msg_log", "podcast_log",
                      "users"):
                # `podcast_log` so nasce quando o modulo do podcast roda;
                # numa base nova ela pode nao existir ainda.
                try:
                    c.execute("DELETE FROM " + t)
                except Exception:
                    pass
    _zera()
    yield
    _zera()


def _msg(tel, direcao, tipo, quando=None, n=1):
    ts = (quando or tempo.agora()).isoformat(timespec="seconds")
    with db.get_conn() as conn:
        for _ in range(n):
            conn.execute(
                "INSERT INTO msg_log (user_id, telefone, direcao, tipo, "
                "preview, ts) VALUES (?,?,?,?,?,?)",
                (None, tel, direcao, tipo, "x", ts))


def _pessoa(nome, tel):
    uid = db.create_user(nome=nome, telefone=tel)
    return uid


def _de(linhas, nome):
    return next((x for x in linhas if x["nome"] == nome), None)


# --- o que a taxa unica escondia --------------------------------------

def test_audio_custa_mais_que_texto(base_limpa):
    """Audio passa por transcricao E pelo LLM. Texto so pelo LLM."""
    _pessoa("Fala", "5511900000001")
    _pessoa("Escreve", "5511900000002")
    _msg("5511900000001", "in", "audio", n=10)
    _msg("5511900000002", "in", "texto", n=10)
    linhas = db.custo_por_usuario(30)
    assert _de(linhas, "Fala")["custo_total"] > _de(linhas, "Escreve")["custo_total"]


def test_foto_de_boleto_entra_na_conta(base_limpa):
    _pessoa("Fotografa", "5511900000003")
    _msg("5511900000003", "in", "imagem_silenciosa", n=5)
    p = _de(db.custo_por_usuario(30), "Fotografa")
    assert p["imagem_in"] == 5
    assert p["custo_visao"] > 0


def test_podcast_entra_na_conta(base_limpa):
    """O TTS e o item mais caro por uso, e nao existia no modelo."""
    uid = _pessoa("Ouve", "5511900000004")
    with db.get_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS podcast_log (quando TEXT, "
            "user_id INTEGER, nicho TEXT, segundos INTEGER, ok INTEGER, "
            "erro TEXT)")
        for _ in range(4):
            conn.execute(
                "INSERT INTO podcast_log (quando,user_id,nicho,segundos,ok,erro)"
                " VALUES (?,?,?,?,?,?)",
                (tempo.agora().isoformat(timespec="seconds"), uid,
                 "tech", 120, 1, ""))
    # sem mensagem nenhuma a pessoa nem aparece: o custo do episodio precisa
    # de alguem na lista pra ser somado.
    _msg("5511900000004", "in", "texto", n=1)
    p = _de(db.custo_por_usuario(30), "Ouve")
    assert p["episodios"] == 4
    assert p["custo_podcast"] > 0


def test_texto_livre_dentro_da_janela_nao_custa(base_limpa):
    """E por isso que fazer a pessoa RESPONDER e economia, nao so engajamento."""
    _pessoa("Conversa", "5511900000005")
    _msg("5511900000005", "out", "texto", n=30)
    p = _de(db.custo_por_usuario(30), "Conversa")
    assert p["livres_out"] == 30
    assert p["custo_livre"] == 0.0


def test_template_custa(base_limpa):
    _pessoa("Recebe", "5511900000006")
    _msg("5511900000006", "out", "template", n=8)
    p = _de(db.custo_por_usuario(30), "Recebe")
    assert p["templates"] == 8
    assert p["custo_template"] > 0


# --- a juncao que quase nao aconteceu ---------------------------------

def test_junta_por_telefone_e_nao_por_user_id(base_limpa):
    """A entrada e gravada com user_id NULO.

    Contar por user_id daria zero de entrada pra todo mundo — o custo de
    LLM, que e o principal, sumiria da conta inteira.
    """
    _pessoa("Anonimo", "5511900000007")
    _msg("5511900000007", "in", "texto", n=6)   # user_id None de proposito
    p = _de(db.custo_por_usuario(30), "Anonimo")
    assert p is not None
    assert p["texto_in"] == 6
    assert p["custo_llm"] > 0


def test_telefone_com_e_sem_ddi_casa(base_limpa):
    """Um lado com 55 e o outro sem e divergencia comum e silenciosa."""
    _pessoa("ComDDI", "5511911111111")
    _msg("11911111111", "in", "texto", n=3)
    p = _de(db.custo_por_usuario(30), "ComDDI")
    assert p is not None and p["texto_in"] == 3


# --- a janela de tempo ------------------------------------------------

def test_so_conta_o_periodo_pedido(base_limpa):
    _pessoa("Antigo", "5511900000008")
    _msg("5511900000008", "in", "texto",
         quando=tempo.agora() - _dt.timedelta(days=90), n=50)
    _msg("5511900000008", "in", "texto", n=2)
    p = _de(db.custo_por_usuario(30), "Antigo")
    assert p["texto_in"] == 2


# --- o retrato da base ------------------------------------------------

def test_mostra_a_media_E_o_mais_caro(base_limpa):
    """Media sozinha esconde o cliente caro. Os dois numeros, sempre."""
    _pessoa("Leve", "5511900000009")
    _pessoa("Pesado", "5511900000010")
    _msg("5511900000009", "in", "texto", n=2)
    _msg("5511900000010", "in", "audio", n=60)
    r = db.custo_medio_por_usuario(30)
    assert r["pessoas"] == 2
    assert r["maior"] > r["medio"]
    assert r["topo"][0]["nome"] == "Pesado"


def test_avisa_que_o_custo_e_estimativa(base_limpa):
    """Mesma regra da tabela de CLT: numero nao conferido sai marcado."""
    r = db.custo_medio_por_usuario(30)
    assert r["conferido"] is db.CUSTOS_CONFERIDOS


def test_base_vazia_nao_estoura(base_limpa):
    r = db.custo_medio_por_usuario(30)
    assert r["pessoas"] == 0 and r["medio"] == 0.0
    assert db.custo_por_usuario(30) == []


# --- a margem por pessoa ----------------------------------------------

def test_quem_nao_paga_tem_margem_negativa(base_limpa):
    _pessoa("Testando", "5511900000011")
    _msg("5511900000011", "in", "texto", n=10)
    p = _de(db.custo_por_usuario(30), "Testando")
    assert p["receita"] == 0.0
    assert p["margem"] < 0


def test_quem_paga_desconta_taxa_e_imposto(base_limpa, monkeypatch):
    monkeypatch.setattr(db, "TAXA_PAGAMENTO_PCT", 5.0)
    monkeypatch.setattr(db, "IMPOSTO_PCT", 6.0)
    uid = _pessoa("Paga", "5511900000012")
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET status='ativo' WHERE id=?", (uid,))
    _msg("5511900000012", "in", "texto", n=5)
    p = _de(db.custo_por_usuario(30), "Paga")
    assert p["paga"] is True
    esperado = db.PRECO_MENSAL * 0.89 - p["custo_total"]
    assert abs(p["margem"] - round(esperado, 2)) < 0.02
