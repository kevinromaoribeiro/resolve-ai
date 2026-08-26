# -*- coding: utf-8 -*-
"""M2.5 itens 2 e 3 — submeter template por API, e o resumo de gastos.

O que estes testes protegem:

  ITEM 2. O submissor mexe com a Meta, que ja restringiu este numero DUAS
  vezes. Entao ele e seco por padrao, diz qual variavel de ambiente falta em
  vez de estourar em KeyError, e rodar duas vezes nao pode virar erro fatal —
  "ja existe" e o resultado NORMAL da segunda execucao.

  ITEM 3. O resumo de segunda so faz sentido pra quem tem o que resumir.
  Mensagem semanal que chega vazia e o jeito mais rapido de ensinar a pessoa
  a ignorar o bot — e depois disso os lembretes tambem passam batido.
"""
from datetime import date, timedelta

import pytest

import canal
import db
import scheduler
import templates
import tempo
import wa_bot
from templates import submeter


# ---------------------------------------------------------------------------
# ITEM 2 — submissao por API
# ---------------------------------------------------------------------------
class _Resposta:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def meta_configurada(monkeypatch):
    monkeypatch.setattr(submeter, "WABA_ID", "123456", raising=False)
    monkeypatch.setattr(submeter, "TOKEN", "TOKEN-DE-TESTE", raising=False)


def test_dry_run_e_o_padrao(meta_configurada, monkeypatch):
    """Seco por padrao. O passo que fala com a Meta e o que voce DIGITA."""
    chamadas = []
    monkeypatch.setattr(submeter, "_post",
                        lambda *a, **kw: chamadas.append(a) or _Resposta(
                            200, {"id": "1"}))
    saida = submeter.main([])
    assert chamadas == [], "dry-run mandou POST pra Meta"
    assert saida == 0


def test_dry_run_mostra_o_payload_de_todos(meta_configurada, capsys):
    submeter.main([])
    texto = capsys.readouterr().out
    for nome in templates.CATALOGO:
        assert nome in texto, f"{nome} nao apareceu no dry-run"
    assert "UTILITY" in texto


def test_falta_de_credencial_diz_qual(monkeypatch, capsys):
    monkeypatch.setattr(submeter, "WABA_ID", "", raising=False)
    monkeypatch.setattr(submeter, "TOKEN", "", raising=False)
    saida = submeter.main(["--enviar"])
    texto = capsys.readouterr().out
    assert saida != 0
    assert "META_WABA_ID" in texto and "META_TOKEN" in texto


def test_sem_credencial_nao_tenta_falar_com_a_meta(monkeypatch):
    monkeypatch.setattr(submeter, "WABA_ID", "", raising=False)
    monkeypatch.setattr(submeter, "TOKEN", "", raising=False)
    chamadas = []
    monkeypatch.setattr(submeter, "_post",
                        lambda *a, **kw: chamadas.append(a) or _Resposta(
                            200, {"id": "1"}))
    submeter.main(["--enviar"])
    assert chamadas == []


def test_envio_manda_um_post_por_template(meta_configurada, monkeypatch):
    chamadas = []

    def _post(url, payload):
        chamadas.append((url, payload))
        return _Resposta(200, {"id": "999", "status": "PENDING"})

    monkeypatch.setattr(submeter, "_post", _post)
    assert submeter.main(["--enviar"]) == 0
    assert len(chamadas) == len(templates.CATALOGO)
    for url, payload in chamadas:
        assert "123456/message_templates" in url
        assert payload["language"] == "pt_BR"
        assert payload["category"] in ("UTILITY", "MARKETING")
        corpo = [c for c in payload["components"] if c["type"] == "BODY"][0]
        assert corpo["text"]


def test_template_que_ja_existe_nao_e_erro_fatal(meta_configurada,
                                                 monkeypatch, capsys):
    """Rodar duas vezes e o caso NORMAL: voce submete, a Meta reprova um,
    voce corrige aquele e roda de novo. Se o primeiro 'ja existe' aborta o
    lote, os outros nunca sobem."""
    def _post(url, payload):
        return _Resposta(400, {"error": {
            "message": "Template name already exists",
            "code": 100, "error_subcode": 2388023}})

    monkeypatch.setattr(submeter, "_post", _post)
    saida = submeter.main(["--enviar"])
    texto = capsys.readouterr().out
    assert saida == 0, "template repetido derrubou o lote inteiro"
    assert texto.lower().count("já existe") >= len(templates.CATALOGO)


