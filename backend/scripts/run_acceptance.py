"""One command, isolated fault drills and a timestamped, non-stale report."""
import argparse,hashlib,json,os,re,shutil,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2]


def wsl_argument(path):return str(path).replace(chr(92),'/')

def source_digest():
 digest=hashlib.sha256()
 files=[]
 for folder in ('backend/app','backend/tests','backend/scripts','scripts','frontend/src','backend/migrations'):
  files.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.vue','.js','.css','.sql'})
 files.extend(ROOT/p for p in ('backend/schema.sql','backend/pyproject.toml','backend/uv.lock','frontend/package.json','frontend/package-lock.json','frontend/vite.config.js'))
 for path in sorted(set(files)):
  if path.is_file():digest.update(path.relative_to(ROOT).as_posix().encode());digest.update(b'\0');digest.update(path.read_bytes())
 return digest.hexdigest()

def overall(stages):
 if not stages:return 'incomplete'
 if any(s['status']=='failed' for s in stages):return 'failed'
 if any(s['status']!='passed' for s in stages):return 'incomplete'
 return 'passed'


def redact(value):
 value=re.sub(r'(mysql(?:\+pymysql)?://[^:\s]+:)[^@\s]+@',r'\1[redacted]@',value)
 try:
  sys.path.insert(0,str(ROOT/'backend'))
  from app import config
  for secret in (config.AGENT_API_KEY,config.MYSQL_URL):
   if secret:value=value.replace(secret,'[redacted]')
 except (ImportError,RuntimeError):pass
 return value


def evidence(path,key):
 if not path.is_file():return False,'缺少本批次验收记录'
 try:data=json.loads(path.read_text(encoding='utf-8'))
 except (ValueError,OSError):return False,'验收记录不可读取'
 if key=='successful' and (data.get('tests_run',0)<=0 or data.get('skipped_count',0)>0):return False,'没有执行测试或存在跳过项'
 return data.get(key) is True,'验收记录未标记通过'


