# -*- coding: utf-8 -*-
"""AUDITORIA DO M3.5 — um teste por defeito encontrado.

Contexto: o M3.5 entregou tres coisas novas (codigo de pagamento colavel,
foto de documento que nao e boleto, e a oferta de remarcar servico que
repete). O auditor achou oito defeitos, e SETE deles estavam no codigo
escrito NAQUELA rodada, nao no codigo antigo. E o padrao desta base inteira:
o defeito grave mora na correcao mais nova.

Cada teste aqui prova UM achado. Se algum voltar, o vermelho aponta pro
mesmo lugar — e a mensagem de assert diz o que a pessoa do outro lado
sentiria, nao o que a funcao devolveu.
"""
import datetime as _dt

import pytest

import boleto
import botoes
import db
import documento
import scheduler
import tempo
import wa_bot
from conftest import TELEFONE, responder


def _foto(monkeypatch, texto, msg_id="IMGDOC", legenda=""):
    """Manda uma foto pelo caminho de producao, com o OCR ja decidido."""
    import canal
    monkeypatch.setattr(canal, "baixar_midia", lambda **kw: "b64")
    monkeypatch.setattr(wa_bot, "_read_image", lambda b64: texto)
    payload = {"data": {"key": {"remoteJid": f"{TELEFONE}@s.whatsapp.net",
                                "fromMe": False, "id": msg_id},
                        "pushName": "Kevin",
                        "message": {"imageMessage": {"caption": legenda}}}}
    return wa_bot.handle_incoming(payload) or {}


NOTA = """NOTA FISCAL ELETRONICA - DANFE
Geladeira Brastemp Frost Free 375L
CNPJ 12.345.678/0001-90
Data de emissao 12/03/2026
Valor total R$ 3.499,00"""

CNH = """REPUBLICA FEDERATIVA DO BRASIL
CARTEIRA NACIONAL DE HABILITACAO
JOAO DA SILVA
Registro 01234567890
Validade 12/03/2027
Primeira Habilitacao 05/01/2010"""


# ---------------------------------------------------------------------------
# P1-3 — o item nascia VENCIDO
# ---------------------------------------------------------------------------

def test_nota_fiscal_nao_nasce_vencida():
    """A ancora da nota e a EMISSAO, que e sempre passado.

    Gravando ela em `data_vencimento`, a pessoa mandava a foto da nota de
    ontem, confirmava, e no ciclo seguinte levava "isso venceu e eu nao vi a
    baixa". Cobrar alguem por algo que acabou de nascer e o defeito que mais
    rapido ensina a desinstalar.
    """
    doc = documento.reconhecer(NOTA)
    assert doc["data"] == "2026-03-12", doc      # emissao, lida certo
    venc = documento.vencimento(doc)
    assert venc == "2027-03-12", (
        "a garantia de 1 ano tem que contar A PARTIR da emissao; %r" % venc)


def test_cnh_mantem_a_validade_que_esta_no_papel():
    """Nem todo tipo converte: na CNH a ancora JA e a data que importa."""
    doc = documento.reconhecer(CNH)
    assert documento.vencimento(doc) == "2027-03-12"


def test_promessa_nunca_promete_mais_do_que_o_motor_faz():
    """A outra metade do P1-3: o texto dizia "60 e 30 dias antes" e o motor
    so sabia avisar na vespera. Agora a frase e GERADA da mesma tabela que
    vai pro banco — nao tem como as duas divergirem de novo."""
    for tipo in ("nota_fiscal", "documento", "receita", "vacina"):
        frase = documento.promessa(tipo)
        for dia in documento.avisos(tipo):
            # D-1 e dito em portugues ("na vespera"), nao como numero.
            marca = "véspera" if dia == 1 else str(dia)
            assert marca in frase, (
                "%s avisa em D-%d e a promessa nao diz: %r"
                % (tipo, dia, frase))


