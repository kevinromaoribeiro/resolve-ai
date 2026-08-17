"""INVARIANTES DE ARQUIVO do M2.1 — varrem TODOS os fixtures, sempre.

Proposta do auditor na rodada 5, depois de o guardrail cair num conserto de
formatação de nome: o teste que faltava não era mais um caso, era uma
propriedade verificada sobre o conjunto inteiro.

O que estes testes cobrem e nenhum caso individual cobria: o mesmo documento
ACHATADO EM UMA LINHA. É o formato que o `_read_image` pede ("responda
apenas a frase") e o único em que a linha digitável fica na mesma linha do
beneficiário — foi exatamente ali que o código de pagamento vazou pro item.
"""
import re

import pytest

import boleto
import db
import wa_bot
from conftest import TELEFONE
import fixtures_boleto as FX1
import fixtures_boleto2 as FX2


def _todos_os_documentos():
    """Todo texto de documento dos dois arquivos de fixture."""
    docs = {}
    for mod in (FX1, FX2):
        for nome in dir(mod):
            if nome.startswith("_"):
                continue
            val = getattr(mod, nome)
            if isinstance(val, str) and len(val) > 40:
                docs[f"{mod.__name__}.{nome}"] = val
            elif isinstance(val, dict):
                for k, v in val.items():
                    texto = v[0] if isinstance(v, tuple) else v
                    if isinstance(texto, str) and len(texto) > 40:
                        docs[f"{mod.__name__}.{nome}.{k}"] = texto
    return docs


DOCUMENTOS = _todos_os_documentos()
UMA_LINHA = {f"{k} (1 linha)": " ".join(v.split())
             for k, v in DOCUMENTOS.items()}
TODOS = {**DOCUMENTOS, **UMA_LINHA}

_CODIGO_RE = re.compile(r"\d{5,}")


def test_o_conjunto_de_fixtures_e_grande_o_bastante():
    """Harness: invariante que varre lista vazia passa por engano."""
    assert len(TODOS) >= 40, f"so {len(TODOS)} documentos"


def test_piso_de_cobertura_efetiva():
    """MEDE COBERTURA, NÃO TAMANHO.

    Os invariantes abaixo dão `return` cedo quando `extrair` devolve None —
    necessário (metade do conjunto é documento que NÃO pode virar conta),
    mas isso significa que uma regressão que fizesse o parser recusar tudo
    deixaria 700+ testes verdes. Este piso é o que impede o invariante de
    virar no-op silencioso.
    """
    aceitos = [k for k, t in TODOS.items() if boleto.extrair(t)]
    assert len(aceitos) >= 40, (
        f"so {len(aceitos)} de {len(TODOS)} documentos viram conta — o "
        f"parser regrediu, ou os invariantes viraram no-op")


def test_os_dois_grupos_declarados():
    """O que TEM que virar item e o que NÃO PODE, assertado dos dois lados —
    pra cobertura não depender do que o parser resolve aceitar."""
    # Sem este piso, esvaziar o dicionário faz o laço abaixo virar no-op
    # verde — a mesma classe de teste-que-passa-por-engano do piso de
    # cobertura logo acima.
    assert len(FX1.NAO_SAO_CONTA) >= 8, "o grupo negativo encolheu"
    for nome, texto in FX1.NAO_SAO_CONTA.items():
        assert boleto.extrair(texto) is None, f"{nome} virou conta"
        assert boleto.extrair(" ".join(texto.split())) is None, (
            f"{nome} (1 linha) virou conta")
    for nome in ("ENEL", "CONDOMINIO", "SABESP_SEM_CAUDA",
                 "CARTAO_MAIUSCULO"):
        texto = getattr(FX1, nome)
        assert boleto.extrair(texto), f"{nome} deixou de ser reconhecido"
        assert boleto.extrair(" ".join(texto.split())), (
            f"{nome} (1 linha) deixou de ser reconhecido")


@pytest.mark.parametrize("nome", sorted(TODOS))
def test_nenhum_campo_carrega_codigo_de_pagamento(nome):
    """NENHUM campo devolvido pode conter sequência de 5+ dígitos.

    Guardrail da seção 2 do CLAUDE.md: o bot lê e lembra, nunca paga.
    Código de pagamento no item é o primeiro passo pra alguém imaginar o
    contrário — e ele chegou a ser gravado no banco e impresso de volta pro
    usuário.
    """
    d = boleto.extrair(TODOS[nome])
    if not d:
        return
    for campo in ("beneficiario", "tipo", "status_sugerido"):
        valor = str(d.get(campo) or "")
        achado = _CODIGO_RE.search(valor)
        assert not achado, (
            f"{nome}: campo {campo} carrega {achado.group()!r} -> {valor!r}")


@pytest.mark.parametrize("nome", sorted(TODOS))
def test_descricao_do_item_nunca_tem_codigo(nome):
    d = boleto.extrair(TODOS[nome])
    if not d:
        return
    desc = boleto.descricao_de(d)
    achado = _CODIGO_RE.search(desc)
    assert not achado, f"{nome}: descricao {desc!r} carrega {achado.group()!r}"


@pytest.mark.parametrize("nome", sorted(TODOS))
def test_resposta_ao_usuario_nunca_tem_codigo(usuario, monkeypatch, nome):
    """O que chega no WhatsApp da pessoa, ponta a ponta."""
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: TODOS[nome])
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False,
                                "id": f"INV{abs(hash(nome))}"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")
    for trecho in ("03399", "12345678901234", "34191", "98110000018745"):
        assert trecho not in reply, f"{nome}: resposta vazou {trecho!r}"
    for item in db.list_items(usuario["id"]):
        achado = _CODIGO_RE.search(item["descricao"])
        assert not achado, (
            f"{nome}: item guardado com {achado.group()!r}: "
            f"{item['descricao']!r}")


@pytest.mark.parametrize("nome", sorted(TODOS))
def test_nunca_oferece_pagamento(usuario, monkeypatch, nome):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: TODOS[nome])
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False,
                                "id": f"PAG{abs(hash(nome))}"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    reply = ((wa_bot.handle_incoming(payload) or {}).get("text", "")).lower()
    for proibido in ("quer que eu pague", "pago pra você", "pago pra voce",
                     "efetuar o pagamento", "pix copia", "copia e cola",
                     "link de pagamento", "codigo de barras"):
        assert proibido not in reply, f"{nome}: ofereceu {proibido!r}"


@pytest.mark.parametrize("nome", sorted(TODOS))
def test_corpo_cabe_no_limite_da_meta(usuario, monkeypatch, nome):
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: TODOS[nome])
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False,
                                "id": f"MET{abs(hash(nome))}"},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": ""}}}}
    reply = (wa_bot.handle_incoming(payload) or {}).get("text", "")
    assert len(reply) <= 1024, f"{nome}: {len(reply)} chars"
