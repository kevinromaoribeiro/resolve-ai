"""Achados da auditoria do M2.0 — 4 P0, todos de perda de dado ou apagão.

A lição do bloco: os 13 primeiros testes passavam porque o HARNESS gravava
`msg_log` com `user_id`, e a produção grava `None` (o webhook não conhece o
usuário quando loga). Teste que grava o que a produção não grava valida um
caminho que não existe. Aqui a mensagem de entrada é gravada como o webhook
grava — sem id, com o `ts` no formato do `log_message`.
"""
import datetime as _dt

import pytest

import canal
import db
import scheduler
import templates
import tempo
import trial_guiado
import wa_bot
from conftest import TELEFONE


def _entrada_como_o_webhook_grava(telefone=TELEFONE, texto="oi"):
    """wa_bot.py, rota do webhook: db.log_message(None, num, "in", ...)."""
    db.log_message(None, telefone, "in", "texto", texto)


# --- P0-1: janela nunca abria em producao --------------------------------

def test_janela_abre_com_o_log_do_webhook(usuario):
    """A primeira versao casava so por user_id. O webhook grava user_id=None,
    entao a janela NUNCA abria: o motor proativo inteiro teria parado."""
    _entrada_como_o_webhook_grava()
    assert db.dentro_da_janela(usuario["id"], TELEFONE) is True


def test_janela_com_telefone_formatado(usuario):
    db.log_message(None, "+55 (11) 98888-7777", "in", "texto", "oi")
    assert db.dentro_da_janela(usuario["id"], TELEFONE) is True


def test_falar_manda_texto_livre_depois_do_webhook(usuario, limpo):
    _entrada_como_o_webhook_grava()
    r = canal.falar(TELEFONE, "chegou a hora: dentista",
                    user_id=usuario["id"])
    assert r["enviado"] is True and r["via"] == "texto", r


# --- P0-2: formato do ts esticava a janela ate ~48h ----------------------

@pytest.mark.parametrize("horas", [25, 30, 36, 47])
def test_mensagem_velha_nao_abre_janela(usuario, horas):
    """log_message grava com 'T' (isoformat) e o corte usava espaco. Em
    comparacao de string 'T' > ' ', entao tudo do mesmo dia-calendario
    passava — a janela de 24h virava quase 48h."""
    _entrada_como_o_webhook_grava()
    velho = (tempo.agora() - _dt.timedelta(hours=horas)).isoformat(
        timespec="seconds")
    with db.get_conn() as conn:
        conn.execute("UPDATE msg_log SET ts=? WHERE direcao='in'", (velho,))
    assert db.dentro_da_janela(usuario["id"], TELEFONE) is False, (
        f"mensagem de {horas}h atras manteve a janela aberta")


def test_mensagem_de_23h_ainda_vale(usuario):
    _entrada_como_o_webhook_grava()
    quase = (tempo.agora() - _dt.timedelta(hours=23)).isoformat(
        timespec="seconds")
    with db.get_conn() as conn:
        conn.execute("UPDATE msg_log SET ts=? WHERE direcao='in'", (quase,))
    assert db.dentro_da_janela(usuario["id"], TELEFONE) is True


# --- P0-3: irmaos do grupo de vencidos -----------------------------------

def test_vencidos_agrupados_nao_somem_quando_nao_envia(usuario, monkeypatch):
    """check_overdue emite 1 disparo com texto e N-1 so de dedup. Se o
    cabeca nao sai, os irmaos NAO podem ser carimbados — eles nunca mais
    voltariam (dispatched_ever_item)."""
    uid = usuario["id"]
    ontem = (tempo.hoje() - _dt.timedelta(days=1)).isoformat()
    ids = [db.add_item(user_id=uid, tipo="despesa", categoria="Contas",
                       descricao=nome, data_vencimento=ontem,
                       status="pendente")
           for nome in ("conta de luz", "conta de agua", "IPVA")]

    disparos = scheduler.check_overdue()
    monkeypatch.setattr(scheduler, "run_proactive_engine", lambda: {
        "overdue_dispatches": disparos, "due_dispatches": [],
        "churn_dispatches": []})
    monkeypatch.setattr(canal, "_aprovados", lambda: set())   # nada sai

    wa_bot.dispatch_proactive()

    for item_id in ids:
        assert db.dispatch_count_item("vencido", item_id) == 0, (
            f"item {item_id} foi marcado como avisado sem nada ter saido")
    assert len(scheduler.check_overdue()) == len(disparos), (
        "os itens irmaos sumiram do proximo ciclo")


# --- P0-4: nudge do trial queimado sem enviar ----------------------------

