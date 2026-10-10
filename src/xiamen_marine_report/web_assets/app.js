'use strict';
const $ = id => document.getElementById(id);
const dateParts = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
const datePart = type => dateParts.find(part => part.type === type).value;
$('date').value = `${datePart('year')}-${datePart('month')}-${datePart('day')}`;
let busy = false;
const states = {queued:'准备取证',running:'正在处理',complete:'候选已生成',blocked:'关键证据未通过',failed:'任务未完成'};
function labelFor(file) {
  if (file.name.endsWith('.zip')) return '冻结证据 ZIP';
  if (file.name.endsWith('.pdf')) return file.name.includes('PARTIAL') ? '部分查看 PDF · 未批准' : '参考 PDF · 未批准';
  if (file.name.endsWith('.png')) {
    const site = ['haicang', 'wuyuan', 'huandong'].find(id => file.name.includes(id));
    const name = {haicang:'海沧湾', wuyuan:'五缘湾', huandong:'环东'}[site];
    return name ? `${name}${file.kind === 'map_png' ? '地图' : '综合图'} PNG` : file.name;
  }
  return file.name;
}
function setBusy(value) {busy=value; $('fetch-button').disabled=value; $('demo-button').disabled=value;}
function status(job) {
  $('status-mark').className = `status-mark ${job.status}`;
  $('status-title').textContent = states[job.status] || job.status;
  $('status-detail').textContent = job.message || '';
  $('stage-fetch').classList.toggle('active', job.status !== 'queued');
  $('stage-validate').classList.toggle('active', Boolean(job.validation) || /核验|绘制/.test(job.message || ''));
  $('stage-render').classList.toggle('active', Boolean(job.candidate_digest) || /绘制/.test(job.message || ''));
}
function issueLabel(issue) {
  const text=typeof issue==='string'?issue:JSON.stringify(issue);
  const site=['haicang','wuyuan','huandong'].find(id=>text.includes(id));
  const prefix=site?({haicang:'海沧湾',wuyuan:'五缘湾',huandong:'环东'}[site]+' · '):'';
  const rules=[
    [/TIMEZONE|timezone|TimeArea/,'潮汐绝对时间与官方时区符号尚未核实'],
    [/SUPPLEMENTAL|optional unused source/,'额外探测日期暂未取得潮表，记录为补充缺口'],
    [/COASTAL_MODEL|local safety|local_applicability/,'海洋模型能否代表湾内局地情况尚未核实'],
    [/OFFICIAL_BODY|official body|warning status|marine_forecast/,'目标日期的官方预警或海洋预报正文尚未核实'],
    [/STATION_BAY|station coordinates|station provenance|station relation/,'参考站位置、基准面及与该水域的关系尚未核实'],
    [/tide_height/,'带已核时间语义的逐时潮位序列尚不完整'],
    [/three complete|CROSS_DAY|coverage|missing hourly/,'三个完整涨潮段及其跨日逐时覆盖尚未确认'],
    [/hash|binding|changed/,'来源或候选文件与冻结记录不一致，需要重新核验'],
    [/grid|coordinate|offset/,'实际格点位置与目标水域的偏移尚未核实'],
    [/unit/,'相关变量单位尚未核实'],
    [/neither bay water depth|slack/,'参考站潮高不是水深，高潮不是憩流'],
    [/synthetic|SYNTHETIC/,'合成数据只用于程序演示'],
    [/HTTP|unavailable|not obtained/,'来源未返回可用数据，请查看取证记录'],
  ];
  return prefix+(rules.find(([pattern])=>pattern.test(text))?.[1] || '来源或逐时覆盖尚未通过核验，详情见取证记录');
}
function showResult(job) {
  $('empty-state').hidden = true; $('result').hidden = false;
  $('source-count').textContent = job.source_count || 0;
  $('series-count').textContent = job.series_count || 0;
  $('data-label').textContent = job.synthetic ? 'SYNTHETIC · 合成演示数据，禁止用于真实计划' : '自动取得的 API 证据 · 模型预报与官方网页';
  $('result-heading').textContent = `${job.request.date} · ${job.synthetic ? '合成演示' : '取证结果'}`;
  const blocked = job.status !== 'complete';
  $('validation-message').className = `validation-message${blocked ? ' blocked' : ''}`;
  $('validation-message').textContent = blocked ? `数据或来源尚不完整，未签发完整三段报告。${job.candidate_digest ? '仅生成带显著限制标记的部分查看版。' : '尚未生成 PDF / PNG。'}未取得预警正文，不代表无预警。` : '确定性数据校验已通过。PDF / PNG 是 UNAPPROVED_REFERENCE 候选；图中文字与布局仍须独立逐页审核，不构成下水许可。';
  $('gaps').replaceChildren();
  const issues = [...(job.validation?.errors || []), ...(job.validation?.warnings || [])];
  if (issues.length) {
    const labels=[...new Set(issues.map(issueLabel))];
    const details = document.createElement('details');details.className='gap-list';details.open=blocked;
    const summary = document.createElement('summary');summary.textContent=`查看 ${labels.length} 项待确认事项`;details.append(summary);
    const list=document.createElement('ul');
    for(const label of labels){const item=document.createElement('li');item.textContent=label;list.append(item);}
    details.append(list);$('gaps').append(details);
  }
  $('downloads').replaceChildren();$('preview-tabs').replaceChildren();$('preview').replaceChildren();
  $('map-attribution').hidden = !(job.files || []).some(file => file.kind === 'map_png');
  for(const file of job.files || []){
    const url=`/api/jobs/${job.id}/files/${encodeURIComponent(file.name)}`;
    const link=document.createElement('a');link.className='download';link.href=url;link.download=file.name;link.textContent=`↓ ${labelFor(file)}`;$('downloads').append(link);
    if (file.name.endsWith('.png') || file.name.endsWith('.pdf')){
      const tab=document.createElement('button');tab.type='button';tab.textContent=labelFor(file);
      tab.addEventListener('click',()=>{
        document.querySelectorAll('.preview-tabs button').forEach(button=>button.classList.remove('selected'));tab.classList.add('selected');
        const element=document.createElement(file.name.endsWith('.pdf')?'iframe':'img');element.src=url;
        if(element.tagName==='IMG')element.alt=`${labelFor(file)}，${job.request.date} 未批准参考版`;else element.title='未批准参考 PDF 预览';
        $('preview').replaceChildren(element);
      });$('preview-tabs').append(tab);
    }
  }
  const tabs=[...$('preview-tabs').children];
  const firstMap=tabs.find(tab=>tab.textContent.includes('地图'));
  const firstPng=tabs.find(tab=>tab.textContent.endsWith('PNG'));
  (firstMap || firstPng || $('preview-tabs').firstElementChild)?.click();
  $('audit-json').textContent=JSON.stringify({state:job.validation?.state,run_id:job.run_id,candidate_digest:job.candidate_digest,analysis_provider:'none',sources:job.sources,validation:job.validation},null,2);
}
async function start(mode){
  if(busy)return;
  $('form-error').textContent='';
  if(!$('report-form').reportValidity())return;
  const sites=[...document.querySelectorAll('input[name=sites]:checked')].map(element=>element.value);
  if(!sites.length){$('form-error').textContent='请至少选择一处水域。';return;}
  setBusy(true);$('result').hidden=true;$('empty-state').hidden=false;
  status({status:'queued',message:mode==='demo'?'准备离线合成演示':'准备自动获取相关 API'});
  try{
    const response=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode,date:$('date').value,sites,allow_partial:$('partial').checked})});
    const data=await response.json();if(!response.ok)throw new Error(data.message || '无法创建任务');
    let consecutiveFailures=0;
    for(;;){
      await new Promise(resolve=>setTimeout(resolve,1800));
      let job;
      try{const result=await fetch(`/api/jobs/${data.id}`);if(!result.ok)throw new Error('无法读取任务');job=await result.json();consecutiveFailures=0;}
      catch(error){if(++consecutiveFailures>=3)throw error;continue;}
      status(job);
      if(['complete','blocked'].includes(job.status)){showResult(job);break;}
      if(job.status==='failed')throw new Error(`${job.message}（${job.error_type || '未知原因'}）`);
    }
  }catch(error){status({status:'failed',message:'请检查网络或本地服务后重试'});$('form-error').textContent=error.message || '服务连接失败';}
  finally{setBusy(false);}
}
$('report-form').addEventListener('submit',event=>{event.preventDefault();start('live');});
$('demo-button').addEventListener('click',()=>start('demo'));
fetch('/api/health').then(response=>{if(!response.ok)throw new Error();return response.json();}).then(()=>{$('connection').textContent='本地服务已连接 · Asia/Shanghai';}).catch(()=>{$('connection').textContent='服务未连接';$('form-error').textContent='请先运行 marine-report app，再通过显示的网址打开界面。';});