def run_stage(name,command,folder,env,timeout=120,proof=None):
 started=time.monotonic();status='failed';reason=''
 try:
  result=subprocess.run(command,cwd=ROOT/'backend',env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
  output=result.stdout+'\n'+result.stderr;status='passed' if result.returncode==0 else 'failed'
  if result.returncode:reason='命令退出码 '+str(result.returncode)
  if status=='passed' and proof:
   valid,reason=evidence(folder/proof[0],proof[1]);status='passed' if valid else 'failed'
   if valid:reason=''
  if status=='passed' and name=='ros_components' and not re.search(r'Ran [1-9][0-9]* tests?',output):status='failed';reason='没有 ROS 组件测试执行记录'
 except (OSError,subprocess.TimeoutExpired) as exc:output=type(exc).__name__+': '+str(exc);reason='环境不可用或验收超时'
 log=folder/(name+'.log');log.write_text(redact(output),encoding='utf-8')
 stage={'name':name,'status':status,'duration_seconds':round(time.monotonic()-started,2),'log':log.name,'reason':reason}
 print(json.dumps(stage,ensure_ascii=False),flush=True)
 return stage


def render(report):
 lines=['# 系统验收报告','',f"批次：{report['run_id']}",f"生成时间：{report['finished_at']}",f"总体状态：{report['status']}",f"验收期间代码未变：{report.get('source_unchanged',False)}",'',
        '本批次使用独立数据库、独立消息服务与独立端口；不向运行中的机器人发送任务，不调用付费模型。','',
        '| 阶段 | 结果 | 耗时（秒） | 日志 |','| --- | --- | --- | --- |']
 for stage in report['stages']:lines.append(f"| {stage['name']} | {stage['status']} | {stage['duration_seconds']} | [{stage['log']}]({stage['log']}) |")
 lines+=['','## 覆盖范围','',
         '- 真实 HTTP 身份校验、MySQL 事务、MQTT 断连与重连、SQLite 消息落盘。',
         '- 重复指令不重复执行、照片应用确认前保留、取消确认前保留占用、延迟指令不复活已取消任务。',
         '- 视觉重试、结果和告警原子提交、人工处理与关联复检关闭、分析进程强制中断后的接管。',
         '- 服务正常启动/停止、依赖故障下就绪检查、请求编号和 JSON 日志。',
         '- 机器人协议由测试端替代；视觉结果为确定性测试数据，不能代表导航效果或识别准确率。',
         '- 故障演练中只加速重试等待时间；独立进程恢复用 2 秒测试租约，生产租约仍为 60 秒。','',
         '本次是否运行 ROS 组件测试：'+('是' if report['ros_components_requested'] else '否'),'','## 历史场景证据（未在本批重跑）','']
 for item in report['historical_evidence']:lines.append('- '+item['file']+'（SHA-256 '+item['sha256']+'）')
 if report.get('regression'):lines+=['','后端回归测试数：'+str(report['regression']['tests_run'])]
 lines+=['','具体失败原因见对应阶段日志；失败或缺少记录不会沿用上一批的通过结果。','']
 return '\n'.join(lines)


def main():
 parser=argparse.ArgumentParser(description='隔离系统验收；默认不启动 Gazebo、不调用模型')
 parser.add_argument('--node',help='Node.js 可执行文件路径（不在 PATH 时指定）')
 parser.add_argument('--ros-components',action='store_true',help='额外运行独立 ROS 域的组件测试')
 parser.add_argument('--output',type=Path,help='本批次输出目录，必须不存在，避免混入旧结果')
 args=parser.parse_args()
 if sys.flags.optimize:parser.error('请勿使用 -O 或 PYTHONOPTIMIZE')
 run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
 folder=args.output.resolve() if args.output else ROOT/'docs/validation/runs'/run_id
 folder.mkdir(parents=True,exist_ok=False)
 env=dict(os.environ);env.pop('PYTHONOPTIMIZE',None);env['ACCEPTANCE_REPORT_DIR']=str(folder)
 python=[sys.executable,'-X','utf8'];stages=[];code_start=source_digest()
 build=python+[str(ROOT/'scripts/manage.py'),'build']
 if args.node:build+=['--node',args.node]
 plan=[('frontend_build',build,90,None),
       ('backend_regression',python+[str(ROOT/'backend/scripts/test_report.py')],120,('backend-regression.json','successful')),
       ('protocol_faults',python+[str(ROOT/'backend/scripts/system_fault_acceptance.py')],90,('system-fault-acceptance.json','passed')),
       ('vision_process_recovery',python+[str(ROOT/'backend/scripts/vision_recovery_acceptance.py')],60,('vision-recovery-acceptance.json','passed')),
       ('service_lifecycle',python+[str(ROOT/'backend/scripts/operations_acceptance.py')],90,('operations-acceptance.json','passed'))]
 if args.ros_components:
  if os.name=='nt':
   try:
    mapped=subprocess.run(['wsl.exe','-d','Ubuntu-24.04','wslpath','-a',wsl_argument(ROOT)],capture_output=True,text=True,timeout=10)
    ros=['wsl.exe','-d','Ubuntu-24.04','bash',mapped.stdout.strip()+'/scripts/test_nav2_components.sh'] if mapped.returncode==0 else ['missing-wsl-path']
   except (OSError,subprocess.TimeoutExpired):ros=['missing-wsl-environment']
  else:ros=['bash',str(ROOT/'scripts/test_nav2_components.sh')]
  plan.append(('ros_components',ros,120,None))
 for name,command,timeout,proof in plan:stages.append(run_stage(name,command,folder,env,timeout,proof))
 history=[]
 for name in ('equipment-acceptance.json','alerts-live-acceptance.json','equipment-first-pass-missed-leak.json'):
  path=ROOT/'docs/validation'/name
  if path.is_file():history.append({'file':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 code_end=source_digest()
 report={'code_start_sha256':code_start,'code_end_sha256':code_end,'source_unchanged':code_start==code_end,'run_id':run_id,'finished_at':datetime.now(timezone.utc).isoformat(),'status':overall(stages),'stages':stages,'ros_components_requested':args.ros_components,'historical_evidence':history}
 if not report['source_unchanged'] and report['status']=='passed':report['status']='incomplete'
 regression=folder/'backend-regression.json'
 if regression.is_file():
  try:report['regression']=json.loads(regression.read_text(encoding='utf-8'))
  except ValueError:pass
 (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 (folder/'report.md').write_text(render(report),encoding='utf-8')
 latest=ROOT/'.runtime/acceptance';latest.mkdir(parents=True,exist_ok=True)
 (latest/'latest.json').write_text(json.dumps({'run_id':run_id,'status':report['status'],'directory':str(folder)},ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'status':report['status'],'report':str(folder/'report.md')},ensure_ascii=False),flush=True)
 return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
