# -*- coding: utf-8 -*-
"""Um painel so.

Eram duas telas com dados diferentes sobre o mesmo negocio: o link do
relatorio diario ia pra uma, o atalho do celular pra outra, e o dono se
perdia entre elas. O /dash absorveu o que so existia no /painel — as
ultimas mensagens e o botao de testar o motor — entao manter as duas so
multiplicaria o lugar onde procurar.

O `?antigo=1` continua abrindo a tela velha. E saida de emergencia, nao
opcao: se faltar alguma coisa no novo, ninguem fica sem painel.
"""
import wa_bot


def _cli(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    return TestClient(wa_bot.app)


def test_painel_antigo_leva_pro_novo(monkeypatch):
    r = _cli(monkeypatch).get("/painel?k=tok", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/dash?k=tok"


def test_o_token_viaja_no_redirecionamento(monkeypatch):
    """Sem levar o token junto, o dono cai num painel que pede token.

    A barra e escapada de proposito: sem isso ela viraria outro caminho na
    URL de destino e o token chegaria cortado.
    """
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "abc/def_123")
    from fastapi.testclient import TestClient
    r = TestClient(wa_bot.app).get("/painel?k=abc/def_123",
                                   follow_redirects=False)
    assert r.status_code == 302
    assert "abc%2Fdef_123" in r.headers["location"]


def test_a_saida_de_emergencia_abre_a_tela_velha(monkeypatch):
    r = _cli(monkeypatch).get("/painel?k=tok&antigo=1")
    assert r.status_code == 200
    assert "Painel ao vivo" in r.text


def test_token_errado_continua_barrado(monkeypatch):
    """O redirecionamento nao pode virar porta aberta."""
    r = _cli(monkeypatch).get("/painel?k=errado", follow_redirects=False)
    assert r.status_code != 302
    assert r.status_code == 401


def test_token_errado_barrado_tambem_na_saida_de_emergencia(monkeypatch):
    r = _cli(monkeypatch).get("/painel?k=errado&antigo=1")
    assert r.status_code == 401


# --- o novo tem o que o velho tinha -----------------------------------

def test_o_novo_tem_as_ultimas_mensagens(monkeypatch):
    html = _cli(monkeypatch).get("/dash?k=tok").text
    assert "Últimas mensagens" in html


def test_o_novo_tem_o_botao_de_testar_o_motor(monkeypatch):
    """Era a unica acao que so existia no painel antigo."""
    html = _cli(monkeypatch).get("/dash?k=tok").text
    assert "testarMotorAgora" in html
    assert "/cron/proactive" in html


def test_o_novo_tem_todas_as_abas(monkeypatch):
    html = _cli(monkeypatch).get("/dash?k=tok").text
    for aba in ("Negócio", "Financeiro", "Crescimento", "Clientes",
                "Produto", "Ao vivo"):
        assert aba in html, aba


def test_os_cinco_conselheiros_estao_na_tela(monkeypatch):
    """Os `onclick` sao montados em tempo de execucao, entao o que se
    afirma aqui e a CHAMADA que cria cada card — nao o html dele.
    """
    html = _cli(monkeypatch).get("/dash?k=tok").text
    import conselho
    for tipo in conselho.CONSELHOS:
        assert "conselheiro('%s'" % tipo in html, tipo


def test_a_tela_nao_esquece_nenhum_conselheiro(monkeypatch):
    """Conselheiro novo no catalogo e sem card na tela seria invisivel."""
    import re
    import conselho
    html = _cli(monkeypatch).get("/dash?k=tok").text
    na_tela = set(re.findall(r"conselheiro\('([a-z]+)'", html))
    assert na_tela == set(conselho.CONSELHOS)
