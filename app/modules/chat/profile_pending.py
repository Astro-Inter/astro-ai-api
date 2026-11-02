"""Count distinct applicable NRs using the existing authorized SST criteria."""
from app.modules.chat.schemas import PendenciasFicha


def summarize_nr_pending(result):
    unavailable = PendenciasFicha(status='indisponivel').model_dump(mode='json')
    if result.get('status') == 'nao_aplicavel':
        return PendenciasFicha(status='nao_aplicavel').model_dump(mode='json')
    rows = result.get('nrs')
    if result.get('status') != 'ok' or not isinstance(rows, list):
        return unavailable
    if 'quantidade' in result and result['quantidade'] != len(rows):
        return unavailable
    states = {}
    pending = {'PENDENTE', 'RENOVACAO_NECESSARIA', 'REALIZACAO_NECESSARIA'}
    for row in rows:
        if not isinstance(row, dict):
            return unavailable
        nr, status = row.get('numero'), row.get('situacao')
        if type(nr) is not int or not 1 <= nr <= 100 or status not in pending | {'VIGENTE'}:
            return unavailable
        if nr in states and states[nr] != status:
            return unavailable
        states[nr] = status
    reference = result.get('data_referencia')
    if reference is not None and (not isinstance(reference, str) or len(reference) > 30):
        return unavailable
    return PendenciasFicha(status='ok', quantidade=sum(status in pending for status in states.values()),
                           data_referencia=reference).model_dump(mode='json')


def pending_caption(summary):
    if summary['status'] == 'ok':
        return f"Pendências de NRs: {summary['quantidade']}. Cada NR é contada uma vez; não inclui outras categorias de pendência."
    if summary['status'] == 'nao_aplicavel':
        return 'O indicador de pendências de NRs não se aplica a este perfil.'
    return 'Não foi possível confirmar a quantidade de pendências de NRs agora.'
