# Análise dos artefatos SATPDV

## Atualização da investigação de login

O primeiro lançamento observado não fazia o clique exigido pela tela inicial. Após clicar em `TFrmPDVCaixaFechado`, o
executável abriu o formulário real `SAT - Senha de acesso` (`TFrmPassWord`). A automação agora executa essa interação
antes de procurar os campos de credenciais, sem digitar login fictício.

Data da análise: 2026-08-26.

## Resultado executivo

O roteiro foi extraído integralmente do DOCX: 1.274 blocos não vazios na ordem do documento. A matriz em `docs/test_matrix.csv` contém 203 cenários funcionais identificáveis, preservando os valores e resultados escritos no roteiro. A classificação atual é: 96 candidatos automatizáveis, 45 parcialmente automatizáveis, 33 manuais e 29 bloqueados por ambiente. O roteiro possui grupos numerados de 1 a 21, com numeração repetida em alguns pontos, além de uma seção final de chamados.

O executável foi inspecionado sem execução: `SATPDV.exe` é um PE 64-bit/Windows de 94.129.664 bytes, sem símbolos ou metadados funcionais úteis na versão disponível; `FileVersionInfo` informa `1.0.0.0` e descrição/produto `SATPDV`. A interface verificável veio principalmente do DFM e do código Delphi.

Em uma execução controlada do executável direto, a janela principal foi observada como classe Win32 `TFrmPDV`, sem caption textual, com `TEdit`, `TMemo`, `TDBGrid` e `TPanel` windowed. Não apareceu diálogo de login e os `TLabel` do DFM não foram expostos como handles Win32. Isso é uma divergência de runtime: o login deve ser exercitado pelo lançador que realmente o apresenta ou o ambiente deve usar `PDV_LOGIN_REQUIRED=false`; a suíte não digita credenciais no campo de produto por engano.

A investigação detalhada de inicialização/autenticação está em `docs/login_investigation.md`. Não existe `PDV.exe` nos artefatos fornecidos; o caminho observável é o `SATPDV.exe` direto. `VerificaLogarPDV` no Delphi controla logging separado, não autenticação.

## Fontes e evidências

| Artefato | Evidência observada |
|---|---|
| `PDV.pas` | Unidade `PDV`, classe `TFrmPDV`, componentes e eventos declarados no início do arquivo; lógica de teclado em `FormKeyDown` (linhas 3667–3834); inserção de produto em `EditCodigoProdutoKeyDown` (1367–1420); totais em `AlterarTotal` (3593–3606); cancelamento em `CancelarVendaAtual` (2204 em diante); orçamento em `SalvarComoOrcamento` (4608–4638). |
| `PDV.dfm` | Formulário `FrmPDV`, caption `SAT - PDV`, `TForm` sem borda, maximizado, `KeyPreview=True`; controles nomeados e eventos VCL. |
| `SATPDV_241023B.docx` | Roteiro completo, incluindo testes iniciais, vendas, descontos, clientes, fiscal, TEF, reimpressão, orçamento, cancelamento, certificado, balança, menu fiscal, mesas, vendedores, reinício, sincronia, configuração, parâmetros e cashback. |
| `SATPDV.exe` | Metadados PE somente; não houve alteração ou instrumentação do binário. |
| `SAT.INI` | Seções de impressoras, dados, SiTEF, Auttar, terminal, balança, NFE, MoovPay, WebService, licença e layout. Valores foram deliberadamente omitidos. |

## Grupos do roteiro