def test_documento_guarda_a_antecedencia_junto_com_o_item(usuario, monkeypatch):
    """De ponta a ponta: foto -> proposta -> Confirmar -> item no banco."""
    r = _foto(monkeypatch, CNH)
    assert "CNH" in r.get("text", ""), r
    assert r.get("botoes") == ["Confirmar", "Ajustar", "Esquece"], r

    responder("Confirmar")
    itens = db.list_items(usuario["id"], status="pendente")
    assert len(itens) == 1, itens
    assert itens[0]["data_vencimento"] == "2027-03-12", itens[0]
    assert itens[0]["avisar_dias"] == "60,30", (
        "a promessa era avisar 60 e 30 dias antes; o item nao carrega isso")


def test_scheduler_honra_a_antecedencia_do_item(usuario, horario_util):
    """D-60 nao existe na politica global (`DUE_ALERT_DAYS = {1}`).

    Este teste e o que prova que a promessa VIRA MENSAGEM: sem ele, gravar
    "60,30" no banco seria enfeite. E a armadilha da janela de SQL mora aqui
    — item a 60 dias nem era LIDO, e o sintoma seria silencio, sem erro
    nenhum no log.
    """
    hoje = tempo.hoje()
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Outros",
                descricao="CNH", data_vencimento=(hoje + _dt.timedelta(days=60)
                                                  ).isoformat(),
                status="pendente", avisar_dias="60,30")
    # controle: mesmo prazo, SEM antecedencia propria -> tem que ficar quieto
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Outros",
                descricao="curso de ingles",
                data_vencimento=(hoje + _dt.timedelta(days=60)).isoformat(),
                status="pendente")

    saidas = scheduler.check_due_items(ref=hoje)
    descricoes = " ".join(d.get("message", "") for d in saidas)
    assert "CNH" in descricoes, (
        "prometeu avisar 60 dias antes e nao avisou: %r" % saidas)
    assert "ingles" not in descricoes, (
        "a antecedencia do item vazou pro resto da lista: %r" % saidas)


# ---------------------------------------------------------------------------
# P1-4 — "Ajustar" era beco sem saida
# ---------------------------------------------------------------------------

def test_ajustar_documento_escuta_a_resposta(usuario, monkeypatch):
    """A pessoa toca em Ajustar, o bot pede a data, ela responde — e antes
    NAO ACONTECIA NADA. Pedir e ignorar e pior que nao ter perguntado."""
    _foto(monkeypatch, CNH)
    r = responder("Ajustar")
    assert "vence" in r.lower(), r

    r = responder("vence 15/06/2028")
    assert "15/06/2028" in r, r
    itens = db.list_items(usuario["id"], status="pendente")
    assert len(itens) == 1, itens
    assert itens[0]["data_vencimento"] == "2028-06-15", itens[0]
    assert itens[0]["avisar_dias"] == "60,30", (
        "o ajuste perdeu a antecedencia que a proposta prometia")


def test_ajuste_com_data_relativa(usuario, monkeypatch, horario_util):
    """"daqui a 3 meses" e resposta de gente.

    A data esperada e ESCRITA A MAO. A primeira versao calculava o esperado
    com `wa_bot._somar_meses`, a propria funcao sob teste — tautologia que
    ficaria verde mesmo com a aritmetica errada (auditoria M3.6, item J).
    Relogio congelado em terca, 18/08/2026.
    """
    _foto(monkeypatch, CNH)
    responder("Ajustar")
    r = responder("daqui a 3 meses")
    itens = db.list_items(usuario["id"], status="pendente")
    assert itens and itens[0]["data_vencimento"] == "2026-11-18", (r, itens)


def test_ajuste_que_o_python_nao_entende_nao_prende_a_conversa(
        usuario, monkeypatch):
    """Se a frase nao e data, a mensagem SEGUE pro motor normal.

    A primeira versao so olhava `PENDING` e ficava VERDE com o wa_bot
    inteiro revertido — o codigo velho tambem esvaziava o PENDING, por outro
    caminho (auditoria M3.6, item J: placebo provado). Agora ela mede as
    duas coisas que importam: nenhum item fantasma nasceu, e o motor normal
    recebeu a frase.
    """
    _foto(monkeypatch, CNH)
    responder("Ajustar")

    chegou = {}

    def _viu(*a, **k):
        chegou["sim"] = True
        return None            # motor mudo: o wa_bot trata None sozinho

    monkeypatch.setattr(wa_bot.motor_v8, "route", _viu, raising=False)
    responder("quanto eu gastei esse mes?")

    assert wa_bot.PENDING.get(TELEFONE) is None, (
        "a conversa ficou presa esperando uma data que nao vem")
    assert chegou.get("sim"), "a mensagem nao chegou ao motor normal"
    # O motor normal pode registrar o que quiser com a frase — o que NAO pode
    # e a frase virar o documento que estava pendente de ajuste.
    assert not [i for i in db.list_items(usuario["id"])
                if "cnh" in (i["descricao"] or "").lower()], (
        "a frase de outro assunto virou o documento pendente")


