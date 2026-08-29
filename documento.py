# -*- coding: utf-8 -*-
"""Imagem que NÃO é boleto: reconhecer, propor, e deixar a pessoa decidir.

Por que este módulo existe (M3.5, 29/08/2026): o caminho de imagem só sabia
duas coisas. Se `boleto.extrair` reconhecia documento financeiro, virava item;
se não, o OCR inteiro caía no menu antigo e virava descrição. Foto de nota
fiscal, CNH, receita médica ou carteirinha de vacina não viravam nada útil —
e são exatamente os documentos cuja data, esquecida, custa caro.

A REGRA QUE MANDA AQUI, e ela é diferente da do boleto: **o bot nunca guarda
sozinho o que apenas achou que entendeu**. Ele propõe e a pessoa confirma.

Boleto pode guardar direto porque tem âncora dura — valor em reais e código de
barras não aparecem por acidente. Já uma foto de documento é interpretação: o
OCR erra data, o layout muda, e "12/03/2027" tanto pode ser validade quanto
primeira habilitação. Item errado na lista é pior que item nenhum, porque a
pessoa deixa de confiar no que está lá.

Divisão de trabalho (regra 2): aqui é só Python — padrão de texto e data. O
LLM não decide o que é o documento, e não inventa data que o OCR não trouxe.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# COMO CADA TIPO SE ANUNCIA
# ---------------------------------------------------------------------------
# Cada entrada é (tipo, marcas que identificam, rótulo pra pessoa).
#
# As marcas são específicas de propósito. "documento" casaria com quase
# qualquer papel; "CARTEIRA NACIONAL DE HABILITACAO" só casa com uma coisa.
# Falso positivo aqui não é neutro: vira pergunta errada, e pergunta errada
# ensina a pessoa a ignorar as perguntas.
_TIPOS = (
    # CUPOM FISCAL SAIU (auditoria M3.6, P1-5). Ele é o papel do mercado, da
    # farmácia, do posto — compra sem garantia, várias por semana. Com ele na
    # lista, cada ida ao supermercado virava um lembrete de "garantia" pra
    # daqui a 11 meses. A conta de compra do dia a dia já tem caminho: é
    # despesa, não documento que vence.
    ("nota_fiscal",
     (r"\bDANFE\b", r"NOTA\s+FISCAL", r"\bNFC-?e\b"),
     "nota fiscal"),
    ("documento",
     (r"CARTEIRA\s+NACIONAL\s+DE\s+HABILITA", r"\bCNH\b",
      r"\bPASSAPORTE\b", r"CERTIFICADO\s+DIGITAL",
      r"CARTEIRA\s+DE\s+IDENTIDADE"),
     "documento"),
    ("receita",
     (r"RECEITU[ÁA]RIO", r"\bCRM\s*\d", r"USO\s+CONT[ÍI]NUO",
      r"PRESCRI[ÇC][ÃA]O\s+M[ÉE]DICA"),
     "receita médica"),
    ("vacina",
     (r"CARTEIRA\s+DE\s+VACINA", r"CADERNETA\s+DE\s+VACINA",
      r"PR[ÓO]XIMA\s+DOSE", r"\bV\d{1,2}\s+aplicada"),
     "carteira de vacinação"),
)

# ---------------------------------------------------------------------------
# QUAL DATA IMPORTA EM CADA UM
# ---------------------------------------------------------------------------
# Um documento traz várias datas e só UMA vale lembrete. Na CNH, a validade
# importa e a primeira habilitação não; na nota, a emissão (que inicia a
# garantia). Pegar "a primeira data que aparecer" daria o lembrete errado com
# ar de certeza — o pior tipo de erro, porque ninguém confere.
_ANCORAS_DE_DATA = {
    "nota_fiscal": (r"emiss[ãa]o", r"\bdata\b"),
    "documento": (r"validade", r"valid[oa]\s+at[ée]", r"vence"),
    "receita": (r"\bdata\b", r"emiss[ãa]o"),
    "vacina": (r"pr[óo]xima\s+dose", r"revacina", r"pr[óo]ximo"),
}

# NOME FINO DO DOCUMENTO DE IDENTIDADE.
#
# O tipo "documento" cobre quatro papéis diferentes e o rótulo genérico não
# ajuda ninguém: na lista, "documento — Joao Da Silva" não diz o que vence, e
# ainda carrega o nome da pessoa pra dentro de um campo que ela lê o tempo
# todo. "CNH" diz tudo em três letras.
_ROTULO_FINO = (
    (r"CARTEIRA\s+NACIONAL\s+DE\s+HABILITA|\bCNH\b", "CNH"),
    (r"\bPASSAPORTE\b", "passaporte"),
    (r"CERTIFICADO\s+DIGITAL", "certificado digital"),
    (r"CARTEIRA\s+DE\s+IDENTIDADE", "carteira de identidade"),
)

# NOTA DE SERVIÇO NÃO TEM GARANTIA DE PRODUTO (auditoria M3.6, P1-5).
#
# Conserto de vazamento, corte de cabelo, frete, mensalidade: são notas
# fiscais de verdade, com DANFE e tudo, e nenhuma delas ganha um ano de
# garantia de fábrica. Prometer isso é o bot afirmando um fato que não é
# verdade — e daqui a onze meses a pessoa recebe um aviso sobre a garantia
# do encanador. Reconhecido como nota de serviço, o documento simplesmente
# não entra por este caminho.
_NOTA_DE_SERVICO_RE = re.compile(
    r"\b(nota\s+fiscal\s+de\s+servi[çc]|NFS-?e|servi[çc]os?\s+prestados?|"
    r"m[ãa]o\s+de\s+obra|conserto|reparo|instala[çc][ãa]o|manuten[çc][ãa]o|"
    r"consultoria|honor[áa]rios|ISS(?:QN)?\b|frete)\b", re.I)

_DATA_RE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b")

# Linhas que NUNCA viram descrição: carregam dado de identificação e não
# ajudam a pessoa a reconhecer o próprio documento na lista.
_LIXO_NA_DESCRICAO = re.compile(
    r"(CNPJ|\bCPF\b|\bCRM\b|\bRG\b|REGISTRO|DANFE|REPUBLICA|FEDERATIVA|"
    r"CATEGORIA|HABILITA|DOCUMENTO\s+AUXILIAR|RECEITU|CARTEIRA)", re.I)


def _iso(m: re.Match) -> Optional[str]:
    """Data do OCR -> ISO. None quando a data não existe no calendário.

    VALIDAR A FAIXA NÃO BASTA (auditoria M3.6, P0-2). "31/09/2026" passa em
    `1<=mes<=12 and 1<=d<=31` e não existe. Um dígito errado da visão gravava
    "2026-09-31" no banco, e daí em diante `date(y, m, d)` estourava DENTRO
    do motor proativo — derrubando o ciclo inteiro, de TODO MUNDO, todo dia,
    até alguém apagar a linha na mão. `date()` é o único juiz de calendário
    que não erra bissexto nem mês de 30.
    """
    d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if a < 100:
        a += 2000
    try:
        return date(a, mes, d).isoformat()
    except ValueError:
        return None


def _data_do_tipo(texto: str, tipo: str) -> Optional[str]:
    """A data ANCORADA no rótulo certo. None quando não dá pra ter certeza."""
    ancoras = _ANCORAS_DE_DATA.get(tipo, ())
    for linha in texto.splitlines():
        if not any(re.search(a, linha, re.I) for a in ancoras):
            continue
        m = _DATA_RE.search(linha)
        if m:
            iso = _iso(m)
            if iso:
                return iso
    return None


def _descricao(texto: str, rotulo: str, marcas: tuple = ()) -> str:
    """Como o item aparece na lista. Curto, sem OCR cru e sem dado sensível.

    Procura a linha que parece o NOME da coisa (o produto na nota, o pet na
    carteirinha) e cai no rótulo genérico quando não acha. Genérico é melhor
    que errado: "nota fiscal" a pessoa reconhece, um pedaço de OCR não.
    """
    melhor = ""
    for linha in texto.splitlines():
        l = linha.strip()
        if len(l) < 4 or len(l) > 60:
            continue
        if _LIXO_NA_DESCRICAO.search(l):
            continue
        # A LINHA QUE É SÓ O RÓTULO NÃO É O NOME DA COISA. Sem isto,
        # "NOTA FISCAL ELETRONICA" no cabeçalho virava a descrição e o item
        # saía como "nota fiscal — Nota Fiscal Eletronica" — o rótulo escrito
        # duas vezes, e nenhuma pista de qual nota é essa.
        #
        # SÓ QUE DESCARTAR A LINHA INTEIRA ERA DEMAIS (auditoria M3.6, P2-1):
        # "USO CONTÍNUO" é marca do tipo E aparece na linha do remédio, então
        # "Losartana 50mg - uso contínuo" era jogada fora e a receita ficava
        # sem o nome do medicamento — o único dado que a pessoa procura na
        # lista. A linha só cai fora quando a marca É a linha; sobrando nome
        # de verdade, ele fica.
        _sem_marca = l
        for _m in marcas:
            _sem_marca = re.sub(_m, " ", _sem_marca, flags=re.I)
        if sum(c.isalnum() for c in _sem_marca) < 4:
            continue
        if _DATA_RE.search(l) or re.search(r"R\$", l):
            continue
        # linha com letras de verdade, não código
        if sum(c.isalpha() for c in l) < len(l) * 0.5:
            continue
        melhor = l
        break
    if not melhor:
        return rotulo
    melhor = re.sub(r"\s{2,}", " ", melhor).strip(" -–—:")
    if len(melhor) > 48:
        melhor = melhor[:48].rstrip() + "…"
    # `.title()` SO EM LINHA TODA MAIUSCULA. Aplicado sempre, ele estragava
    # o que o OCR tinha lido certo: "Losartana 50mg" virava "Losartana 50Mg"
    # — e dose de remedio escrita errada e o tipo de detalhe que faz a pessoa
    # duvidar de tudo o que esta na lista.
    if melhor.isupper():
        melhor = melhor.title()
    return "%s — %s" % (rotulo, melhor) if melhor else rotulo


def reconhecer(texto: Optional[str]) -> Optional[dict]:
    """Texto de OCR -> o que o documento parece ser. None quando não sabe.

    Devolve {"tipo", "rotulo", "descricao", "data"}. `data` pode vir None: o
    documento foi reconhecido mas a data não estava clara. Melhor perguntar a
    data do que inventar uma.
    """
    if not texto or not str(texto).strip():
        return None
    bruto = str(texto)
    for tipo, marcas, rotulo in _TIPOS:
        if not any(re.search(m, bruto, re.I) for m in marcas):
            continue
        if tipo == "nota_fiscal" and _NOTA_DE_SERVICO_RE.search(bruto):
            return None
        if tipo == "documento":
            for padrao, fino in _ROTULO_FINO:
                if re.search(padrao, bruto, re.I):
                    rotulo = fino
                    break
            # Documento de identidade não ganha descrição derivada do OCR: o
            # que sobra depois do filtro de lixo é o nome da pessoa, e o nome
            # dela na lista não a ajuda a saber o que vence.
            descricao = rotulo
        else:
            descricao = _descricao(bruto, rotulo, marcas)
        return {
            "tipo": tipo,
            "rotulo": rotulo,
            "descricao": descricao,
            "data": _data_do_tipo(bruto, tipo),
        }
    return None


# ---------------------------------------------------------------------------
# DA DATA QUE ESTÁ NO PAPEL PARA A DATA QUE IMPORTA
# ---------------------------------------------------------------------------
# Auditoria M3.5 (P1-3): o item nascia VENCIDO. A âncora da nota fiscal e da
# receita é a data de EMISSÃO — que é sempre passado — e ela ia direto pro
# campo `data_vencimento`. Resultado: a pessoa mandava a foto da nota de
# ontem, confirmava, e no ciclo seguinte levava "isso venceu e eu não vi a
# baixa". O bot cobrando algo que acabou de nascer é o defeito que mais
# rápido ensina alguém a desinstalar.
#
# A conversão é por tipo porque o significado da data é por tipo:
#   nota fiscal  emissão + 1 ano  -> a garantia legal (CDC, art. 26) acaba aí
#   receita      emissão + 6 meses -> validade usual de receita de uso contínuo
#   documento    a âncora JÁ é a validade -> não mexe
#   vacina       a âncora JÁ é a próxima dose -> não mexe
_PRAZO_APOS_A_DATA = {
    "nota_fiscal": 365,
    "receita": 180,
    "documento": 0,
    "vacina": 0,
}

# COM QUANTA ANTECEDÊNCIA AVISAR, POR TIPO.
#
# A outra metade do mesmo P1-3: a promessa dizia "60 e 30 dias antes" e o
# motor só sabia avisar na véspera (`scheduler.DUE_ALERT_DAYS = {1}`). Aviso
# de CNH um dia antes de vencer não serve pra nada — não dá tempo de marcar
# exame nem de ir ao Detran; a promessa era simplesmente falsa.
#
# Estes números viajam COM O ITEM (coluna `items.avisar_dias`) em vez de
# virarem política por categoria. É de propósito: a categoria da CNH é
# "Outros", e dar 60 dias de antecedência a "Outros" faria o bot avisar de
# tudo com dois meses de antecedência — o ruído que silencia o bot inteiro.
_AVISOS = {
    "nota_fiscal": (30,),
    "receita": (15,),
    "documento": (60, 30),
    "vacina": (7, 1),
}


def _iso_mais_dias(iso: str, dias: int) -> str:
    a, m, d = (int(x) for x in iso.split("-"))
    return (date(a, m, d) + timedelta(days=dias)).isoformat()


def vencimento(doc: Optional[dict]) -> Optional[str]:
    """A data que vira `data_vencimento` no item. None se não dá pra saber.

    TODO CAMINHO PASSA PELO CALENDÁRIO, inclusive o de prazo zero. Antes,
    `if prazo else doc["data"]` devolvia a data crua sem conferir — e como
    CNH e vacina têm prazo zero, eram justamente os dois tipos que podiam
    gravar "31/09" no banco (auditoria M3.6, P0-2).
    """
    if not doc or not doc.get("data"):
        return None
    prazo = _PRAZO_APOS_A_DATA.get(doc.get("tipo"), 0)
    try:
        return _iso_mais_dias(doc["data"], prazo)
    except (ValueError, TypeError, AttributeError):
        return None


def avisos(tipo: Optional[str]) -> tuple:
    """Com quantos dias de antecedência avisar. Vazio = política padrão."""
    return _AVISOS.get(tipo or "", ())


def _dias_que_ainda_cabem(tipo: str, faltam: Optional[int]) -> tuple:
    """Dos avisos do tipo, os que ainda dá tempo de fazer.

    A CNH que vence em 20 dias não tem como receber o aviso de D-60 nem o de
    D-30 — e a confirmação prometia os dois assim mesmo (auditoria M3.6,
    P1-1). A pessoa ouviria uma única mensagem, no dia, e concluiria — com
    razão — que o bot fala o que não cumpre.
    """
    dias = avisos(tipo)
    if faltam is None:
        return dias
    return tuple(d for d in dias if d <= faltam)


def _frase_de_aviso(tipo: str, faltam: Optional[int] = None) -> str:
    dias = _dias_que_ainda_cabem(tipo, faltam)
    if not dias:
        # Sem antecedência que caiba, quem avisa é a política global: D-1.
        return "te aviso na véspera"

    # "60 dias antes e 30 dias antes" e o jeito que ninguem escreve. Quando
    # todos os avisos sao numeros, o "dias antes" sai uma vez so.
    if 1 not in dias:
        if len(dias) == 1:
            return "te aviso %d dias antes" % dias[0]
        return "te aviso %s e %d dias antes" % (
            ", ".join(str(d) for d in dias[:-1]), dias[-1])

    def _um(d):
        return "na véspera" if d == 1 else "%d dias antes" % d

    if len(dias) == 1:
        return "te aviso %s" % _um(dias[0])
    return "te aviso %s e %s" % (
        ", ".join(_um(d) for d in dias[:-1]), _um(dias[-1]))


# O que cada tipo promete fazer, em uma linha — é o que convence a pessoa a
# confirmar. "Guardei sua nota" não diz nada; "te aviso 30 dias antes de a
# garantia acabar" diz.
#
# O trecho dos dias é GERADO a partir de `_AVISOS`, nunca escrito à mão: foi
# exatamente a cópia manual que deixou a promessa dizer 60/30 enquanto o
# motor avisava na véspera. Se alguém mudar a antecedência, o texto muda
# junto — não tem como esquecer.
#
# "COSTUMA" NA NOTA FISCAL, não "é" (auditoria M3.6, P1-5). Um ano é o prazo
# usual de garantia de fábrica, não uma regra da lei — o CDC trata de outra
# coisa (prazo de reclamação, 30 e 90 dias). O bot lembra da data; ele não
# dá parecer jurídico.
_PROMESSA = {
    "nota_fiscal": "%s de completar 1 ano da compra, "
                   "que é quando a garantia costuma acabar",
    "documento": "%s de vencer",
    "receita": "%s de a receita completar 6 meses",
    "vacina": "%s da próxima dose",
}


def promessa(tipo: Optional[str], faltam: Optional[int] = None) -> str:
    molde = _PROMESSA.get(tipo or "")
    if not molde:
        return "eu te aviso na hora certa"
    return molde % _frase_de_aviso(tipo or "", faltam)


# "Isso parece UMA CNH" / "UM passaporte". Concordância errada num texto que
# a pessoa lê no primeiro contato com a feature parece descuido — e descuido
# é o que faz alguém não confiar o documento ao bot.
# COMO CHAMAR A DATA EM CADA TIPO. "Vence em" numa carteirinha de vacina
# esta errado: dose nao vence, chega a hora. E na nota o que a pessoa precisa
# ver e ate quando a garantia vai, nao "quando a nota vence".
_ROTULO_DA_DATA = {
    "nota_fiscal": "Garantia vai até",
    "vacina": "Próxima dose em",
    "receita": "Vale até",
}

_ARTIGO = {
    "passaporte": "um",
    "certificado digital": "um",
    "documento": "um",
}

BOTOES = ["Confirmar", "Ajustar", "Esquece"]


def pergunta_de_confirmacao(doc: Optional[dict],
                            hoje: Optional[date] = None) -> Optional[dict]:
    """A pergunta que a pessoa responde com um toque. None sem documento.

    Os três caminhos existem por motivos diferentes:
      Confirmar — o caso comum, um toque.
      Ajustar   — o OCR erra data e nome; sem esta saída a pessoa desiste em
                  vez de corrigir, e a gente perde o item inteiro.
      Esquece   — não guardar é uma resposta legítima. Sem ela, a pessoa
                  aprende que mandar foto gera trabalho.
    """
    if not doc:
        return None
    # Mostra a data QUE VALE, não a que está impressa: pra nota fiscal a
    # pessoa precisa ver o fim da garantia, não a emissão que ela já conhece.
    quando = vencimento(doc)
    faltam = None
    if quando:
        d = "%s/%s/%s" % (quando[8:10], quando[5:7], quando[0:4])
        rotulo_data = _ROTULO_DA_DATA.get(doc.get("tipo"), "Vence em")
        linha_data = "%s: *%s*" % (rotulo_data, d)
        try:
            a, m, dd = (int(x) for x in quando.split("-"))
            faltam = (date(a, m, dd) - (hoje or date.today())).days
        except (ValueError, TypeError):
            faltam = None
    else:
        linha_data = ("_Não consegui ler a data — toca em *Ajustar* que você "
                      "me diz._")

    # A DESCRIÇÃO SÓ ENTRA QUANDO ACRESCENTA (auditoria M3.6, P2-5). No
    # documento de identidade ela É o rótulo, e a mensagem saía com "CNH"
    # escrito duas vezes seguidas — o mesmo defeito que o filtro de marcas
    # tinha acabado de matar na nota fiscal.
    corpo = (doc["descricao"] or "").strip()
    _rot = (doc["rotulo"] or "").strip()
    if corpo.lower() == _rot.lower():
        corpo = ""
    elif corpo.lower().startswith(_rot.lower() + " — "):
        # "nota fiscal — Geladeira Brastemp" logo abaixo de "Isso parece uma
        # *nota fiscal*" e o rotulo escrito duas vezes na mesma tela. Na
        # LISTA o prefixo serve (identifica o item); aqui ele so ocupa
        # espaco, porque a frase de cima ja disse o que e.
        corpo = corpo[len(_rot) + 3:].strip()

    texto = (
        "Isso parece %s *%s*. 📄\n\n"
        "%s%s\n\n"
        "Se estiver certo, %s."
        % (_ARTIGO.get(doc["rotulo"], "uma"), doc["rotulo"],
           (corpo + "\n") if corpo else "", linha_data,
           promessa(doc.get("tipo"), faltam))
    )
    return {"texto": texto, "botoes": list(BOTOES), "doc": doc}
