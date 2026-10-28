"""Generate separate business and technical Grafana dashboards. Run after editing panel definitions."""
import json
from pathlib import Path

panels = []
y = 0


def row(title):
    global y
    panels.append({'id': len(panels)+1, 'type': 'row', 'title': title, 'collapsed': False,
                   'gridPos': {'x': 0, 'y': y, 'w': 24, 'h': 1}, 'panels': []})
    y += 1


def panel(title, expressions, unit='short', stat=False, description='', warning=False):
    global y
    index = sum(p.get('type') != 'row' and p['gridPos']['y'] == y for p in panels)
    width = 6 if stat else 12
    if index * width >= 24:
        y += 4 if stat else 8
        index = 0
    thresholds = [{'color': 'green', 'value': None}, {'color': 'red', 'value': 1}] if warning else [
        {'color': 'blue', 'value': None}]
    targets = [{'refId': chr(65+i), 'expr': expr, 'legendFormat': legend,
                'datasource': {'type': 'prometheus', 'uid': 'prometheus'}, 'instant': stat, 'range': not stat}
               for i, (expr, legend) in enumerate(expressions)]
    panels.append({'id': len(panels)+1, 'type': 'stat' if stat else 'timeseries', 'title': title,
                   'description': description, 'datasource': {'type': 'prometheus', 'uid': 'prometheus'},
                   'gridPos': {'x': index*width, 'y': y, 'w': width, 'h': 4 if stat else 8},
                   'targets': targets,
                   'fieldConfig': {'defaults': {'unit': unit, 'thresholds': {'mode': 'absolute', 'steps': thresholds},
                                               'color': {'mode': 'thresholds' if stat else 'palette-classic'}}, 'overrides': []},
                   'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                               'colorMode': 'value', 'graphMode': 'none', 'textMode': 'auto',
                               'legend': {'displayMode': 'list', 'placement': 'bottom'},
                               'tooltip': {'mode': 'multi'}}})


def end(height=8):
    global y
    y += height


def write_dashboard(uid, title, tag):
    for item in panels:
        defaults = item.get('fieldConfig', {}).get('defaults', {})
        if defaults.get('unit') == 'percentunit':
            defaults.update(min=0, max=1)
    dashboard = {
        'uid': uid, 'title': title, 'tags': ['local', 'demo', tag],
        'schemaVersion': 39, 'version': 1, 'timezone': 'browser', 'refresh': '10s',
        'time': {'from': 'now-30m', 'to': 'now'}, 'panels': panels, 'editable': False,
        'annotations': {'list': []}, 'templating': {'list': []},
        'links': [
            {'title': 'Бизнес', 'type': 'link', 'url': '/d/prediction-market', 'keepTime': True},
            {'title': 'Техника', 'type': 'link', 'url': '/d/prediction-market-technical', 'keepTime': True},
        ],
    }
    Path(__file__).parent.joinpath(f'grafana/dashboards/{uid}.json').write_text(
        json.dumps(dashboard, ensure_ascii=False, indent=2)+'\n')


row('Обзор · синтетические локальные данные')
panel('Пользователи Mini App', [('pm_users{kind="registered",window="all"}', 'Аккаунты')], stat=True)
panel('Активные · 24 часа', [('pm_users{kind="active",window="24h"}', 'Пользователи')], stat=True,
      description='Уникальные аккаунты с app_open за последние 24 часа.')
panel('Оборот · 24 часа', [('sum(pm_trade_volume_coins{window="24h"})', 'Монеты')], unit='suffix: монет', stat=True,
      description='Сумма абсолютных Trade.cost: покупки и продажи, включая комиссии. Не выручка.')