def test_erro_de_verdade_aparece_e_conta(meta_configurada, monkeypatch,
                                         capsys):
    def _post(url, payload):
        return _Resposta(400, {"error": {"message": "Invalid parameter",
                                         "code": 100}})

    monkeypatch.setattr(submeter, "_post", _post)
    saida = submeter.main(["--enviar"])
    texto = capsys.readouterr().out
    assert saida != 0, "erro real saiu com codigo de sucesso"
    assert "Invalid parameter" in texto


def test_o_corpo_submetido_e_o_do_catalogo(meta_configurada, monkeypatch):
    """Submeter um corpo diferente do que o codigo manda e o pior dos dois
    mundos: aprovado na Meta, recusado no envio."""
    enviados = {}

    def _post(url, payload):
        enviados[payload["name"]] = payload
        return _Resposta(200, {"id": "1"})

    monkeypatch.setattr(submeter, "_post", _post)
    submeter.main(["--enviar"])
    for nome, t in templates.CATALOGO.items():
        corpo = [c for c in enviados[nome]["components"]
                 if c["type"] == "BODY"][0]
        assert corpo["text"] == t.corpo


# ---------------------------------------------------------------------------
# ITEM 2b — todo momento do motor tem template (ou e excecao declarada)
# ---------------------------------------------------------------------------
def test_todo_kind_do_motor_tem_template_ou_excecao():
    """A falha que este teste pega e MUDA: alguem cria um kind novo, ele
    funciona lindamente dentro da janela de 24h, e some pra quem esta fora —
    que e justamente quem mais precisa ser lembrado."""
    for kind in scheduler.KINDS_PROATIVOS:
        assert (kind in templates.KIND_TEMPLATE
                or kind in templates.KINDS_SEM_TEMPLATE), (
            f"kind {kind!r} nao tem template nem esta declarado como "
            f"excecao: fora da janela ele some calado")


def test_todo_template_do_catalogo_esta_engatilhado():
    """A outra ponta: template submetido, aprovado e nunca usado e custo e
    ruido na conta da Meta."""
    usados = set(templates.KIND_TEMPLATE.values())
    assert set(templates.CATALOGO) == usados, (
        f"templates sem gatilho: {set(templates.CATALOGO) - usados}")


def test_conta_a_vencer_tem_template():
    """O aviso de vencimento e o disparo mais comum do produto. Sem template
    ele nunca saiu fora da janela — o buraco maior, e o mais silencioso."""
    assert templates.KIND_TEMPLATE["vencimento"] == "resolveai_conta_a_vencer"
    nome, variaveis = templates.para_disparo(
        {"kind": "vencimento", "user_nome": "Kevin Santos",
         "item_id": None, "quando": "20/08"})
    assert nome == "resolveai_conta_a_vencer"
    assert len(variaveis) == len(templates.CATALOGO[nome].variaveis)


# ---------------------------------------------------------------------------
# ITEM 3 — resumo de gastos de segunda
# ---------------------------------------------------------------------------
def _despesa(uid, valor, categoria="Contas", dias_atras=1, desc="conta"):
    iid = db.add_item(user_id=uid, tipo="despesa", categoria=categoria,
                      descricao=f"{desc} {valor}", valor_reais=valor,
                      data_vencimento=tempo.hoje().isoformat(),
                      status="pendente")
    quando = (tempo.agora() - timedelta(days=dias_atras)).strftime(
        "%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        conn.execute("UPDATE items SET data_criacao=? WHERE id=?",
                     (quando, iid))
    return iid


@pytest.fixture
def segunda_de_manha(monkeypatch):
    """A manha do resumo de gastos: TERCA, 9h.

    O nome ficou por continuidade, mas o dia mudou na rodada 2 da auditoria
    (P1-C): o `dia_resumo` default e segunda, e os dois digests na mesma
    manha davam duas proativas em segundos, com conteudo sobreposto, num
    numero que ja levou duas restricoes da Meta. Quem tem o resumo de
    compromissos na segunda recebe os gastos na terca. Ver `dia_de_gastos`.
    """
    import datetime as _dt
    agora = _dt.datetime(2026, 8, 18, 9, 0, 0)      # 18/08/2026 e terca
    assert agora.weekday() == 1
    monkeypatch.setattr(tempo, "agora", lambda: agora)
    monkeypatch.setattr(tempo, "hoje", lambda: agora.date())
    return agora