1. Testes iniciais: abertura/login, pausa, ajuda, ESC, gaveta, consulta de preço, produto, remoção e licença.
2. Venda: SiTEF, CPF/CNPJ, finalização fiscal/não fiscal, pagamentos múltiplos, voucher, alto valor, parcelamento, fiscais, balança e convênio.
3. Descontos: percentual, valor, quantidade negativa, limites por produto/usuário/loja, cupons, acréscimo, arredondamento e rateio.
4. Alteração de produtos e observações.
5. Tabela de preços.
6. Gestão de preços e promoções.
7. Cadastro e saldo de cliente.
8. Notas fiscais: NFC-e/NF-e, contingência, XML, duplicidade, série, devolução e entrega.
9. TEF, Pix Inter, Moovpay e validações de pagamento.
10. Reimpressão.
11. Orçamento.
12. Cancelamento.
13. Certificado digital.
14. Balança e menu fiscal.
15. Mesas.
16. Funcionários/vendedores.
17. Reinício/fechamento.
18. Sincronia.
19. Configuração local.
20. Parâmetros do sistema.
21. Cashback.

## Atalhos confirmados no código

| Atalho | Implementação observada em `FormKeyDown` | Resultado/método |
|---|---|---|
| `Ctrl+F1` | `VK_F1` + `ssCtrl` | `ExibirAjuda` |
| `F1` | `VK_F1` sem modificador, salvo PAF | `F1AlternarEntreConsultaEVendaDeProdutos` |
| `Shift+F1` | `VK_F1` + `ssShift` | `ExibeListProd` |
| `F2` | `VK_F2` | `FinalizarVendaSemValorFiscal` |
| `F3` | `VK_F3` | `FinalizarVenda` |
| `F4` | `VK_F4` | CPF/CNPJ no PinPad quando aplicável |
| `Shift+F4` | `VK_F4` + `ssShift` | `AlterarTabelaPrecos` |
| `Ctrl+F4` | `VK_F4` + `ssCtrl` | `SalvarComoOrcamento` |
| `F5` | `VK_F5` | CPF/CNPJ pelo teclado |
| `Ctrl+F5` | `VK_F5` + `ssCtrl` | CNPJ pelo teclado |
| `F6` | `VK_F6` | `CancelarVendaAtual` |
| `F7` | `VK_F7` | `ReprintVendaFiscal` |
| `Ctrl+F7` | `VK_F7` + `ssCtrl` | `ReimprimirCupomNaoFiscal` |
| `Shift+F7` | `VK_F7` + `ssShift` | `ExibirRelatorioDeEntrega` |
| `F8` | `VK_F8` | `ExibirTelaDeMenuFiscal` |
| `F9` | `VK_F9` | `AbrirGaveta` |
| `F10` / `Shift+F10` | `VK_F10` | `SolicitarVendedor`, por item ou todos |
| `F11` / `F12` | `VK_F11` / `VK_F12` | desconto percentual / valor |
| `Shift+F11` / `Shift+F12` | `VK_F11` / `VK_F12` + `ssShift` | acréscimo percentual / valor |
| `Ctrl+F2` / `Ctrl+F3` | `VK_F2` / `VK_F3` + `ssCtrl` | suprimento / sangria |
| `Ctrl+F6` | `VK_F6` + `ssCtrl` | cancela última venda/documento |
| `Ctrl+F8` / `Shift+F8` | `VK_F8` + modificador | fechamento, com ou sem operador/data |
| `Ctrl+F9` | `VK_F9` + `ssCtrl` | observações |
| `Ctrl+F10` | `VK_F10` + `ssCtrl` | TEF administrativo |
| `Ctrl+F11` | `VK_F11` + `ssCtrl` | Sincronia |
| `Ctrl+F12` | `VK_F12` + `ssCtrl` | parcelamento |
| `Ctrl+C` | `C` + `ssCtrl` | saldo do cliente |
| `Ctrl+O` | `O` + `ssCtrl` | consultar/inserir orçamento |
| `Alt+C` | `C` + `ssAlt` | cupom de desconto |
| `Alt+P` | `P` + `ssAlt` | pausa |
| `ESC` | `VK_ESCAPE` | fecha ajuda/consulta ou fecha/cancela o PDV |

