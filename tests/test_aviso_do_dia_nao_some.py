# -*- coding: utf-8 -*-
"""O QUE VENCE HOJE NAO PODE PASSAR EM SILENCIO.

A politica de aviso e D-1: o bot avisa na vespera. Se ele estiver fora do ar
naquele dia — apagao da VPS, teto diario, pessoa fora da janela — o aviso
nunca sai, e no dia do vencimento a pessoa nao ouve nada. So no dia SEGUINTE
ela recebe "venceu e nao vi a baixa", quando ja e tarde.

Isto foi encontrado simulando a volta do apagao de 25-28/08/2026: a Elena
tinha condominio vencendo no dia da volta e foi a unica com item do dia que
nao recebeu nada.

Rede de seguranca: se o item vence HOJE e nenhum aviso saiu pra ele em dia
nenhum, avisa hoje. Quem ja foi avisado na vespera NAO recebe de novo.
"""
import datetime as _dt

import db
import scheduler
import tempo


def _vence(uid, quando_dias, desc="condominio", hora=None, criado_dias=9):
    """criado_dias=9 e o caso normal: a pessoa cadastrou dias atras.

    Item cadastrado HOJE (criado_dias=0) e outro caso — a rede nao vale pra
    ele, senao o bot ecoa de volta o que a pessoa acabou de dizer.
    """
    iid = db.add_item(user_id=uid, tipo="despesa", categoria="Contas",
                      descricao=desc, valor_reais=250.0,
                      data_vencimento=(tempo.hoje()
                                       + _dt.timedelta(days=quando_dias)
                                       ).isoformat(),
                      hora_alvo=hora, status="pendente")
    if criado_dias:
        with db.get_conn() as c:
            c.execute("UPDATE items SET data_criacao=? WHERE id=?",
                      ((tempo.agora() - _dt.timedelta(days=criado_dias)
                        ).strftime("%Y-%m-%d %H:%M:%S"), iid))
    return iid


def test_vence_hoje_e_nunca_foi_avisado_recebe_aviso(usuario):
    iid = _vence(usuario["id"], 0)
    alvos = [d.get("item_id") for d in scheduler.check_due_items()]
    assert iid in alvos, (
        "item vence HOJE, o aviso da vespera nunca saiu, e mesmo assim a "
        "pessoa nao ouve nada — so vai saber amanha, depois de vencido")


def test_quem_ja_foi_avisado_na_vespera_nao_recebe_de_novo(usuario):
    """O guarda contra duplicata: o dedup de vencimento e por DIA."""
    iid = _vence(usuario["id"], 0)
    ontem = (tempo.agora() - _dt.timedelta(days=1)).isoformat(
        timespec="seconds")
    with db.get_conn() as c:
        c.execute("INSERT INTO dispatches (user_id, item_id, kind, sent_at) "
                  "VALUES (?,?,?,?)",
                  (usuario["id"], iid, "vencimento", ontem))
    alvos = [d.get("item_id") for d in scheduler.check_due_items()]
    assert iid not in alvos, (
        "avisou na vespera E no dia: duas vibracoes pro mesmo compromisso")


def test_item_com_hora_marcada_continua_com_o_alarme_dele(usuario):
    """Quem tem hora tem alarme proprio; a rede nao pode duplicar isso."""
    iid = _vence(usuario["id"], 0, desc="dentista", hora="15:00")
    alvos = [d.get("item_id") for d in scheduler.check_due_items()]
    assert iid not in alvos, "item com hora recebeu aviso do dia E alarme"


def test_a_rede_nao_vale_pra_link_de_afiliado(usuario):
    """1-click-buy e link comercial: nao ganha caminho novo de envio."""
    iid = _vence(usuario["id"], 0, desc="racao do cachorro")
    with db.get_conn() as c:
        c.execute("UPDATE items SET link_afiliado=? WHERE id=?",
                  ("https://exemplo.com/x", iid))
    alvos = [d.get("item_id") for d in scheduler.check_due_items()]
    assert iid not in alvos, "abriu caminho novo pra disparo comercial"


def test_item_cadastrado_hoje_nao_recebe_aviso_de_hoje(usuario):
    """A rede e pra aviso PERDIDO, nao pra eco do que a pessoa acabou de dizer.

    Quem manda "pagar o condominio hoje" sabe que e hoje. Receber de volta,
    segundos depois, "passando pra lembrar: condominio vence em 28/08" faz o
    bot parecer bobo — e e uma vibracao que nao informa nada.
    """
    iid = _vence(usuario["id"], 0, desc="condominio de hoje",
                 criado_dias=0)
    alvos = [d.get("item_id") for d in scheduler.check_due_items()]
    assert iid not in alvos, (
        "o bot repetiu de volta um item que a pessoa cadastrou hoje mesmo")


def test_a_rede_pega_item_antigo_que_vence_hoje(usuario):
    """O caso que a rede existe pra cobrir: cadastrado dias atras."""
    iid = _vence(usuario["id"], 0, desc="condominio antigo", criado_dias=9)
    alvos = [d.get("item_id") for d in scheduler.check_due_items()]
    assert iid in alvos, "o aviso perdido continua perdido"
