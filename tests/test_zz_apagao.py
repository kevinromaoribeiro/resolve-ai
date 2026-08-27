# -*- coding: utf-8 -*-
"""SIMULACAO: o bot volta depois de dias fora do ar.

Nao e teste ainda — e instrumento. Monta uma base parecida com a real (11
pessoas, itens variados), congela o relogio no dia da volta, roda o motor de
verdade e imprime o que CADA pessoa receberia no primeiro ciclo.

Rodar com:  pytest tests/test_zz_apagao.py -s -q
"""
import datetime as _dt

import pytest

import db
import scheduler
import tempo
import wa_bot


DIAS_FORA = 4                      # 25/08 caiu, volta dia 28
VOLTA = _dt.datetime(2026, 8, 28, 9, 0, 0)
CAIU = VOLTA - _dt.timedelta(days=DIAS_FORA)


def _u(nome, tel, dias_de_casa=20, ultima_interacao_dias=1):
    uid = db.create_user(nome=nome, telefone=tel)
    quando = (VOLTA - _dt.timedelta(days=dias_de_casa)).strftime(
        "%Y-%m-%d %H:%M:%S")
    visto = (VOLTA - _dt.timedelta(days=ultima_interacao_dias)).strftime(
        "%Y-%m-%d %H:%M:%S")
    with db.get_conn() as c:
        c.execute("UPDATE users SET data_criacao=?, trial_base=?, "
                  "ultima_interacao=?, onboarding_step='done', "
                  "status='trial', dia_resumo='Segunda-feira' WHERE id=?",
                  (quando, quando, visto, uid))
    return uid


def _item(uid, desc, venc_dias, categoria="Contas", tipo="despesa",
          valor=120.0, criado_dias=25, hora=None, rec=None):
    """venc_dias: negativo = venceu ha N dias; positivo = vence em N dias."""
    iid = db.add_item(user_id=uid, tipo=tipo, categoria=categoria,
                      descricao=desc, valor_reais=valor,
                      data_vencimento=(VOLTA.date()
                                       + _dt.timedelta(days=venc_dias)
                                       ).isoformat(),
                      hora_alvo=hora, recorrencia=rec, status="pendente")
    with db.get_conn() as c:
        c.execute("UPDATE items SET data_criacao=? WHERE id=?",
                  ((VOLTA - _dt.timedelta(days=criado_dias)).strftime(
                      "%Y-%m-%d %H:%M:%S"), iid))
    return iid


