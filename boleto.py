# -*- coding: utf-8 -*-
"""
boleto.py — extração determinística de conta a partir do texto de uma imagem/PDF.

GUARDRAIL DE PRODUTO, NÃO NEGOCIÁVEL (seção 2 do CLAUDE.md):
    O bot lê o boleto, guarda e avisa antes de vencer.
    Ele NUNCA paga, compra ou transfere.

Consequência prática aqui: a **linha digitável é descartada de propósito**.
Ela não ajuda a lembrar de nada — só serve pra pagar — e guardá-la é o
primeiro passo pra alguém (usuário, LLM ou uma feature futura) imaginar que
o bot paga. O que fica é o que a promessa precisa: quanto, quando, pra quem.

DIVISÃO DE TRABALHO (regra 2): a visão lê os pixels, porque não tem outro
jeito. Quem decide se aquilo é boleto ou comprovante, se a data faz sentido
e se vira lembrete ou despesa paga é este arquivo, em Python.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

import tempo

log = logging.getLogger("resolveai")

# Janela de sanidade da data. A visão erra dígito ("2026" vira "2049") e um
# lembrete pra 2049 é lixo que a pessoa carrega pra sempre; um pra 2019 é
# alarme que dispara na hora, como vencido. Fora da janela: melhor não ter
# data do que ter data errada.
ANOS_PRA_FRENTE = 3
ANOS_PRA_TRAS = 1

# Valor máximo aceito sem desconfiar. Acima disso é quase certo erro de
# leitura (ponto/vírgula trocados) — e um lembrete de R$ 1.870.000 assusta.
VALOR_MAXIMO = 1_000_000.0

# A cauda estruturada que o prompt de visão pede. Pode não vir (modelo
# ignora, imagem ruim), e por isso o texto livre também é varrido.
_CAMPO_RE = re.compile(
    r"\b(valor|vencimento|venc|beneficiario|benefici[áa]rio|tipo)\s*=\s*"
    r"([^;\n]+)", re.I)

# R$ 1.234,56 | 1.234,56 | 89,90 | 45,00 reais | "R$ 119, 45" (OCR com espaço)
_VALOR_RE = re.compile(
    r"(?:r\$\s*)?(\d{1,3}(?:\.\d{3})+\s*,\s*\d{2}|\d+\s*,\s*\d{2})"
    r"(?:\s*reais)?", re.I)

# BOLETO TEM CINCO VALORES E O PRIMEIRO QUASE NUNCA É O CERTO.
# Medido na auditoria: "(-) Desconto 0,50 ... (=) Valor do Documento
# R$ 187,45" gravava R$ 0,50, e conta de luz com "Tarifa 0,74" gravava 0,74.
# Rótulo manda; posição só decide quando não há rótulo nenhum.
_ROTULOS_VALOR = (
    r"valor\s+cobrado", r"valor\s+pago", r"valor\s+do\s+documento",
    r"total\s+a\s+pagar", r"valor\s+a\s+pagar", r"valor\s+total",
    r"valor\s+do\s+t[íi]tulo", r"total\s+do\s+documento", r"valor",
)
# Só estes contam como sinal de que o documento é uma COBRANÇA. O `valor`
# genérico ficou de fora: ele é a palavra mais comum de qualquer contexto de
# preço ("Valor R$ 24,90 a unidade" numa etiqueta) e, valendo como sinal
# forte, desligava sozinho todos os vetos fracos. Pra EXTRAIR o número ele
# continua servindo — o que ele não pode é classificar o documento.
_ROTULOS_VALOR_FORTES = tuple(r for r in _ROTULOS_VALOR if r != r"valor")

# MESMA COISA COM DATA: boleto tem documento, processamento, vencimento e às
# vezes a parcela anterior. Pegar a primeira grava a EMISSÃO como vencimento
# — o item nasce vencido e dispara cobrança na hora (o defeito do caso
# Carol, 11/08, agora ao contrário).
_ROTULOS_DATA = (
    r"data\s+de\s+vencimento", r"vencimento", r"vence\s+em", r"vence",
    r"venc\b", r"vcto", r"pagamento\s+at[ée]", r"pagar\s+at[ée]",
    r"v[áa]lido\s+at[ée]",
)
_ROTULOS_DATA_PROIBIDOS = re.compile(
    r"(data\s+do\s+documento|emiss[ãa]o|emitid[oa]\s+em|processamento|"
    r"parcela\s+anterior|data\s+de\s+emiss[ãa]o|pago\s+em)", re.I)

_DATA_BR_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{2}|\d{4})\b")
_DATA_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MESES = ("janeiro", "fevereiro", "mar[çc]o", "abril", "maio", "junho",
          "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
_DATA_EXTENSO_RE = re.compile(
    r"\b(\d{1,2})\s+de\s+(" + "|".join(_MESES) + r")\s+de\s+(\d{4})", re.I)

# JÁ PAGO. "recibo" sozinho NÃO entra: todo boleto brasileiro tem "Recibo do
# Pagador"/"Recibo do Sacado" impresso no canhoto, e isso fazia a conta
# entrar como paga — ela sumia da lista e vencia sem aviso.
# ESTRITO. Três palavras saíram e cada uma custou uma rodada de auditoria:
#   `valor pago`  -> é NOME DE CAMPO de boleto, não marca de recibo (segue
#                    valendo em _ROTULOS_VALOR, que é onde ela serve).
#   `comprovante` -> solto casava "Comprovante de entrega" no carnê.
#   `recibo de pagamento` -> está impresso no canhoto do boleto.
_COMPROVANTE_RE = re.compile(
    r"\b(comprovante\s+de\s+(?:pagamento|pix|transfer[êe]ncia|dep[óo]sito|"
    r"agendamento)|"
    r"pagamento\s+(?:efetuado|realizado|conclu[íi]do)|"
    r"transfer[êe]ncia\s+(?:efetuada|realizada))\b", re.I)

# CARIMBO DO BANCO — a evidência mais forte de pagamento consumado que
# existe em papel no Brasil. Estava em `_COMPROVANTE_RE` como
# `autentica[çc][ãa]o\s+mec\b`, e o `\b` impedia justamente a única forma em
# que a expressão aparece: "autenticação MECÂNICA". Regex que compila, não
# casa nunca e some no meio de uma lista — o defeito que a regra 5 persegue.
_AUTENTICACAO_RE = re.compile(r"autentica[çc][ãa]o\s+mec\w*", re.I)

# Data de pagamento CONSUMADO. É o que separa "recibo de conta paga antes do
# vencimento" de "boleto a vencer" — e pagar antes do vencimento é o
# comportamento normal, não caso de borda.
# `efetuado em` SOLTO saiu: casava "cadastro efetuado em", "contrato
# efetuado em", "reajuste efetuado em" — e marcava boleto a pagar como pago,
# com a data do evento errado virando a data do item. Já está coberto por
# `pagamento efetuado em`.
_PAGO_EM_RE = re.compile(
    r"(?:pag[oa]\s+em|pagamento\s+(?:efetuado|realizado)\s+em|"
    r"data\s+do\s+pagamento|liquidado\s+em)"
    r"[^\d\n]{0,15}(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})", re.I)

# "COMPROVANTE" sozinho vale como TÍTULO do documento (cabeçalho), não como
# menção no corpo — é o que separa o extrato de caixa do "Comprovante de
# entrega" impresso no meio de um carnê.
_COMPROVANTE_CABECALHO_RE = re.compile(r"^\s*\W*comprovante\b", re.I)

# Marcas que só existem em boleto. Ganham do comprovante: um boleto que cita
# "pago em 10/08" (parcela anterior) continua sendo um boleto a pagar.
# SÓ o que não existe em comprovante. `nosso número`, `cedente`, `sacado`,
# `linha digitável` e `código de barras` SAÍRAM: o comprovante bancário de
# pagamento de título imprime todos eles, e com eles na lista o comprovante
# virava cobrança — o bot cobrando o que a pessoa acabou de pagar.
_BOLETO_FORTE_RE = re.compile(
    r"\b(ficha\s+de\s+compensa[çc][ãa]o|recibo\s+do\s+(?:pagador|sacado))\b",
    re.I)

_BOLETO_RE = re.compile(
    r"\b(boleto|fatura|conta\s+de|vencimento|vence)\b", re.I)

# NÃO É CONTA, mesmo tendo dinheiro escrito. Sem esta lista, cardápio de
# pizzaria, print de "te devo 50,00" e até Pix RECEBIDO viravam conta a
# pagar com lembrete agendado.
_NAO_E_CONTA_RE = re.compile(
    r"\b(card[áa]pio|voc[êe]\s+recebeu|pix\s+recebido|recebido\s+de|"
    r"cr[ée]dito\s+em\s+conta|saldo\s+dispon[íi]vel|"
    r"extrato\s+(?:banc[áa]rio|da\s+conta)|"
    r"[úu]ltimos\s+lan[çc]amentos|te\s+devo|me\s+paga|vaquinha|"
    r"or[çc]amento|pedido\s+entregue|pe[çc]a\s+j[áa]|"
    r"entrada\s+\d{2}:\d{2}|a\s+unidade\s+de)\b", re.I)
# VETOS FRACOS: só valem quando NÃO há marca forte de boleto. Sozinhos eles
# recusavam boleto legítimo — mensalidade de estacionamento, condomínio
# "referente a unidade 42", fatura de telefonia com "extrato detalhado de
# chamadas" e conta de luz com "promoção válida para débito automático".
_VETO_FRACO_RE = re.compile(
    r"\b(etiqueta|promo[çc][ãa]o\s+v[áa]lida|estacionamento|"
    r"a\s+unidade)\b", re.I)

# Tudo que é código de pagamento: nunca sai daqui pra dentro do banco.
_LINHA_DIGITAVEL_RE = re.compile(r"[\d.\s]{20,}")


def _num(bruto: str) -> Optional[float]:
    bruto = re.sub(r"\s+", "", bruto or "")
    try:
        v = float(bruto.replace(".", "").replace(",", "."))
    except ValueError:
        return None
    if v <= 0 or v > VALOR_MAXIMO:
        return None
    return round(v, 2)


def _valor_rotulado(texto: str, rotulos=_ROTULOS_VALOR) -> Optional[float]:
    """O valor que vem depois de um rótulo de cobrança, na ordem certa."""
    for rotulo in rotulos:
        m = re.search(rotulo + r"[^\d\n]{0,20}" + _VALOR_RE.pattern,
                      texto, re.I)
        if m:
            v = _num(m.group(1))
            if v:                       # 0,00 (desconto/juros) não conta
                return v
    return None


def _valor(texto: str) -> Optional[float]:
    """O valor COBRADO, não o primeiro número que aparecer."""
    if not texto:
        return None
    rotulado = _valor_rotulado(texto)
    if rotulado is not None:
        return rotulado
    m = _VALOR_RE.search(texto)
    return _num(m.group(1)) if m else None


def _para_date(texto: str) -> Optional[date]:
    m = _DATA_BR_RE.search(texto)
    if m:
        dia, mes, ano = m.groups()
        ano_int = int(ano) + (2000 if len(ano) == 2 else 0)
        try:
            return date(ano_int, int(mes), int(dia))
        except ValueError:
            log.warning("[boleto] data impossivel: %s/%s/%s", dia, mes, ano)
            return None
    m = _DATA_ISO_RE.search(texto)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _DATA_EXTENSO_RE.search(texto)
    if m:
        mes_nome = m.group(2).lower().replace("ç", "c")
        meses = [x.replace("[çc]", "c") for x in _MESES]
        try:
            return date(int(m.group(3)), meses.index(mes_nome) + 1,
                        int(m.group(1)))
        except (ValueError, IndexError):
            return None
    return None


def _sanidade(achada: Optional[date]) -> Optional[str]:
    if not achada:
        return None
    hoje = tempo.hoje()
    if achada.year > hoje.year + ANOS_PRA_FRENTE or \
            achada.year < hoje.year - ANOS_PRA_TRAS:
        log.warning("[boleto] data fora da janela de sanidade: %s", achada)
        return None
    return achada.isoformat()


def _quantas_datas(texto: str) -> int:
    return (len(_DATA_BR_RE.findall(texto)) + len(_DATA_ISO_RE.findall(texto))
            + len(_DATA_EXTENSO_RE.findall(texto)))


def _data(texto: str) -> Optional[str]:
    return _data_com_origem(texto)[0]


def _data_com_origem(texto: str):
    """(data ISO, veio_de_rotulo_de_vencimento).

    A ORIGEM IMPORTA e por isso é devolvida: quem decide baixa automática
    precisa saber se a data é mesmo o vencimento do título ou se é a última
    data que sobrou no texto. Um comprovante com `Data 16/08/2026` (rótulo
    neutro, comum em recibo) preenchia o campo chamado "vencimento do
    título" com a data do PAGAMENTO — e essa data virava chave pra fechar
    conta. O nome do campo mentia, e a decisão que dependia dele errava.

    O `_read_image` pede UMA frase, então na produção as quatro datas do
    boleto vêm na mesma linha: "emitido em 05/08/2026 para pagamento até
    20/08/2026". Varrer por linha não bastava; agora a busca é por rótulo
    dentro do texto todo, e o trecho de cada data PROIBIDA (emissão,
    processamento, parcela anterior, pago em) é apagado antes.
    """
    if not texto:
        return None, False
    # Apaga "<rótulo proibido> <data>" — o rótulo e a data que vem colada.
    # `[^\d\n.;,]`: a limpeza não pode ATRAVESSAR pontuação. Com vírgula
    # permitida, "parcela anterior, vencimento 10/09/2026" tinha a data do
    # VENCIMENTO apagada junto com o rótulo da parcela — o boleto ficava sem
    # data e caía no menu.
    limpo = re.sub(
        _ROTULOS_DATA_PROIBIDOS.pattern
        + r"(?:(?!vencimento|vence|venc\b|pagar\s+at|pagamento\s+at)"
          r"[^\d\n.;,]){0,20}"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}"
        r"|\d{1,2}\s+de\s+\w+\s+de\s+\d{4})",
        " ", texto, flags=re.I)

    for rotulo in _ROTULOS_DATA:
        m = re.search(rotulo + r"[^\d\n]{0,20}(.{0,30})", limpo, re.I)
        if m:
            iso = _sanidade(_para_date(m.group(1)))
            if iso:
                return iso, True

    # SEM RÓTULO DE VENCIMENTO. Com duas ou mais datas no texto, chutar a
    # primeira é o caminho mais curto pra gravar a EMISSÃO como vencimento —
    # e aí o item nasce vencido e cobra na hora. A política deste arquivo já
    # está escrita lá em cima: melhor não ter data do que ter data errada.
    # A contagem é sobre `limpo`, não sobre o texto cru: as datas proibidas
    # (emissão, processamento) já foram apagadas acima, e contá-las de novo
    # recusava boleto que ficou SEM ambiguidade nenhuma — sobrou uma data só.
    if _quantas_datas(limpo) > 1:
        log.warning("[boleto] varias datas e nenhum rotulo de vencimento — "
                    "nao chuto")
        return None, False
    return _sanidade(_para_date(limpo)), False


def _beneficiario(texto: str, campos: dict) -> Optional[str]:
    """O nome de quem cobra.

    Sem `re.I` (a primeira versão) isto era NO-OP em boleto de verdade:
    boleto imprime "Beneficiário:" capitalizado ou em caixa alta, e a regex
    exigia minúscula. Todo boleto real virava "conta a pagar" — que era o
    combustível do dedup fundir contas de empresas diferentes.
    """
    bruto = (campos.get("beneficiario") or campos.get("beneficiário") or "")
    if not bruto:
        m = re.search(
            # `para` só com dois-pontos: solto, ele casava "para pagamento
            # ate 20/08" e o nome virava "pagar dia".
            r"(?:benefici[áa]rio|cedente|favorecido|pagamento\s+para|para\s*:)"
            # A janela é CRUA (com dígitos), e o corte por rótulo vem logo
            # abaixo. Capturar só letras truncava o nome antes do rótulo
            # aparecer, e aí o corte tinha que adivinhar pelo fim do trecho —
            # foi assim que "Companhia Energetica Total" virou "Companhia
            # Energetica" e "Grupo Recibo" virou "Grupo".
            # Aceita começar com DÍGITO ("99 Tecnologia Ltda"): exigir letra
            # apagava o beneficiário inteiro desses nomes. O corte por bloco
            # de 3+ dígitos, logo abaixo, é quem protege do código de
            # pagamento — se a janela começar com código, sobra vazio e a
            # função devolve None.
            r"\s*:?\s*([A-Za-zÀ-ú\d][^\n]{2,80})", texto, re.I)
        bruto = m.group(1) if m else ""
    bruto = " ".join(str(bruto).split())
    # CORTA NO PRÓXIMO RÓTULO — mas rótulo é o que vem seguido de ":" ou de
    # número ("Vencimento 20/08", "Valor: R$"). Cortar na palavra solta
    # apagava o nome da empresa: "Total Energia S.A.", "Recibo Verde Ltda",
    # "Data Center Brasil" e "Valor Seguros S.A." viravam None, a descrição
    # caía pra "conta a pagar" e duas contas diferentes ganhavam a MESMA
    # frase sugerida — o P0-7 voltando por outra porta.
    # RÓTULO É O QUE VEM SEGUIDO DE ":" OU DE NÚMERO. A alternativa "fim do
    # trecho" saiu: rótulo no fim, sem número depois, não é rótulo — é a
    # última palavra do nome ("Companhia Energética Total", "Grupo Recibo",
    # "Supermercado Total"). Truncar o nome embaralha a lista e a frase de
    # baixa.
    _corte = re.search(
        r"(?i)\s\b(?:vencimento|vence|venc|vcto|valor|nosso\s+n[úu]mero|"
        r"data|linha|c[óo]digo|cnpj|cpf|ag[êe]ncia|pagamento|refer[êe]nte|"
        r"recibo|total|autentica[çc][ãa]o|pag[oa]\s+em)\b"
        # Rótulo composto: "Vencimento DO TÍTULO: 21/09", "Data DO
        # PAGAMENTO: ...". Exigindo `[:\d]` colado, esses passavam inteiros
        # pro nome do beneficiário.
        #
        # O CONECTIVO É OBRIGATÓRIO no elo. Com ele opcional, o padrão
        # aceitava DUAS PALAVRAS QUAISQUER entre o rótulo e o número — e aí
        # truncava razão social legítima: "Supermercado Total Atacado 2
        # Ltda" virava "Supermercado", "Colegio Data Vida 3 Marias" virava
        # "Colegio". Nome truncado reabre o item fantasma do M2.1 (duas
        # contas com a mesma descrição e a mesma frase de baixa). Rótulo
        # composto real sempre tem o conectivo; razão social não.
        r"(?:\s+d[eoa]s?\s+\w+){0,2}\s*[:\d]", bruto)
    if _corte and _corte.start() > 0:
        bruto = bruto[:_corte.start()]

    # CORTA NO PRIMEIRO DÍGITO. Nome de empresa não tem número, e a janela
    # crua (necessária pro corte por rótulo acima) traz o resto da linha
    # junto — inclusive a linha digitável, quando o OCR vem em UMA LINHA SÓ,
    # que é o formato que o `_read_image` pede.
    #
    # Isto derrubou o guardrail do produto por uma rodada inteira: o código
    # de pagamento entrou na descrição do item, foi gravado no banco e
    # impresso de volta pro usuário. As três defesas anteriores não pegavam
    # — o corte por rótulo não casa "Valor do Documento", o strip de cauda só
    # agia no fim da string, e o `[:60]` cortava NO MEIO do código,
    # transformando-o num número que nenhuma checagem reconhece.
    # Corta no primeiro BLOCO de 3+ dígitos, não no primeiro dígito: nome de
    # empresa tem número com frequência no Brasil ("Rede 3 Corações", "Loja
    # 5 Estrelas", "Condomínio Edifício 4 Estações"), e cortar no primeiro
    # dígito truncava todos — inclusive apagando inteiro o nome que COMEÇA
    # com número ("99 Tecnologia"). Linha digitável é bloco de 5+; número de
    # nome de empresa é de 1 ou 2.
    bruto = re.split(r"\d{3,}|\d+[.\s]\d", bruto, 1)[0]

    bruto = re.sub(r"\s+(R|RS|R\$)$", "", bruto, flags=re.I).strip(" .-/&,;")
    bruto = bruto[:60]                      # trunca ANTES de validar
    # `search`, não `fullmatch`: código com texto na frente também é código.
    if not bruto or _LINHA_DIGITAVEL_RE.search(bruto):
        return None
    return bruto


def extrair(texto: Optional[str]) -> Optional[dict]:
    """Texto lido de uma imagem/PDF -> dados do documento financeiro.

    Devolve None quando NÃO é documento financeiro (foto de cachorro, print
    de conversa, cardápio). Sem valor não há conta: é o único sinal que
    separa "isso é dinheiro" de "isso é uma foto qualquer", e chutar aqui
    encheria a lista da pessoa de lixo.
    """
    if not texto or not str(texto).strip():
        return None
    texto = str(texto)

    campos = {k.lower().replace("á", "a"): v.strip()
              for k, v in _CAMPO_RE.findall(texto)}
    tipo_campo = (campos.get("tipo") or "").lower()

    # VALOR NÃO BASTA. Cardápio, extrato, print de "te devo 50,00" e até Pix
    # RECEBIDO têm dinheiro escrito — e viravam conta a pagar com lembrete
    # agendado. Precisa de marca positiva de documento de cobrança.
    # "FORTE" = tem algo que conversa não tem: o `tipo=` explícito do
    # modelo, o canhoto do boleto, marca de comprovante, ou um valor com
    # RÓTULO de cobrança ("Valor do Documento", "Total a pagar"). É isso que
    # autoriza ignorar os vetos fracos — sem essa saída, "conta de luz com
    # promoção válida para débito automático" era recusada por causa da
    # palavra "promoção".
    forte = bool(tipo_campo or _BOLETO_FORTE_RE.search(texto)
                 or _COMPROVANTE_RE.search(texto)
                 or _valor_rotulado(texto, _ROTULOS_VALOR_FORTES) is not None)
    if not (forte or _BOLETO_RE.search(texto)):
        return None
    if _NAO_E_CONTA_RE.search(texto):
        return None
    if not forte and _VETO_FRACO_RE.search(texto):
        return None

    valor = _valor(campos.get("valor", "")) or _valor(texto)
    if valor is None:
        return None

    # `de_rotulo`: a data veio de um rótulo de VENCIMENTO (ou da cauda
    # estruturada), e não do fallback de "última data que sobrou". Só ela
    # pode servir de chave pra fechar conta automaticamente.
    data_iso = (_sanidade(_para_date(campos.get("vencimento", "")))
                or _sanidade(_para_date(campos.get("venc", ""))))
    de_rotulo = bool(data_iso)
    if not data_iso:
        data_iso, de_rotulo = _data_com_origem(texto)

    # PRECEDÊNCIA POR ESPECIFICIDADE, com saída antecipada. A ordem importa
    # e cada degrau tem motivo:
    #   1. `tipo=` da cauda: sinal EXPLÍCITO do modelo, decide sozinho. Na
    #      versão anterior a heurística era avaliada junto e vencia — o
    #      modelo dizia "comprovante" e uma palavra solta no corpo mandava
    #      mais. Isso é a regra 2 ao contrário.
    #   2. comprovante: quem já pagou não pode ser cobrado.
    #   3. marca exclusiva de boleto (canhoto): "Recibo do Pagador" é o que
    #      fazia boleto virar pago na rodada 1.
    #   4. resto: boleto pendente.
    # PAGAMENTO CONSUMADO: carimbo do banco, ou data de pagamento no passado.
    # Vem ANTES do canhoto porque o carimbo é evidência de que o documento
    # já foi pago, e o canhoto não é evidência de nada além de ser boleto —
    # todo boleto autenticado no caixa tem os dois.
    # O HISTÓRICO NÃO FALA DESTA CONTA. Boleto de condomínio imprime
    # "PARCELA ANTERIOR PAGO EM 10/08" e fatura imprime "histórico:
    # pagamento realizado no mês anterior" — ler isso como pagamento DESTE
    # documento faz a conta do mês entrar como paga.
    # A máscara para na PONTUAÇÃO: histórico é cláusula, e a vírgula é onde
    # ele termina. Com `[^\n]{0,60}` ela atravessava a vírgula e engolia o
    # "vencimento 10/09/2026" que vinha logo depois — no OCR de uma linha, o
    # boleto ficava sem data e a feature não funcionava.
    # A máscara acaba na pontuação OU no próximo campo — histórico é
    # cláusula, e ela termina onde o campo seguinte começa. Só com pontuação,
    # o OCR de uma linha sem vírgula ("historico da parcela anterior
    # vencimento 10/09/2026") tinha o vencimento engolido.
    texto_atual = re.sub(
        r"(?i)((?:parcela|m[êe]s|per[íi]odo|fatura)\s+anterior|"
        r"hist[óo]rico)"
        r"(?:(?!vencimento|vence|venc\b|valor|total)[^\n.;,]){0,60}",
        " ", texto)

    _cabecalho = (texto.strip().splitlines() or [""])[0][:60]
    _e_recibo = bool(_COMPROVANTE_RE.search(texto_atual)
                     or _COMPROVANTE_CABECALHO_RE.search(_cabecalho))

    _m_pago = _PAGO_EM_RE.search(texto_atual)
    _data_pago = _sanidade(_para_date(_m_pago.group(1))) if _m_pago else None
    # A DATA só prova pagamento junto com marca de recibo. O CARIMBO do
    # banco prova sozinho — é o ponto do P0-14 e não pode ser enfraquecido.
    pago_consumado = bool(
        _AUTENTICACAO_RE.search(texto_atual)
        or (_e_recibo and _data_pago
            and _data_pago <= tempo.hoje().isoformat()))

    # CARIMBO É PROVA; DATA É MENÇÃO — e por isso eles ficam em degraus
    # diferentes. Pôr os dois acima do canhoto fazia "Comprovante de entrega
    # ao sacado" + "pago em 10/08" no corpo de um CARNÊ vencer a Ficha de
    # Compensação: carnê a pagar entrava como pago. O carimbo do banco
    # continua acima (é o conserto do P0-14); a data desceu.
    if "comprovante" in tipo_campo:
        tipo, status = "comprovante", "concluido"
    elif "boleto" in tipo_campo:
        tipo, status = "boleto", "pendente"
    elif _AUTENTICACAO_RE.search(texto_atual):
        tipo, status = "comprovante", "concluido"
    elif _BOLETO_FORTE_RE.search(texto):
        tipo, status = "boleto", "pendente"
    elif pago_consumado or _e_recibo:
        tipo, status = "comprovante", "concluido"
    else:
        tipo, status = "boleto", "pendente"

    # A data do item de um recibo é a do PAGAMENTO, não a do vencimento do
    # título. Guardar 20/09 num comprovante pago em 12/08 é registrar no
    # histórico uma data em que nada aconteceu. Mas o vencimento do título
    # NÃO é jogado fora: é ele que identifica, sem ambiguidade, qual conta
    # da lista este comprovante quita.
    # `vencimento_titulo` é preenchido SEMPRE que o documento traga um
    # vencimento, mesmo quando ele é igual à data de pagamento. A versão
    # anterior descartava o sinal na igualdade — e pagar NO DIA do
    # vencimento é o caso mais comum de todos, justo aquele em que esse
    # dado é o que identifica qual conta o comprovante quita. Igualdade é
    # informação, não ausência de informação.
    vencimento_titulo = None
    if tipo == "comprovante":
        # SÓ com rótulo de vencimento. Sem isto, "Data 16/08/2026" num
        # recibo (rótulo neutro, comum) virava "vencimento do título" e
        # fechava a conta de outro credor que vencia naquele dia.
        vencimento_titulo = data_iso if de_rotulo else None
        if _data_pago:
            data_iso = _data_pago

    # O INVARIANTE QUE NÃO DEPENDE DE VOCABULÁRIO.
    #
    # Três rodadas de auditoria trocaram de lado neste par: lista de
    # palavras marcava boleto como pago, aí marcava comprovante como a
    # pagar, aí de novo. E vai continuar trocando, porque as palavras
    # ("cedente", "nosso número", "recibo", "pagamento") existem NOS DOIS
    # documentos — só a estrutura os separa.
    #
    # Recibo não tem data a vencer. Documento com vencimento no FUTURO é
    # cobrança, ponto — não importa quantas vezes a palavra "pagamento"
    # apareça nele.
    # EXCEÇÃO DO INVARIANTE: pagamento AGENDADO tem data futura e já foi
    # resolvido pela pessoa. Cobrar quem agendou é cobrar duas vezes. Exige
    # `agendado|agendamento|programado` — o imperativo "agende seu
    # pagamento", que boleto imprime como propaganda, não conta.
    agendado = bool(re.search(
        r"\b(agendad[oa]|agendamento|programad[oa])\b", texto, re.I))
    if (tipo == "comprovante" and not agendado and not pago_consumado
            and data_iso and data_iso > tempo.hoje().isoformat()):
        log.info("[boleto] vencimento no futuro (%s): nao e comprovante",
                 data_iso)
        tipo, status = "boleto", "pendente"

    return {
        "valor_reais": valor,
        "data_vencimento": data_iso,
        "vencimento_titulo": vencimento_titulo,
        "beneficiario": _beneficiario(texto, campos),
        "tipo": tipo,
        "status_sugerido": status,
    }


def sem_codigo_de_pagamento(texto: Optional[str]) -> str:
    """Tira linha digitável e código de barras de um texto qualquer.

    Vale pro caminho de ESCAPE: quando `extrair` recusa o documento, o OCR
    inteiro seguia pro menu 1/2 e virava descrição do item — com o código de
    pagamento junto. O guardrail não pode valer só no caminho feliz.
    """
    if not texto:
        return ""
    limpo = re.sub(r"(?:\d[\d.\s]{18,}\d)", "[codigo removido]", str(texto))
    return re.sub(r"\s{2,}", " ", limpo).strip()


def descricao_de(dados: dict) -> str:
    """Como o item aparece na lista da pessoa. Sem código, sem juridiquês."""
    quem = (dados.get("beneficiario") or "").strip()
    if dados.get("tipo") == "comprovante":
        bruto = f"pagamento para {quem}" if quem else "pagamento registrado"
    else:
        bruto = f"conta {quem}" if quem else "conta a pagar"
    return bruto[:120]      # o corte vale pros dois ramos


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
# Boleto que o banco manda por e-mail é PDF de TEXTO, não imagem — dá pra ler
# sem OCR nenhum. Import protegido de propósito: se o `pypdf` não estiver no
# build, o caminho degrada pro pedido de print (que já existia) em vez de
# derrubar o webhook. Ver DECISOES.md.
def _leitor_padrao(dados: bytes) -> Optional[str]:
    import io

    from pypdf import PdfReader
    leitor = PdfReader(io.BytesIO(dados))
    partes = []
    for pagina in leitor.pages[:3]:      # boleto útil está na 1ª página
        partes.append(pagina.extract_text() or "")
    return "\n".join(partes)


try:                                     # noqa: SIM105
    import pypdf  # noqa: F401
    _LEITOR_PDF = _leitor_padrao
except ImportError:
    log.warning("[boleto] pypdf ausente — PDF continua pedindo print")
    _LEITOR_PDF = None


def texto_de_pdf(dados: bytes) -> Optional[str]:
    """Texto da primeira página do PDF, ou None se não der pra ler.

    None é resposta legítima aqui: PDF escaneado é imagem dentro de PDF e
    não tem texto nenhum. Quem chama trata os dois casos igual — pede print.
    """
    if not dados or _LEITOR_PDF is None:
        return None
    try:
        texto = _LEITOR_PDF(dados)
    except Exception:
        log.warning("[boleto] falha ao ler PDF", exc_info=True)
        return None
    return texto if (texto or "").strip() else None


# ---------------------------------------------------------------------------
# O CÓDIGO DE PAGAMENTO, PRONTO PRA COLAR (M3.5)
# ---------------------------------------------------------------------------
# Pedido do Kevin em 29/08/2026: na hora de pagar, devolver o código do jeito
# que o app do banco aceita — sem espaço, sem ponto — e dizendo se é código de
# barras ou PIX.
#
# ISTO INVERTE UMA DECISÃO ANTERIOR, e de propósito. `sem_codigo_de_pagamento`
# (acima) existe porque o OCR inteiro virava descrição do item e o código
# ficava salvo na lista da pessoa. O motivo continua válido: o código NÃO pode
# morar na descrição nem em log. Ele passa a morar em coluna própria e só sai
# no aviso de vencimento, que é o único momento em que serve pra alguma coisa.
#
# Por que separar boleto de PIX: colar código de barras no campo de PIX
# simplesmente não funciona, e a pessoa conclui que o bot errou. O rótulo é
# metade da utilidade.

# Boleto bancário tem 47 dígitos na linha digitável; concessionária, 48.
_TAMANHOS_BOLETO = (47, 48)

# PIX copia-e-cola (BR Code / EMV) sempre abre com o payload format "0002"
# seguido da versão "01", e carrega o domínio do BCB.
_PIX_INICIO = "000201"
_PIX_MARCA = "BR.GOV.BCB.PIX"

# SEM QUEBRA DE LINHA no conjunto, de propósito: a linha digitável vive numa
# linha só, e aceitar quebra fazia o número da linha anterior colar nela. No
# OCR real, o valor "R$ 187,40" seguido da linha digitável virava um código de
# 46 dígitos (o "40" do valor mais os 44 da linha) e era descartado por
# tamanho — o código certo estava ali e o bot dizia que não achou.
_SO_DIGITOS_RE = re.compile(r"[\d.\t ]{40,}")


def _limpar(bruto: str) -> str:
    return re.sub(r"\D", "", bruto or "")


def codigo_de_pagamento(texto: Optional[str]) -> Optional[dict]:
    """Acha o código de pagamento num texto de OCR. None se não houver.

    Devolve {"tipo": "boleto"|"pix", "colavel": str}.

    Conservador de propósito: só reconhece o que tem tamanho de código de
    pagamento de verdade. CPF, telefone e valor têm dígito demais pra chutar —
    e um código errado no aviso é pior que aviso sem código, porque a pessoa
    cola, o banco recusa e ela para de confiar na mensagem.
    """
    if not texto:
        return None
    bruto = str(texto)

    # --- PIX primeiro: o payload é alfanumérico e não colide com boleto ---
    if _PIX_MARCA in bruto.upper():
        i = bruto.upper().find(_PIX_INICIO)
        if i >= 0:
            # o BR Code vai até o CRC final (4 chars depois de "6304")
            trecho = re.sub(r"\s+", "", bruto[i:])
            m = re.search(r"6304[0-9A-Fa-f]{4}", trecho)
            if m:
                return {"tipo": "pix", "colavel": trecho[:m.end()]}
            # SEM O CRC, ESTE PIX NÃO VALE (auditoria M3.5, P1-7).
            #
            # O fallback antigo devolvia TODO o resto do texto como se fosse
            # o código. Num OCR real isso arrastava nome, CPF, endereço e
            # telefone do beneficiário pra dentro da coluna, pra mensagem que
            # a pessoa recebe e — pelo caminho de falha — pro log.
            #
            # PIX sem CRC também não seria aceito pelo banco: devolver algo
            # aqui só criaria a expectativa de colar e a frustração de ver
            # recusado.
            #
            # MAS SÓ O PIX É DESCARTADO, NÃO A FOTO (M3.6, P2-6): o `return
            # None` que estava aqui cortava a busca de boleto logo abaixo, e
            # um documento com o PIX truncado E a linha digitável legível
            # saía sem código nenhum. Agora o texto segue sendo lido.
            log.info("[boleto] PIX sem CRC (6304xxxx) — ignorado")

    # --- boleto: sequência longa de dígitos, ponto e espaço ---
    #
    # LINHA A LINHA: a linha digitável nunca se parte, e varrer o texto
    # inteiro de uma vez faz o número da linha anterior colar nela.
    candidatos = []
    for linha in bruto.splitlines():
        candidatos.extend(_SO_DIGITOS_RE.findall(linha))
    validos = set(_TAMANHOS_BOLETO) | {44}   # 44 = código de barras puro
    for achado in candidatos:
        digitos = _limpar(achado)
        if len(digitos) in validos:
            return {"tipo": "boleto", "colavel": digitos}
        # PREFIXO GRUDADO NA MESMA LINHA (auditoria M3.5, P2).
        #
        # A quebra de linha já era tratada, mas o valor na MESMA linha não:
        # "Valor do documento 187.40 34191.79001…" virava 49 dígitos e era
        # descartado por tamanho — o código estava ali e o bot dizia que não
        # achou.
        #
        # DESCARTA POR BLOCO, NUNCA POR CONTAGEM. A primeira tentativa foi
        # cortar os N últimos dígitos, e ela está errada: 44, 47 e 48 são
        # todos comprimentos válidos, então "cortar o maior que couber"
        # devolve um código de 48 dígitos que ninguém conferiu — cola no
        # banco, é recusado, e a pessoa perde a confiança na mensagem. Aqui
        # os blocos do OCR são jogados fora inteiros, da esquerda pra
        # direita, até o que sobra ter exatamente um tamanho válido. Se
        # nenhum corte de bloco fecha, não há código: `None`.
        blocos = achado.split()
        for i in range(1, len(blocos)):
            resto = _limpar("".join(blocos[i:]))
            if len(resto) in validos:
                return {"tipo": "boleto", "colavel": resto}
    return None


def bloco_para_pagar(codigo: Optional[dict]) -> str:
    """O pedaço da mensagem que carrega o código. "" quando não há código.

    O código sai SOZINHO numa linha: no WhatsApp, o toque-e-copia seleciona o
    parágrafo, e código grudado em texto vem com palavra junto — o banco
    recusa e a pessoa não entende por quê.
    """
    if not codigo:
        return ""
    colavel = (codigo.get("colavel") or "").strip()
    if not colavel:
        return ""
    if codigo.get("tipo") == "pix":
        return ("\n\nPIX copia e cola:\n"
                + colavel
                + "\n_(copia e cola no PIX do seu banco)_")
    return ("\n\nCódigo de barras:\n"
            + colavel
            + "\n_(cola no campo de código de barras do seu banco)_")


def nome_do_codigo(codigo: Optional[dict]) -> str:
    """"PIX copia e cola" ou "código de barras". "" quando não há código."""
    if not codigo or not (codigo.get("colavel") or "").strip():
        return ""
    return ("PIX copia e cola" if codigo.get("tipo") == "pix"
            else "código de barras")


def aviso_de_codigo(codigo: Optional[dict]) -> str:
    """A LINHA que anuncia o código — sem o código dentro.

    O código não viaja no lembrete: ele sai quando a pessoa toca no botão, e
    aí vai numa mensagem só dele, onde o toque-e-segura → Copiar entrega
    exatamente o que o app do banco aceita.
    """
    nome = nome_do_codigo(codigo)
    if not nome:
        return ""
    return ("\n\n📋 Toque em *Copiar código* que eu te mando o *%s* "
            "pronto pra colar no app do banco." % nome)


def mensagem_so_do_codigo(codigo: Optional[dict]) -> str:
    """O código, SOZINHO. "" quando não há código.

    Nada de rótulo, emoji, aspas ou formatação: o toque-e-segura do WhatsApp
    copia a MENSAGEM INTEIRA, então tudo o que estiver aqui vai junto pro
    campo do banco — e o banco recusa. Esta função existe para que o que a
    pessoa cola seja exatamente o que o app do banco espera.

    (Botão nativo de copiar não é opção: no WhatsApp ele só existe em
    template de AUTENTICAÇÃO, que é para código de verificação de identidade
    e não pode ser usado para cobrança.)
    """
    if not codigo:
        return ""
    return (codigo.get("colavel") or "").strip()