def test_nudge_nao_e_queimado_sem_envio(usuario, monkeypatch):
    """d6_fim e a UNICA mensagem de conversao do trial, com o link de
    pagamento. Marcar na geracao apagava o pitch de todo mundo."""
    uid = usuario["id"]
    d = trial_guiado._mk({"id": uid, "nome": "Kevin", "telefone": TELEFONE},
                         "trial_d6", "mensagem de fechamento", nudge="d6_fim")
    monkeypatch.setattr(scheduler, "run_proactive_engine", lambda: {
        "trial_dispatches": [d], "due_dispatches": [], "churn_dispatches": []})
    monkeypatch.setattr(canal, "_aprovados", lambda: set())

    wa_bot.dispatch_proactive()

    assert not db.nudge_already_sent(db.get_user(uid), "d6_fim"), (
        "o nudge foi marcado como enviado sem ter saido")


def test_nudge_e_marcado_quando_envia(usuario, monkeypatch):
    uid = usuario["id"]
    _entrada_como_o_webhook_grava()          # dentro da janela: texto livre
    d = trial_guiado._mk({"id": uid, "nome": "Kevin", "telefone": TELEFONE},
                         "trial_d6", "mensagem de fechamento", nudge="d6_fim")
    monkeypatch.setattr(scheduler, "run_proactive_engine", lambda: {
        "trial_dispatches": [d], "due_dispatches": [], "churn_dispatches": []})

    wa_bot.dispatch_proactive()

    assert db.nudge_already_sent(db.get_user(uid), "d6_fim"), (
        "enviou e nao marcou — a pessoa recebe de novo no proximo ciclo")


def test_geracao_nao_marca_mais_nada(usuario, monkeypatch):
    """A regra: quem marca e quem envia. A geracao nao pode tocar no dedup."""
    import inspect
    fonte = inspect.getsource(trial_guiado.run_trial_nudges)
    assert "mark_nudge_sent" not in fonte, (
        "run_trial_nudges voltou a marcar nudge na geracao")


# --- P1-5: a demo de 90s ------------------------------------------------

def test_demo_nao_e_marcada_quando_nao_envia(usuario, monkeypatch):
    """A amostra de 90s e o 'aha' do produto. Marcada sem enviar, ela
    sumia no minuto 2 e nunca voltava."""
    import jornada
    uid = usuario["id"]
    monkeypatch.setattr(jornada, "demos_prontas", lambda: [
        {"user_id": uid, "descricao": "pagar o condominio", "quando": ""}])
    marcadas = []
    monkeypatch.setattr(jornada, "marcar_demo_enviada",
                        lambda u: marcadas.append(u))
    monkeypatch.setattr(jornada, "texto_demo", lambda *a, **kw: "olha eu aqui")
    monkeypatch.setattr(scheduler, "run_proactive_engine", lambda: {
        "due_dispatches": [], "churn_dispatches": []})
    monkeypatch.setattr(canal, "send_text", lambda *a, **kw: False)
    # A janela precisa estar ABERTA, senao o `falar` recusa antes e o teste
    # passa pelo motivo errado — sem exercitar a falha de ENVIO, que e o que
    # o nome dele promete (auditoria M2.0 rodada 2, P2-7).
    _entrada_como_o_webhook_grava()

    wa_bot.dispatch_proactive()

    assert not marcadas, "marcou a demo como enviada sem ela ter saido"


def test_demo_marcada_quando_envia(usuario, monkeypatch):
    import jornada
    uid = usuario["id"]
    monkeypatch.setattr(jornada, "demos_prontas", lambda: [
        {"user_id": uid, "descricao": "pagar o condominio", "quando": ""}])
    marcadas = []
    monkeypatch.setattr(jornada, "marcar_demo_enviada",
                        lambda u: marcadas.append(u))
    monkeypatch.setattr(jornada, "texto_demo", lambda *a, **kw: "olha eu aqui")
    monkeypatch.setattr(scheduler, "run_proactive_engine", lambda: {
        "due_dispatches": [], "churn_dispatches": []})
    _entrada_como_o_webhook_grava()

    wa_bot.dispatch_proactive()

    assert marcadas == [uid], "enviou a demo e nao marcou — vai repetir"


# --- P1-6: template nao pode dizer outra coisa ---------------------------

def test_alarme_atrasado_nao_usa_template_de_na_hora():
    """"Chegou a hora" cinco horas depois e mentira — foi o caso da Carol."""
    nome, _ = templates.para_disparo(
        {"kind": "hora", "variante": "atrasado", "item_id": 1})
    assert nome is None


def test_escalonamento_nao_usa_template():
    """O M1.5 promete PARAR de cobrar na terceira vez. O template de alarme
    voltaria a cobrar."""
    nome, _ = templates.para_disparo(
        {"kind": "hora", "variante": "escalonado", "item_id": 1})
    assert nome is None


