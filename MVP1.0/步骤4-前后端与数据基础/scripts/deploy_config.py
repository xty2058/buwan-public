# -*- coding: utf-8 -*-
"""Generate runtime/ model configs and key files from environment variables (deployment).

Keys never enter Git or the image: they come from the host environment (e.g. a server-side
.env read by docker compose) and are written to runtime/secrets/*.key with mode 0600.
Run at container start, before the server. Existing local runtime configs are untouched
unless this script runs, so the developer machine keeps its own setup.

Required: DEEPSEEK_API_KEY (reply), DASHSCOPE_API_KEY (memory extraction, rerank, summary, embedding).
Optional: MINIMAX_API_KEY (voice; omitted = voice disabled), TTS_MAX_CALLS, TTS_MAX_CHARS,
REPLY_MODEL, MEMORY_MODEL, PROMPT_VERSION.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'runtime'
SECRETS = RUNTIME / 'secrets'
DASHSCOPE = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
# Same tuned values as the local service (selected on the training split, see retrieval.py).
RETRIEVAL_POLICY = {'threshold': 0.3, 'top_k': 2, 'pool_k': 8, 'semantic_filter': True,
                    'scope_filter': True, 'selected_on': 'train_only'}


def secret(name, value):
    SECRETS.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS, 0o700)
    path = SECRETS / f'{name}.key'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(value.strip())
    return str(path)


def write(name, data):
    path = RUNTIME / f'{name}.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    missing = [k for k in ('DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY') if not os.getenv(k, '').strip()]
    if missing:
        sys.exit('缺少环境变量：' + '、'.join(missing))
    RUNTIME.mkdir(parents=True, exist_ok=True)
    approval = 'deploy-env'
    write('reply-config', {
        'allow_live': True, 'provider': 'deepseek', 'model': os.getenv('REPLY_MODEL', 'deepseek-flash'),
        'base_url': 'https://api.deepseek.com', 'key_file': secret('deepseek', os.environ['DEEPSEEK_API_KEY']),
        'approval_id': approval, 'max_calls': None, 'max_output_tokens': 512, 'timeout': 60,
        'input_price': '', 'output_price': '', 'currency': '', 'temperature': 0.7,
        'prompt_version': os.getenv('PROMPT_VERSION', 'linyu-v1.4')})
    dashscope = secret('dashscope', os.environ['DASHSCOPE_API_KEY'])
    write('memory-config', {
        'allow_live': True, 'provider': 'aliyun-qwen', 'model': os.getenv('MEMORY_MODEL', 'qwen-flash'),
        'base_url': DASHSCOPE, 'key_file': dashscope, 'approval_id': approval, 'max_calls': None,
        'max_output_tokens': 1024, 'timeout': 60, 'input_price': '', 'output_price': '', 'currency': ''})
    write('embedding-config', {
        'allow_live': True, 'model': 'text-embedding-v4', 'dimensions': 1024, 'region': 'cn-beijing',
        'base_url': DASHSCOPE, 'key_file': dashscope, 'approval_id': approval,
        'budget_db': str(RUNTIME / 'embedding-call-budget.sqlite3'), 'max_requests': None,
        'unlimited_authorized': True})
    write('retrieval-policy', RETRIEVAL_POLICY)
    if os.getenv('MINIMAX_API_KEY', '').strip():
        write('tts-config', {
            'allow_live': True, 'provider': 'minimax', 'key_file': secret('minimax', os.environ['MINIMAX_API_KEY']),
            'approval_id': approval, 'max_calls': int(os.getenv('TTS_MAX_CALLS', '300')),
            'max_chars': int(os.getenv('TTS_MAX_CHARS', '20000')), 'timeout': 45})
    else:
        (RUNTIME / 'tts-config.json').unlink(missing_ok=True)
    print('runtime 配置已生成：回复、记忆、向量、检索策略' + ('、语音' if os.getenv('MINIMAX_API_KEY') else '（未配置语音）'))


if __name__ == '__main__':
    main()
