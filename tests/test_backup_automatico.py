# -*- coding: utf-8 -*-
"""O banco sai da VPS sozinho, toda semana.

O banco e a UNICA peca insubstituivel do negocio: codigo se clona do
GitHub, segredo se gera de novo — cliente, conversa e historico, nao.

Ate aqui a copia dependia do dono lembrar de abrir o painel e baixar. E
backup que depende de memoria nao e backup, e intencao: funciona nas
primeiras semanas e some justamente quando a rotina aperta.

O RISCO DESTA FEATURE nao e vazamento — e ela falhar CALADA. Backup que
nao acontece mas parece que aconteceu e pior que nenhum, porque a pessoa
para de se preocupar.
"""
import pytest

import canal
import db
import wa_bot


@pytest.fixture
def canal_que_aceita(monkeypatch):
    """Canal de mentira que registra o que sairia."""
    saiu = []
    monkeypatch.setattr(
        canal._mod, "send_document",
        lambda tel, dados, nome, mime, leg: saiu.append(
            {"tel": tel, "nome": nome, "bytes": len(dados),
             "mime": mime, "legenda": leg}) or True,
        raising=False)
    monkeypatch.setattr(db, "dentro_da_janela", lambda uid, tel: True)
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511999999999")
    return saiu


def test_o_banco_sai_sozinho(canal_que_aceita):
    assert wa_bot.backup_para_o_dono() is True
    assert len(canal_que_aceita) == 1
    envio = canal_que_aceita[0]
    assert envio["nome"].startswith("resolveai-")
    assert envio["nome"].endswith(".db")
    assert envio["bytes"] > 1000, "mandou arquivo vazio"


def test_o_arquivo_enviado_e_um_banco_legivel(canal_que_aceita):
    """Backup corrompido descobre-se no pior dia possivel."""
    import sqlite3
    import tempfile
    enviados = []
    canal._mod.send_document = lambda t, d, n, m, l: enviados.append(d) or True
    wa_bot.backup_para_o_dono()
    caminho = f"{tempfile.mkdtemp()}/copia.db"
    with open(caminho, "wb") as f:
        f.write(enviados[0])
    conn = sqlite3.connect(caminho)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0] > 3, "copia sem as tabelas"


def test_nao_repete_dentro_da_semana(canal_que_aceita):
    """Anexo pesado toda hora vira ruido, e ruido vira silenciado."""
    assert wa_bot.backup_para_o_dono() is True
    assert wa_bot.backup_para_o_dono() is False
    assert wa_bot.backup_para_o_dono() is False
    assert len(canal_que_aceita) == 1


def test_a_legenda_diz_o_que_fazer_com_o_arquivo(canal_que_aceita):
    """Arquivo .db sem contexto no WhatsApp e arquivo ignorado."""
    wa_bot.backup_para_o_dono()
    legenda = canal_que_aceita[0]["legenda"].lower()
    assert "backup" in legenda
    assert "drive" in legenda, "nao diz onde guardar"


# --- o que NAO pode acontecer ----------------------------------------

def test_recusa_NAO_marca_a_semana_como_feita(monkeypatch):
    """O defeito mais perigoso possivel aqui.

    Anexo e texto livre e nao existe template com arquivo: fora da janela
    de 24h a Meta recusa. Se a recusa marcasse o disparo, o backup sumiria
    por SETE DIAS — e o dono acharia que estava protegido.
    """
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511999999999")
    monkeypatch.setattr(db, "dentro_da_janela", lambda uid, tel: False)
    assert wa_bot.backup_para_o_dono() is False

    # Janela abre: tem que sair na proxima passada, nao daqui a uma semana.
    saiu = []
    monkeypatch.setattr(canal._mod, "send_document",
                        lambda *a: saiu.append(1) or True, raising=False)
    monkeypatch.setattr(db, "dentro_da_janela", lambda uid, tel: True)
    assert wa_bot.backup_para_o_dono() is True
    assert saiu


def test_falha_no_envio_tambem_nao_marca(monkeypatch, canal_que_aceita):
    monkeypatch.setattr(canal._mod, "send_document",
                        lambda *a: False, raising=False)
    assert wa_bot.backup_para_o_dono() is False
    monkeypatch.setattr(canal._mod, "send_document",
                        lambda *a: True, raising=False)
    assert wa_bot.backup_para_o_dono() is True


def test_envio_que_estoura_nao_derruba_o_ciclo(monkeypatch):
    """Este roda dentro do ciclo proativo. Se explodir aqui, leva junto os
    lembretes de todo mundo."""
    def _explode(*a, **k):
        raise RuntimeError("meta fora do ar")
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511999999999")
    monkeypatch.setattr(db, "dentro_da_janela", lambda uid, tel: True)
    monkeypatch.setattr(canal._mod, "send_document", _explode, raising=False)
    assert wa_bot.backup_para_o_dono() is False


def test_banco_que_nao_copia_nao_derruba_o_ciclo(monkeypatch):
    def _explode(*a, **k):
        raise RuntimeError("disco cheio")
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511999999999")
    monkeypatch.setattr(db, "copia_para_backup", _explode)
    assert wa_bot.backup_para_o_dono() is False


def test_sem_ADMIN_PHONE_nao_tenta(monkeypatch, canal_que_aceita):
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "")
    assert wa_bot.backup_para_o_dono() is False
    assert not canal_que_aceita


def test_fora_da_janela_o_canal_nem_tenta_enviar(monkeypatch):
    """A janela de 24h protege o numero, e vale pra QUALQUER coisa que sai.
    Um caminho novo que chame o envio por fora reabre um buraco fechado."""
    tentou = []
    monkeypatch.setattr(canal._mod, "send_document",
                        lambda *a: tentou.append(1) or True, raising=False)
    monkeypatch.setattr(db, "dentro_da_janela", lambda uid, tel: False)
    assert canal.enviar_documento("5511999999999", b"x" * 100,
                                  "x.db") is False
    assert not tentou, "mandou arquivo fora da janela de 24h"


def test_o_backup_roda_no_ciclo_proativo():
    """Se ninguem chamar, a automacao nao existe."""
    import inspect
    fonte = inspect.getsource(wa_bot)
    assert fonte.count("backup_para_o_dono()") >= 2, (
        "o backup precisa ser chamado no cron externo E no interno")