def test_apagao(monkeypatch, capsys):
    monkeypatch.setattr(tempo, "agora", lambda: VOLTA)
    monkeypatch.setattr(tempo, "hoje", lambda: VOLTA.date())
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)

    # --- a base: 11 pessoas, como as dele -------------------------------
    nomes = ["Ana", "Bruno", "Carla", "Diego", "Elena", "Felipe",
             "Gabi", "Hugo", "Iris", "Joao", "Kaue"]
    users = {}
    for i, nome in enumerate(nomes):
        users[nome] = _u(nome, "5511900%06d" % (700000 + i),
                         dias_de_casa=10 + i,
                         ultima_interacao_dias=1 + (i % 12))

    # itens que VENCERAM durante o apagao
    _item(users["Ana"], "conta de luz", -3)
    _item(users["Ana"], "IPVA", -2, categoria="Veículo", tipo="lembrete")
    _item(users["Bruno"], "aluguel", -4)
    _item(users["Carla"], "cartao", -1)
    # itens que vencem HOJE ou amanha
    _item(users["Diego"], "internet", 1)
    _item(users["Elena"], "condominio", 0)
    # item VELHO, prestes a cruzar os 15 dias de arquivamento
    _item(users["Felipe"], "consulta no dentista", -16, tipo="lembrete",
          categoria="Saúde", valor=None, criado_dias=40)
    _item(users["Gabi"], "revisar o seguro", -20, tipo="lembrete",
          categoria="Outros", valor=None, criado_dias=50)
    # alarme de hora que passou durante o apagao
    _item(users["Hugo"], "buscar exame", -2, tipo="lembrete",
          categoria="Saúde", valor=None, hora="10:00")
    # gente que sumiu (anti-churn)
    _item(users["Iris"], "trocar o oleo", 20, tipo="lembrete",
          categoria="Veículo", valor=None, criado_dias=45)
    _item(users["Joao"], "pagar o IPTU", 30, criado_dias=48)
    # recorrente
    _item(users["Kaue"], "academia", -2, tipo="lembrete", categoria="Treino",
          valor=None, rec="mensal")

    tel_nome = {}
    for nome in nomes:
        tel_nome[db.get_user(users[nome])["telefone"]] = nome

    # O CENARIO REAL: no dia da volta o Kevin manda `resetar trial de todos`.
    # Sem isso metade da base esta com trial vencido e nao recebe nada — e o
    # risco de rajada some por acidente, nao por desenho.
    tocados = db.resetar_trial(list(users.values()), por="simulacao")
    print("trials resetados: %d de %d" % (len(tocados), len(users)))

    # --- o ciclo de volta ------------------------------------------------
    recebidas = []

    def _falar(tel, txt, **kw):
        recebidas.append((tel, txt))
        return {"enviado": True, "via": "texto", "motivo": ""}

    monkeypatch.setattr(wa_bot.wasender, "falar", _falar)

    ciclos = []
    for n in range(1, 13):            # 12 ciclos = ~1 a 3 horas de cron
        antes = len(recebidas)
        wa_bot.dispatch_proactive()
        ciclos.append(len(recebidas) - antes)
        if len(recebidas) == antes and n > 2:
            break

    por_tel = {}
    for tel, txt in recebidas:
        por_tel.setdefault(tel, []).append(txt)
    tel_de = {}
    for nome in nomes:
        u = db.get_user(users[nome])
        tel_de[u["telefone"]] = nome

    linhas = []
    linhas.append("=" * 72)
    linhas.append("APAGAO DE %d DIAS — volta em %s" % (
        DIAS_FORA, VOLTA.strftime("%d/%m %H:%M")))
    linhas.append("=" * 72)
    linhas.append("mensagens por ciclo: %s" % ciclos)
    linhas.append("TOTAL enviadas: %d  |  pessoas atingidas: %d de %d"
                  % (len(recebidas), len(por_tel), len(nomes)))
    linhas.append("")
    for tel, msgs in sorted(por_tel.items(),
                            key=lambda kv: -len(kv[1])):
        linhas.append("--- %s (%d mensagem(ns)) ---"
                      % (tel_de.get(tel, tel), len(msgs)))
        for m in msgs:
            linhas.append("    | " + m.replace("\n", " ")[:110])
        linhas.append("")
    sem_nada = [n for n in nomes
                if db.get_user(users[n])["telefone"] not in por_tel]
    linhas.append("nao receberam nada: %s" % (sem_nada or "-"))

    # arquivamento
    arquivados = []
    with db.get_conn() as c:
        for r in c.execute("SELECT descricao, status FROM items "
                           "WHERE status='aglutinado' OR status='vencido'"):
            arquivados.append((r["descricao"], r["status"]))
    linhas.append("itens que mudaram de status: %s" % (arquivados or "-"))

    print("\n".join(linhas))

    # ---- O QUE ESTE CENARIO GARANTE -----------------------------------
    # 1. NADA DE RAJADA. O primeiro ciclo depois de dias fora nao pode
    #    despejar a fila inteira: o freio por ciclo vale tambem na volta,
    #    que e justamente quando ha acumulo.
    assert ciclos[0] <= wa_bot.DISPATCH_MAX_PER_CYCLE, (
        "o primeiro ciclo da volta furou o freio: %s" % (ciclos,))

    # 2. NINGUEM LEVA METRALHADA. Quatro dias de acumulo nao viram quatro
    #    vibracoes pra mesma pessoa no mesmo minuto.
    for _tel, _msgs in por_tel.items():
        assert len(_msgs) <= 2, (
            "%s recebeu %d mensagens de uma vez: %r"
            % (tel_de.get(_tel, _tel), len(_msgs), [m[:60] for m in _msgs]))

    # 3. QUEM TEM COMPROMISSO DO DIA E AVISADO. A Elena tinha condominio
    #    vencendo no dia da volta e, antes da rede de D-0, era a unica com
    #    item do dia que nao ouvia nada — o aviso dela caiu dentro do apagao.
    assert db.get_user(users["Elena"])["telefone"] in por_tel, (
        "item vencendo no dia da volta passou em silencio")

    # 4. NADA E ARQUIVADO SEM AVISO. Felipe (16 dias) e Gabi (20) estao fora
    #    da janela de 24h, e "arquivado" nao tem template: o aviso nao tem
    #    como sair. Entao o item TEM que continuar na lista deles.
    with db.get_conn() as _c:
        _mudados = [r["descricao"] for r in _c.execute(
            "SELECT descricao FROM items WHERE status='aglutinado'")]
    assert not _mudados, (
        "item arquivado sem que o dono fosse avisado: %r" % (_mudados,))

    capsys.disabled()