panel('Сделки · 24 часа', [('sum(pm_trades{window="24h"})', 'Сделки')], stat=True)
end(4)
row('Пользователи · когорты определены стартовым пакетом')
panel('Новые регистрации', [('pm_users{kind="registered",window!="all"}', '{{window}}')])
panel('Активность и торговля', [('pm_users{kind=~"active|trading",window!="all"}', '{{kind}} · {{window}}')])
end()
row('Торговля и рынки · пользовательские LMSR')
panel('Оборот · скользящие окна', [('pm_trade_volume_coins{window!="all"}', '{{direction}} · {{window}}')], 'suffix: монет')
panel('Сделки · скользящие окна', [('pm_trades{window!="all"}', '{{direction}} · {{window}}')])
end()
panel('Рынки по статусам', [('pm_markets', '{{state}}')])
panel('Создание рынков', [('pm_markets_created', '{{window}}')])
end()
panel('Начисленные комиссии · до возвратов', [('pm_trade_fees_coins{window="24h"}', '{{direction}}')], 'suffix: монет')
panel('Споры', [('pm_disputes', '{{state}}')])
end()
row('Экономика · виртуальные монеты, не денежная выручка')
panel('Баланс пользователей', [('pm_balance_coins', 'Баланс')], 'suffix: монет')
panel('Эмиссия бонусов · 24 часа', [('pm_ledger_coins{window="24h",kind=~"starter|daily|insurance|referral"}', '{{kind}}')], 'suffix: монет')
end()
panel('Выплаты и возвраты · 24 часа', [('pm_ledger_coins{window="24h",kind=~"payout|refund|subsidy_return|dispute_refund"}', '{{kind}}')], 'suffix: монет')
panel('Комиссия авторам · 24 часа', [('pm_ledger_coins{window="24h",kind="creator_fee"}', 'Авторам')], 'suffix: монет')
end()
write_dashboard('prediction-market', 'Рынок прогнозов · бизнес', 'business')
panels = []
y = 0

row('HTTP · реальные запросы, /metrics исключён')
panel('Запросы в секунду', [('sum by (route) (rate(pm_http_requests_total[1m]))', '{{route}}')], 'reqps')
panel('Коды ответа · запросов/сек', [('sum by (status) (rate(pm_http_requests_total[1m]))', '{{status}}')], 'reqps')
end()
panel('Задержка ответа', [(f'histogram_quantile({q}, sum by (le) (rate(pm_http_request_duration_seconds_bucket[1m])))', label)
                           for q, label in [(.5,'p50'),(.95,'p95'),(.99,'p99')]], 's',
      description='До формирования заголовков ответа; без передачи тела. При отсутствии трафика квантиль не определён.')
panel('Доля 5xx', [('(sum(rate(pm_http_requests_total{status=~"5.."}[1m])) or vector(0)) / clamp_min(sum(rate(pm_http_requests_total[1m])), 0.000001)', '5xx')], 'percentunit')
end()
row('Состояние сервисов и сбора')
panel('Недоступные цели', [('count(up == 0) or vector(0)', 'Цели')], stat=True, warning=True)
panel('Ошибка сбора БД', [('1 - pm_database_up', 'Ошибка')], stat=True, warning=True)
panel('Воркер: heartbeat устарел > 60с', [('(time() - pm_worker_last_success_timestamp_seconds) > bool 60', 'Устарел')], stat=True, warning=True)
panel('Ошибки HTTP · 5 минут', [('sum(increase(pm_http_requests_total{status=~"5.."}[5m])) or vector(0)', '5xx')], stat=True, warning=True)
end(4)
panel('Доступность целей', [('up', '{{job}}')])
panel('Свежесть успешного сбора', [('time() - pm_business_last_success_timestamp_seconds', 'Бизнес'),
                                   ('time() - pm_worker_last_success_timestamp_seconds', 'Воркер')], 's')
end()
panel('Время сбора бизнес-метрик', [('pm_business_collection_duration_seconds', 'Сбор')], 's')
panel('Ошибки сбора и воркера · 5 минут', [('increase(pm_business_collection_failures_total[5m])','БД'),
      ('increase(pm_worker_ticks_total{result="error"}[5m])','Воркер'),
      ('increase(pm_worker_telegram_failures_total[5m])','Telegram · {{action}}')])
end()
panel('Воркер · циклы/сек', [('rate(pm_worker_ticks_total[1m])','{{result}}')], 'ops')
panel('Воркер · p95 цикла', [('histogram_quantile(0.95, sum by (le) (rate(pm_worker_tick_duration_seconds_bucket[1m])))','p95')], 's')

write_dashboard('prediction-market-technical', 'Рынок прогнозов · техника', 'technical')
