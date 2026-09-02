# -*- coding: utf-8 -*-
"""Nenhum fonte carrega caractere de controle (M9.13).

Achado na verificacao reversa: sete `\\b` de regex tinham virado o byte 0x08
(backspace) dentro do `podcast.py`. Um heredoc do Git Bash interpretou o
escape na hora de escrever o arquivo — o mesmo mecanismo que ja tinha comido
`\\n` seis vezes nesta sessao.

Sao regex CRUAS, entao o byte fica literal e o padrao passa a procurar um
backspace de verdade no texto da noticia. Nunca acha. Nao da erro, nao aparece
no log: da episodio com o assunto trocado.

O pior deles era `estrela\\b` em ciencia — A CORRECAO feita justamente pra
"chef estrelado" nao entrar no podcast de ciencia. Estava morta havia horas e
o teste dela passava por outro termo da mesma linha ("galaxia"), que e como
uma guarda continua verde sem guardar nada.

Este teste e barato e pega a classe inteira, nao o caso.
"""
import io
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tudo abaixo de 0x20 menos tab, LF e CR — que sao os unicos que um fonte
# Python tem motivo de conter.
_LIXO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_NOMES = {8: r"\b (backspace)", 12: r"\f (form feed)", 11: r"\v",
          7: r"\a (bell)", 27: r"\e (escape)", 0: "NUL"}


def _fontes():
    """RECURSIVO (auditoria M9, P2): a primeira versao varria so a raiz e
    `tests/`, e deixava `templates/__init__.py` — que monta o texto dos
    templates aprovados — fora da guarda."""
    for pasta, subs, arquivos in os.walk(RAIZ):
        subs[:] = [d for d in subs
                   if d not in (".venv", "__pycache__", ".git", ".pytest_cache")]
        for nome in sorted(arquivos):
            if nome.endswith(".py"):
                yield os.path.join(pasta, nome)


@pytest.mark.parametrize("caminho", list(_fontes()),
                         ids=lambda c: os.path.basename(c))
def test_nenhum_caractere_de_controle(caminho):
    texto = io.open(caminho, encoding="utf-8", newline="").read()
    achados = []
    for n, linha in enumerate(texto.split("\n"), 1):
        for m in _LIXO.finditer(linha):
            code = ord(m.group())
            achados.append(
                "linha %d: %s — provavelmente era a sequencia de dois "
                "caracteres, comida por um heredoc"
                % (n, _NOMES.get(code, "0x%02x" % code)))
    assert not achados, "%s\n  %s" % (os.path.basename(caminho),
                                      "\n  ".join(achados))


def test_o_teste_enxerga_o_lixo_quando_ele_existe():
    """Sem isto, um regex quebrado deixaria o arquivo inteiro sempre verde e
    esta guarda seria a proxima a nao guardar nada."""
    assert _LIXO.search("estrela" + chr(8))
    assert not _LIXO.search("estrela\\b")
    assert not _LIXO.search("linha\tcom\ttab")
