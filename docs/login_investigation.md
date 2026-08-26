# Investigação de inicialização e autenticação

Data: 2026-08-26.

## 1. Como o PDV é iniciado

Na pasta `C:\Users\carollina.silva\Documents\PDV-Homologacao` não existe um arquivo `PDV.exe`. O único executável real encontrado é:

`C:\Users\carollina.silva\Documents\PDV-Homologacao\SATPDV.exe`

Também não foram fornecidos o executável do SAT principal, um projeto Delphi (`.dpr`/`.dproj`) ou um atalho de lançamento. O roteiro menciona tanto “pelo SAT principal” quanto “atalho direto”, mas os artefatos necessários para o primeiro caminho não estão disponíveis.

A automação atual inicia somente o executável configurado em `PDV_EXE_PATH`, sem argumentos ou login inventado:

```text
SATPDV.exe
```

## 2. Janelas observadas

Foi feita uma execução controlada do `SATPDV.exe`, sem informar credenciais e encerrada logo depois da inspeção. Pelo backend Win32 foram observadas:

- `TFrmAbout`, janela transitória de inicialização;
- `TFrmPDV`, janela principal VCL;
- `TApplication` e janelas auxiliares do sistema/VCL;
- nenhum título/classe que indicasse login, senha ou matrícula.

`TFrmPDV` apareceu sem caption textual, mas com classe Win32 `TFrmPDV`. Os controles windowed identificáveis foram `TEdit`, `TMemo`, `TDBGrid` e `TPanel`. Isso explica por que a automação deve localizar a janela também por `class_name`, e não somente por título.

O backend UIA, executado durante a mesma investigação, expôs apenas a janela transitória `TFrmAbout` no intervalo observado; o backend Win32 foi mais informativo para esta aplicação VCL 32-bit. Isso não prova que nenhuma tela filha exista em outro fluxo, apenas que ela não apareceu no lançamento direto observado.

## 3. O que existe no `PDV.pas`

Não foi encontrada rotina de login/autenticação do operador em `PDV.pas`: não há implementação de `Login`, `Autenticar` ou abertura de uma tela de matrícula/senha para a entrada no PDV.

Há três pontos que podem ser confundidos com autenticação:

1. `VerificaLogarPDV` (linhas 3499–3502) apenas define `PDVController.LogarPDV` a partir de `ParamString('PDVRegistrarLogSeparado')`. Esse sinal é usado nas linhas 1432–1440 para registrar teclas no log; não valida usuário ou senha.
2. `Autoriza(...)` é chamado em operações protegidas — descontos, cancelamento, suprimento/sangria e reimpressão — depois que o operador já está carregado. Isso é autorização pontual, não o login inicial.
3. `DUsuarios`, `SENHAS` e `FUNC` aparecem em consultas/autorizações. A consulta em `SENHAS` nas linhas 3163–3164 é para notificações de autorização por token; não é o fluxo de entrada do operador.

## 4. Dependência de estado externo

O formulário usa valores globais já preenchidos antes ou fora dele:

- `VG.sOp_Matricula`, `VG.sOp_Name`, `VG.sOp_Acesso` e `VG.Op_Nivel` são usados para identificar o operador e permissões;
- `LojaPadrao` e `ConfigLocal.Loja` são usados para validar loja/funcionário;
- `FormShow` escreve `VG.sOp_Matricula` e `VG.sOp_Name` em `LabelUsuario` (linhas 4975–4977);
- `FormShow` recupera venda, abre datasets e pode exigir suprimento de abertura (linhas 4933–4966);
- `FormCreate` abre `EMP`, `LOJAS` e outros datasets e valida certificado/configuração NFe (linhas 1767–1806).

Portanto, o login, caso exista no fluxo completo, depende de um módulo anterior — provavelmente o SAT principal ou um bootstrap não fornecido — que popula essas variáveis e seleciona a loja. Não é possível atribuir a tela de login ao `FrmPDV` apenas com os arquivos entregues.

## 5. Banco/configuração

O `PDV.pas` depende de banco FireDAC e usa `EMP`, `LOJAS`, `OE`, `IOE`, `PGTOS`, `CLI`, `PROD`, `NF`, `NFI`, `FUNC`, `SENHAS` e outras estruturas. O login não é consultado diretamente nesse arquivo, mas o operador e permissões precisam estar disponíveis no estado global/banco para o PDV operar.

O `SAT.INI` contém `SolicitaSenha=0` na seção `[Terminal]`, além de configurações de banco, terminal, TEF e NFE. Como não há referência a `SolicitaSenha` no `PDV.pas` analisado, não é seguro concluir que essa chave controla o login do operador; ela deve ser investigada no módulo inicializador ou em outras unidades não fornecidas.

## Conclusão operacional

Não há base para implementar um login fictício. A suíte mantém o login parametrizado e só o executa quando uma janela distinta, com pelo menos dois `TEdit` e título compatível, for realmente encontrada. Se `PDV_LOGIN_REQUIRED=true` e a execução direta abrir apenas `TFrmPDV`, o teste falha com diagnóstico explícito. Se o ambiente usa o executável direto sem login, deve configurar `PDV_LOGIN_REQUIRED=false`; se o login é apresentado pelo SAT principal, é necessário fornecer esse módulo/lançador para automatizar o fluxo correto.

## Revalidação com `C:\SAT Sistemas\SAT_HOMOLOGADAS\SATPDV.exe`

Esse caminho contém o mesmo binário da primeira instalação (SHA-256 `E801E7D229316F75F44A9458C858FC79AA76DB9948501E17E97FCBF23492B65C`). A execução abriu `SAT - PDV`, classe `TFrmPDV`, e uma janela visível `FrmPDVCaixaFechado`. A captura visual mostrou “Caixa Fechado — Por favor dirija-se ao próximo caixa”; essa tela é estado operacional, não autenticação. O teste não clicou nem digitou nela.

### Clique necessário para abrir o login

Após enviar um clique no centro de `TFrmPDVCaixaFechado`, o executável abriu a janela `SAT - Senha de acesso`, classe
`TFrmPassWord`. A janela contém quatro `TEdit` e os botões `OK`, `Cancelar`, `Sair` e `Alterar Senha`; os dois
primeiros campos ordenados verticalmente são usados como campos de acesso. Em desktop não interativo, o Windows reporta
esses controles como invisíveis, por isso a automação usa a classe da janela e mensagens Win32, sem depender de
coordenadas fixas da tela. O preenchimento continua condicionado a `PDV_USER` e `PDV_PASSWORD` reais.
