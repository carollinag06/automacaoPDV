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

## Classificação antes da execução

`manual` identifica dependência física/externa ou decisão expressa do revisor
(CFG-01/CFG-02). Os casos reservados em `tests/test_configuration.py` registram
SKIPPED e o motivo, sem iniciar o PDV.

`blocked(reason="...")` identifica massa, configuração compartilhada ou seletor
pendente. `pytest_collection_modifyitems` exclui esses casos após os filtros
`-k`/`-m`, antes de qualquer fixture, e o relatório os registra como BLOCKED,
com duração zero. A saída nativa do pytest os conta como `deselected`.
`--collect-only` apenas inventaria os casos e não grava resultado.
Uma seleção contendo somente blocked pode retornar código 5 do pytest
(nenhum teste executado); o relatório explica os bloqueios. Ao resolver a
pendência, implemente o fluxo e retire o marker antes da validação funcional.

O último resultado por nodeid representa a execução do código disponível
naquele momento, não necessariamente a cobertura integral do item do roteiro.
Reclassificações do revisor e IDs renomeados devem ser registrados separadamente,
preservando o resultado bruto e sua fonte. MFI-04/SUP-03 são dois nodeids,
mas representam um único problema conhecido.

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
# Fechamento seguro e REI-02 (10/09/2026)

`PdvApplication.close()` solicita apenas fechamento normal. Não envia ESC de
fallback e não chama `process.kill()` quando o processo permanece ativo.
Reconhece o aviso de venda aberta em janela do mesmo PID ou no `EditMsg` do
`TFrmPDV` (`PDV.pas`, `FormClose`/`ExibirMsg`). O aviso gera
`PdvCloseBlockedError`; timeout sem aviso reconhecido gera `PdvCloseError`.
Ambos preservam referências e registram evidências em `reports/YYYY-MM-DD/`.
Erros de captura não autorizam limpeza. O pool preserva a instância bloqueada,
recusa reutilização/novo lançamento e não repete fechamento no fim da sessão.
Isso pode expor testes antigos que dependiam do kill implícito: não converter
essas falhas automaticamente em XFAIL do produto.

REI-02 permanece `blocked` por decisão do revisor (ambiguidade de fechamento
normal versus abrupto), não bug nem SKIPPED por hardware. Nenhuma chamada de
terminação forçada foi adicionada. Os testes em `unit_tests/test_app_close.py`
usam dublês, não abrem o PDV e não contam como PASS do roteiro.