def test_pergunta_de_ajuste_nao_sequestra_outro_assunto(usuario, monkeypatch):
    """P1-3 da auditoria M3.6, o achado mais caro deste bloco.

    Medido pelo auditor: com o ajuste pendente, "paguei a luz dia 20" virava
    a data do documento — e a baixa da luz nunca acontecia. A pessoa dizia
    que pagou e o bot guardava outra coisa.
    """
    _foto(monkeypatch, CNH)
    responder("Ajustar")
    antes = len(db.list_items(usuario["id"], status="pendente"))
    responder("paguei a luz dia 20")
    depois = db.list_items(usuario["id"], status="pendente")
    assert len(depois) == antes, (
        "a frase de outro assunto virou item de documento: %r" % depois)
    assert wa_bot.PENDING.get(TELEFONE) is None


def test_recusa_no_ajuste_nao_vira_data(usuario, monkeypatch):
    """"hoje nao precisa" e uma recusa. Ela virava um item pra HOJE."""
    _foto(monkeypatch, CNH)
    responder("Ajustar")
    r = responder("hoje não precisa")
    assert "não guardei" in r.lower(), r
    assert not db.list_items(usuario["id"], status="pendente")


def test_ajuste_guarda_o_que_a_pessoa_disse_que_e(usuario, monkeypatch):
    """O bot pede "o que e e quando vence" e usava so a data (P1-2).

    Quem toca em Ajustar esta dizendo que a leitura do OCR esta errada —
    manter a descricao dele e ignorar a correcao inteira.
    """
    _foto(monkeypatch, CNH)
    responder("Ajustar")
    responder("nao e minha CNH, e o passaporte da minha filha, vence "
              "15/06/2028")
    itens = db.list_items(usuario["id"], status="pendente")
    assert itens, "nada guardado"
    assert "passaporte" in itens[0]["descricao"].lower(), itens[0]
    assert itens[0]["data_vencimento"] == "2028-06-15", itens[0]


def test_ajuste_guarda_a_hora_quando_ela_e_dita(usuario, monkeypatch):
    """"dia 15 as 14h" guardava o dia e perdia o horario."""
    wa_bot.PENDING[TELEFONE] = {
        "tipo": "ajustar_retorno", "descricao": "dentista",
        "quando": tempo.agora()}
    responder("dentista dia 15 as 14h")
    itens = db.list_items(usuario["id"], status="pendente")
    assert itens and itens[0]["hora_alvo"] == "14:00", itens


def test_data_sem_ano_e_sempre_pra_frente():
    """"vence 12/03" dito em agosto e marco do ANO QUE VEM.

    Gravar 12/03 deste ano faria o item nascer vencido — o mesmo P1-3 que
    esta rodada esta consertando, entrando de novo por outra porta.
    """
    base = _dt.date(2026, 8, 29)
    assert wa_bot._data_do_texto("vence 12/03", base=base) == "2027-03-12"
    assert wa_bot._data_do_texto("dia 30/09", base=base) == "2026-09-30"


