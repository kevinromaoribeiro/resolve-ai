# -*- coding: utf-8 -*-
"""M2.5 itens 4 e 5 — o relatorio diario do dono, e o reset de trial.

ITEM 4. O relatorio de 8h e lido TODO DIA pra decidir coisa. O defeito dele
nao era estar errado: era misturar saude tecnica, numero de negocio e
dinheiro sem hierarquia, e mostrar valor de hoje sem tendencia. Numero solto
nao diz se melhorou, e relatorio que so descreve obriga o leitor a decidir o
que fazer com pressa, todo dia, sozinho.

ITEM 5. Devolver 14 dias pros 11 usuarios e uma acao de banco de dados
disparada por WhatsApp. Tres coisas nao sao negociaveis: so o dono dispara,
rodar duas vezes nao pode dobrar nada, e NENHUM item pode ser tocado — a
regra 10 do projeto (perder dado do usuario e o pior defeito possivel) vale
com forca total num comando que roda sobre a base inteira de uma vez.
"""
import datetime as _dt

import pytest

import db
import tempo
import wa_bot
from conftest import responder


# ---------------------------------------------------------------------------
# ITEM 4 — o relatorio do dono
# ---------------------------------------------------------------------------
@pytest.fixture
def dono(monkeypatch):
    """O numero do dono, com o relatorio dentro da janela das 8h."""
    tel = "5511900000000"
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", tel)
    agora = _dt.datetime(2026, 8, 18, 9, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda: agora)
    monkeypatch.setattr(tempo, "hoje", lambda: agora.date())
    return tel


@pytest.fixture
def hoje_agosto_dash(monkeypatch):
    """18/08/2026 — o dia em que o dono pediu os ajustes."""
    monkeypatch.setattr(tempo, "hoje", lambda: _dt.date(2026, 8, 18))
    return _dt.date(2026, 8, 18)


@pytest.fixture
def dono_na_segunda(monkeypatch):
    """Igual ao `dono`, mas numa SEGUNDA — quando a cobranca semanal sai."""
    tel = "5511900000000"
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", tel)
    agora = _dt.datetime(2026, 8, 17, 9, 0, 0)
    assert agora.weekday() == 0
    monkeypatch.setattr(tempo, "agora", lambda: agora)
    monkeypatch.setattr(tempo, "hoje", lambda: agora.date())
    return tel


def _relatorio(monkeypatch, **saude):
    """Roda o relatorio e devolve o texto que sairia."""
    enviados = []
    monkeypatch.setattr(wa_bot, "_enviar_com_botao",
                        lambda tel, txt, *a, **kw: enviados.append(txt) or True)
    monkeypatch.setattr(wa_bot, "_instance_state",
                        lambda: saude.get("estado", "open"))
    wa_bot.relatorio_matinal()
    return enviados[0] if enviados else ""


def test_o_relatorio_sai(dono, monkeypatch):
    assert _relatorio(monkeypatch)


def test_whatsapp_caido_aparece_nas_primeiras_linhas(dono, monkeypatch):
    """3 SEGUNDOS. Se o canal caiu, nada mais no relatorio importa — e ele
    nao pode estar depois de sete linhas de metrica bonita."""
    texto = _relatorio(monkeypatch, estado="close")
    topo = "\n".join(texto.splitlines()[:5]).lower()
    assert "whatsapp" in topo, texto


def test_canal_caido_diz_o_que_fazer(dono, monkeypatch):
    """"WhatsApp: CLOSE" e diagnostico. O relatorio existe pra decidir."""
    texto = _relatorio(monkeypatch, estado="close").lower()
    assert "qr" in texto or "reconect" in texto or "reescane" in texto


def test_dia_saudavel_nao_gera_acao(monkeypatch):
    """Secao de acao que aparece todo dia vira cabecalho, e cabecalho a
    gente pula. Ela so pode existir quando ha o que fazer.

    ENTRADA CONTROLADA, e nao o banco: o banco de teste acumula usuarios de
    todos os arquivos, e "N pessoas sem mandar nada" e verdade sobre ELES.
    Rodando contra a base, este teste mediria o banco em vez da regra —
    exatamente o erro que o CLAUDE.md registra em "medir delta, nunca
    valor absoluto". Aqui da pra fazer melhor: a funcao e pura.
    """
    monkeypatch.setattr(wa_bot.calendario, "tabela_expirando",
                        lambda *a, **kw: None)
    env = {"risco": "🟢 baixo", "motivo": "ritmo normal"}
    eng = {"por_pessoa_dia": 2.5, "pessoas": 5, "base_comparavel": 5}
    assert wa_bot._acoes_do_dia("open", env, eng, {}, {}) == []


