# -*- coding: utf-8 -*-
"""Local-only user-operated credential entry. Never prints, echoes, or sends the key."""
import html
import json
import os
import re
import secrets
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT=Path(__file__).resolve().parent.parent
PORT=8841
ORIGIN='http://127.0.0.1:'+str(PORT)
TOKEN=secrets.token_urlsafe(32)

PAGE='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>千问 API · 本机配置</title><style>body{font:15px -apple-system,sans-serif;background:#f0f1e9;color:#314a39;max-width:560px;margin:50px auto;padding:24px}main{background:#fafbf6;border:1px solid #dce2d2;border-radius:18px;padding:30px}h1{font-size:26px}p{font-size:13px;color:#7b8973;line-height:1.9}label{display:block;margin:22px 0;font-size:13px}input{display:block;width:100%;box-sizing:border-box;border:1px solid #d7dfce;border-radius:8px;padding:13px;margin-top:9px;background:white;font:13px monospace}button{background:#355a49;color:white;border:0;border-radius:9px;padding:14px;width:100%;font:14px sans-serif}.note{background:#edf0e2;padding:12px;border-radius:8px}.error{color:#a6634b}</style><main><small>MVP 1.0 · 仅本机保存</small><h1>接入千问模型</h1><p>请将刚创建的百炼 API Key 填在这里，不要发送到聊天。保存后密钥仅供项目后端读取，不返回网页、不写日志。</p>{message}<form method="post" action="/save" autocomplete="off"><input type="hidden" name="csrf" value="{csrf}"><label>模型名称<input name="model" value="qwen-flash" required maxlength="80"></label><label>与 API Key 同地域的 Base URL<input name="base_url" value="https://dashscope.aliyuncs.com/compatible-mode/v1" required></label><label>API Key<input name="api_key" type="password" autocomplete="new-password" required maxlength="4096" placeholder="在此粘贴完整密钥"></label><p class="note">默认按北京地域、qwen-flash 配置，可按控制台实际信息修改。<br>本次测试按你的授权不设置调用次数上限。保存即允许项目后端使用此凭证完成步骤 5 测试；此页面本身不调用模型。</p><button>保存到本机并授权步骤 5 使用</button></form><p>供应商：阿里云百炼 · 凭证保存在本项目 runtime/secrets/。本入口不需要填写阿里云登录密码。</p></main></html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,status,message=''):
        body=PAGE.replace('{csrf}',TOKEN).replace('{message}',message).encode()
        self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.headers.get('Host')!='127.0.0.1:'+str(PORT):self.send_error(421);return
        saved=bool((ROOT/'runtime/qwen-config.json').exists())
        self.respond(200,'<p class="note">本机已保存配置。若刚刚填写完成，可回复“已填好”，继续真实模型验证。页面不会显示原密钥。</p>' if saved else '')
    def do_POST(self):
        if self.path!='/save' or self.headers.get('Host')!='127.0.0.1:'+str(PORT) or self.headers.get('Origin')!=ORIGIN:self.send_error(403);return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=10000:raise ValueError()
            data=parse_qs(self.rfile.read(length).decode(),max_num_fields=8)
            if not hmac.compare_digest(data.get('csrf',[''])[0],TOKEN):self.send_error(403);return
            key=data.get('api_key',[''])[0].strip();model=data.get('model',[''])[0].strip();base=data.get('base_url',[''])[0].strip().rstrip('/')
            parsed=urlsplit(base)
            if not key or any(c.isspace() for c in key) or len(key)>4096 or not re.fullmatch('[a-zA-Z0-9_.:-]{1,80}',model):raise ValueError()
            host=parsed.hostname or ''
            if parsed.scheme!='https' or parsed.username or parsed.password or parsed.query or parsed.fragment or not(host in ('dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com','dashscope-us.aliyuncs.com') or host.endswith('.maas.aliyuncs.com')) or parsed.path!='/compatible-mode/v1':raise ValueError()
            folder=ROOT/'runtime/secrets';folder.mkdir(parents=True,exist_ok=True,mode=0o700);folder.chmod(0o700)
            path=folder/'qwen.key'
            flags=os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0)
            descriptor=os.open(str(path),flags,0o600)
            with os.fdopen(descriptor,'w') as stream:stream.write(key+'\n')
            path.chmod(0o600);key=''
            settings={'allow_live':True,'provider':'aliyun-qwen','model':model,'base_url':base,'key_file':str(path),'approval_id':'user-step5-2026-11-14-qwen','max_calls':None,'max_output_tokens':1024,'timeout':60,'input_price':'','output_price':'','currency':''}
            config=ROOT/'runtime/qwen-config.json';temp=config.with_suffix('.tmp');temp.write_text(json.dumps(settings,ensure_ascii=False,indent=2)+'\n');temp.chmod(0o600);temp.replace(config)
        except (ValueError,OSError,UnicodeError):self.respond(400,'<p class="error">保存失败，请核对模型、地域地址和完整密钥。密钥未回显。</p>');return
        self.send_response(303);self.send_header('Location','/');self.send_header('Cache-Control','no-store');self.end_headers()

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    print('千问密钥本机填写入口：'+ORIGIN,flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
