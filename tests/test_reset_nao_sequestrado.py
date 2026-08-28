# -*- coding: utf-8 -*-
"""O RESET DE TRIAL NAO PODE SER SEQUESTRADO PELO MODO TESTE.

Aconteceu em producao em 28/08/2026. O Kevin mandou `resetar trial de todos`
e o bot respondeu "Modo teste: seus dados foram zerados" — apagou o cadastro
DELE (6 itens) e nao tocou em nenhum dos 10 clientes.

A causa: `_MASTER_RESET_RE` casa com QUALQUER mensagem que comece com
"resetar" e e avaliado ANTES do `_RESET_TRIAL_RE`, que exige a frase
completa. Como o numero dele e MASTER e ADMIN ao mesmo tempo, o comando mais
especifico era inalcancavel — nenhuma frase funcionaria.

Regra: comando ESPECIFICO ganha do GENERICO, sempre.
"""
import pytest

import wa_bot


# O comando NOVO (M3.1): nao comeca com "resetar", entao nem chega perto do
# modo teste, e e improvavel de digitar sem querer.
FRASES_DE_TRIAL = [
    "liberar 14 dias para todos",
    "liberar 14 dias pra todos",
    "liberar 14 dias para todos os clientes",
    "Liberar 14 Dias Para Todos",
    "  liberar 14 dias para todos  ",
]

# A frase ANTIGA: nao executa mais, mas continua barrada do modo teste — o
# acidente de 28/08 foi exatamente ela caindo la e apagando o dono.
FRASES_ANTIGAS = [
    "resetar trial de todos",
    "resetar o trial de todos",
    "zerar trial de todos",
]


@pytest.mark.parametrize("frase", FRASES_DE_TRIAL)
def test_frase_de_trial_nao_cai_no_modo_teste(frase):
    """O que decide e a ordem: o especifico tem que vencer."""
    assert wa_bot._RESET_TRIAL_RE.match(frase), (
        "a frase nem casa o padrao de trial: %r" % frase)
    assert not wa_bot._master_reset_pega(frase), (
        "%r ainda seria capturada pelo modo teste — o reset de trial "
        "continua inalcancavel" % frase)


@pytest.mark.parametrize("frase", ["resetar", "reset", "zerar", "novo teste",
                                   "sou novo", "reiniciar teste",
                                   "resetar meus dados"])
def test_modo_teste_continua_funcionando(frase):
    """A correcao nao pode matar o comando de teste do dono."""
    assert wa_bot._master_reset_pega(frase), frase


@pytest.mark.parametrize("frase", FRASES_ANTIGAS)
def test_frase_antiga_nao_zera_o_dono(frase):
    """A protecao que faltava: ela NAO pode cair no modo teste."""
    assert not wa_bot._master_reset_pega(frase), (
        "%r ainda zera o cadastro do dono" % frase)


def test_reset_de_trial_chega_no_handler(usuario, monkeypatch):
    """De ponta a ponta: a frase certa reseta os CLIENTES, nao o dono."""
    import db
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", usuario["telefone"])
    monkeypatch.setattr(wa_bot, "MASTER_PHONE", usuario["telefone"])
    outro = db.create_user(nome="Cliente", telefone="5511900004444")
    resp = wa_bot._handle_commands(usuario, usuario["telefone"],
                                   "liberar 14 dias para todos")
    assert resp and "14" in resp, resp
    assert "zerados" not in (resp or "").lower(), (
        "caiu no modo teste: %r" % resp)
    assert db.get_user(outro) is not None, "apagou o cliente"


def test_frase_antiga_ensina_a_nova(usuario, monkeypatch):
    """Silencio aqui e pior: o dono acharia que resetou."""
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", usuario["telefone"])
    resp = wa_bot._handle_commands(usuario, usuario["telefone"],
                                   "resetar trial de todos") or ""
    assert "liberar 14 dias para todos" in resp, resp
    assert "nada foi alterado" in resp.lower(), resp