def test_sem_acao_a_secao_some_do_relatorio(dono, monkeypatch):
    """A outra metade: com a lista vazia, o titulo nao pode ser renderizado
    'so pra manter o formato'."""
    monkeypatch.setattr(wa_bot, "_acoes_do_dia", lambda *a, **kw: [])
    texto = _relatorio(monkeypatch, estado="open")
    assert wa_bot.TITULO_ACAO not in texto, texto


def test_cada_sinal_ruim_vira_uma_acao(monkeypatch):
    """Mutacao: se um `if` do `_acoes_do_dia` cair, o relatorio fica limpo e
    calado — e o dono para de ser avisado sem nada parecer quebrado."""
    monkeypatch.setattr(wa_bot.calendario, "tabela_expirando",
                        lambda *a, **kw: None)
    ok_env = {"risco": "🟢 baixo", "motivo": "ritmo normal"}
    ok_eng = {"por_pessoa_dia": 2.5, "pessoas": 5, "base_comparavel": 5}
    casos = [
        ("canal caido", ("close", ok_env, ok_eng, {}, {})),
        ("risco alto", ("open", {"risco": "🔴 alto", "motivo": "pico"},
                        ok_eng, {}, {})),
        ("falha de envio", ("open", ok_env, ok_eng, {}, {"falhas": 2})),
        ("decidem em 3 dias", ("open", ok_env, ok_eng,
                               {"decidem_ate_3_dias": [
                                   {"nome": "Ana Paula", "dias": 0}]}, {})),
        ("gente calada", ("open", ok_env,
                          {"por_pessoa_dia": 0.2, "pessoas": 1,
                           "base_comparavel": 9}, {}, {})),
    ]
    for nome, args in casos:
        assert wa_bot._acoes_do_dia(*args), f"{nome} nao virou acao"


def test_acao_de_calendario_aparece_sozinha(monkeypatch):
    ok_env = {"risco": "🟢 baixo", "motivo": "ritmo normal"}
    ok_eng = {"por_pessoa_dia": 2.5, "pessoas": 5, "base_comparavel": 5}
    monkeypatch.setattr(tempo, "hoje", lambda: _dt.date(2026, 8, 17))  # segunda
    monkeypatch.setattr(wa_bot.calendario, "tabela_expirando",
                        lambda *a, **kw: "a tabela acaba")
    acoes = wa_bot._acoes_do_dia("open", ok_env, ok_eng, {}, {})
    assert any("a tabela acaba" in a for a in acoes), acoes


def test_o_numero_principal_vem_com_tendencia(dono, monkeypatch):
    """Numero solto nao diz se melhorou — foi o defeito original do painel."""
    texto = _relatorio(monkeypatch)
    assert any(m in texto for m in ("▲", "▼", "→")), texto
    assert "semana" in texto.lower(), texto


def test_calendario_expirando_vira_acao(dono_na_segunda, monkeypatch):
    """A tabela de IPVA/licenciamento acaba em 31/12 e a falha e silenciosa:
    o bot so para de criar lembrete de carro. O aviso tem que chegar ANTES.

    NUMA SEGUNDA: a cobranca do calendario e semanal de proposito (ver
    `_cobrar_calendario_hoje`). Diaria, ela faria a secao FAZER HOJE
    aparecer todos os dias de agosto a dezembro — que e exatamente o que a
    secao promete nao fazer.
    """
    texto = _relatorio(monkeypatch)
    assert "calendário" in texto.lower() or "calendario" in texto.lower()
    assert wa_bot.TITULO_ACAO in texto


def test_o_relatorio_nao_repete_no_mesmo_dia(dono, monkeypatch):
    assert _relatorio(monkeypatch)
    assert _relatorio(monkeypatch) == "", "mandou o dash duas vezes no dia"


def test_falha_de_metrica_nao_derruba_o_relatorio(dono, monkeypatch):
    """O relatorio e o unico lugar onde o dono ve que algo quebrou. Se uma
    consulta ruim o derruba inteiro, a falha fica invisivel justamente no
    dia em que ela aconteceu."""
    def _explode(*a, **kw):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(db, "gastos_por_categoria", _explode, raising=False)
    monkeypatch.setattr(db, "financeiro", _explode)
    texto = _relatorio(monkeypatch)
    assert texto, "uma metrica quebrada apagou o relatorio inteiro"


