# -*- coding: utf-8 -*-
"""As três fixtures que a rodada 2 provou faltar.

A auditoria diagnosticou o padrão dos três P0: regra nova escrita como regex
de palavra-chave e validada contra os textos que a motivaram. O
`_BOLETO_FORTE_RE` foi montado olhando boleto e não olhando comprovante; a
regex de legenda foi montada olhando "já paguei" e não "não paguei"; os
rótulos de data foram montados olhando o boleto de quatro linhas e não a
frase única que o próprio prompt do `_read_image` pede.
"""

# 1. COMPROVANTE bancário de pagamento de TÍTULO.
#    Imprime cedente, nosso número e linha digitável — os mesmos campos do
#    boleto. Era o que fazia o comprovante virar cobrança.
COMPROVANTE_DE_TITULO = """COMPROVANTE DE PAGAMENTO DE TITULO
Cedente: ENEL DISTRIBUICAO SAO PAULO
Nosso Numero: 12345678901
Linha Digitavel: 03399.63290 64000.000006 00125.201020 4 12345678901234
Valor Pago: R$ 187,45
Pagamento efetuado em 12/08/2026
Autenticacao: A1B2C3D4E5"""

COMPROVANTE_DE_TITULO_COM_CAUDA = COMPROVANTE_DE_TITULO + """
DADOS: valor=187,45; vencimento=12/08/2026; beneficiario=Enel; tipo=comprovante"""

# 2. LEGENDAS NEGADAS. Quem fotografa boleto costuma comentar que FALTA
#    pagar — é a leitura mais provável, e era a que marcava como paga.
LEGENDAS_NEGADAS = [
    "essa eu ainda não paguei",
    "não paguei ainda",
    "nao paga ainda",
    "esqueci, não paguei",
    "essa nao foi paga",
    "preciso pagar essa",
    "vou pagar amanhã",
    "ainda não foi paga",
]

LEGENDAS_AFIRMATIVAS = [
    "essa eu já paguei ontem",
    "paguei",
    "já paguei essa",
    "essa está paga",
    "quitei hoje de manhã",
]

# 3. OS MESMOS BOLETOS ACHATADOS EM UMA LINHA.
#    É assim que o `_read_image` responde: o prompt pede UMA frase.
UMA_LINHA_ENEL = (
    "Boleto da Enel Distribuicao Sao Paulo, emitido em 05/08/2026 para "
    "pagamento ate 20/08/2026, valor do documento R$ 187,45")

UMA_LINHA_CONDOMINIO = (
    "Boleto de condominio do Edificio Residencial Sao Jose, data do "
    "documento 01/09/2026, vencimento 10/09/2026, valor R$ 450,00")

UMA_LINHA_SABESP = (
    "Conta de agua da Sabesp com emissao em 02/08/2026 e vencimento em "
    "25/08/2026, total a pagar R$ 92,10")

UMA_LINHA_SEM_ROTULO_DE_VENC = (
    "Boleto Enel emitido em 05/08/2026, valor do documento R$ 187,45, "
    "referente ao consumo de julho")

UMA_LINHA = {
    "enel": (UMA_LINHA_ENEL, 187.45, "2026-08-20"),
    "condominio": (UMA_LINHA_CONDOMINIO, 450.00, "2026-09-10"),
    "sabesp": (UMA_LINHA_SABESP, 92.10, "2026-08-25"),
}

# 4. Boletos legítimos que a lista negativa recusou na rodada 2.
BOLETOS_QUE_A_LISTA_NEGATIVA_COMEU = {
    "estacionamento_mensal": (
        "Boleto Ficha de Compensacao. Beneficiario: Estacionamento Central "
        "Ltda. Mensalidade de estacionamento. Vencimento 20/08/2026. "
        "Valor do Documento R$ 350,00"),
    "condominio_unidade": (
        "Boleto Ficha de Compensacao. Beneficiario: Condominio Solar. "
        "Referente a unidade 42. Vencimento 10/09/2026. "
        "Valor do Documento R$ 620,00"),
    "telefonia_extrato": (
        "Fatura Vivo. Beneficiario: Telefonica Brasil. Extrato detalhado de "
        "chamadas. Vencimento 28/08/2026. Total a pagar R$ 119,90"),
    "luz_com_promocao": (
        "Conta de luz Enel. Beneficiario: Enel SP. Promocao valida para "
        "debito automatico. Vencimento 20/08/2026. "
        "Valor do Documento R$ 187,45"),
}

# 5. Conversa que CITA dinheiro e não é conta nenhuma.
CONVERSAS_COM_DINHEIRO = {
    "vaquinha": ("Print de conversa: me paga os 50,00 da vaquinha? A conta "
                 "de la vence 22/08"),
    "orcamento": ("Orcamento de pintura. Conta de material 2.500,00, mao de "
                  "obra a combinar"),
    "cardapio_fatura": ("Cardapio da pizzaria. Peca ja! Pizza grande 45,00. "
                        "Fatura fechada todo dia 30/08/2026"),
}