def test_outra_data_escuta_a_resposta(usuario, horario_util, monkeypatch):
    """O mesmo beco sem saida do lado da oferta de remarcar.

    Vai pelo caminho de producao inteiro — motor gera, envio real de
    disparo, pessoa responde — porque a versao anterior desta feature
    "passava nos testes" justamente por montar o contexto na mao.
    """
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="sobrancelha",
                      valor_reais=None, status="pendente")
    db.update_item_status(iid, "concluido")
    with db.get_conn() as c:
        c.execute("UPDATE items SET data_conclusao=? WHERE id=?",
                  ((tempo.agora() - _dt.timedelta(hours=12)
                    ).strftime("%Y-%m-%d %H:%M:%S"), iid))
    db.log_message(None, usuario["telefone"], "in", "texto", "oi")
    monkeypatch.setattr(wa_bot.wasender, "falar",
                        lambda *a, **k: {"enviado": True, "via": "botoes",
                                         "motivo": ""})
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MIN", 0.0)
    monkeypatch.setattr(wa_bot, "ENVIO_INTERVALO_MAX", 0.0)
    wa_bot.dispatch_proactive()

    r = wa_bot._handle_commands(usuario, usuario["telefone"], "Outra data")
    assert r and "data" in r.lower(), r
    r = wa_bot._handle_commands(usuario, usuario["telefone"], "dia 12/10")
    assert r and "12/10" in r, r
    novos = [i for i in db.list_items(usuario["id"], status="pendente")
             if i["descricao"] == "sobrancelha"]
    assert novos and novos[0]["data_vencimento"].endswith("-10-12"), novos


# ---------------------------------------------------------------------------
# P1-5 — pendencia de CONVERSA virando item fantasma
# ---------------------------------------------------------------------------

def test_oferta_de_remarcar_nao_vira_item_fantasma(usuario):
    """`_resgatar_pendencia` salva como lembrete o que ficou preso no
    PENDING — e esta certo pra item que a PESSOA mandou. A oferta de
    remarcar nao e isso: e o bot esperando resposta a uma pergunta DELE.
    Resgatar viraria um lembrete de unha, sem data, que ninguem pediu."""
    wa_bot.PENDING[TELEFONE] = {"tipo": "confirmar_retorno",
                                "descricao": "fazer as unhas",
                                "sugestao": {"dias": 21,
                                             "proxima": "2026-09-19"},
                                "quando": tempo.agora()}
    antes = len(db.list_items(usuario["id"]))
    resgatado = wa_bot._resgatar_pendencia(usuario, TELEFONE)
    assert resgatado == "", resgatado
    assert len(db.list_items(usuario["id"])) == antes, (
        "o bot inventou um item que a pessoa nunca pediu")
    assert wa_bot.PENDING.get(TELEFONE) is None, "a pendencia continuou presa"


# ---------------------------------------------------------------------------
# P1-7 — PIX sem CRC devolvia o OCR inteiro
# ---------------------------------------------------------------------------

def test_pix_truncado_nao_vira_codigo():
    """Sem o CRC final o payload nao e aceito pelo banco de jeito nenhum.

    Devolver "o resto do texto" arrastava nome, CPF e endereco do
    beneficiario pra mensagem da pessoa — e, pelo caminho de falha, pro log.
    """
    ocr = ("PIX COPIA E COLA\n"
           "00020126580014BR.GOV.BCB.PIX0136abc\n"
           "JOAO DA SILVA CPF 123.456.789-00\n"
           "Rua das Flores 100 Telefone 11 98888-7777")
    assert boleto.codigo_de_pagamento(ocr) is None


# ---------------------------------------------------------------------------
# P1-8 — botao explicito era ignorado
# ---------------------------------------------------------------------------

def test_botao_explicito_chega_no_envio(monkeypatch):
    """A proposta de documento JA SABE seus botoes, e `escolher` nao casa
    com essas frases novas. Sem passar a lista, a pergunta saia como texto
    puro e a pessoa tinha que DIGITAR "confirmar" — o oposto do pedido."""
    import canal
    monkeypatch.setattr(canal, "OFICIAL", True, raising=False)
    vistos = {}

    def _fake_enviar(number, texto, bts):
        vistos["botoes"] = bts
        return True

    monkeypatch.setattr(botoes, "enviar", _fake_enviar)
    ok = botoes.enviar_resposta("5511999999999", "Isso parece uma *CNH*.",
                                lambda n, t: True,
                                botoes=["Confirmar", "Ajustar", "Esquece"])
    assert ok
    assert vistos.get("botoes") == ["Confirmar", "Ajustar", "Esquece"], vistos