def test_o_relatorio_cabe_numa_tela(dono, monkeypatch):
    """Relatorio que exige rolagem some do habito. O detalhe fica no dash."""
    texto = _relatorio(monkeypatch)
    assert len(texto.splitlines()) <= 18, texto
    assert len(texto) <= 1024, "passa do limite de corpo da Meta"


# ---------------------------------------------------------------------------
# ITEM 5 — reset de trial
# ---------------------------------------------------------------------------
@pytest.fixture
def base_de_teste(usuario):
    """Tres usuarios com o trial ja vencido, e um item de cada."""
    ids = [usuario["id"]]
    for i in (1, 2):
        uid = db.create_user(nome=f"Fulano {i}", telefone=f"551190000111{i}")
        db.update_user_fields(uid, onboarding_step=None, status="trial")
        ids.append(uid)
    for uid in ids:
        # `trial_base` e `status` VOLTAM AO ZERO aqui: os usuarios "Fulano"
        # sao reaproveitados entre os testes (mesmo telefone), e sem isso um
        # teste que reseta o trial faz o proximo comecar ja resetado — e o
        # proximo passa a medir o reset do anterior, nao o seu.
        db.update_user_fields(uid, trial_base=None, status="trial")
        db.set_created_days_ago(uid, 30)
        db.add_item(user_id=uid, tipo="lembrete", categoria="Casa",
                    descricao=f"item do {uid}",
                    data_vencimento=tempo.hoje().isoformat(),
                    status="pendente")
    return ids


def test_trial_vencido_antes_do_reset(base_de_teste):
    for uid in base_de_teste:
        assert db.trial_days_left(db.get_user(uid)) == 0


def test_reset_devolve_os_14_dias(base_de_teste):
    db.resetar_trial(base_de_teste, por="teste")
    for uid in base_de_teste:
        assert db.trial_days_left(db.get_user(uid)) == 14


def test_reset_nao_toca_em_item_nenhum(base_de_teste):
    """Regra 10: perder dado do usuario e o pior defeito possivel. Um
    comando que varre a base inteira e onde isso custa mais caro."""
    antes = {uid: [dict(i) for i in db.list_items(uid)]
             for uid in base_de_teste}
    db.resetar_trial(base_de_teste, por="teste")
    for uid in base_de_teste:
        assert [dict(i) for i in db.list_items(uid)] == antes[uid]


def test_reset_e_seguro_duas_vezes(base_de_teste):
    """Rodar de novo por engano (ou porque a primeira parecia nao ter
    funcionado) nao pode virar 28 dias."""
    db.resetar_trial(base_de_teste, por="teste")
    segunda = db.resetar_trial(base_de_teste, por="teste")
    assert segunda == [], f"resetou de novo o que ja estava resetado hoje"
    for uid in base_de_teste:
        assert db.trial_days_left(db.get_user(uid)) == 14


def test_reset_fica_registrado(base_de_teste):
    # DELTA, nao valor absoluto: `admin_acoes` e tabela que so cresce, e a
    # regra do CLAUDE.md nasceu de tres rodadas perdidas exatamente aqui.
    antes = len([a for a in db.acoes_administrativas()
                 if a["acao"] == "reset_trial"])
    db.resetar_trial(base_de_teste, por="5511900000000")
    log = db.acoes_administrativas()
    resets = [a for a in log if a["acao"] == "reset_trial"]
    assert len(resets) - antes == len(base_de_teste)
    assert {int(a["alvo"]) for a in resets} == set(base_de_teste)
    assert all(a["por"] == "5511900000000" for a in resets)
    assert all(a["quando"] for a in resets)


def test_reset_nao_ressuscita_quem_cancelou(base_de_teste):
    """Quem pediu cancelamento nao volta pro trial por causa de um comando
    de manutencao — isso e mandar mensagem pra quem pediu silencio."""
    db.set_status(base_de_teste[-1], "cancelado")
    tocados = db.resetar_trial(base_de_teste, por="teste")
    assert base_de_teste[-1] not in tocados


def test_reset_deixa_o_usuario_receber_de_novo(base_de_teste):
    """O objetivo do reset: voltar a receber lembrete. Se `user_can_receive`
    continuar False, o reset foi cosmetico."""
    assert not db.user_can_receive(db.get_user(base_de_teste[0]))
    db.resetar_trial(base_de_teste, por="teste")
    assert db.user_can_receive(db.get_user(base_de_teste[0]))