O pedido original também cita `Ctrl+E`, `Ctrl+D`, `Ctrl+R`, `Ctrl+S`, `Alt+Delete` e `Shift+F2`; `Ctrl+E` (estoque), `Ctrl+D` (devolução), `Ctrl+R` (cashback) e os demais aparecem no código ou nos chamados. `Ctrl+S` não foi observado em `TFrmPDV.FormKeyDown`; o roteiro deve ser tratado como autoridade funcional, mas a divergência deve permanecer documentada.

## Produtos, clientes, pagamentos e valores do roteiro

Produtos recorrentes: 1, 2, 3, 4, 5, 12, 17, 21, 25, 30, 31, 32, 33, 36, 37, 38, 40, 43, 45, 46, 47 e códigos de barras `7894900011531` e `0000000000024`. O roteiro atribui semântica específica a vários deles; esses dados precisam existir no banco de testes e não são inventados pela suíte.

Clientes recorrentes: CPF `539.606.291-68`, `291.447.310-94`, `591.928.280-00`, `70893440108` e CNPJ `74.681.956/0001-32`/`74.681.956/0001-12`. O código Delphi grava/consulta `CLI`, `CodCli`, `CPF`, `Limite` e campos de cliente, mas o roteiro exige muitos campos cadastrais que não são carregados no DFM principal.

Formas de pagamento: dinheiro, cartão/TEF, Pix, Moovpay, Outros, Convênio, Limite de crédito e Vale Troca/Voucher. Os cenários fiscais exigem NFC-e/NF-e, XML, impressão, autorização e, em TEF, tags `tPag`, `tpIntegra`, `CNPJ` e `cAut`.

Valores explícitos que devem ser preservados nas asserções: venda de R$ 28,00 com desconto R$ 2,00; desconto R$ 1,00; desconto negativo levando a R$ -0,90 e depois R$ -0,80; produto 37 com piso R$ 5,00; produto 38 com desconto limitado a R$ 0,50 e total R$ 4,50; produto 43 com total R$ 9,00 e desconto R$ 0,99; desconto R$ 300,50; produto 17 com subtotal R$ 3,00, desconto R$ 0,75 e total R$ 2,25; produto 33 em tabelas 2/3/4 com R$ 2.050,00/R$ 3.750,00/R$ 4.500,00; item 12 com dizima final R$ 0,01.

## Banco e efeitos colaterais observados

O código usa FireDAC (`DZ.ZDatabase`) e abre/atualiza pelo menos `EMP`, `LOJAS`, `OE`, `IOE`, `PGTOS`, `CLI`, `PROD`, `PRODUTOSPRECOS`, `NF`, `NFI`, `MOV_SUPRIMENTOS`, `FUNC` e `SENHAS`. Há consultas explícitas a `OE`, `IOE`, `NF`, `NFI`, `CLI`, `LOJAS`, `PROD` e `MOV_SUPRIMENTOS`; há `DELETE` de `NFI`, `NF`, `OE` e `IOE` ao cancelar uma venda. Por isso a camada de banco da suíte é somente leitura por padrão e não contém UPDATE/DELETE automático.

## Dependências externas

| Dependência | Classificação |
|---|---|
| Banco SAT/FireDAC | necessária para quase todos os fluxos reais; ambiente isolado e backup são pré-condições |
| Internet/SEFAZ/Sincronia | NFC-e, contingência, rejeição, duplicidade, CEP, cashback e Pix Inter |
| Certificado digital | abertura/emissão NFC-e/NF-e |
| Impressora fiscal/não fiscal/spooler/gaveta | impressão, fechamento, recibo, reimpressão, suprimento/sangria e gaveta |
| PinPad/SiTEF/Auttar/TEF | CPF pelo PinPad, cartões, cancelamentos, comprovantes e XML integrado |
| Pix Inter/Moovpay | pagamentos e cancelamentos externos |
| Balança/VSPE | itens de balança e pesos manuais |
| Gerenciador de tarefas/reinício | cenários de recuperação de venda e etapas de emissão |

Nenhuma aprovação externa é simulada como sucesso. Sem o recurso, o cenário é `BLOCKED` ou `MANUAL`.
