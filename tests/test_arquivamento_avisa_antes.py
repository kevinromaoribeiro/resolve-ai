# -*- coding: utf-8 -*-
"""ARQUIVAR SO DEPOIS QUE O AVISO SAIU.

Um item parado ha 15+ dias e arquivado com aviso. Mas o aviso ("arquivado")
esta em KINDS_SEM_TEMPLATE: fora da janela de 24h ele NAO sai. E quem tem item
parado ha 15 dias e justamente quem nao conversa ha semanas — ou seja, a
condicao que dispara o arquivamento e quase a mesma que impede o aviso de
sair. Se o arquivamento acontecer na geracao do disparo, o item some da lista
da pessoa e ela nunca fica sabendo. Regra 10: nao se perde dado do usuario.
"""
import datetime as _dt

import pytest

import db
import scheduler
import tempo
import wa_bot


def _velho(usuario, dias=20):
    uid = usuario["id"]
    iid = db.add_item(user_id=uid, tipo="lembrete", categoria="Outros",
                      descricao="revisar o seguro", valor_reais=None,
                      data_vencimento=(tempo.hoje()
                                       - _dt.timedelta(days=dias)).isoformat(),
                      status="pendente")
    return iid


@pytest.fixture
def alcancavel():
    """Alguem DENTRO da janela, e que some do banco no fim.

    Sem o cleanup este usuario e o item dele sobrevivem ao arquivo e entram
    na soma de `_dados_do_painel`, quebrando testes de outro arquivo que
    medem o total da base. E o quinto vazamento de estado entre arquivos
    deste projeto — todos com o mesmo sintoma: verde sozinho, vermelho na
    suite.
    """
    tel = "5511955554444"
    uid = db.create_user(nome="Alcancavel", telefone=tel)
    db.update_user_fields(uid, onboarding_step=None, status="trial",
                          lgpd_aceite_em=tempo.agora().isoformat())
    db.log_message(None, tel, "in", "texto", "oi")
    yield uid, tel
    with db.get_conn() as c:
        c.execute("DELETE FROM items WHERE user_id=?", (uid,))
        c.execute("DELETE FROM dispatches WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))


def _abre_janela(usuario):
    """A pessoa falou agora: sai texto livre (o aviso nao tem template)."""
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")


def _status(iid):
    with db.get_conn() as c:
        r = c.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()
    return r["status"]


def test_envio_recusado_nao_arquiva(usuario, monkeypatch):
    """Se o aviso nao saiu, o item continua na lista da pessoa."""
    iid = _velho(usuario)
    _abre_janela(usuario)      # chega ate o `falar`; a recusa e que importa
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": False, "via": "",
                                         "motivo": "numero invalido"})
    wa_bot.dispatch_proactive()
    assert _status(iid) == "pendente", (
        "item arquivado sem que o dono fosse avisado — some da lista dele "
        "e nao volta em nenhum ciclo futuro")


def test_arquiva_depois_que_o_aviso_saiu(usuario, monkeypatch):
    """Aviso confirmado num ciclo, arquivamento no seguinte."""
    iid = _velho(usuario)
    _abre_janela(usuario)
    saiu = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, txt, **k: (saiu.append(txt) or
                                               {"enviado": True, "via": "texto",
                                                "motivo": ""}))
    wa_bot.dispatch_proactive()
    assert any("rquiv" in t for t in saiu), (
        "o aviso de arquivamento nao saiu: %r" % (saiu,))
    wa_bot.dispatch_proactive()
    assert _status(iid) == "aglutinado", (
        "aviso saiu mas o item nunca foi arquivado — vira lixo eterno")


def test_avisado_uma_vez_so(usuario, monkeypatch):
    """Varios ciclos nao viram varias mensagens de arquivamento."""
    _velho(usuario)
    _abre_janela(usuario)
    saiu = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, txt, **k: (saiu.append(txt) or
                                               {"enviado": True, "via": "texto",
                                                "motivo": ""}))
    for _ in range(4):
        wa_bot.dispatch_proactive()
    avisos = [t for t in saiu if "rquiv" in t]
    assert len(avisos) == 1, "avisou %d vezes: %r" % (len(avisos), avisos)


def test_natimorto_nao_come_vaga_do_ciclo(usuario, alcancavel, monkeypatch):
    """Disparo que NAO tem como sair nao pode ocupar vaga no freio do ciclo.

    Consequencia da correcao acima: o aviso de arquivamento agora fica na fila
    enquanto nao sair. Como o corte do ciclo e `all_dispatches[:MAX]` — os
    primeiros CANDIDATOS, nao os primeiros ENVIADOS — um aviso de alguem fora
    da janela de 24h passaria a ocupar uma vaga em todo ciclo, para sempre,
    empurrando pra tras quem esta alcancavel. Fome permanente.
    """
    _velho(usuario)                  # fixture nao tem msg de entrada: fora da janela
    assert not db.dentro_da_janela(telefone=usuario["telefone"]), (
        "o cenario exige alguem FORA da janela — senao o teste nao mede nada")

    outro, TEL_OK = alcancavel
    assert db.dentro_da_janela(telefone=TEL_OK)
    db.add_item(user_id=outro, tipo="despesa", categoria="Contas",
                descricao="conta de luz", valor_reais=90.0,
                data_vencimento=(tempo.hoje()
                                 - _dt.timedelta(days=1)).isoformat(),
                status="pendente")

    fila = [d.get("kind") for d in scheduler.check_overdue()]
    assert fila[0] == "arquivado", (
        "o natimorto precisa vir ANTES na fila pra este teste medir a fome; "
        "ordem observada: %r" % (fila,))

    monkeypatch.setattr(wa_bot, "DISPATCH_MAX_PER_CYCLE", 1)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    saiu = []
    real = wa_bot.wasender.falar

    def _espiao(tel, txt, **kw):
        res = real(tel, txt, **kw)
        if res.get("enviado"):
            saiu.append((tel, txt))
        return res

    monkeypatch.setattr(wa_bot.wasender, "falar", _espiao)
    wa_bot.dispatch_proactive()
    assert any(t.endswith("55554444") for t, _ in saiu), (
        "a unica vaga do ciclo foi gasta com um disparo que nao tinha como "
        "sair; quem estava alcancavel ficou sem nada. saiu=%r" % (saiu,))