def test_botao_de_string_monta_o_payload_de_verdade(monkeypatch):
    """P0-1 da auditoria M3.6 — o defeito mais grave desta rodada inteira.

    Existem DUAS convencoes de botao nesta base: tupla (titulo, payload) no
    `botoes.py` e string no `meta_cloud`/`BOTOES_POR_KIND`. Quando o envio
    passou a aceitar botoes explicitos, os das features novas chegaram como
    STRING e o `for i, (titulo, _payload)` estourou ValueError — fora do try
    do httpx e fora do try do webhook. A pessoa mandava a foto do documento
    e NAO RECEBIA NADA; e como o msg_id ja estava carimbado, o reenvio da
    Meta caia no dedup e a mensagem dela sumia pra sempre.

    O teste anterior dublava justamente o `botoes.enviar` — ficava verde com
    a feature 100% morta em producao. Este chama a funcao REAL e dubla so a
    rede, que e onde o dublê pertence.
    """
    import meta_cloud
    monkeypatch.setattr(meta_cloud, "configurado", lambda: True)
    monkeypatch.setattr(meta_cloud, "PHONE_NUMBER_ID", "123", raising=False)
    monkeypatch.setattr(meta_cloud, "_HEADERS", {}, raising=False)
    enviados = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"messages": [{"id": "wamid.TESTE"}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        enviados["corpo"] = json
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "post", _fake_post)

    # string (convencao nova) e tupla (convencao antiga): as duas tem que
    # virar o mesmo payload.
    for lista in (["Confirmar", "Ajustar", "Esquece"],
                  [("Confirmar", "c"), ("Ajustar", "a"), ("Esquece", "e")]):
        enviados.clear()
        assert botoes.enviar("5511999999999", "Isso parece uma *CNH*.",
                             lista), "recusou %r" % (lista,)
        titulos = [b["reply"]["title"] for b in
                   enviados["corpo"]["interactive"]["action"]["buttons"]]
        assert titulos == ["Confirmar", "Ajustar", "Esquece"], titulos


def test_interativo_quebrado_nunca_engole_a_mensagem(monkeypatch):
    """O docstring do `enviar_resposta` promete "NUNCA deixa de enviar".

    Era mentira: qualquer excecao dentro do `enviar` subia, e a chamada esta
    FORA do try que protege o webhook. Uma linha de formatacao de botao
    virava silencio total.
    """
    import canal
    monkeypatch.setattr(canal, "OFICIAL", True, raising=False)

    def _explode(*a, **k):
        raise RuntimeError("formato de botao mudou")

    monkeypatch.setattr(botoes, "enviar", _explode)
    caiu = {}
    ok = botoes.enviar_resposta("5511999999999", "Isso parece uma *CNH*.",
                                lambda n, t: caiu.setdefault("texto", True),
                                botoes=["Confirmar"])
    assert ok and caiu.get("texto"), "a pessoa ficaria sem resposta nenhuma"


def test_botao_explicito_cai_pra_texto_quando_o_corpo_e_gigante(monkeypatch):
    """Interativo com corpo acima do limite a Meta engole SEM AVISAR — a
    pessoa fica sem resposta nenhuma. Texto puro chega."""
    import canal
    monkeypatch.setattr(canal, "OFICIAL", True, raising=False)
    monkeypatch.setattr(botoes, "enviar",
                        lambda *a: pytest.fail("mandou interativo gigante"))
    caiu = {}
    ok = botoes.enviar_resposta(
        "5511999999999", "x" * (botoes.MAX_CORPO + 1),
        lambda n, t: caiu.setdefault("texto", True),
        botoes=["Confirmar"])
    assert ok and caiu.get("texto"), "nao caiu pro texto puro"


# ---------------------------------------------------------------------------
# P2 — codigo de pagamento velho sobrevivendo a virada do mes
# ---------------------------------------------------------------------------