def test_quem_nao_tem_gasto_nao_recebe(usuario, segunda_de_manha):
    """Silencio e a resposta certa. Resumo vazio ensina a ignorar o bot."""
    assert scheduler.montar_resumo_de_gastos(usuario) is None


def test_um_lancamento_so_nao_vira_resumo(usuario, segunda_de_manha):
    _despesa(usuario["id"], 90.0)
    assert scheduler.montar_resumo_de_gastos(usuario) is None


def test_resumo_traz_total_e_categoria(usuario, segunda_de_manha):
    _despesa(usuario["id"], 120.0, "Contas", 2, "luz")
    _despesa(usuario["id"], 80.0, "Casa", 3, "faxina")
    msg = scheduler.montar_resumo_de_gastos(usuario)
    assert msg
    assert "200,00" in msg, msg
    assert "Contas" in msg and "Casa" in msg


def test_resumo_compara_com_a_semana_anterior(usuario, segunda_de_manha):
    """Numero solto nao diz se melhorou — foi o defeito do painel do dono, e
    seria o mesmo defeito aqui."""
    _despesa(usuario["id"], 100.0, "Contas", 2)
    _despesa(usuario["id"], 100.0, "Casa", 3)
    _despesa(usuario["id"], 50.0, "Contas", 9)       # semana passada
    msg = scheduler.montar_resumo_de_gastos(usuario)
    assert "semana passada" in msg.lower(), msg
    assert "50,00" in msg or "150" in msg, msg


def test_resumo_termina_com_convite_concreto(usuario, segunda_de_manha):
    _despesa(usuario["id"], 10.0, "Contas", 2)
    _despesa(usuario["id"], 20.0, "Casa", 3)
    msg = scheduler.montar_resumo_de_gastos(usuario)
    assert any(p in msg.lower() for p in ("manda", "me diz", "me mande")), msg


def test_o_convite_varia(usuario, segunda_de_manha):
    """Mesma frase toda segunda vira ruido, e ruido semanal e como a pessoa
    aprende a nao ler o bot."""
    vistos = set()
    for semana in range(len(scheduler.CONVITES_DE_USO)):
        vistos.add(scheduler.convite_de_uso(usuario["id"], semana))
    assert len(vistos) == len(scheduler.CONVITES_DE_USO)


def test_o_convite_nao_repete_na_semana_seguinte(usuario):
    for semana in range(12):
        a = scheduler.convite_de_uso(usuario["id"], semana)
        b = scheduler.convite_de_uso(usuario["id"], semana + 1)
        assert a != b, f"semana {semana} repetiu o convite"


def test_o_convite_nao_promete_pagamento(usuario):
    """O guardrail de produto vale tambem pro convite: o bot lembra, nunca
    paga. Convite que sugere 'pago pra voce' e promessa que o produto nao
    cumpre — e a que mais gera pedido de reembolso."""
    proibido = ("pago", "pagar pra", "pagamos", "transfer", "pix", "compro")
    for texto in scheduler.CONVITES_DE_USO:
        baixo = texto.lower()
        for p in proibido:
            assert p not in baixo, f"convite promete pagamento: {texto}"


def test_dispara_na_segunda_e_so_uma_vez(usuario, segunda_de_manha):
    _despesa(usuario["id"], 10.0, "Contas", 2)
    _despesa(usuario["id"], 20.0, "Casa", 3)
    primeira = scheduler.check_gastos_semanais()
    assert [d for d in primeira if d["user_id"] == usuario["id"]]
    for d in primeira:
        db.log_dispatch(d["user_id"], d["kind"])
    segunda = scheduler.check_gastos_semanais()
    assert not [d for d in segunda if d["user_id"] == usuario["id"]], \
        "mandou o resumo de gastos duas vezes no mesmo dia"


