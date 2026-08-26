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

Coordenadas de tela não fazem parte do caminho padrão. Se um controle VCL não for exposto por UIA/Win32, a falha inclui diagnóstico dos títulos, classes e automation IDs vistos para permitir um mapeamento posterior baseado em evidência.
