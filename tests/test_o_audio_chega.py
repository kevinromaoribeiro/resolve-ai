# -*- coding: utf-8 -*-
"""O mime do áudio sai dos BYTES, não da capacidade da máquina.

02/09/2026, em produção: o dono pediu a amostra, escolheu o tema, o painel
respondeu "Amostra pronta: 1 de 1 · 80s · 199 palavras" — e nenhum áudio
chegou no telefone dele.

Todo mundo no caminho relatou sucesso: a Meta aceitou o upload, devolveu
message id, `send_audio` respondeu True e o relatório saiu pelo ramo de
sucesso. É o pior tipo de defeito que existe.

A causa: `formato_de_saida()` responde "tem ffmpeg?", e ter ffmpeg NÃO
garante que o Opus saiu. Quando a colagem em Opus falha, o `voz` cai no MP3
de propósito — e aí saíam bytes MP3 rotulados como `audio/ogg`. A Meta
aceita o arquivo e o WhatsApp não entrega.

"Se isso rola com um cliente, já era, perdemos pra sempre." — o dono.
"""
import pytest

import canal
import voz


# ---------------------------------------------------------------------------
# 1. o formato vem do arquivo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dados,esperado", [
    (b"OggS\x00\x02" + b"x" * 50, "audio/ogg"),
    (b"ID3\x04\x00" + b"x" * 50, "audio/mpeg"),
    (b"\xff\xfb\x90\x64" + b"x" * 50, "audio/mpeg"),
    (b"", "audio/mpeg"),
    (None, "audio/mpeg"),
])
def test_o_mime_sai_dos_bytes(dados, esperado):
    assert voz.formato_dos_bytes(dados)[1] == esperado


def test_mp3_com_ffmpeg_instalado_nao_vira_ogg(monkeypatch):
    """O cenário exato do defeito: a máquina TEM ffmpeg (então
    `formato_de_saida` diz ogg), mas o arquivo que saiu é MP3."""
    monkeypatch.setattr(voz, "tem_ffmpeg", lambda: True)
    assert voz.formato_de_saida()[1] == "audio/ogg"
    assert voz.formato_dos_bytes(b"ID3\x04rest")[1] == "audio/mpeg"


# ---------------------------------------------------------------------------
# 2. e é ele que viaja no envio
# ---------------------------------------------------------------------------

def _envio(monkeypatch, dados):
    vistos = {}
    monkeypatch.setattr(canal, "send_audio",
                        lambda tel, d, m: vistos.update(mime=m) or True)
    import db as _db
    monkeypatch.setattr(_db, "dentro_da_janela", lambda *a, **k: True)
    canal.falar_audio("5511999999999", dados, user_id=1)
    return vistos.get("mime")


def test_o_envio_rotula_mp3_como_mp3(monkeypatch):
    """Era aqui que o áudio morria: rótulo ogg, bytes mp3."""
    monkeypatch.setattr(voz, "tem_ffmpeg", lambda: True)
    assert _envio(monkeypatch, b"ID3\x04" + b"x" * 100) == "audio/mpeg"


def test_o_envio_rotula_ogg_como_ogg(monkeypatch):
    monkeypatch.setattr(voz, "tem_ffmpeg", lambda: True)
    assert _envio(monkeypatch, b"OggS\x00\x02" + b"x" * 100) == "audio/ogg"


def test_sem_ffmpeg_o_mp3_continua_certo(monkeypatch):
    monkeypatch.setattr(voz, "tem_ffmpeg", lambda: False)
    assert _envio(monkeypatch, b"ID3\x04" + b"x" * 100) == "audio/mpeg"


def test_mime_explicito_do_chamador_manda(monkeypatch):
    """Quem sabe o formato pode dizer — a leitura dos bytes é o default,
    não uma imposição."""
    vistos = {}
    monkeypatch.setattr(canal, "send_audio",
                        lambda tel, d, m: vistos.update(mime=m) or True)
    import db as _db
    monkeypatch.setattr(_db, "dentro_da_janela", lambda *a, **k: True)
    canal.falar_audio("5511999999999", b"OggS-x", user_id=1,
                      mime="audio/mpeg")
    assert vistos["mime"] == "audio/mpeg"


# ---------------------------------------------------------------------------
# 3. o teto de 3 minutos
# ---------------------------------------------------------------------------

def test_o_episodio_nao_passa_de_tres_minutos():
    """Decisão do dono (02/09/2026): "o limite das notícias deve ser 3min".
    A folga de 15% entregava 3min27 — passar do que se promete é a primeira
    coisa que faz alguém desativar o recurso."""
    import podcast
    assert podcast.PALAVRAS_TETO <= podcast.PALAVRAS_POR_MINUTO * 3
    assert podcast.duracao_estimada_s("x " * podcast.PALAVRAS_TETO) <= 180