def test_nao_dispara_em_outro_dia(usuario, monkeypatch):
    import datetime as _dt
    quinta = _dt.datetime(2026, 8, 20, 9, 0, 0)
    assert quinta.weekday() == 3
    monkeypatch.setattr(tempo, "agora", lambda: quinta)
    monkeypatch.setattr(tempo, "hoje", lambda: quinta.date())
    _despesa(usuario["id"], 10.0, "Contas", 2)
    _despesa(usuario["id"], 20.0, "Casa", 3)
    assert not [d for d in scheduler.check_gastos_semanais()
                if d["user_id"] == usuario["id"]]


def test_os_dois_resumos_nunca_caem_no_mesmo_dia(usuario, monkeypatch):
    """P1-C da rodada 2: dois digests semanais na mesma manha davam duas
    proativas em segundos, com conteudo sobreposto — e o numero ja levou
    duas restricoes da Meta.

    A regra e por `dia_resumo`, e nao por "ja disparou hoje": o dedup so e
    marcado no ENVIO, entao no momento do check o resumo do dia ainda nao
    saiu. Se dependesse do dedup, seria corrida entre dois checks.
    """
    import datetime as _dt
    for escolhido in ("Segunda-feira", "Terça-feira", "Quarta-feira",
                      "Sexta-feira"):
        db.update_user_fields(usuario["id"], dia_resumo=escolhido)
        u = db.get_user(usuario["id"])
        assert (scheduler.dia_de_gastos(u)
                != scheduler.dia_resumo_weekday(escolhido)), escolhido
    # e o default (segunda) cai na terca, nao noutro canto qualquer
    db.update_user_fields(usuario["id"], dia_resumo="Segunda-feira")
    assert scheduler.dia_de_gastos(db.get_user(usuario["id"])) == 1
    assert _dt.date(2026, 8, 18).weekday() == 1


def test_o_resumo_de_gastos_e_um_por_semana(usuario, monkeypatch):
    """O cooldown semanal, no unico cenario em que ele e load-bearing.

    A primeira versao deste teste ia de TERCA pra QUARTA — e `dia_de_gastos`
    nunca devolve quarta (o loop pega o primeiro dia != `dia_resumo`, ou
    seja, so segunda ou terca). O filtro de dia cortava antes, a lista vinha
    vazia por outro motivo, e o assert passava com o cooldown E sem ele.
    Teste cego, achado pelo auditor na rodada 3.

    O cenario real e SEGUNDA -> TERCA: quem tem o resumo na terca recebe os
    gastos na segunda; se trocar o `dia_resumo` pra segunda, na terca o
    `dispatched_today` diz False e so o cooldown de 6 dias segura. Sem ele,
    dois digests em 24h no numero que ja levou duas restricoes da Meta.
    """
    import datetime as _dt
    db.update_user_fields(usuario["id"], dia_resumo="Terça-feira")
    segunda = _dt.datetime(2026, 8, 17, 9, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda: segunda)
    monkeypatch.setattr(tempo, "hoje", lambda: segunda.date())
    _despesa(usuario["id"], 10.0, "Contas", 2)
    _despesa(usuario["id"], 20.0, "Casa", 3)
    saiu = [d for d in scheduler.check_gastos_semanais()
            if d["user_id"] == usuario["id"]]
    assert saiu, "nao disparou nem no dia certo"
    for d in saiu:
        db.log_dispatch(d["user_id"], d["kind"])

    db.update_user_fields(usuario["id"], dia_resumo="Segunda-feira")
    terca = _dt.datetime(2026, 8, 18, 9, 0, 0)
    monkeypatch.setattr(tempo, "agora", lambda: terca)
    monkeypatch.setattr(tempo, "hoje", lambda: terca.date())
    assert not [d for d in scheduler.check_gastos_semanais()
                if d["user_id"] == usuario["id"]],         "dois resumos de gastos em 24h — o cooldown semanal caiu"


def test_o_motor_proativo_inclui_o_resumo_de_gastos(usuario,
                                                    segunda_de_manha):
    """Funcao construida e nao ligada e no-op — regra 5 do projeto."""
    _despesa(usuario["id"], 10.0, "Contas", 2)
    _despesa(usuario["id"], 20.0, "Casa", 3)
    out = scheduler.run_proactive_engine()
    assert "gastos_dispatches" in out
    assert [d for d in out["gastos_dispatches"]
            if d["user_id"] == usuario["id"]]


