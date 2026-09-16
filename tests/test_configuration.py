"""Functional tests for the locally supplied PAR-01/PAR-02 expectations."""

import pytest

from tests.config.test_data import PRODUTOS


@pytest.mark.automated
@pytest.mark.configuration
@pytest.mark.par_configuration("PAR-01")
def test_par01_quantity_limit_rejected(pdv, product_code, par_parameters, evidence):
    """PAR-01: quantity above PDVQTDEMAXIMA is rejected by the PDV dialog."""
    rejected_quantity = par_parameters["quantity_maxima"] + 1
    dialog_text = pdv.assert_quantity_limit_rejected(
        product_code or PRODUTOS["PADRAO"], rejected_quantity
    )
    evidence[1].info(
        "PAR-01: quantidade %s acima do limite local %s rejeitada; texto completo=%s",
        rejected_quantity,
        par_parameters["quantity_maxima"],
        dialog_text.replace("\n", " | "),
    )


@pytest.mark.automated
@pytest.mark.configuration
@pytest.mark.par_configuration("PAR-02")
@pytest.mark.skip(
    reason="SKIPPED: requer navegação fora do módulo PDV, fora do escopo desta suíte"
)
def test_par02_inverted_product_layout(pdv, par_parameters, evidence):
    """PAR-02: reservado para execução manual no módulo de Parâmetros.

    O roteiro exige alterar o checkbox ``Inverter a lista de produtos e
    totais à esquerda`` no módulo Parâmetros do Sistema. Esse módulo não é
    acessível nesta suíte/ambiente; portanto o caso deve ser SKIPPED antes de
    criar a fixture ``par_parameters`` ou tentar inferir a configuração por
    variável de ambiente.
    """
    positions = pdv.assert_inverted_product_layout(
        expected_inverted=par_parameters["inverter_lista"] == "S"
    )
    evidence[1].info(
        "PAR-02: layout invertido validado por geometria dos controles Delphi; posições=%s",
        positions,
    )
