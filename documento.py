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
    ("nota_fiscal",
     (r"\bDANFE\b", r"NOTA\s+FISCAL", r"\bNFC-?e\b", r"CUPOM\s+FISCAL"),
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

_DATA_RE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b")

# Linhas que NUNCA viram descrição: carregam dado de identificação e não
# ajudam a pessoa a reconhecer o próprio documento na lista.
_LIXO_NA_DESCRICAO = re.compile(
    r"(CNPJ|\bCPF\b|\bCRM\b|\bRG\b|REGISTRO|DANFE|REPUBLICA|FEDERATIVA|"
    r"CATEGORIA|HABILITA|DOCUMENTO\s+AUXILIAR|RECEITU|CARTEIRA)", re.I)


def _iso(m: re.Match) -> Optional[str]:
    d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if a < 100:
        a += 2000
    if not (1 <= mes <= 12 and 1 <= d <= 31):
        return None
    return "%04d-%02d-%02d" % (a, mes, d)


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


def _descricao(texto: str, rotulo: str) -> str:
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
    return "%s — %s" % (rotulo, melhor.title()) if melhor else rotulo


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
        return {
            "tipo": tipo,
            "rotulo": rotulo,
            "descricao": _descricao(bruto, rotulo),
            "data": _data_do_tipo(bruto, tipo),
        }
    return None


# O que cada tipo promete fazer, em uma linha — é o que convence a pessoa a
# confirmar. "Guardei sua nota" não diz nada; "te aviso um mês antes da
# garantia acabar" diz.
_PROMESSA = {
    "nota_fiscal": "te aviso um mês antes da garantia de 1 ano acabar",
    "documento": "te aviso 60 e 30 dias antes de vencer",
    "receita": "te aviso quando estiver perto de acabar",
    "vacina": "te aviso quando chegar a hora da próxima dose",
}

BOTOES = ["Confirmar", "Ajustar", "Esquece"]


def pergunta_de_confirmacao(doc: Optional[dict]) -> Optional[dict]:
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
    quando = doc.get("data")
    if quando:
        d = "%s/%s/%s" % (quando[8:10], quando[5:7], quando[0:4])
        linha_data = "Data que eu peguei: *%s*" % d
    else:
        linha_data = "_Não consegui ler a data — toca em *Ajustar* que você me diz._"
    texto = (
        "Isso parece uma *%s*. 📄\n\n"
        "%s\n%s\n\n"
        "Se estiver certo, %s."
        % (doc["rotulo"], doc["descricao"], linha_data,
           _PROMESSA.get(doc["tipo"], "eu te aviso na hora certa"))
    )
    return {"texto": texto, "botoes": list(BOTOES), "doc": doc}