def test_fora_da_janela_o_resumo_de_gastos_nao_sai(usuario,
                                                   segunda_de_manha):
    """DECISAO INVERTIDA EM 26/08/2026, e de proposito.

    Este teste exigia que o resumo tivesse template. A Meta recusou: resumo
    semanal e AGREGADO, e agregado e mensagem sobre o produto, nao sobre um
    compromisso da pessoa — entao ela classifica como marketing. Nao da pra
    "consertar o texto" de um resumo sem ele deixar de ser resumo.

    Escolha registrada: ele vive dentro da janela de 24h. O custo e pequeno
    porque so e montado pra quem registrou 2+ despesas na semana, e quem
    esta usando o bot quase sempre falou com ele nas ultimas 24h.
    """
    _despesa(usuario["id"], 10.0, "Contas", 2)
    _despesa(usuario["id"], 20.0, "Casa", 3)
    d = [x for x in scheduler.check_gastos_semanais()
         if x["user_id"] == usuario["id"]][0]
    nome, variaveis = templates.para_disparo(d)
    assert nome is None, f"voltou a existir template de gastos: {nome}"
    assert variaveis == []


def test_o_resumo_de_gastos_fora_da_janela_e_recusado_com_motivo(usuario):
    """Trava de canal: fora da janela e sem template, nao sai — e diz por
    que. Nada de texto livre escapando pela porta nova.

    O motivo mudou de `template_nao_aprovado` pra `fora_da_janela_sem_
    template` quando o resumo saiu do catalogo (26/08/2026). O que importa
    e o mesmo: recusa explicita e registrada, nunca envio silencioso.
    """
    d = {"kind": "gastos", "user_id": usuario["id"], "user_nome": "Kevin"}
    nome, variaveis = templates.para_disparo(d)
    res = canal.falar("5511999998888", "resumo qualquer",
                      user_id=usuario["id"], template=nome,
                      variaveis=variaveis)
    assert res["enviado"] is False
    assert res["motivo"] == "fora_da_janela_sem_template", res


# ---------------------------------------------------------------------------
# ITEM 3b — a conta bate
# ---------------------------------------------------------------------------
def test_gastos_da_semana_nao_conta_o_que_esta_fora_da_janela(usuario,
                                                              segunda_de_manha):
    _despesa(usuario["id"], 100.0, "Contas", 2)
    _despesa(usuario["id"], 999.0, "Contas", 30)
    g = db.gastos_da_semana(usuario["id"])
    assert g["total"] == 100.0, g


def test_gastos_da_semana_ignora_lembrete_com_valor(usuario,
                                                    segunda_de_manha):
    """Consulta que VAI custar 300 nao e dinheiro que saiu. Mesma regra do
    `gastos_por_categoria` — se divergir, painel e WhatsApp se contradizem."""
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Saúde",
                descricao="consulta", valor_reais=300.0,
                data_vencimento=tempo.hoje().isoformat(), status="pendente")
    _despesa(usuario["id"], 40.0, "Contas", 1)
    g = db.gastos_da_semana(usuario["id"])
    assert g["total"] == 40.0, g


def test_gastos_da_semana_e_por_pessoa(usuario, segunda_de_manha):
    """Somar a base inteira no resumo de UMA pessoa seria vazamento de dado
    dos outros usuarios — e o produto vende justamente confianca."""
    outro = db.create_user(nome="Outro", telefone="5511911112222")
    _despesa(outro, 500.0, "Contas", 1)
    _despesa(usuario["id"], 30.0, "Contas", 1)
    g = db.gastos_da_semana(usuario["id"])
    assert g["total"] == 30.0, g


def test_dry_run_explicito_vence_o_enviar(meta_configurada, monkeypatch):
    """Quem digita `--dry-run` nao pode ser surpreendido por um envio."""
    chamadas = []
    monkeypatch.setattr(submeter, "_post",
                        lambda *a, **kw: chamadas.append(a) or _Resposta(
                            200, {"id": "1"}))
    submeter.main(["--enviar", "--dry-run"])
    assert chamadas == []


