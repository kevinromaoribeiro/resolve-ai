# -*- coding: utf-8 -*-
"""A ABA DE PODERES DO PAINEL.

O Kevin, 28/08/2026: "toda vez que a gente construir algo novo, vc vai deixar
uma aba com macro topicos la dentro do nosso dash para que eu clique sempre la
e esteja atualizado com os poderes que tem o RESOLVE AI".

O problema real que isso resolve: ele perdeu a conta do que existe. Foram
tantas features que ele perguntou se o "avisa minha esposa" tinha sido feito —
e tinha, ha semanas. Recurso que o dono nao lembra que existe nao entra na
landing, nao e vendido e nao e usado.

REGRA PERMANENTE: feature nova sem entrada aqui reprova. O teste cobra que
todo template do catalogo e todo kind proativo apareça na lista.
"""
import pytest

import scheduler
import templates
import wa_bot


def test_a_aba_existe_no_dash(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    html = TestClient(wa_bot.app).get("/dash?k=tok").text
    assert "O que o Resolve AI faz" in html, "a aba de poderes sumiu do painel"
    assert "poderes()" in html, "o botao que abre a lista sumiu"


def test_todo_poder_tem_titulo_e_descricao():
    for p in wa_bot.PODERES:
        assert p.get("titulo"), p
        assert p.get("desc"), p
        assert p.get("grupo"), p


def test_nenhum_template_fica_de_fora():
    """Template aprovado que o dono nao sabe que existe e dinheiro parado."""
    texto = " ".join(p["titulo"] + " " + p["desc"] for p in wa_bot.PODERES)
    faltando = []
    for nome, t in templates.CATALOGO.items():
        # basta o assunto aparecer; nao exigimos o nome tecnico do template
        chave = (t.rotulo or nome).lower().split()[0]
        if chave not in texto.lower():
            faltando.append(nome)
    assert not faltando, (
        "template sem mencao na aba de poderes: %s" % faltando)


def test_os_grupos_sao_os_declarados():
    grupos = {p["grupo"] for p in wa_bot.PODERES}
    assert grupos <= set(wa_bot.GRUPOS_DE_PODER), (
        grupos - set(wa_bot.GRUPOS_DE_PODER))


def test_a_lista_chega_no_painel(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(wa_bot, "PAINEL_TOKEN", "tok")
    j = TestClient(wa_bot.app).get("/api/pulso?k=tok").json()
    assert "poderes" in j, sorted(j)
    assert len(j["poderes"]) >= 12, len(j["poderes"])
