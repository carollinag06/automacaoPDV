# Mapa da interface VCL

Fonte: `PDV.dfm` e declarações/eventos de `PDV.pas`. O DFM contém 138 objetos, mas vários são painéis de layout e imagens. O `Name` do componente é a referência lógica; o runtime pode não expô-lo como `AutomationId`.

## Hierarquia principal

```text
FrmPDV : TFrmPDV (Caption="SAT - PDV", maximizado, KeyPreview=True)
├── pnlTopo : TPanel
│   ├── LabLojaEVendedor : TLabel
│   ├── LabelUsuario : TLabel
│   ├── Label15 : TLabel (tabela de preço)
│   ├── Label17 : TLabel (vendedor)
│   ├── LabTerminal : TLabel
│   └── LabelTroco : TLabel / LabelTrocoTitle
├── pnlProduto : TPanel
│   ├── EditCodigoProduto : TEdit
│   ├── Label1 : TLabel (Código do produto)
│   ├── LabQde : TPanel (Quantidade)
│   ├── LabPreco : TPanel (Valor Unid)
│   └── LabTotalItem : TPanel (Total)
├── pnlDescProduto : TPanel
│   ├── EditTexto : TLabel (descrição/mensagem do item)
│   └── DBImgFoto : TJvDBImage
├── pnlProdutos : TMemo (grid textual da venda)
├── GridProcura : TDBGrid (pesquisa; inicialmente Visible=False)
├── GridLoja : TDBGrid (estoque; inicialmente Visible=False)
├── pnlTotalizadores : TPanel
│   ├── LabSubTotal : TLabel
│   ├── LabTotalDesconto : TLabel
│   ├── LabTotalCashback : TLabel
│   ├── LabTotalVenda : TLabel
│   └── LabTotalQuantidade : TLabel
└── pnlRodape : TPanel
    ├── Panel2/Label20 (Digitar Código de Barras)
    ├── Panel28/Label21 (Alt+Delete Excluir Produto)
    ├── Panel31/Label22 (F3 - Fechar Venda)
    ├── Panel32/Label23 (Ctrl+F1 - Ajuda)
    ├── LabelStatusVendas
    ├── LabelStatusNFCeContigencia
    ├── LabelStatusSincronia
    └── pnlCancelar/Label24 (Cancelar Venda)
```

## Controles para automação

| Componente lógico | Classe VCL | Uso no teste | Evidência |
|---|---|---|---|
| `FrmPDV` | `TFrmPDV` | janela principal e escopo dos seletores | DFM linha 1 |
| `EditCodigoProduto` | `TEdit` | entrada de código, quantidade prefixada e Enter | DFM: `OnKeyDown=EditCodigoProdutoKeyDown`; PAS: `ParseRef` |
| `pnlProdutos` | `TMemo` | linhas exibidas dos itens da venda | PAS: `EscalaMemo`, `AdicionarLinhaMemo` |
| `GridProcura` | `TDBGrid` | resultado de busca de produto | DFM: `DProcura`, colunas de código/descrição |
| `GridLoja` | `TDBGrid` | estoque no modo consulta | DFM hint `Estoque das Lojas`; `Ctrl+E` chama `ExibirEstoqueModoConsulta` |
| `LabQde`, `LabPreco`, `LabTotalItem` | `TPanel` | dados do item corrente | DFM captions `0,00`; labels adjacentes descrevem o campo |
| `LabSubTotal` | `TLabel` | subtotal | `AlterarTotal` atualiza com `Val(PDVController.Total)` |
| `LabTotalDesconto` | `TLabel` | desconto acumulado | `AlterarTotal` atualiza com `TotalDescontoVenda` |
| `LabTotalVenda` | `TLabel` | total a pagar | `AlterarTotal` calcula `Total - descontos - cashback` |
| `LabTotalQuantidade` | `TLabel` | quantidade total | `AlterarTotal` atualiza `eQtdeTotal` |
| `EditMsg` | `TLabel` | mensagem persistente no topo/rodapé | `ExibirMsg` atribui `Caption` |
| `EditTexto` | `TLabel` | descrição do produto e mensagens de estado | `ExibirTexto` atribui `Caption` |
| `LabelTroco` | `TLabel` | troco | `ExibirTroco` |
| `Panel31`/`Label22` | `TPanel`/`TLabel` | finalização visual | chama `VK_F3` |
| `pnlCancelar`/`Label24` | `TPanel`/`TLabel` | cancelamento visual | chama `VK_F6` |

## Eventos importantes

- `FrmPDV.OnKeyDown=FormKeyDown`: roteia os atalhos funcionais.
- `FrmPDV.OnKeyPress=FormKeyPress`: envia caracteres ao `EditCodigoProduto` quando outro controle está ativo.
- `EditCodigoProduto.OnKeyDown=EditCodigoProdutoKeyDown`: ao Enter chama `PDVController.ParseRef`.
- `GridProcura.OnKeyPress=GridProcuraKeyPress`: confirma seleção de busca.
- `Panel1`, `Label3`: alternam a exibição do estoque.
- `Panel2`: abre entrada de código de barras.
- `Panel28`: abre exclusão de produto.
- `Panel31`: finaliza com F3.
- `Panel32`: abre ajuda.
- `pnlCancelar`: cancela com F6.

## Seletores e limitações

O DFM não é garantia de que o nome Delphi será exposto pelo Win32/UIA. A implementação tenta primeiro o backend UIA e depois Win32, restringindo a busca à janela do processo. Na execução controlada do `SATPDV.exe`, `TFrmPDV` foi observado sem caption textual; por isso o localizador considera também `class_name`. Quando não consegue obter um controle por classe/título, a suíte registra uma árvore diagnóstica e falha com mensagem de ação; não usa coordenada como fallback padrão.

As telas filhas citadas no `uses` de `PDV.pas` (`PDVPgtos`, `PDVParcelamento`, `PDVAjuda`, `MenuFiscal`, `PDVCPF`, `DPDVConsultarOrcamentos`, `DPDVSuprimentoSangria`, entre outras) não têm seus DFM/fontes anexados. Elas ficam classificadas como `runtime dependency`: seus títulos e controles precisam ser capturados em uma execução instrumentada antes de adicionar seletores específicos.