def test_o_inventario_de_kinds_esta_completo():
    """Mantem `KINDS_PROATIVOS` honesto varrendo o CODIGO, nao a memoria.

    Sem esta volta, a lista envelhece em silencio: alguem cria um kind novo,
    nao mexe na lista, e o teste de cobertura de template passa a atestar
    uma lista que nao descreve mais o motor. Teste que valida a si mesmo e
    exatamente o que o auditor pediu no protocolo do CLAUDE.md.
    """
    import pathlib
    import re

    raiz = pathlib.Path(__file__).resolve().parent.parent
    achados = set()
    for arquivo in ("scheduler.py", "trial_guiado.py"):
        fonte = (raiz / arquivo).read_text(encoding="utf-8")
        achados |= set(re.findall(r'"kind":\s*"([a-z0-9_\-]+)"', fonte))
        achados |= set(re.findall(r'_mk\(user,\s*"([a-z0-9_\-]+)"', fonte))
        achados |= set(re.findall(r'kind\s*=\s*"([a-z0-9_\-]+)"', fonte))
    faltando = achados - scheduler.KINDS_PROATIVOS
    assert not faltando, (
        f"kind emitido pelo motor e nao declarado em KINDS_PROATIVOS: "
        f"{sorted(faltando)}")
    assert achados, "a varredura nao achou kind nenhum — regex quebrada"


# ---------------------------------------------------------------------------
# CONSERTOS DA AUDITORIA M2.5
# ---------------------------------------------------------------------------
def _sem_espera(monkeypatch):
    """Tira os dois freios que atrapalham um teste de ROTEAMENTO.

    1. O envio espaca os disparos com `time.sleep` de 8 a 15s. O `time` e
       importado DENTRO da funcao, entao nao da pra patchar o modulo; quem
       controla o intervalo sao estas duas constantes.
    2. `DISPATCH_MAX_PER_CYCLE` e 5. O banco de teste acumula usuarios de
       todos os arquivos, entao na suite inteira os cinco primeiros lugares
       vao pros disparos deles e o que se quer medir aqui — se a chave
       chega no envio — nunca e alcancado. O freio tem testes proprios; aqui
       ele so esconderia a pergunta.
    """
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    monkeypatch.setattr(wa_bot, "DISPATCH_MAX_PER_CYCLE", 500)


@pytest.mark.parametrize("nome", sorted(templates.CATALOGO))
def test_corpo_nao_comeca_nem_termina_em_parametro(nome):
    """A Meta reprova a submissao nos dois casos.

    Cinco dos sete corpos comecavam em `{{1}}` — inclusive depois de o
    proprio repo declarar a regra num comentario e aplicar so a metade
    "termina". Reprovacao aqui custa uma rodada inteira de espera por causa
    de um "Oi" que ninguem ia reparar.
    """
    corpo = templates.CATALOGO[nome].corpo.strip()
    assert not corpo.startswith("{{"), f"{nome} comeca em parametro"
    assert not corpo.endswith("}}"), f"{nome} termina em parametro"


def test_parametros_nao_ficam_colados():
    """`{{1}} {{2}}` tambem e reprovacao certa na submissao."""
    import re as _re
    for nome, t in templates.CATALOGO.items():
        assert not _re.search(r"\}\}\s*\{\{", t.corpo), \
            f"{nome} tem parametros adjacentes"


def test_o_aviso_de_vencimento_sai_com_a_data_de_verdade(usuario):
    """FIM A FIM, do item ate o corpo renderizado.

    O P0-2 da auditoria: `check_due_items` nao punha `quando` no disparo, e
    o template caia no default "em breve" em 100% dos envios. O teste que
    existia FABRICAVA o campo — atestava o que nao verificou. Este parte do
    item no banco e le o que a pessoa receberia.
    """
    venc = tempo.hoje() + timedelta(days=1)
    db.add_item(user_id=usuario["id"], tipo="despesa", categoria="Contas",
                descricao="conta de luz", valor_reais=187.0,
                data_vencimento=venc.isoformat(), status="pendente")
    disparo = [d for d in scheduler.check_due_items(ref=tempo.hoje())
               if d["user_id"] == usuario["id"]][0]
    nome, variaveis = templates.para_disparo(disparo)
    assert nome == "resolveai_conta_a_vencer"
    corpo = templates.CATALOGO[nome].corpo
    for i, v in enumerate(variaveis, 1):
        corpo = corpo.replace("{{%d}}" % i, str(v))
    esperado = f"{venc.day:02d}/{venc.month:02d}"
    assert esperado in corpo, corpo
    assert "em breve" not in corpo, corpo


