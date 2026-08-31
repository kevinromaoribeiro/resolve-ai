# -*- coding: utf-8 -*-
"""O cadastro vindo da landing oficial (M7.5).

É o PRIMEIRO CONTATO de todo cliente novo — se algo se perde aqui, se perde
calado e a pessoa nunca sabe. Achado testando a mensagem exata que
resolveai.ia.br manda, minutos depois de o Kevin acionar gente real.

Os dois defeitos que este arquivo trava:
  1. o último interesse vinha grudado no resto da mensagem e era descartado
     — quem marcava dois perdia todos menos o primeiro;
  2. o assunto do áudio escolhido no formulário nunca era salvo, porque o
     cadastro devolve antes do handler que lia isso.
"""
import pytest

import db
import podcast
import wa_bot
from conftest import texto as _texto

BASE = "5511922220000"


def _entra(sufixo, interesses="contas,saude,carro", audio="", nome="João Pedro"):
    """Manda a mensagem EXATA que a landing no ar monta."""
    tel = BASE[:-2] + "%02d" % sufixo
    u = db.get_user_by_phone(tel)
    if u:
        db.delete_user(u["id"])
    extra = ("\n\nE quero o resumo semanal de %s." % audio) if audio else ""
    wa_bot.handle_incoming(_texto(
        "#RESOLVE|%s|34|%s\n\n"
        "Oi! Quero começar meus 14 dias grátis do Resolve AI 🚀%s"
        % (nome, interesses, extra), tel))
    return db.get_user_by_phone(tel)


# ---------------------------------------------------------------------------
# 1. nada do formulário se perde
# ---------------------------------------------------------------------------

def test_todos_os_interesses_marcados_sao_guardados(limpo):
    """O último vinha grudado em "\n\nOi! Quero começar..." e era
    descartado por não casar a lista de válidos. Em silêncio."""
    u = _entra(1, interesses="contas,saude,carro")
    try:
        assert u["interesses"] == "contas,saude,carro", u["interesses"]
    finally:
        db.delete_user(u["id"])


def test_um_interesse_so_continua_funcionando(limpo):
    u = _entra(2, interesses="pet")
    try:
        assert u["interesses"] == "pet", u["interesses"]
    finally:
        db.delete_user(u["id"])


def test_os_oito_interesses_da_landing_sao_reconhecidos(limpo):
    todos = ("contas,mercado,carro,saude,datas,encomendas,pet,burocracia")
    u = _entra(3, interesses=todos)
    try:
        assert u["interesses"] == todos, u["interesses"]
    finally:
        db.delete_user(u["id"])


def test_nome_e_idade_chegam_inteiros(limpo):
    u = _entra(4, nome="Ana Carolina de Souza")
    try:
        assert u["nome"] == "Ana Carolina de Souza", u["nome"]
        assert u["idade"] == 34
    finally:
        db.delete_user(u["id"])


# ---------------------------------------------------------------------------
# 2. o assunto do áudio escolhido no formulário
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("escolha,chave", [
    ("futebol", "futebol"), ("games", "games"),
    ("inteligência artificial", "ia"), ("moda", "moda"),
    ("varejo online", "varejo online")])
def test_o_assunto_escolhido_e_guardado_no_cadastro(limpo, escolha, chave):
    """A extração morava só no `_handle_commands`, e o cadastro devolve
    antes de chegar lá — a escolha sumia e o bot perguntava de novo."""
    u = _entra(5, audio=escolha)
    try:
        assert u["podcast_nicho"] == chave, u["podcast_nicho"]
    finally:
        db.delete_user(u["id"])


def test_quem_pulou_o_audio_fica_sem_assunto(limpo):
    """"Depois eu escolho" é opção legítima — não inventa assunto."""
    u = _entra(6, audio="")
    try:
        assert not u["podcast_nicho"]
    finally:
        db.delete_user(u["id"])


def test_assunto_desconhecido_nao_vira_lixo_no_banco(limpo):
    u = _entra(7, audio="criptomoeda")
    try:
        assert not u["podcast_nicho"], u["podcast_nicho"]
    finally:
        db.delete_user(u["id"])


# ---------------------------------------------------------------------------
# 3. o cadastro não pode quebrar
# ---------------------------------------------------------------------------

def test_o_bot_responde_o_cliente_novo(limpo):
    """Silêncio no primeiro contato é o cliente perdido antes de começar."""
    u = _entra(8, audio="games")
    try:
        assert len(limpo) >= 2, limpo
        assert "Resolve AI" in limpo[0][1]
        assert "aceite" in limpo[1][1].lower()
    finally:
        db.delete_user(u["id"])


def test_payload_quebrado_nao_derruba_o_cadastro(limpo):
    tel = "5511922229999"
    u0 = db.get_user_by_phone(tel)
    if u0:
        db.delete_user(u0["id"])
    wa_bot.handle_incoming(_texto("#RESOLVE|||\n\nOi!", tel))
    u = db.get_user_by_phone(tel)
    try:
        assert u, "cadastro nao aconteceu"
    finally:
        if u:
            db.delete_user(u["id"])


def test_o_parser_devolve_o_assunto_junto(limpo):
    """Contrato do parser: quem lê o payload lê TUDO o que veio."""
    d = wa_bot._parse_landing_payload(
        "#RESOLVE|Ana|30|contas,pet\n\nOi!\n\n"
        "E quero o resumo semanal de moda.")
    assert d["interesses"] == "contas,pet", d
    assert d["podcast_nicho"] == "moda", d


def test_so_o_assunto_do_audio_ja_registra_a_escolha(limpo):
    """Guarda defensiva: se um dia o formulário mudar e mandar só a escolha
    do áudio, ela não pode ser descartada. Hoje a landing sempre manda nome
    e interesses, então este caminho é o que protege a mudança futura."""
    tel = "5511922228888"
    u0 = db.get_user_by_phone(tel)
    if u0:
        db.delete_user(u0["id"])
    wa_bot.handle_incoming(_texto(
        "#RESOLVE|||\n\nOi!\n\nE quero o resumo semanal de futebol.", tel))
    u = db.get_user_by_phone(tel)
    try:
        assert u["podcast_nicho"] == "futebol", u["podcast_nicho"]
    finally:
        db.delete_user(u["id"])