def test_vencimento_futuro_nao_usa_template_de_alarme():
    """Aviso antecipado ("vence em 20/08") nao pode virar "chegou a hora".

    ATUALIZADO NO M2.5. Antes este teste exigia `None`, porque em M2.0 o
    unico template disponivel era o do alarme e usa-lo aqui seria urgencia
    falsa. O kind agora TEM template proprio — mas a invariante que importa
    e a mesma de sempre, e continua sendo cobrada abaixo: o aviso de conta a
    vencer diz a DATA, e nunca "chegou a hora".
    """
    nome, _ = templates.para_disparo(
        {"kind": "vencimento", "item_id": 1, "quando": "20/08"})
    assert nome != "resolveai_lembrete_hora", (
        "o aviso antecipado voltou a usar o template de alarme")
    corpo = templates.CATALOGO[nome].corpo.lower()
    assert "chegou a hora" not in corpo, corpo
    assert "vence" in corpo, ("o aviso antecipado precisa dizer QUANDO "
                              "vence; sem isso ele e so urgencia")


def test_sem_data_o_aviso_de_vencimento_nao_sai():
    """FAIL-CLOSED. O corpo promete "vence em X"; sem X, o template nao
    pode sair preenchendo com "em breve" — fora da janela de 24h essa e a
    UNICA mensagem que a pessoa recebe, e ela chegaria oca.

    Achado da auditoria M2.5 (P0-2): o `check_due_items` nao punha `quando`
    no disparo, e 100% dos envios sairiam "vence em *em breve*". O teste
    que existia fabricava o campo na mao — atestava o que nao verificou.
    """
    nome, variaveis = templates.para_disparo(
        {"kind": "vencimento", "item_id": 1})
    assert nome is None, f"saiu template sem data: {variaveis}"


def test_alarme_na_hora_usa_template(usuario):
    item_id = db.add_item(user_id=usuario["id"], tipo="lembrete",
                          categoria="Outros", descricao="dentista",
                          status="pendente")
    nome, variaveis = templates.para_disparo(
        {"kind": "hora", "variante": "na_hora", "item_id": item_id,
         "user_id": usuario["id"], "user_nome": "Kevin"})
    assert nome == "resolveai_lembrete_hora"
    assert variaveis == ["dentista"]


def test_scheduler_marca_a_variante(usuario):
    """A variante tem que vir do scheduler, senao para_disparo nao tem como
    saber qual dos tres textos foi montado."""
    uid = usuario["id"]
    hoje = tempo.hoje().isoformat()
    agora = tempo.agora().strftime("%H:%M")
    db.add_item(user_id=uid, tipo="lembrete", categoria="Outros",
                descricao="dentista", data_vencimento=hoje, hora_alvo=agora,
                status="pendente")
    disparos = scheduler.check_time_alarms()
    assert disparos, "nenhum alarme gerado"
    assert disparos[0].get("variante") in ("na_hora", "atrasado",
                                           "escalonado"), disparos[0]


# --- P1-7: o corpo manda responder o que o bot entende -------------------

@pytest.mark.parametrize("comando", ["ver tudo", "Ver tudo", "lista",
                                     "itens", "pendentes"])
def test_comando_da_lista_funciona(usuario, comando):
    """Os templates mandam responder "ver tudo". Antes do conserto isso
    devolvia "nao identifiquei conta, data nem valor"."""
    from conftest import responder
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="conta de luz", data_vencimento="2026-08-20",
                status="pendente")
    reply = responder(comando)
    assert "pendente" in reply.lower() and "conta de luz" in reply, (
        f"'{comando}' nao devolveu a lista: {reply!r}")


def test_instrucoes_dos_templates_existem_no_codigo():
    """Cada comando citado num corpo de template tem que funcionar.

    O `conhecidos` e DERIVADO da implementacao, nao escrito a mao: lista
    literal pega copy nova citando comando inexistente, mas nao pega alguem
    REMOVENDO o handler (auditoria M2.0 rodada 2, P2-6).
    """
    import re as _re
    citados = set()
    for t in templates.CATALOGO.values():
        # SEM PADDING dentro dos asteriscos. `*feito*` e comando; o
        # " vence em " de `*{{2}}* vence em *{{3}}*` e so o miolo entre dois
        # negritos, e sinalizava comando inexistente onde nao havia nenhum.
        # Nenhum comando de verdade tem espaco colado ao asterisco, entao o
        # filtro nao afrouxa a checagem.
        for m in _re.finditer(r"\*([a-zà-ú0-9][a-zà-ú0-9 ]{1,19})\*",
                              t.corpo.lower()):
            achado = m.group(1)
            if achado != achado.strip():
                continue
            citados.add(achado)

    conhecidos = set(wa_bot.LISTA_COMANDOS)
    conhecidos |= {p.strip() for p in wa_bot._BAIXA_RE.pattern.split("|")
                   if p.strip().isalpha()}
    conhecidos |= {"feito", "adiar", "adiar 1h"}   # baixa e adiamento
    # DERIVADO, nao escrito a mao: o template de fim de trial (MARKETING,
    # 27/08/2026) manda responder *assinar*, e o comando existe no wa_bot.
    # Se alguem renomear la, este teste reprova aqui — que e o ponto.
    conhecidos |= set(wa_bot.COMANDOS_ASSINATURA)
    desconhecidos = citados - conhecidos
    assert not desconhecidos, (
        f"template manda responder comando que ninguem implementou: "
        f"{desconhecidos}")