def test_o_motor_entrega_o_resumo_de_gastos_ao_envio(usuario,
                                                     segunda_de_manha,
                                                     monkeypatch):
    """DO MOTOR ATE O ENVIO, que e onde o P0-1 morava.

    `gastos_dispatches` existia, entrava no `total`, tinha template e tinha
    teste — e nunca era enviado, porque a lista de chaves do
    `dispatch_proactive` era escrita a mao e ninguem acrescentou a nova.
    Sem erro, sem log, suite verde. Este teste vai ate o `falar`.
    """
    _despesa(usuario["id"], 120.0, "Contas", 2, "luz")
    _despesa(usuario["id"], 80.0, "Casa", 3, "faxina")
    enviadas = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, txt, **kw: enviadas.append(txt)
                        or {"enviado": True, "via": "texto", "motivo": ""})
    _sem_espera(monkeypatch)
    wa_bot.dispatch_proactive()
    assert any("resumo da sua semana" in t for t in enviadas), enviadas


def test_nenhuma_lista_do_motor_fica_de_fora_do_envio(usuario,
                                                      segunda_de_manha,
                                                      monkeypatch):
    """A regra geral, e nao o caso do gastos: TODA chave `*_dispatches` que
    o motor produzir tem que chegar no envio. Sem isso, cada checagem nova
    do scheduler e uma chance de repetir o P0-1 em silencio."""
    marcador = "PROVA DE CHAVE NOVA"
    real = scheduler.run_proactive_engine

    def _com_chave_nova(*a, **kw):
        out = real(*a, **kw)
        out["experimental_dispatches"] = [{
            "user_id": usuario["id"], "user_nome": usuario["nome"],
            "telefone": usuario["telefone"], "item_id": None,
            "kind": "experimental", "message": marcador}]
        return out

    monkeypatch.setattr(wa_bot.scheduler, "run_proactive_engine",
                        _com_chave_nova)
    enviadas = []
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda tel, txt, **kw: enviadas.append(txt)
                        or {"enviado": True, "via": "texto", "motivo": ""})
    _sem_espera(monkeypatch)
    wa_bot.dispatch_proactive()
    assert marcador in enviadas, (
        "chave nova de disparo nao chegou no envio — o P0-1 voltou")


def test_gastos_ignora_categoria_que_zerou(usuario, segunda_de_manha):
    """Categoria somando zero nao e gasto, e listar 'Casa — R$ 0,00' faz o
    resumo parecer quebrado."""
    _despesa(usuario["id"], 50.0, "Contas", 2)
    _despesa(usuario["id"], 0.0, "Casa", 2)
    _despesa(usuario["id"], 30.0, "Contas", 3)
    g = db.gastos_da_semana(usuario["id"])
    assert "Casa" not in g["por_categoria"], g
    assert g["total"] == 80.0, g


def test_a_comparacao_usa_o_mesmo_filtro_dos_dois_lados(usuario,
                                                        segunda_de_manha):
    """P2-9: `total_anterior` somava categorias que o lado atual descarta
    (as que zeram), entao a comparacao tinha vies embutido sempre na mesma
    direcao — parecia queda quando nao houve queda."""
    _despesa(usuario["id"], 100.0, "Contas", 2)
    _despesa(usuario["id"], 100.0, "Contas", 9)
    _despesa(usuario["id"], 0.0, "Casa", 9)
    g = db.gastos_da_semana(usuario["id"])
    assert g["total"] == 100.0, g
    assert g["total_anterior"] == 100.0, g