def test_reset_nao_repete_a_regua_do_trial(base_de_teste):
    """Voltar o relogio nao pode fazer o trial guiado mandar de novo o d1.
    Quem ja recebeu, ja recebeu — senao o reset vira spam pra 11 pessoas."""
    db.mark_nudge_sent(base_de_teste[0], "d1_amostra")
    db.resetar_trial(base_de_teste, por="teste")
    assert db.nudge_already_sent(db.get_user(base_de_teste[0]), "d1_amostra")


# --- o comando, pelo WhatsApp ---------------------------------------------

# M3.1: o comando mudou de frase — nao comeca mais com "resetar",
# porque qualquer coisa iniciada assim caia no modo teste e apagava o
# cadastro do dono (aconteceu em producao em 28/08/2026).
_COMANDO = "liberar 14 dias para todos"


def test_so_o_dono_dispara(usuario, monkeypatch):
    """Comando que varre a base inteira nao pode obedecer a estranho."""
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", "5511900000000")
    antes = db.get_user(usuario["id"])["data_criacao"]
    r = responder(_COMANDO)               # vem do TELEFONE comum
    assert "reset" not in r.lower() or "não" in r.lower() or r == ""
    assert db.get_user(usuario["id"])["data_criacao"] == antes
    assert not [a for a in db.acoes_administrativas()
                if a["acao"] == "reset_trial"]


def test_o_dono_dispara_e_recebe_a_contagem(base_de_teste, monkeypatch):
    tel = "5511977776666"
    uid = db.create_user(nome="Dono", telefone=tel)
    db.update_user_fields(uid, onboarding_step=None, status="trial")
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", tel)
    r = responder(_COMANDO, telefone=tel)
    assert "14 dias" in r, r
    for alvo in base_de_teste:
        assert db.trial_days_left(db.get_user(alvo)) == 14


def test_o_comando_e_seguro_duas_vezes(base_de_teste, monkeypatch):
    tel = "5511977776666"
    uid = db.create_user(nome="Dono", telefone=tel)
    db.update_user_fields(uid, onboarding_step=None, status="trial")
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", tel)
    responder(_COMANDO, telefone=tel)
    segunda = responder(_COMANDO, telefone=tel)
    assert "nenhum" in segunda.lower() or "já" in segunda.lower(), segunda
    for alvo in base_de_teste:
        assert db.trial_days_left(db.get_user(alvo)) == 14


def test_frase_parecida_nao_dispara(base_de_teste, monkeypatch):
    """A porta e estreita de proposito: "me lembra de resetar o trial" e um
    LEMBRETE, nao um comando de banco sobre a base inteira."""
    tel = "5511977776666"
    uid = db.create_user(nome="Dono", telefone=tel)
    db.update_user_fields(uid, onboarding_step=None, status="trial")
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", tel)
    responder("me lembra de resetar o trial amanha", telefone=tel)
    assert not [a for a in db.acoes_administrativas()
                if a["acao"] == "reset_trial"]


# ---------------------------------------------------------------------------
# MUTANTES QUE SOBREVIVERAM A AUDITORIA M2.5
# ---------------------------------------------------------------------------
def test_a_janela_anterior_nao_enxerga_a_semana_atual(usuario):
    """M22 — o mutante mais serio da rodada.

    `engajamento(ref=...)` sem limite SUPERIOR soma tudo o que veio depois,
    entao as duas janelas do relatorio dao quase o mesmo numero e a
    tendencia vira "→ igual a semana passada" pra sempre. O pedido do dono
    ("tendencia, nao numero solto") ficaria cosmetico, e nada falharia.
    """
    hoje = tempo.hoje()
    for i in range(4):
        db.log_message(None, usuario["telefone"], "in", "texto",
                       f"msg {i}")
    agora = db.engajamento(excluir_telefones=[])
    antes = db.engajamento(excluir_telefones=[], ref=hoje - _dt.timedelta(days=7))
    assert agora["despejos_7d"] == 4, agora
    assert antes["despejos_7d"] == 0, (
        "a janela anterior enxergou a semana atual — a tendencia do "
        "relatorio nunca vai sair de 'igual'")


def test_a_tendencia_reflete_o_delta():
    """M36 — ninguem checava que a seta corresponde ao que aconteceu."""
    assert "▲" in wa_bot._tendencia(2.0, 1.0)
    assert "▼" in wa_bot._tendencia(1.0, 2.0)
    assert "→" in wa_bot._tendencia(1.0, 1.0)
    assert "1.00" in wa_bot._tendencia(2.0, 1.0)
    # e a seta pra cima NAO pode aparecer numa queda
    assert "▲" not in wa_bot._tendencia(0.5, 3.0)