def test_codigo_de_pagamento_morre_na_virada(usuario):
    """Cada boleto tem o seu: o de setembro nao paga outubro.

    Mantendo a coluna, o aviso do mes seguinte sairia com o codigo do mes
    passado — e ou o banco recusa (e ela para de confiar na mensagem) ou ela
    paga de novo o que ja pagou.
    """
    hoje = tempo.hoje()
    item_id = db.add_item(
        user_id=usuario["id"], tipo="despesa", categoria="Contas",
        descricao="conta de luz", valor_reais=187.45,
        data_vencimento=(hoje - _dt.timedelta(days=1)).isoformat(),
        status="pendente", recorrencia="mensal:20",
        codigo_pagamento="34191790010104351004791020150008291070026000",
        codigo_tipo="boleto")

    scheduler.roll_recurring(ref=hoje)
    item = db.get_item(item_id)
    assert item["data_vencimento"] > hoje.isoformat(), item
    assert not item["codigo_pagamento"], (
        "o codigo do mes passado ficou no item do mes que vem")
    assert not item["codigo_tipo"], item


# ---------------------------------------------------------------------------
# AUDITORIA M3.6 — o que a correcao do M3.5 quebrou
# ---------------------------------------------------------------------------

def test_data_que_nao_existe_no_calendario_nao_entra_no_banco():
    """P0-2: "31/09" passa em `1<=dia<=31` e nao existe.

    O item entrava, e dai em diante `date(y, m, d)` estourava DENTRO do motor
    proativo — derrubando o ciclo de TODO MUNDO, todo dia, ate alguem apagar
    a linha na mao. E CNH e vacina eram justamente os tipos que escapavam da
    validacao, porque o prazo deles e zero.
    """
    doc = documento.reconhecer("CNH\nValidade 31/09/2026")
    assert doc is not None
    assert doc["data"] is None, (
        "aceitou 31 de setembro: %r" % doc["data"])
    assert documento.vencimento(doc) is None


def test_uma_data_podre_no_banco_nao_derruba_o_ciclo(usuario, horario_util):
    """A segunda camada da mesma defesa.

    O `check_overdue` foi blindado contra isso na v23.4 e o `check_due_items`
    ficou de fora — a janela era de 1 dia e o problema quase nunca chegava
    la. Com a janela em 90 dias ele chega. Item ruim e pulado; o resto do
    ciclo tem que sair.
    """
    hoje = tempo.hoje()
    bom = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="dentista",
                      data_vencimento=(hoje + _dt.timedelta(days=1)
                                       ).isoformat(), status="pendente")
    assert bom
    ruim = db.add_item(user_id=usuario["id"], tipo="lembrete",
                       categoria="Outros", descricao="CNH",
                       data_vencimento=(hoje + _dt.timedelta(days=30)
                                        ).isoformat(), status="pendente")
    with db.get_conn() as c:      # so o SQL crava data invalida
        c.execute("UPDATE items SET data_vencimento='2026-09-31' WHERE id=?",
                  (ruim,))

    saidas = scheduler.check_due_items(ref=hoje)
    descricoes = " ".join(d.get("message", "") for d in saidas)
    assert "dentista" in descricoes, (
        "uma linha ruim calou o ciclo inteiro: %r" % saidas)


def test_avisar_dias_soma_com_a_vespera_em_vez_de_apagar(usuario,
                                                         horario_util):
    """P1-1: com `or`, "60,30" APAGAVA o D-1.

    A CNH era avisada 60 e 30 dias antes e ficava MUDA na vespera — e a nota
    fiscal, que so tem D-30, perdia a vespera de vez. A vespera e a rede de
    baixo de todo item com data; nada que a gente some pode tirar ela.
    """
    hoje = tempo.hoje()
    db.add_item(user_id=usuario["id"], tipo="lembrete", categoria="Outros",
                descricao="CNH",
                data_vencimento=(hoje + _dt.timedelta(days=1)).isoformat(),
                status="pendente", avisar_dias="60,30")
    saidas = scheduler.check_due_items(ref=hoje)
    assert any("CNH" in d.get("message", "") for d in saidas), (
        "a antecedencia propria apagou o aviso de vespera: %r" % saidas)