def test_gastos_nao_dispara_no_dia_do_outro_resumo(usuario, monkeypatch):
    """O par do `test_os_dois_resumos_nunca_caem_no_mesmo_dia`: aquele mede a
    funcao pura, este mede o CHECK. Sem os dois, tirar o filtro de dia do
    `check_gastos_semanais` passava despercebido — a funcao continuava
    calculando o dia certo e ninguem usava a resposta."""
    import datetime as _dt
    segunda = _dt.datetime(2026, 8, 17, 9, 0, 0)     # o dia_resumo default
    monkeypatch.setattr(tempo, "agora", lambda: segunda)
    monkeypatch.setattr(tempo, "hoje", lambda: segunda.date())
    _despesa(usuario["id"], 10.0, "Contas", 2)
    _despesa(usuario["id"], 20.0, "Casa", 3)
    assert not [d for d in scheduler.check_gastos_semanais()
                if d["user_id"] == usuario["id"]], \
        "gastos disparou no mesmo dia do resumo de compromissos"


def test_a_politica_de_aviso_vale_sem_o_wa_bot(tmp_path):
    """P2-8: o `app.py` importa `scheduler` SEM importar `wa_bot`, entao a
    politica tem que ser o DEFAULT do modulo — nao um override.

    Roda em processo separado de proposito: dentro da suite o `wa_bot` ja
    foi importado pela conftest, e ai qualquer default erra pra verde.
    """
    import subprocess
    import sys
    import pathlib
    raiz = pathlib.Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, "-c",
         "import scheduler; print(sorted(scheduler.DUE_ALERT_DAYS))"],
        cwd=str(raiz), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[1]", (
        f"scheduler sozinho avisa em {r.stdout.strip()} e a producao em [1]: "
        f"o painel volta a divergir do WhatsApp")


def test_a_comparacao_ignora_categoria_negativa_dos_dois_lados(
        usuario, segunda_de_manha):
    """O filtro `> 0` so e OBSERVAVEL com valor negativo (estorno, correcao
    de lancamento). Sem este caso, o teste anterior passava com o filtro e
    sem ele — atestava o que nao verificava."""
    _despesa(usuario["id"], 100.0, "Contas", 2)
    _despesa(usuario["id"], 100.0, "Contas", 9)
    _despesa(usuario["id"], -40.0, "Casa", 9)
    g = db.gastos_da_semana(usuario["id"])
    assert g["total"] == 100.0, g
    assert g["total_anterior"] == 100.0, (
        f"a semana anterior somou a categoria negativa que o lado atual "
        f"descarta — comparacao com vies embutido: {g}")


def test_cooldown_do_gastos_conta_semana_e_nao_24h(usuario, monkeypatch):
    """O cooldown de 6 dias precisa valer por SEMANA, nao por 24h.

    Mutacao que sobreviveu ao teste anterior: `GASTOS_COOLDOWN_DIAS = 1`.
    Ele passava porque os dois envios caiam com a mesma hora (9h de segunda
    e 9h de terca), e ai 24h contadas pra tras ainda alcancam o primeiro.
    Mas o cron roda a cada 5-15 min dentro da janela das 8h as 12h — basta o
    primeiro disparo sair 8h30 e a checagem do dia seguinte rodar 9h pra
    24h nao alcancarem mais nada, e a pessoa levar dois resumos de gastos em
    menos de um dia.
    """
    import datetime as _dt
    db.update_user_fields(usuario["id"], dia_resumo="Terça-feira")
    segunda = _dt.datetime(2026, 8, 17, 8, 30, 0)
    monkeypatch.setattr(tempo, "agora", lambda: segunda)
    monkeypatch.setattr(tempo, "hoje", lambda: segunda.date())
    _despesa(usuario["id"], 10.0, "Contas", 2)
    _despesa(usuario["id"], 20.0, "Casa", 3)
    saiu = [d for d in scheduler.check_gastos_semanais()
            if d["user_id"] == usuario["id"]]
    assert saiu
    for d in saiu:
        db.log_dispatch(d["user_id"], d["kind"])

    db.update_user_fields(usuario["id"], dia_resumo="Segunda-feira")
    terca = _dt.datetime(2026, 8, 18, 11, 45, 0)
    monkeypatch.setattr(tempo, "agora", lambda: terca)
    monkeypatch.setattr(tempo, "hoje", lambda: terca.date())
    assert not [d for d in scheduler.check_gastos_semanais()
                if d["user_id"] == usuario["id"]], \
        "dois resumos de gastos em 27h — o cooldown esta contando 24h"