@pytest.mark.parametrize("status", ["bloqueado", "cancelado"])
def test_lista_respeita_os_gates_de_acesso(usuario, status):
    """AUDITORIA rodada 2 (P1-1): posto no `_handle_commands`, o comando
    rodava ANTES dos gates — usuario bloqueado pelo admin recebia a lista
    inteira, e quem cancelou recebia a lista em vez do convite de
    reativacao."""
    from conftest import responder
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="conta de luz", status="pendente")
    db.update_user_fields(usuario["id"], status=status)

    reply = responder("ver tudo")

    assert "conta de luz" not in reply, (
        f"usuario {status} recebeu a lista de itens: {reply!r}")
    if status == "cancelado":
        assert "cancelada" in reply.lower(), (
            "quem cancelou perdeu o convite de reativacao")


def test_lista_com_asterisco_do_template(usuario):
    """O corpo aprovado mostra *ver tudo*; quem copia o texto cru manda os
    asteriscos junto — e e mais provavel justamente em quem esta
    desengajado, que e o publico do reengajamento."""
    from conftest import responder
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="conta de luz", status="pendente")
    assert "conta de luz" in responder("*ver tudo*")


def test_resgate_do_painel_nao_abre_a_janela(usuario):
    """O dono escrevendo pela pessoa no painel nao pode abrir a janela: a
    Meta nao conhece o nosso msg_log, e o texto livre seria recusado."""
    db.log_message(None, TELEFONE, "in", "resgate_painel", "oi")
    assert db.dentro_da_janela(usuario["id"], TELEFONE) is False


def test_falha_de_entrega_volta_a_ser_registrada_no_dia_seguinte(usuario,
                                                                 monkeypatch):
    """Sem o dia na chave, o container que fica semanas de pe registrava a
    falha uma vez e nunca mais — e o dash matinal mostrava zero falha com o
    motor mudo."""
    uid = usuario["id"]
    item_id = db.add_item(user_id=uid, tipo="lembrete", categoria="Outros",
                          descricao="dentista", status="pendente")
    d = {"user_id": uid, "user_nome": "Kevin", "telefone": TELEFONE,
         "item_id": item_id, "kind": "hora", "variante": "na_hora",
         "message": "chegou a hora"}
    monkeypatch.setattr(scheduler, "run_proactive_engine", lambda: {
        "alarm_dispatches": [d], "due_dispatches": [], "churn_dispatches": []})
    monkeypatch.setattr(canal, "_aprovados", lambda: set())

    wa_bot.dispatch_proactive()
    wa_bot.dispatch_proactive()          # mesmo dia: nao repete

    def _falhas():
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM msg_log WHERE direcao='out_falhou'"
            ).fetchone()[0]

    assert _falhas() == 1, "registrou a mesma falha duas vezes no mesmo dia"

    ontem = (tempo.hoje() - _dt.timedelta(days=1)).isoformat()
    wa_bot.FALHA_JA_LOGADA.clear()
    wa_bot.FALHA_JA_LOGADA.add((ontem, item_id, "hora",
                                "template_nao_aprovado"))
    wa_bot.dispatch_proactive()
    assert _falhas() == 2, (
        "a falha de ontem calou a de hoje — o dash mostraria zero com o "
        "motor mudo")


# --- P2-9 e P2-10 --------------------------------------------------------

def test_variavel_sem_quebra_de_linha(usuario):
    """A Cloud API recusa parametro com \\n, tab ou 4+ espacos."""
    item_id = db.add_item(user_id=usuario["id"], tipo="lembrete",
                          categoria="Outros",
                          descricao="pagar\na conta    de   luz",
                          status="pendente")
    _, variaveis = templates.para_disparo(
        {"kind": "hora", "variante": "na_hora", "item_id": item_id})
    assert "\n" not in variaveis[0] and "    " not in variaveis[0], variaveis


def test_resumo_sem_pendente_nao_sai(usuario):
    nome, _ = templates.para_disparo(
        {"kind": "resumo", "user_id": usuario["id"], "user_nome": "Kevin"})
    assert nome is None, "resumo de 0 itens e mensagem sem servico"
