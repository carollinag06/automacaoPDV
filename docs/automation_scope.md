# Escopo de automação

## REAL

Os fluxos abaixo podem ser executados contra o ambiente real de homologação quando os dados existirem e `PDV_ALLOW_REAL_RUN=true`:

- localizar o processo/janela e confirmar `SAT - PDV`;
- login com credenciais vindas de `.env`/JSON;
- autorizações de gerente devem reutilizar `TestConfig.manager_credentials`, carregado do `.env` sem registrar a senha;
- pausa, ajuda e ESC;
- inserir produto no `TEdit` real e validar o código no `TMemo` real;
- remoção/cancelamento de venda quando o ambiente de teste permitir;
- totais e descontos quando a tela filha expuser controles/títulos verificáveis;
- orçamento e cliente, após mapear os DFM das telas filhas.

## MOCK/STUB

Não há mock implementado para aprovar TEF, Pix, Moovpay, SEFAZ ou impressora. O código Delphi chama integrações reais (`DMTEF`, `DMPix`, emissão NFC-e/NF-e e impressoras); substituir esses resultados sem uma interface de teste do próprio SAT poderia mascarar falhas. A evolução recomendada é criar adaptadores no lado da suíte somente para dados de entrada/consulta, mantendo o resultado externo explicitamente falso ou indisponível.

## MANUAL/BLOCKED

Permanecem manuais ou bloqueados, conforme a matriz:

- SiTEF, Auttar, PinPad, Pix Inter, Moovpay e consultas web de status;
- impressora fiscal/não fiscal, spooler, comprovantes, guilhotina e gaveta;
- balança/VSPE;
- certificado digital, SEFAZ, NFC-e/NF-e, contingência, rejeição e XML autorizado;
- alterações de cadastro, parâmetros, banco e restauração de dados;
- reinícios no Gerenciador de Tarefas e interrupções em passos de emissão;
- sincronia e cashback externos.

Não são falsos PASS: um cenário sem dependência disponível deve terminar em `SKIPPED`, `BLOCKED` ou `MANUAL`, com a dependência registrada.

## Limpeza

A PoC cancela a venda aberta com F6 e encerra o processo no teardown. A suíte não executa UPDATE/DELETE no banco nem cria produtos, clientes, cupons, fretes ou preços. Cenários que exigem criação/alteração precisam de fixture transacional aprovada para o banco de homologação e restauração explícita antes de serem ativados.