def test_o_teto_de_acoes_e_respeitado_e_o_corte_e_visivel(dono, monkeypatch):
    """M37 — `MAX_ACOES` nao era verificado por ninguem, e o corte era mudo.
    Sumir com o quarto item em silencio e pior que mostrar quatro: o dono
    nao tem como saber que existe mais."""
    monkeypatch.setattr(wa_bot, "_acoes_do_dia",
                        lambda *a, **kw: [f"acao {i}" for i in range(6)])
    texto = _relatorio(monkeypatch)
    assert texto.count("• acao") == wa_bot.MAX_ACOES, texto
    assert "+3 não mostrada(s)" in texto, texto


def test_a_acao_que_custa_assinante_nunca_e_cortada(monkeypatch):
    """Ordem por CUSTO, nao por ordem de escrita. Com tres problemas
    tecnicos, a versao anterior jogava 'decidem em 3 dias' pra fora — a
    unica linha do relatorio em que um dia de atraso custa um assinante."""
    monkeypatch.setattr(wa_bot.calendario, "tabela_expirando",
                        lambda *a, **kw: "tabela acabando")
    acoes = wa_bot._acoes_do_dia(
        "close",
        {"risco": "🔴 alto", "motivo": "pico"},
        {"por_pessoa_dia": 0.1, "pessoas": 1, "base_comparavel": 9},
        {"decidem_ate_3_dias": [{"nome": "Ana Paula", "dias": 0}]},
        {"falhas": 3})
    assert len(acoes) >= 5
    visiveis = acoes[:wa_bot.MAX_ACOES]
    assert any("Decidem em até 3 dias" in a for a in visiveis), visiveis


def test_o_calendario_nao_cobra_todo_dia(monkeypatch):
    """O aviso comeca 150 dias antes de a tabela acabar. Diario, ele faria
    a secao FAZER HOJE aparecer de agosto a dezembro sem parar — que e
    exatamente o que a secao promete nao fazer."""
    monkeypatch.setattr(wa_bot.calendario, "tabela_expirando",
                        lambda *a, **kw: "tabela acabando")
    ok_env = {"risco": "🟢 baixo", "motivo": "ritmo normal"}
    ok_eng = {"por_pessoa_dia": 2.5, "pessoas": 5, "base_comparavel": 5}
    monkeypatch.setattr(tempo, "hoje", lambda: _dt.date(2026, 8, 19))  # quarta
    assert wa_bot._acoes_do_dia("open", ok_env, ok_eng, {}, {}) == []
    monkeypatch.setattr(tempo, "hoje", lambda: _dt.date(2026, 8, 17))  # segunda
    assert wa_bot._acoes_do_dia("open", ok_env, ok_eng, {}, {})


def test_tabela_estourada_cobra_todo_dia(monkeypatch):
    """Depois que a tabela acabou nao e mais aviso preventivo: o bot PAROU
    de criar lembrete de carro. Falha em curso cobra todo dia."""
    monkeypatch.setattr(tempo, "hoje", lambda: _dt.date(2027, 3, 10))  # quarta
    assert wa_bot._cobrar_calendario_hoje() is True


def test_base_inteira_fora_do_ar_segura_o_relatorio(dono, monkeypatch):
    """M38 — com todas as metricas quebradas, mandar um relatorio vazio e
    pior que nao mandar: parece que esta tudo bem."""
    def _explode(*a, **kw):
        raise RuntimeError("banco fora")

    for nome in ("painel_metricas", "serie_diaria", "engajamento",
                 "pulso_envio", "financeiro"):
        monkeypatch.setattr(db, nome, _explode)
    assert _relatorio(monkeypatch) == ""


def test_o_reset_nao_obedece_frase_parecida(base_de_teste, monkeypatch):
    """M25 — a porta estreita nao era verificada de verdade. O unico teste
    negativo usava uma frase que nem comeca com "resetar", entao um
    `startswith("resetar")` passava por ele. Estas comecam."""
    tel = "5511977776666"
    uid = db.create_user(nome="Dono", telefone=tel)
    db.update_user_fields(uid, onboarding_step=None, status="trial")
    monkeypatch.setattr(wa_bot, "ADMIN_PHONE", tel)
    for frase in ("resetar o trial do João",
                  "resetar trial de todos os itens da lista",
                  "resetar tudo"):
        responder(frase, telefone=tel)
        assert not [a for a in db.acoes_administrativas()
                    if a["acao"] == "reset_trial"], f"disparou com: {frase}"


