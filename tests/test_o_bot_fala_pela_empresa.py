# -*- coding: utf-8 -*-
"""O bot nunca cita o dono pelo nome pra um cliente.

Regra dele, textual (30/08/2026): "nunca cite o meu nome pra nenhum cliente
JAMAIS" e "lembre-se, é uma empresa". Um assistente que diz "eu aviso o
Fulano" vira o recado de uma pessoa; a Resolve AI precisa soar como empresa,
inclusive porque o cliente paga uma assinatura, não um favor.

A auditoria do M9 achou uma linha viva com o nome dele, na resposta de quem
pede mais tempo de trial.

VARRE TUDO E DECLARA AS EXCEÇÕES, em vez de manter uma lista de alvos
(auditoria, 2ª passada). A primeira versão listava 8 arquivos à mão e deixava
de fora justamente `ai_engine.py` — os prompts, que é o lugar mais provável de
o nome dele entrar na boca do modelo — além de `templates/`, `textos.py` e
outros. A docstring prometia "não achar a segunda"; a cobertura não sustentava
a frase. É a mesma virada que o `KINDS_SEM_TEMPLATE` já fez nesta base: lista
de exceções conhecidas envelhece bem, lista de alvos não.

COMENTÁRIO E DOCSTRING PODEM CITAR — é onde a decisão fica registrada, e nada
disso chega ao WhatsApp. O painel interno também: só o dono o abre.
"""
import ast
import io
import os

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_NOMES = ("Kevin", "kevin")

_IGNORA_PASTAS = {".venv", "__pycache__", ".git", ".pytest_cache", "tests"}

# Scripts de teste manual da raiz (`teste_v17_2.py` e irmãos). Não fazem
# parte do runtime do produto: ninguém os importa, e o nome aparece neles
# como dado de fixture ("Kevin Ribeiro"), não como texto que alguém lê.
_PREFIXOS_DISPENSADOS = ("teste_",)

# Cópia de demonstração, fora do caminho do WhatsApp.
_ARQUIVOS_DISPENSADOS = {"app.py"}


def _relativo(caminho):
    return os.path.relpath(caminho, RAIZ).replace(os.sep, "/")


def _fontes():
    for pasta, subs, arquivos in os.walk(RAIZ):
        subs[:] = [d for d in subs if d not in _IGNORA_PASTAS]
        for nome in sorted(arquivos):
            if nome.endswith(".py"):
                yield os.path.join(pasta, nome)


def _docstrings(arvore):
    """Todo literal que é docstring de módulo, classe ou função."""
    achados = set()
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef,
                           ast.AsyncFunctionDef)):
            corpo = getattr(no, "body", None)
            if (corpo and isinstance(corpo[0], ast.Expr)
                    and isinstance(corpo[0].value, ast.Constant)
                    and isinstance(corpo[0].value.value, str)):
                achados.add(id(corpo[0].value))
    return achados


def _e_painel_interno(arquivo: str, texto: str) -> bool:
    """O HTML do painel — só o dono abre, e o nome dele aparece em comentário
    de JS explicando decisão de produto.

    SÓ NO `wa_bot.py`, que é onde o painel mora (auditoria, 3ª passada).
    Quando a varredura passou a cobrir a árvore inteira, esta heurística de
    HTML passou junto — e um prompt do `ai_engine.py` que falasse de
    "function " ficaria isento sem ninguém decidir isso. Hoje não há nenhum;
    a questão é não deixar a porta aberta."""
    if arquivo != "wa_bot.py":
        return False
    return "<div" in texto or "function " in texto or "<!doctype" in texto


def _exemplos_de_template(arvore):
    """Os literais que são valor de `exemplo=[...]` no catálogo.

    Eles vão no formulário de revisão da Meta — lidos por um revisor da
    plataforma, nunca por um cliente. A dispensa é DESTES literais, não do
    arquivo: os corpos dos templates, que estão no mesmo módulo, continuam
    sob a guarda.
    """
    achados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.keyword) and no.arg == "exemplo":
            for filho in ast.walk(no.value):
                if isinstance(filho, ast.Constant):
                    achados.add(id(filho))
    return achados


def _arquivo_dispensado(arquivo: str) -> bool:
    base = arquivo.split("/")[-1]
    return (arquivo in _ARQUIVOS_DISPENSADOS
            or base.startswith(_PREFIXOS_DISPENSADOS))


_ARQUIVOS = sorted(_fontes())


def test_a_varredura_alcanca_o_codigo_todo():
    """A lista de alvos anterior deixava de fora os prompts e os templates —
    e uma guarda que não olha onde o problema mora não é guarda."""
    nomes = {_relativo(c) for c in _ARQUIVOS}
    for obrigatorio in ("wa_bot.py", "ai_engine.py", "scheduler.py",
                        "templates/__init__.py", "podcast.py"):
        assert obrigatorio in nomes, obrigatorio
    assert len(_ARQUIVOS) > 15, len(_ARQUIVOS)


@pytest.mark.parametrize("caminho", _ARQUIVOS, ids=_relativo)
def test_nenhum_texto_de_cliente_cita_o_dono(caminho):
    arquivo = _relativo(caminho)
    if _arquivo_dispensado(arquivo):
        pytest.skip("fora do runtime do produto")
    arvore = ast.parse(io.open(caminho, encoding="utf-8").read())
    docs = _docstrings(arvore) | _exemplos_de_template(arvore)

    vazamentos = []
    for no in ast.walk(arvore):
        if not (isinstance(no, ast.Constant) and isinstance(no.value, str)):
            continue
        if id(no) in docs or _e_painel_interno(arquivo, no.value):
            continue
        if any(n in no.value for n in _NOMES):
            vazamentos.append("linha %d: %r" % (no.lineno, no.value[:110]))

    assert not vazamentos, "%s — texto que vai pro cliente citando o dono:\n  %s" % (
        arquivo, "\n  ".join(vazamentos))


def test_o_teste_enxerga_o_vazamento_quando_ele_existe():
    """Sem isto, um bug no filtro de docstring deixaria tudo verde e esta
    guarda seria a próxima a não guardar nada."""
    fonte = 'def f():\n    """doc com Kevin dentro."""\n    return "eu aviso o Kevin"\n'
    arvore = ast.parse(fonte)
    docs = _docstrings(arvore)
    soltos = [n.value for n in ast.walk(arvore)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and id(n) not in docs and "Kevin" in n.value]
    assert soltos == ["eu aviso o Kevin"], soltos