def test_promessa_nao_promete_antecedencia_que_nao_cabe():
    """A CNH que vence em 20 dias nao tem como receber o aviso de D-60.

    A confirmacao prometia os dois assim mesmo. A pessoa ouviria UMA
    mensagem, no dia, e concluiria — com razao — que o bot fala o que nao
    cumpre.
    """
    doc = documento.reconhecer("CNH\nValidade 18/09/2026")
    p = documento.pergunta_de_confirmacao(doc, hoje=_dt.date(2026, 8, 29))
    assert "60" not in p["texto"], p["texto"]
    assert "véspera" in p["texto"], p["texto"]


def test_nota_de_servico_nao_ganha_garantia_de_produto():
    """P1-5: conserto de vazamento e nota fiscal de verdade, com DANFE.

    Garantia de 1 ano nao existe ali. Antes do M3.6 esses itens nasciam
    vencidos (mortos); depois viraram itens REAIS que iam disparar daqui a
    onze meses, com o bot afirmando um fato que nao e verdade.
    """
    assert documento.reconhecer(
        "NOTA FISCAL ELETRONICA DANFE\nServicos prestados\n"
        "Conserto de vazamento na pia\nData de emissao 12/03/2026") is None


def test_cupom_de_mercado_nao_vira_documento_que_vence():
    """Toda ida ao supermercado viraria um lembrete de garantia."""
    assert documento.reconhecer(
        "CUPOM FISCAL\nSUPERMERCADO BOM PRECO LTDA\n"
        "CNPJ 12.345.678/0001-90\nData 29/08/2026") is None


def test_receita_nao_perde_o_nome_do_remedio():
    """P2-1: "USO CONTINUO" e marca do tipo E aparece na linha do remedio.

    Descartar a linha inteira tirava o unico dado que a pessoa procura na
    lista. A linha so cai fora quando a marca E a linha.
    """
    doc = documento.reconhecer(
        "RECEITUARIO\nCRM 12345\nLosartana 50mg - uso continuo\n"
        "Tomar 1 comprimido ao dia\nData 01/08/2026")
    assert "losartana" in doc["descricao"].lower(), doc["descricao"]
    # e sem estragar o que o OCR leu certo: `.title()` fazia "50mg" -> "50Mg"
    assert "50mg" in doc["descricao"], doc["descricao"]


def test_avisar_dias_invalido_nao_derruba_o_add_item(usuario):
    """P2-2: o ramo de erro chamava `logging` sem importar.

    Codigo novo que nunca tinha rodado — e ele derrubava o `add_item`
    inteiro, ou seja, a pessoa perderia o item.
    """
    for lixo in ("-5", "abc", "999", "", "90,91", "0"):
        iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                          categoria="Outros", descricao="teste %s" % lixo,
                          status="pendente", avisar_dias=lixo)
        assert iid, lixo


def test_dias_de_aviso_le_sqlite_row(usuario):
    """P2-3: `sqlite3.Row` nao tem `.get`, e o item perdia 60/30 CALADO."""
    iid = db.add_item(user_id=usuario["id"], tipo="lembrete",
                      categoria="Outros", descricao="CNH",
                      data_vencimento="2027-03-12", status="pendente",
                      avisar_dias="60,30")
    with db.get_conn() as c:
        row = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
    assert db.dias_de_aviso(row) == {60, 30}, db.dias_de_aviso(row)


def test_pix_truncado_nao_apaga_o_boleto_da_mesma_foto():
    """P2-6: o `return None` do PIX cortava a busca de boleto logo abaixo."""
    ocr = ("PIX COPIA E COLA\n"
           "00020126580014BR.GOV.BCB.PIX0136abc\n"
           "34191.79001 01043.510047 91020.150008 2 91070026000")
    achado = boleto.codigo_de_pagamento(ocr)
    assert achado and achado["tipo"] == "boleto", achado


# ---------------------------------------------------------------------------
# AUDITORIA M3.7 — o que a correcao do M3.6 deixou passar
# ---------------------------------------------------------------------------

