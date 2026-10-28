"""Verify a running local demo. Produces HTTP traffic but never trades or sends Telegram messages."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import time

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--traffic-seconds', type=int, default=65)
    args = parser.parse_args()
    app, prometheus, grafana = 'http://localhost:18000', 'http://localhost:19090', 'http://localhost:13000'
    auth = ('admin', 'local-observability')
    start = time.monotonic()
    codes = {}
    while time.monotonic()-start < args.traffic_seconds:
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = pool.map(lambda path: requests.get(app+path, timeout=5),
                                 ['/', '/api/me', '/missing-demo']*2)
            for response in responses:
                codes[response.status_code] = codes.get(response.status_code, 0)+1
        time.sleep(.5)
    print('Traffic codes:', codes)
    assert {200, 401, 404} <= codes.keys()
    # Allow at least one scrape to observe the last traffic burst.
    time.sleep(12)
    response = requests.get(prometheus+'/api/v1/targets', timeout=10)
    response.raise_for_status()
    targets = response.json()['data']['activeTargets']
    assert len(targets) == 3 and all(t['health'] == 'up' for t in targets), targets
    health = requests.get(grafana+'/api/health', timeout=10).json()
    assert health['database'] == 'ok', health
    ds = requests.get(grafana+'/api/datasources/uid/prometheus/health', auth=auth, timeout=10)
    ds.raise_for_status()
    assert ds.json()['status'] == 'OK', ds.text
    failures, checked, dashboards = [], 0, []
    for uid in ('prediction-market', 'prediction-market-technical'):
        dashboard = requests.get(grafana+'/api/dashboards/uid/'+uid, auth=auth, timeout=10)
        dashboard.raise_for_status()
        data = dashboard.json()
        assert data['meta']['provisioned']
        dashboards.append(data['dashboard']['title'])
        for panel in data['dashboard']['panels']:
            for target in panel.get('targets', []):
                expression = target['expr']
                result = requests.get(prometheus+'/api/v1/query', params={'query': expression}, timeout=15).json()
                values = result.get('data', {}).get('result', [])
                if result['status'] != 'success' or not values or any(not math.isfinite(float(v['value'][1])) for v in values):
                    failures.append((uid, panel['title'], expression, result))
                checked += 1
    report = {'targets': {t['labels']['job']: t['health'] for t in targets}, 'grafana': health,
              'dashboards': dashboards, 'panel_queries': checked, 'failures': failures,
              'traffic_codes': codes}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not failures, 'Panel queries failed or returned no finite samples'
    for query, expected in [('pm_users{kind="registered",window="all"}', 8),
                            ('sum(pm_trades{window="all"})', 42), ('sum(pm_markets)', 6)]:
        result = requests.get(prometheus+'/api/v1/query', params={'query': query}, timeout=10).json()
        assert float(result['data']['result'][0]['value'][1]) == expected, result
    print('All live checks passed')


if __name__ == '__main__':
    main()
