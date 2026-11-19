# -*- coding: utf-8 -*-
"""Run zero-model reply checks (roadmap 1.8) on an evaluation results file.

Usage:
  python3 scripts/reply_checks.py RESULTS.json [--out REPORT.json]

RESULTS.json may be a list of records, or an object with "results" / "records".
Each record needs "reply" (or "text"/"content"); "case_id", "repeat" and
"finish_reason" are used when present. No network or model calls are made.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.reply_checks import check  # noqa: E402


def load(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(data, dict):
        data = data.get('results') or data.get('records') or []
    if not isinstance(data, list):
        raise SystemExit('结果文件格式不支持：需要记录列表，或含 results/records 的对象')
    return data


def main():
    parser = argparse.ArgumentParser(description='零模型调用的回复机器检查（截断、重复、抢话、口癖、模板化）')
    parser.add_argument('results')
    parser.add_argument('--out')
    args = parser.parse_args()
    report = check(load(args.results))
    labels = {'truncated': '截断', 'repeated': '回复内重复', 'speaks_for_user': '抢话/代写用户',
              'pet_phrases': '跨题口癖', 'case_templates': '同题模板化（题数）'}
    print('共 %d 条' % report['total'])
    for key, label in labels.items():
        print('- %s：%d' % (label, report['counts'][key]))
    for t in report['case_templates']:
        print('  · %s %d/%d 次复现「%s」' % (t['case_id'], t['repeats'], t['of'], t['phrase']))
    for p in report['pet_phrases']:
        print('  · 口癖「%s」出现在 %d 道题' % (p['phrase'], len(p['cases'])))
    print(report['note'])
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
