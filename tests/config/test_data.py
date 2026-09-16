"""Massa fixa de homologacao informada para os testes do roteiro.

Este modulo contem a massa de teste fornecida pelo QA. A autenticacao real de
gerente continua sendo lida de ``PDV_MANAGER_USER``/``PDV_MANAGER_PASSWORD``
no ``.env``; a senha abaixo serve como dado de referencia do roteiro e nunca
deve ser escrita em logs ou relatorios.
"""

GERENCIA = {
    "MATRICULA": "0003",
    "SENHA": "suporte",
}

CLIENTES = {
    "BLOQUEADO": "06307307170",
    # CPF valido matematicamente, reservado ao fluxo de cliente nao cadastrado
    # (CLI-05); nao deve ser confundido com o cadastro bloqueado acima.
    # CPF válido matematicamente, reservado ao fluxo de cliente não cadastrado
    # (CLI-05). A ausência no cadastro deve ser confirmada pelo comportamento
    # real do ambiente; não confundir com CLIENTES["BLOQUEADO"].
    "NAO_CADASTRADO": "84613297087",
    "CNPJ": "08876956000163",
    "GOIAS": "01991902166",
    "DF": "22249252041",
    # Massa prevista no roteiro para voucher (VEN-11..VEN-15).
    "VOUCHER": "53960629168",
    # Cliente/funcionario de convenio informado pelo QA para VEN-30/VEN-35.
    "CONVENIO": "59192828000",
}

CUPONS = {
    "PERCENTUAL": "teste10",
    "VALOR_FIXO": "teste11",
}

PRODUTOS = {
    "PADRAO": "1",
}

# Massa confirmada no ambiente de homologação: somente estas quatro tabelas
# podem ser exercitadas pelos cenários PRE/GEST.
TABELAS_PRECO = ["1", "2", "3", "4"]

# O ambiente de homologacao autorizado para esta rodada possui apenas o
# vendedor 0; os cenarios FUN/ORC devem reutilizar essa massa sem inventar
# um segundo cadastro.
VENDEDORES = ["0"]
