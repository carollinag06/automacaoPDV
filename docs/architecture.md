# Arquitetura

```text
tests/                 casos pytest, pequenos e orientados a resultado
pages/                 Page Objects das telas VCL
core/                  processo, configuração, teclado, evidências e parsing
config/                exemplos; nenhum segredo real
docs/                  análise, mapa, matriz e classificação
reports/YYYY-MM-DD/    screenshots, logs e resultados gerados
```

O Page Object principal usa `FrmPDV` como escopo e os elementos confirmados no DFM: `TEdit` para `EditCodigoProduto`, `TMemo` para `pnlProdutos`, `TLabel` por caption para totalizadores e `TPanel`/`TLabel` visuais somente quando o evento está confirmado no código. O nome Delphi permanece documentado como referência, mas não é usado como `AutomationId` sem confirmação no runtime.

O fluxo de processo é:

```text
configuração -> start SATPDV.exe -> localizar janela por PID/título
             -> login configurável -> Page Object FrmPDV
             -> ação -> asserção explícita -> cancelamento/limpeza
             -> screenshot + log + contexto em caso de erro
```

## Reuso de processo e isolamento

Os testes que usam a fixture `app` compartilham uma única instância autenticada
por sessão. A fixture de função ainda executa o teardown de cada teste e chama
`PdvApplication.reset_for_next_test()`: fecha modais conhecidos, cancela venda
aberta preenchendo o motivo, retorna de consulta de preços com F1 e reabre o
login quando o teste deixou `TFrmPDVCaixaFechado`. Assim, o estado residual é
observado no relatório do teste que o produziu, em vez de ser limpo
silenciosamente no setup seguinte.

Os cenários que precisam do processo recém-iniciado, inclusive os que validam
fechamento deliberado do PDV como INI-06, usam `@pytest.mark.fresh_instance`.
`raw_app` também é isolada por desenho para testes de login, como
`test_poc_invalid_login`; ao executar um cenário fresh, o pool encerra a
instância compartilhada, inicia uma nova e permite que os testes normais a
recriem depois. O fechamento definitivo ocorre no teardown da sessão quando
`PDV_CLOSE_AFTER_TEST=true`.

Coordenadas de tela não fazem parte do caminho padrão. Se um controle VCL não for exposto por UIA/Win32, a falha inclui diagnóstico dos títulos, classes e automation IDs vistos para permitir um mapeamento posterior baseado em evidência.

## Visibilidade durante a execução

O SATPDV pode criar `TFrmPDV`/`TFrmPDVCaixaFechado` como janela VCL sem
borda. O pool chama `PdvApplication.maximize_window()`, que aplica
`ShowWindow`/`SetWindowPos` no HWND, sempre limitado ao monitor atual. O modo
`fullscreen` (padrão) ocupa somente o monitor atual; `native` preserva o
layout fixo 800x600 do DFM e centraliza a janela caso seja preferível evitar
área vazia no canvas. Os Page Objects não chamam
`restore()` nas ações da tela principal, pois isso reduziria novamente a
janela e esconderia parte do fluxo do QA; modais específicos continuam
podendo ser restaurados quando necessário.
