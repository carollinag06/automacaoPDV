# Suíte E2E do SATPDV

Suíte inicial de automação Windows para o `SATPDV.exe`, baseada no roteiro `SATPDV_241023B.docx`, no `PDV.pas` e no `PDV.dfm` fornecidos.

## Estado atual

Esta primeira etapa entrega a análise dos artefatos, o mapa do formulário, a matriz do roteiro, a arquitetura Page Object, a PoC de abertura/login e o primeiro fluxo de inserção de produto. Os testes são opt-in: sem configuração real eles são marcados como `SKIPPED`, nunca como PASS.

## Requisitos

- Windows com acesso ao executável e ao ambiente de homologação.
- Python 3.11+.
- Dependências de `requirements.txt`.
- Banco, certificado, impressora e integrações configurados conforme o cenário executado.

## Instalação e configuração

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Preencha `.env` somente com credenciais de teste. O arquivo é ignorado pelo Git. Também é possível usar `config/test_config.json`; o `.env` tem precedência.

Variáveis essenciais:

- `PDV_EXE_PATH`: caminho do `SATPDV.exe`.
- `PDV_USER` e `PDV_PASSWORD`: credenciais de homologação.
- `PDV_LOGIN_REQUIRED`: `true` quando o executável exigir login.
- `PDV_PRODUCT_CODE`: produto estável do banco de testes.
- `PDV_ALLOW_REAL_RUN`: `true` somente quando o teste puder alterar o ambiente de homologação.

## Execução

```powershell
pytest
pytest tests/test_initial.py
pytest tests/test_poc.py -m automated
pytest -m discounts
pytest -m automated
pytest -m manual
```

Os relatórios ficam em `reports/YYYY-MM-DD/`. Em falha são gravados screenshot, log JSONL e contexto da janela. A opção `--html=reports/.../results.html` pode ser usada quando `pytest-html` estiver instalado.

## Segurança e limites

O projeto não copia nem modifica `SATPDV.exe`, `PDV.pas`, `PDV.dfm` ou `SAT.INI`. Não contém credenciais reais. Cenários que exigem banco, internet, SEFAZ, certificado, impressora, gaveta, balança, TEF, Pix, Moovpay ou alteração de cadastros estão classificados em `docs/automation_scope.md` e não são simulados para produzir falso PASS.

## Referências analisadas

- `C:\Users\carollina.silva\Documents\PDV-Homologacao\PDV.pas`
- `C:\Users\carollina.silva\Documents\PDV-Homologacao\PDV.dfm`
- `C:\Users\carollina.silva\Documents\PDV-Homologacao\SATPDV_241023B.docx`
- `C:\Users\carollina.silva\Documents\PDV-Homologacao\SATPDV.exe`
- `C:\Users\carollina.silva\Documents\PDV-Homologacao\SAT.INI` (somente inspeção de chaves; valores sensíveis não foram reproduzidos)