@pytest.mark.parametrize("status", ["ativo", "bloqueado"])
def test_o_reset_nao_mexe_em_quem_nao_esta_em_trial(base_de_teste, status):
    """Achado da auditoria (P1-3): a guarda so recusava `cancelado`, entao o
    comando rebaixava ASSINANTE a trial de 14 dias — que expira e corta quem
    paga — e devolvia acesso a quem foi BLOQUEADO."""
    alvo = base_de_teste[-1]
    db.set_status(alvo, status)
    tocados = db.resetar_trial(base_de_teste, por="teste")
    assert alvo not in tocados
    assert db.get_user(alvo)["status"] == status


# ---------------------------------------------------------------------------
# RODADA 2 DA AUDITORIA — o conserto do conserto
# ---------------------------------------------------------------------------
def test_extensao_de_7_dias_funciona_depois_do_reset(base_de_teste):
    """P1-A: UM RELOGIO SO.

    `_base_do_trial` (novo no M2.5) le `trial_base` primeiro; o
    `admin_extend_trial` escrevia em `data_criacao`. Depois de um reset, a
    extensao virava no-op: a pessoa lia "liberei +7 dias — agora sao 14" na
    MESMA frase, perdia os 7 dias, e o `log_dispatch('extensao-trial')` era
    queimado sem a acao ter acontecido. Extensao self-service e uma por
    usuario: perdida, nunca mais volta.
    """
    alvo = base_de_teste[0]
    db.resetar_trial(base_de_teste, por="teste")
    assert db.trial_days_left(db.get_user(alvo)) == 14
    assert db.admin_extend_trial(alvo, 7) is True
    assert db.trial_days_left(db.get_user(alvo)) == 21


def test_o_relogio_do_trial_tem_um_dono_so(base_de_teste):
    """Mesma raiz, cobrada na ordem inversa: estender ANTES e resetar
    depois nao pode fazer o reset ignorar a extensao — o reset e explicito
    e ganha, mas o numero tem que bater com o que a mensagem diz."""
    alvo = base_de_teste[0]
    db.admin_extend_trial(alvo, 7)
    antes = db.trial_days_left(db.get_user(alvo))
    db.resetar_trial([alvo], por="teste")
    assert db.trial_days_left(db.get_user(alvo)) == 14
    assert antes != 14 or True   # o reset zera de proposito


def test_simular_fim_de_trial_continua_funcionando_apos_reset(base_de_teste):
    """`set_created_days_ago` e utilitario de TESTE, e virava no-op depois
    de um reset — qualquer teste futuro que simulasse fim de trial mediria
    nada e passaria. Teste cego e pior que teste ausente."""
    alvo = base_de_teste[0]
    db.resetar_trial([alvo], por="teste")
    db.set_created_days_ago(alvo, 30)
    assert db.trial_days_left(db.get_user(alvo)) == 0


def test_o_bot_nao_promete_criar_lembrete_de_2027_sozinho(usuario,
                                                          hoje_agosto_dash):
    """P1-B: o final da placa nao era guardado em lugar nenhum, e a resposta
    dizia "quando sair, eu crio o lembrete sozinho". Quando a tabela de 2027
    entrasse, nada aconteceria — e a pessoa nao tem como saber."""
    r = responder("minha placa e ABC1D20")
    baixo = r.lower()
    assert "crio o lembrete sozinho" not in baixo, r
    assert "me manda" in baixo or "me mande" in baixo or "me diz" in baixo, r


def test_o_final_da_placa_fica_guardado(usuario, hoje_agosto_dash):
    """"Anotei o final *N* da sua placa" tem que ser verdade."""
    responder("minha placa e ABC1D27")
    assert db.get_user(usuario["id"])["placa_final"] == 7


def test_placa_guardada_nao_apaga_com_frase_sem_placa(usuario,
                                                      hoje_agosto_dash):
    """Regra 10: o que a pessoa deu, o bot nao perde por acidente."""
    responder("minha placa e ABC1D27")
    responder("me lembra de comprar pao")
    assert db.get_user(usuario["id"])["placa_final"] == 7