def test_fim_do_mes_e_o_ultimo_dia():
    """"fim do mes que vem" caia no ramo generico e devolvia o dia 29.

    Data errada com cara de certa — o pior tipo, porque ninguem confere.
    """
    base = _dt.date(2026, 8, 29)
    assert wa_bot._data_do_texto("fim do mes que vem", base=base) == "2026-09-30"
    assert wa_bot._data_do_texto("no fim do mes", base=base) == "2026-08-31"
    assert wa_bot._data_do_texto("final do mes", base=base) == "2026-08-31"


def test_ano_absurdo_nao_vira_item():
    """"12/03/2126" e dedo escorregando, nao lembrete pra daqui a 100 anos.

    Item com data absurda nunca dispara e fica na lista pra sempre. Mesma
    janela de sanidade que o `boleto.py` tem desde o M2.1.
    """
    base = _dt.date(2026, 8, 29)
    assert wa_bot._data_do_texto("12/03/2126", base=base) is None
    assert wa_bot._data_do_texto("12/03/1990", base=base) is None
    assert wa_bot._data_do_texto("12/03/2028", base=base) == "2028-03-12"


def test_hora_truncada_nao_vira_alarme():
    """"9:5" virava 09:00 — hora inventada a partir de numero cortado.

    `hora_alvo` e o que dispara o alarme: mensagem na hora errada ensina a
    pessoa a ignorar o alarme, que e pior que nao ter alarme.
    """
    assert wa_bot._hora_do_texto("9:5") is None
    assert wa_bot._hora_do_texto("as 9h30") == "09:30"
    assert wa_bot._hora_do_texto("14:30") == "14:30"
    assert wa_bot._hora_do_texto("15h") == "15:00"
    assert wa_bot._hora_do_texto("as 25h") is None


def test_descricao_nao_carrega_cpf_nem_telefone():
    """O `documento.py` ja tinha essa regra pro OCR; aqui vale igual.

    A pessoa as vezes responde com o telefone na mesma frase, e o numero
    ficaria na lista dela, visivel, pra sempre.
    """
    assert wa_bot._descricao_do_texto(
        "meu telefone 11 98888-7777, vence 05/09") is None
    assert wa_bot._descricao_do_texto(
        "e o cpf 123.456.789-00, vence 05/09") is None


def test_virgula_decimal_nao_parte_a_descricao():
    """"custou R$ 1,50, vence 05/09" virava a descricao "custou R$ 1"."""
    r = wa_bot._descricao_do_texto("custou R$ 1,50, vence 05/09")
    assert r == "custou R$ 1,50", repr(r)


def test_documento_usa_o_relogio_do_produto(monkeypatch):
    """`date.today()` e a hora da VPS; `tempo.hoje()` e America/Sao_Paulo.

    Entre 21h e meia-noite no Brasil os dois discordam de UM DIA numa
    maquina em UTC — e esse dia decide se o D-60 "ainda cabe" na promessa.
    """
    doc = documento.reconhecer("CNH\nValidade 05/10/2026")
    # Relogio do produto adiantado em relacao ao do servidor de proposito: com
    # `tempo.hoje()` faltam 15 dias (nenhuma antecedencia cabe -> "vespera");
    # com o relogio do servidor faltariam mais de 30 e o texto prometeria
    # "30 dias antes". E assim que este teste separa os dois.
    monkeypatch.setattr(tempo, "hoje", lambda: _dt.date(2026, 9, 20))
    p = documento.pergunta_de_confirmacao(doc)   # sem passar `hoje`
    assert "véspera" in p["texto"], (
        "usou o relogio do servidor em vez do relogio do produto: %s"
        % p["texto"])
    assert "30 dias" not in p["texto"], p["texto"]


def test_valor_com_ponto_nao_esconde_o_codigo():
    """P2 do prefixo: o descarte e por BLOCO, nunca por contagem.

    Cortar "os N ultimos digitos" devolveria um codigo de 48 digitos que
    ninguem conferiu — 44, 47 e 48 sao todos tamanhos validos. Bloco que
    nao fecha vira None, que e a resposta honesta.
    """
    linha = "34191.79001 01043.510047 91020.150008 2 91070026000"
    achado = boleto.codigo_de_pagamento("Valor do documento 187.40 " + linha)
    assert achado and achado["colavel"] == (
        "34191790010104351004791020150008291070026000"), achado
