"""Incremental audit of close safety; collection only, no functional reruns."""
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from xml.etree import ElementTree

root = Path(__file__).resolve().parents[1]
day = root / 'reports' / '2026-09-10'
baseline_path = day / 'incremental_classification_20260910_120454.json'
baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
collected = subprocess.run(
    [sys.executable, '-m', 'pytest', '--collect-only', '-q', 'tests'],
    cwd=root, env=dict(os.environ, PYTHONIOENCODING='utf-8'),
    text=True, encoding='utf-8', capture_output=True, check=True,
)
nodes = {line.strip() for line in collected.stdout.splitlines()
         if line.startswith('tests/') and '::' in line}
assert nodes, 'No collected nodeids'
latest = {}
pattern = re.compile(r'^Teste: (.+)\nRoteiro:.*\nResultado: (\w+)', re.MULTILINE)
for path in sorted((root / 'reports').glob('*/sets/test_set_*.txt')):
    content = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    for node, status in pattern.findall(content):
        latest[node] = {'test': node, 'outcome': status, 'source': str(path)}
rows = [latest.get(node, {'test': node, 'outcome': 'NOT_RUN', 'source': None})
        for node in sorted(nodes)]
before = Counter(row['outcome'] for row in baseline['current_rows'])
after = Counter(row['outcome'] for row in rows)
delta = {key: after[key] - before[key] for key in sorted(before.keys() | after.keys())}
unit_xml = day / 'close_safety_unit_20260910_reviewed.xml'
unit_suite = ElementTree.parse(unit_xml).find('.//testsuite')
unit_results = {key: unit_suite.get(key) for key in ['tests', 'failures', 'errors', 'skipped', 'time']}
assert unit_results['tests'] == '15' and unit_results['failures'] == unit_results['errors'] == '0'
new_rows = [row for row in rows if row['source'] and Path(row['source']).name > 'test_set_20260910_120117_882996.txt']
stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
output = day / f'incremental_close_safety_{stamp}'
payload = {
    'generated': datetime.now().isoformat(), 'baseline_source': str(baseline_path),
    'unit': 'nodeid pytest em tests/; resultados brutos, não cobertura integral do roteiro',
    'baseline': dict(before), 'delta': delta, 'total': dict(after),
    'baseline_nodes': len(baseline['current_rows']), 'total_nodes': len(rows),
    'new_results': new_rows, 'current_rows': rows,
    'infrastructure_tests_separate': {'source': str(unit_xml), **unit_results},
    'new_functional_pass': 0, 'real_pdv_launched': False,
    'reviewer_classifications_separate_from_execution': baseline['reviewer_classifications_separate_from_execution'],
}
output.with_suffix('.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
lines = [
    'SATPDV — INCREMENTAL: PROTEÇÃO DE FECHAMENTO / PENDÊNCIAS',
    f'Gerado: {payload["generated"]}', '',
    'NESTA RODADA — NÃO CONFUNDIR COM PASS FUNCIONAL DO ROTEIRO',
    'PASS funcionais novos: 0. SATPDV não foi iniciado. Nenhum PASS histórico reexecutado.',
    '15 testes unitários da automação passaram (dublês de janela/processo, não homologação).',
    f'JUnit final: {unit_xml}; tempo da suite XML: {unit_results["time"]}s.',
    'As baterias intermediárias tinham 14 casos; a final acrescenta preservação de fresh_instance.',
    'São 15 casos únicos; revalidações após ajustes não são PASS novos distintos.',
    'compileall tests pages core unit_tests: aprovado. Uma indentação foi corrigida',
    'após a primeira checagem estática; nenhuma execução funcional ocorreu com esse erro.',
    'git diff --check aprovado; nenhuma chamada .kill( permanece em core/app.py.', '',
    'CORREÇÃO APLICADA',
    'core/app.py: close não envia ESC nem kill como fallback. Aviso conhecido de venda',
    'aberta (modal ou EditMsg de TFrmPDV) gera PdvCloseBlockedError; timeout/estado',
    'não confirmado gera PdvCloseError. Filtragem por PID, polling, evidência e latch',
    'preservam handles/processo. Captura usa o coletor existente, sem alterar OCR.',
    'tests/conftest.py: pool retém a instância bloqueada, impede reutilização/novo',
    'lançamento e não repete limpeza no teardown da sessão. Fresh não contorna bloqueio.',
    'Não foi adicionada API de kill nem decidido o significado de fechamento de REI-02.',
    'Regressão cobriu sucesso, atraso, modal, EditMsg, PID alheio, timeout, handle',
    'destruído, captura indisponível, ausência de process e preservação no pool.',
    'Limitação: proteção validada em testes unitários; comportamento real de close',
    'não foi reexecutado e não se declara estabilidade E2E por essa validação.', '',
    'CLASSIFICAÇÕES REGISTRADAS',
    'Execução seletiva: 1 skipped, 2 deselected/BLOCKED em 0,17s; sem fixture app.',
    'REI-02: BLOCKED — Pendente de Decisão / Ambiguidade de Roteiro.',
    'FRT-01/02: BLOCKED — Frete1, ordem 1, R$15,00 ausentes; decisão pendente, não gerar.',
    'VEN-20/22/23/32: SKIPPED — depende de configuração externa da homologação,',
    'fora do escopo desta suíte. Um nodeid agrupa os quatro itens.',
    'O texto descritivo de frete foi corrigido para BLOCKED (antes herdava texto',
    'genérico de SKIPPED). Nova coleta só desse caso confirmou bloqueio em 0,10s;',
    'nenhum teste funcional executou; o conjunto anterior foi preservado como histórico.',
    'PAR-01/PAR-02: bloqueios anteriores mantidos; configuração não alterada nesta rodada.',
    'CFG-01/CFG-02: manual por decisão do revisor, sem reclassificação.', '',
    'PRÓXIMAS SEÇÕES RECONSULTADAS NO DOCX',
    'Fiscal/sincronia: critérios envolvem emissão, SEFAZ e atualização fiscal externa.',
    'Parâmetros: PAR-01/PAR-02 aguardam ajuste solicitado; PAR-03 exige gaveta física.',
    'Cashback: F5, Ctrl+R, F2/F3 são pelo teclado, mas resultados de geração/resgate',
    'dependem da API externa. Não há variante exclusivamente de teclado que comprove',
    'esses mesmos resultados sem a integração. Não houve reexecução dos skips históricos.',
    'Não foi declarada nenhuma seção integralmente concluída.', '',
    'AGREGADO BRUTO HISTÓRICO — BASELINE + DELTA = TOTAL',
    f'Baseline: {baseline_path}',
]
for status in delta:
    lines.append(f'{status}: {before[status]} + ({delta[status]:+d}) = {after[status]}')
lines.extend([
    f'Nodeids E2E/classificação: {len(baseline["current_rows"])} + {len(rows)-len(baseline["current_rows"])} = {len(rows)}.',
    'SKIPPED líquido zero: frete sai de SKIPPED (-1) e grupo financeiro entra (+1).',
    'BLOCKED +2: frete reclassificado (+1) e REI-02 individualizado (+1). NOT_RUN -1:',
    'o grupo financeiro agora tem SKIPPED registrado. Os 15 unitários são separados.',
    'O total inclui EXT-01, teste extra; não converter nodeids em cobertura do roteiro.',
    'FAILED históricos (CLI-05/VEN-14) e decisões posteriores de XFAIL constam separados',
    'no JSON; nenhuma execução histórica foi reescrita. MFI-04/SUP-03 é um só bug.',
    'XFAIL novos: 0. Causa-raiz nova de bug do PDV: nenhuma investigada nesta rodada.',
    'VEN-15: pendência de regra/roteiro mantida, sem reclassificação nem reexecução.', '',
    'DIALOGS OBSERVADAS — COMANDOS/INSTRUÇÕES',
    'Nenhuma dialog real observada. Mensagens dos testes unitários são dublês,',
    'não novas evidências do produto. Não houve nova divergência de runtime.', '',
    'FONTES DOS RESULTADOS NOVOS',
])
lines.extend(f'{row["outcome"]} | {row["test"]} | {row["source"]}' for row in new_rows)
output.with_suffix('.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
for suffix in ('.txt', '.json'):
    path = output.with_suffix(suffix)
    assert path.is_file() and path.stat().st_size > 0
    print(path)
print(json.dumps({'baseline': dict(before), 'delta': delta, 'total': dict(after), 'nodes': len(rows)}, ensure_ascii=False))