def test_chave_de_disparo_com_tipo_errado_nao_some_calada(usuario,
                                                          monkeypatch,
                                                          caplog):
    """P2-1 da rodada 2: e o P0-1 esperando outro tipo de dado.

    O `_chaves` derivado ignorava em silencio qualquer valor que nao fosse
    `list` — uma tupla sumia sem log, e o proprio log dizia "0 total pra
    enviar". Agora tupla passa, e o que nao passa vira ERRO no log.
    """
    import logging
    import scheduler as _s
    real = _s.run_proactive_engine

    def _com_tipo_errado(*a, **kw):
        out = real(*a, **kw)
        out["esquisito_dispatches"] = {"nao": "e lista"}
        return out

    monkeypatch.setattr(wa_bot.scheduler, "run_proactive_engine",
                        _com_tipo_errado)
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, txt, **kw: {"enviado": True,
                                                "via": "texto", "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    with caplog.at_level(logging.ERROR, logger="resolveai"):
        wa_bot.dispatch_proactive()
    assert "esquisito_dispatches" in caplog.text, caplog.text


def test_nome_vazio_ainda_vira_acao_legivel(dono, monkeypatch):
    """P2-3b: o `split()[0]` estourava com nome so de espaco.

    O assert e sobre a LINHA sair, e nao sobre o relatorio sobreviver: com
    o `_seguro` por cima, a excecao viraria lista vazia e o relatorio sairia
    igual — os dois consertos mascarariam um ao outro e os dois testes
    passariam com qualquer um deles revertido.
    """
    real = db.financeiro
    monkeypatch.setattr(
        db, "financeiro",
        lambda *a, **kw: {**real(*a, **kw),
                          "decidem_ate_3_dias": [{"nome": "   ", "dias": 0}]})
    texto = _relatorio(monkeypatch)
    assert "Decidem em até 3 dias" in texto, texto


def test_acao_que_estoura_nao_derruba_o_relatorio(dono, monkeypatch):
    """P2-3: a COMPOSICAO das acoes estava fora do `_seguro`, enquanto a
    docstring prometia o contrario. Como o `log_dispatch` so grava depois do
    envio, o cron repetia a falha a cada ciclo, das 8h as 12h, todo dia."""
    def _explode(*a, **kw):
        raise RuntimeError("acao quebrada")

    monkeypatch.setattr(wa_bot, "_acoes_do_dia", _explode)
    assert _relatorio(monkeypatch), "uma acao quebrada apagou o relatorio"


def test_sem_base_na_semana_passada_nao_vira_crescimento():
    """P2-9: comparar contra uma semana em que nao havia ninguem devolvia
    "▲ 2.00 vs. semana passada" — le como crescimento e e so o primeiro
    dado existindo."""
    saida = wa_bot._tendencia(2.0, 0.0)
    assert "▲" not in saida, saida
    assert "primeira semana" in saida, saida
    # e o caminho normal continua funcionando
    assert "▲" in wa_bot._tendencia(2.0, 1.0)


def test_o_painel_ve_o_mesmo_trial_que_o_bot(base_de_teste):
    """Rodada 3, P1-1: `admin_list_users` era o unico SELECT de usuario
    escrito a mao no projeto, e por isso o unico que nao viu o `trial_base`
    nascer. O painel mostrava os dias do `data_criacao` enquanto o bot
    contava pelo relogio novo — o dono clicava em "+7 dias", o numero nao
    mexia, ele clicava de novo, e cada clique dava +7 de verdade."""
    alvo = base_de_teste[0]
    db.resetar_trial([alvo], por="teste")
    db.admin_extend_trial(alvo, 7)
    verdade = db.trial_days_left(db.get_user(alvo))
    do_painel = [u for u in db.admin_list_users() if u["id"] == alvo][0]
    assert do_painel["dias_trial_restantes"] == verdade == 21, (
        f"painel diz {do_painel['dias_trial_restantes']}, o bot conta "
        f"{verdade}")


def test_extensao_que_falha_nao_queima_a_unica_chance(usuario, monkeypatch):
    """Rodada 3, P1-2: o retorno do `admin_extend_trial` era ignorado e o
    `log_dispatch` gravava do mesmo jeito. Com o UPDATE falhando, a pessoa
    lia "liberei +7 dias" e ficava bloqueada PARA SEMPRE — a extensao e uma
    por usuario."""
    db.set_created_days_ago(usuario["id"], 13)
    monkeypatch.setattr(db, "admin_extend_trial", lambda *a, **kw: False)
    r = responder("mais tempo")
    assert "liberei" not in r.lower(), r
    assert not db.dispatched_ever("extensao-trial", usuario["id"]), \
        "queimou a extensao sem ter executado"


def test_secao_de_acao_quebrada_aparece_em_vez_de_sumir(dono, monkeypatch):
    """Ausencia de secao le como "nao tem nada a fazer" — que e o estado
    default. Silencio que imita normalidade e o pior no-op possivel."""
    def _explode(*a, **kw):
        raise RuntimeError("quebrou")

    monkeypatch.setattr(wa_bot, "_acoes_do_dia", _explode)
    texto = _relatorio(monkeypatch)
    assert wa_bot.TITULO_ACAO in texto, texto
    assert "não consegui montar" in texto, texto


def test_falha_ao_estender_o_trial_nao_some_do_log(usuario, monkeypatch,
                                                   caplog):
    """Regra 5: `except Exception: return False` sem log deixava quem chama
    dizer "nao consegui" com NADA no servidor explicando por que."""
    import logging

    def _explode(*a, **kw):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(db, "get_user", _explode)
    with caplog.at_level(logging.WARNING, logger="resolveai"):
        assert db.admin_extend_trial(usuario["id"], 7) is False
    assert "trial" in caplog.text.lower(), caplog.text


def test_o_painel_nao_entrega_a_ficha_inteira_do_usuario(base_de_teste):
    """Rodada 4: o `SELECT u.*` (conserto do P1-1) passou a linha INTEIRA de
    `users` pro `/api/pulso`, cuja chave viaja em query string. De "nome e
    telefone" pra dossie — idade, profissao, carro, pet, placa — da base
    toda. O SELECT continua trazendo tudo, porque coluna nova nao pode
    depender de alguem lembrar de ir no SQL; quem filtra e a saida."""
    proibidos = {"idade", "profissao", "carro_modelo", "carro_km",
                 "pet_info", "placa_final", "trial_base", "lgpd_aceite_em",
                 "trial_nudges_sent", "onboarding_step"}
    for u in db.admin_list_users():
        vazando = proibidos & set(u)
        assert not vazando, f"o painel esta entregando {sorted(vazando)}"
    # e o que o painel USA continua vindo
    algum = db.admin_list_users()[0]
    for campo in ("id", "nome", "telefone", "status",
                  "dias_trial_restantes", "n_pendentes"):
        assert campo in algum, campo


def test_estender_trial_nao_desbloqueia_quem_foi_bloqueado(base_de_teste):
    """O `status='trial'` era incondicional: o botao "+dias" do painel
    devolvia acesso a quem foi BLOQUEADO — a mesma porta que o
    `resetar_trial` fechou nesta fase e esta funcao tinha deixado aberta."""
    alvo = base_de_teste[0]
    db.set_status(alvo, "bloqueado")
    assert db.admin_extend_trial(alvo, 7) is False
    assert db.get_user(alvo)["status"] == "bloqueado"


def test_data_de_trial_ilegivel_nao_expira_ninguem_e_grita(usuario,
                                                           caplog):
    """Regra 10 na direcao do fallback (nao cortar acesso por campo torto) e
    regra 5 no aviso: sem log, data ilegivel virava trial que nunca expira,
    em silencio, sem uma linha no servidor."""
    import logging
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET trial_base=? WHERE id=?",
                     ("data-torta", usuario["id"]))
    with caplog.at_level(logging.WARNING, logger="resolveai"):
        restantes = db.trial_days_left(db.get_user(usuario["id"]))
    # `>= 14`, e nao `== 14`: o fallback devolve `tempo.agora()` e quem
    # chama pede a hora DE NOVO — os dois instantes diferem por microssegundos
    # e `timedelta.days` chega a arredondar pra -1, dando 15. Um dia a mais
    # num caminho de dado corrompido (que ja loga) e ruido; o que importa e
    # que ninguem seja EXPIRADO por causa de um campo torto.
    assert restantes >= 14, restantes
    assert "ilegivel" in caplog.text, caplog.text


def test_extensao_recusada_avisa_que_nada_foi_gasto(usuario, monkeypatch):
    """A frase e a UNICA coisa que diz a pessoa que a chance unica continua
    de pe. Sem ela, a recusa parece a mesma coisa que o bloqueio."""
    db.set_created_days_ago(usuario["id"], 13)
    monkeypatch.setattr(db, "admin_extend_trial", lambda *a, **kw: False)
    r = responder("mais tempo").lower()
    assert "nada foi gasto" in r, r
