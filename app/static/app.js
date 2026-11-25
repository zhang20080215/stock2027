'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v === undefined || v === null || v === '' || !Number.isFinite(Number(v)) ? '—' : new Intl.NumberFormat('zh-CN', {maximumFractionDigits:3}).format(v);
const date = v => v ? new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Hong_Kong',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(v)) : '未记录';
const channel = c => c?.includes('weixin') ? '微信' : c === 'unknown' ? '历史会话 · 来源未确认' : c || '来源未确认';
const market = v => v === 'hk' ? '港股' : 'A 股';
const names = {overview:'工作总览',scans:'选股记录',paper:'模拟盘',messages:'微信对话',positions:'持仓台账',notes:'智能体记忆',tasks:'执行记录',automation:'自动任务',rules:'策略与规则',project:'项目状态'};
const state = {overview:null,tab:'overview',generation:0,scan:null,stocks:[],messages:[],offset:0,taskOffset:0};
const empty = (title, text='') => `<div class="empty"><strong>${esc(title)}</strong>${esc(text)}</div>`;
const header = title => `<div class="page-head"><h1>${esc(title)}</h1></div>`;
const refinedHead = (title, subtitle) => `<div class="page-head"><div><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div><button id="pageRefresh" class="refresh-button">刷新</button></div>`;
const metricTiles = items => `<div class="metrics">${items.map(([label,value,detail,href])=>`<${href?'a':'div'} class="metric" ${href?`href="${href}"`:''}><span class="metric-label">${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(detail||'')}</small></${href?'a':'div'}>`).join('')}</div>`;
function bindPageRefresh(){const b=$('#pageRefresh');if(b)b.addEventListener('click',async()=>{b.disabled=true;b.textContent='刷新中…';try{if(state.tab==='paper')await paper(state.generation,true);else await route();}catch(e){error(e.message);}finally{if(b.isConnected){b.disabled=false;b.textContent='刷新';}}});}
const error = text => { $('#error').textContent=text; $('#error').hidden=!text; };

async function api(path, options={}) {
  const res = await fetch('/api/' + path, options);
  const body = await res.json();
  if (res.status === 401 && path !== 'login') { showLogin(); throw new Error('登录已过期，请重新输入访问口令'); }
  if (!res.ok) throw new Error(body.error || '读取失败，请稍后重试');
  return body;
}
function showLogin(){ $('#login').hidden=false; $('#shell').hidden=true; }
function noticeStatus(info){ state.overview=info; }
function rich(text){
  // Escape every cell/line before formatting. Never insert raw source HTML.
  const inline=s=>esc(s).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>');
  const lines=String(text).split('\n'), out=[];
  const cells=s=>s.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
  for(let i=0;i<lines.length;i++){
    const line=lines[i];
    if(line.trim().startsWith('```')){const block=[];while(++i<lines.length&&!lines[i].trim().startsWith('```'))block.push(lines[i]);out.push('<pre><code>'+esc(block.join('\n'))+'</code></pre>');continue;}
    if(line.includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])){
      out.push('<div class="markdown-table scroll"><table><thead><tr>'+cells(line).map(x=>'<th>'+inline(x)+'</th>').join('')+'</tr></thead><tbody>');i++;
      while(i+1<lines.length&&lines[i+1].includes('|')){i++;out.push('<tr>'+cells(lines[i]).map(x=>'<td>'+inline(x)+'</td>').join('')+'</tr>');}out.push('</tbody></table></div>');continue;
    }
    if(/^#{1,6} /.test(line))out.push('<h3>'+inline(line.replace(/^#{1,6} /,''))+'</h3>');
    else if(/^\s*---+\s*$/.test(line))out.push('<hr>');
    else out.push(inline(line)+'<br>');
  }
  return out.join('');
}
function scanRow(s){return `<div class="record"><div class="record-head"><a href="#scans?id=${encodeURIComponent(s.id)}">${market(s.market)}快照</a><time class="small">${date(s.timestamp)}</time></div></div>`;}

function overview(info){
  const sync=info.sync||{}, stale=!sync.last_sync || Date.now()-new Date(sync.last_sync)>120000;
  let h=refinedHead('工作总览','集中查看数据更新、持仓记录与最近分析');
  h+=`<section class="panel"><div class="panel-heading"><h2>项目运行概况</h2><a href="#project">查看状态与数据覆盖</a></div><p>采集最近更新：${date(sync.last_sync)} · ${sync.errors?.length?'存在采集异常':'本次采集无报错'}</p><p><a href="#notes?id=MEMORY.md">长期记忆</a> · <a href="#automation">自动任务</a> · <a href="#rules">策略与规则</a></p></section>`;
  if(stale || sync.errors?.length) h+=`<div class="notice warning">${stale?'同步超过两分钟未更新。':''}${esc((sync.errors||[]).join('；'))}</div>`;
  h+=`<div class="metrics">${[['选股快照',info.counts.scans,''],['对话消息',info.counts.messages,info.counts.sessions+' 个会话'],['持仓记录',info.positions_count,'更新 '+date(info.positions_updated)],['智能体记忆',info.counts.documents,'']].map(([a,b,c])=>`<div class="metric"><span class="metric-label">${a}</span><strong>${fmt(b)}</strong>${c?`<small>${esc(c)}</small>`:''}</div>`).join('')}</div>`;
  h+=`<div class="grid"><section class="panel"><div class="panel-heading"><h2>最近分析</h2><a href="#messages?role=assistant">全部回复</a></div>${info.recent_messages.length?info.recent_messages.map(m=>`<article class="record"><div class="record-head"><span class="pill neutral">${esc(channel(m.channel))}</span><time class="small">${date(m.timestamp)}</time></div><div class="excerpt">${esc(m.text)}</div><p><a href="#messages?session=${encodeURIComponent(m.session_id)}">查看会话</a></p></article>`).join(''):empty('暂无分析')}</section><div class="stack"><section class="panel"><div class="panel-heading"><h2>最近选股快照</h2><a href="#scans">历史记录</a></div>${info.latest_scans.length?info.latest_scans.slice(0,4).map(scanRow).join(''):empty('暂无扫描快照')}</section></div></div>`;
  $('#main').innerHTML=h;
  document.querySelectorAll('#main .metric').forEach((tile,i)=>{const link=document.createElement('a');link.href=['#scans','#messages','#positions','#notes'][i];link.className=tile.className;link.innerHTML=tile.innerHTML;tile.replaceWith(link);});
}

async function scans(params, generation){
  const all=await api('scans'); if(generation!==state.generation)return;
  const selected=params.get('id') || all[0]?.id;
  const snapshot=!!params.get('id');
  $('#main').innerHTML=refinedHead('选股记录','按时间查看模型结论、程序筛选与行情快照')+`<div class="toolbar segmented"><button id="modelMode" aria-pressed="${!snapshot}">模型最终结果</button><button id="selectionMode" aria-pressed="false">程序筛选</button><button id="snapshotMode" aria-pressed="${snapshot}">行情数据</button><button id="reportMode" aria-pressed="false">原始报告</button></div><section id="modelPanel" ${snapshot?'hidden':''}></section><section id="selectionPanel" hidden></section><section class="panel" id="reportsPanel" hidden></section><section id="snapshotPanel" ${snapshot?'':'hidden'}><div class="toolbar"><label for="scanMarket">市场</label><select id="scanMarket"><option value="">全部市场</option><option value="a">A 股</option><option value="hk">港股</option></select><label for="scanSelect">历史快照</label><select id="scanSelect">${all.map(s=>`<option value="${esc(s.id)}" ${s.id===selected?'selected':''}>${market(s.market)} · ${date(s.timestamp)}</option>`).join('')}</select></div><div id="scanContent">${empty('暂无扫描快照')}</div></section>`;
  function mode(view){for(const [button,panel]of[['modelMode','modelPanel'],['selectionMode','selectionPanel'],['snapshotMode','snapshotPanel'],['reportMode','reportsPanel']]){$('#'+panel).hidden=button!==view;$('#'+button).setAttribute('aria-pressed',button===view);}}
  $('#modelMode').addEventListener('click',()=>mode('modelMode'));$('#selectionMode').addEventListener('click',()=>mode('selectionMode'));$('#reportMode').addEventListener('click',()=>mode('reportMode'));$('#snapshotMode').addEventListener('click',()=>mode('snapshotMode'));
  $('#scanMarket').addEventListener('change',()=>{
    const items=all.filter(s=>!$('#scanMarket').value || s.market===$('#scanMarket').value);
    $('#scanSelect').innerHTML=items.map(s=>`<option value="${esc(s.id)}">${market(s.market)} · ${date(s.timestamp)}</option>`).join('');
    if(items.length)loadScan(items[0].id).catch(e=>error(e.message)); else $('#scanContent').innerHTML=empty('该市场暂无快照');
  });
  $('#scanSelect').addEventListener('change',()=>loadScan($('#scanSelect').value).catch(e=>error(e.message)));
  await Promise.all([loadModelResults(),loadSelectionReports(),loadReports()]); if(selected)await loadScan(selected);
}
async function loadScan(id){
  const generation=state.generation;
  const request=state.scanRequest=(state.scanRequest||0)+1;
  const s=await api('scan?id='+encodeURIComponent(id)); if(generation!==state.generation || state.tab!=='scans'||request!==state.scanRequest)return;
  state.scan=s; state.stocks=s.data.stocks || [];
  const d=s.data, old=Date.now()-new Date(s.timestamp)>86400000;
  $('#scanContent').innerHTML=`<div class="notice ${old?'warning':''}">快照 ${date(s.timestamp)}${old?' · 历史行情':''}${d.freshness.state?' · '+esc(d.freshness.state):''}</div>`+
    (d.missing?.length?`<div class="notice warning">缺失：${d.missing.map(x=>({industry:'行业排名',concept:'概念排名',moneyflow:'资金流'}[x]||esc(x))).join('、')}</div>`:'')+
    `<div class="metrics">${[['扫描股票池',state.stocks.length,''],['K 线覆盖',d.kline_count,''],['涨停数量',s.market==='a'?d.limit_up:'—',''],['跌停数量',s.market==='a'?d.limit_down:'—','']].map(([a,b,c])=>`<div class="metric"><span class="metric-label">${a}</span><strong>${fmt(b)}</strong>${c?`<small>${c}</small>`:''}</div>`).join('')}</div><div class="toolbar"><input id="stockSearch" type="search" placeholder="搜索股票名称或代码" aria-label="搜索股票"><select id="stockSort" aria-label="排序方式"><option value="pct">涨跌幅从高到低</option><option value="amt">成交额从高到低</option><option value="vq">量比从高到低</option><option value="name">股票名称</option></select><button id="downloadScan">导出 CSV</button>${s.session_id?`<a href="#messages?session=${encodeURIComponent(s.session_id)}">查看关联会话</a>`:''}</div><section class="panel table-panel"><div class="scroll"><table><thead><tr><th>股票 / 代码</th><th>快照价</th><th>涨跌幅</th><th>量比</th><th>成交额（亿）</th><th>行情时间</th><th></th></tr></thead><tbody id="stocksBody"></tbody></table></div><div id="stockCount" class="small help"></div></section>`;
  $('#stockSearch').addEventListener('input',stockTable);$('#stockSort').addEventListener('change',stockTable);$('#downloadScan').addEventListener('click',downloadScan);
  stockTable();
}
async function loadModelResults(){
  const generation=state.generation;
  const records=await api('model-results');if(generation!==state.generation)return;
  $('#modelPanel').innerHTML=`<div class="selection-workspace model-workspace"><aside class="selection-history"><div class="message-pane-heading">模型选股记录</div><div class="selection-record-list">${records.map(r=>`<button class="selection-record" data-model-result="${esc(r.id)}" aria-pressed="false"><span class="selection-record-title"><strong>${esc(r.prompt.slice(0,24))}</strong><span>最终回复</span></span><time>${date(r.timestamp)}</time></button>`).join('')||empty('暂无模型最终结果')}</div></aside><section class="model-result-content"><div id="modelResult">${empty('暂无模型最终结果')}</div></section></div>`;
  $('#modelPanel .selection-record-list').addEventListener('click',e=>{const button=e.target.closest('[data-model-result]');if(button&&button.dataset.modelResult!==state.modelResult?.id)loadModelResult(button.dataset.modelResult).catch(e=>error(e.message));});
  $('#modelPanel .message-pane-heading').insertAdjacentHTML('afterend','<div class="record-search"><input id="modelRecordSearch" type="search" aria-label="搜索模型记录" placeholder="搜索请求或日期"><p id="modelRecordEmpty" class="small" hidden>没有匹配的记录</p></div>');
  $('#modelRecordSearch').addEventListener('input',()=>{const q=$('#modelRecordSearch').value.trim().toLowerCase();let count=0;document.querySelectorAll('[data-model-result]').forEach((button,i)=>{button.hidden=!`${records[i].prompt} ${date(records[i].timestamp)}`.toLowerCase().includes(q);if(!button.hidden)count++;});$('#modelRecordEmpty').hidden=!!count;});
  if(records.length)await loadModelResult(records[0].id);
}
async function loadModelResult(id){
  const generation=state.generation,request=state.modelResultRequest=(state.modelResultRequest||0)+1;
  const record=await api('model-result?id='+encodeURIComponent(id));if(generation!==state.generation||request!==state.modelResultRequest)return;
  state.modelResult=record;
  document.querySelectorAll('[data-model-result]').forEach(b=>{const selected=b.dataset.modelResult===id;b.classList.toggle('active',selected);b.setAttribute('aria-pressed',selected);});
  const data=record.data,stocks=data.stocks||[],provider=record.provider==='deepseek'?'DeepSeek':record.provider||'大模型';
  const summary=data.summary||{},headline=summary.conclusion||record.prompt;
  const summaryFields=Object.entries({market_state:'市场判断',participation:'参与建议',overall_position:'总体仓位'}).filter(([key])=>summary[key]&&summary[key]!==headline);
  const fields=Object.entries({reason:'理由',entry:'买入条件',stop:'止损',target:'目标',shares:'股数',amount:'金额',position:'仓位',invalid:'失效条件',risk:'风险'}).filter(([key])=>stocks.some(s=>s[key]));
  const table=stocks.length?`<div class="model-stock-heading"><h3>结构化选股结果</h3><span class="small">${stocks.length} 条</span></div><div class="model-stock-scroll"><table><thead><tr><th>股票</th><th>模型判断</th>${fields.map(([,label])=>`<th>${label}</th>`).join('')}<th></th></tr></thead><tbody>${stocks.map((stock,index)=>`<tr data-model-stock="${esc(stock.code)}"><td><strong>${esc(stock.name)}</strong><div class="small">${esc(stock.code)}</div></td><td>${esc(stock.decision)}</td>${fields.map(([key])=>`<td class="model-plan-cell" title="${esc(stock.evidence?.[key]||'未能从原文明确提取')}">${esc(stock[key]||'—')}</td>`).join('')}<td><button data-model-evidence="${index}">核对原文</button></td></tr>`).join('')}</tbody></table></div>`:'';
  $('#modelResult').innerHTML=`<div class="selection-heading"><div><h2>${esc(record.prompt)}</h2><span class="small">${date(record.timestamp)} · ${esc(provider)} · ${esc(record.model)}</span></div><span class="pill">模型最终回复</span></div><div class="model-conclusion">${esc(headline)}</div>${summaryFields.length?`<div class="model-summary-fields">${summaryFields.map(([key,label])=>`<div><span>${label}</span><p title="${esc(summary.evidence?.[key]||'')}">${esc(summary[key])}</p></div>`).join('')}</div>`:''}<div class="toolbar model-result-links"><a href="#messages?anchor=${encodeURIComponent(record.id)}">定位微信原文</a><button id="exportModelJson">导出 JSON</button><span class="small">历史结论 · ${date(record.timestamp)}</span></div>${table}<details class="model-original" ${stocks.length?'':'open'}><summary>完整原文</summary><div class="prose">${rich(record.text)}</div></details>`;
  $('#exportModelJson').addEventListener('click',()=>{
    const payload={schema_version:data.schema_version,extraction:'source_text',source:{message_id:record.id,request_message_id:record.user_message_id,session_id:record.session_id,timestamp:record.timestamp,provider:record.provider,model:record.model,prompt:record.prompt},summary:data.summary,stocks,original_text:record.text};
    const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=`model-result-${messageDay(record.timestamp)}-${record.id.split(':').at(-1)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  document.querySelectorAll('[data-model-evidence]').forEach(button=>button.addEventListener('click',()=>{
    const stock=stocks[Number(button.dataset.modelEvidence)];$('#detailTitle').textContent=stock.name+' · 原文核对';
    $('#detailBody').innerHTML=`<p class="small">${date(record.timestamp)} · ${esc(record.model)}</p><dl class="model-evidence-fields">${Object.entries(stock.evidence||{}).map(([key,quote])=>`<div><dt>${esc(({name:'名称',code:'代码',decision:'模型判断',...Object.fromEntries(fields)})[key]||key)}</dt><dd>${esc(quote)}</dd></div>`).join('')}</dl><details><summary>该股票原文段落</summary><div class="prose">${rich(stock.source_text||stock.source_line)}</div></details>`;$('#detail').showModal();
  }));
  $('.model-result-content').scrollTop=0;
}async function loadSelectionReports(){
  const generation=state.generation;
  const reports=await api('selection-reports');if(generation!==state.generation)return;
  const counts=r=>['pass','watch','reserve'].reduce((sum,key)=>sum+(r.groups[key].total??r.groups[key].shown),0);
  $('#selectionPanel').innerHTML=`<div class="selection-workspace"><aside class="selection-history"><div class="message-pane-heading">扫描记录</div><div class="selection-record-list">${reports.map(r=>`<button class="selection-record" data-selection-record="${esc(r.id)}" aria-pressed="false"><span class="selection-record-title"><strong>${esc(r.source)}</strong><span>${fmt(counts(r))} 只${counts(r)>0&&!['pass','watch','reserve'].some(key=>r.groups[key].shown)?' · 仅统计':''}</span></span><time>${date(r.timestamp)}:${String(new Date(r.timestamp).getUTCSeconds()).padStart(2,'0')}</time></button>`).join('')||empty('暂无记录')}</div></aside><section class="selection-content"><div id="selectionResult">${empty('暂无可整理的筛选结果')}</div></section></div>`;
  $('#selectionPanel .selection-record-list').addEventListener('click',e=>{const button=e.target.closest('[data-selection-record]');if(!button||button.dataset.selectionRecord===state.selection?.id)return;loadSelection(button.dataset.selectionRecord).catch(e=>error(e.message));});
  if(reports.length)await loadSelection(reports[0].id);
}
async function loadSelection(id){
  const generation=state.generation,request=state.selectionRequest=(state.selectionRequest||0)+1;
  const report=await api('selection-report?id='+encodeURIComponent(id));if(generation!==state.generation||request!==state.selectionRequest)return;
  state.selection=report;
  document.querySelectorAll('[data-selection-record]').forEach(button=>{const selected=button.dataset.selectionRecord===id;button.classList.toggle('active',selected);button.setAttribute('aria-pressed',selected);});
  $('#selectionResult').innerHTML=`<div class="selection-heading"><div><h2>${esc(report.source)}</h2><span class="small">${date(report.timestamp)} · ${market(report.market)}${report.delayed?' · 行情延迟':''}</span></div><span class="pill neutral">脚本筛选</span></div><div class="selection-summary" aria-label="分组统计">${['pass','watch','reserve'].map(key=>{const g=report.groups[key];return `<div class="selection-summary-item"><span>${esc(g.label)}</span><strong>${g.total??g.shown}</strong></div>`;}).join('')}</div><div class="toolbar"><input id="selectionSearch" type="search" aria-label="搜索筛选股票" placeholder="搜索股票"><span id="selectionTableCount" class="small"></span><button id="selectionOriginal">查看原文</button><a href="#messages?session=${encodeURIComponent(report.session_id)}">机器人分析</a></div><p id="selectionCoverage" class="small" hidden></p><div class="selection-table-scroll"><table class="selection-table"><thead><tr><th>股票</th><th>分组</th><th>当时价格 · ${report.market==='hk'?'港元':'元'}</th><th>涨跌幅</th><th>得分</th><th>脚本止损</th><th>脚本目标价</th><th>收益 / 风险比</th><th>脚本股数</th><th>筛选原因</th><th></th></tr></thead><tbody id="selectionTableBody"></tbody></table></div>`;
  const total=['pass','watch','reserve'].reduce((sum,key)=>sum+(report.groups[key].total??report.groups[key].shown),0);
  $('#selectionCoverage').hidden=total<=report.candidates.length;
  $('#selectionCoverage').textContent=report.candidates.length?`报告分组共 ${total} 只，现有记录含 ${report.candidates.length} 只明细`:`此记录仅含分组统计（${total} 只），无股票明细`;
  function render(){
    const query=$('#selectionSearch').value.trim().toLowerCase();
    const stocks=report.candidates.filter(s=>(s.name+' '+s.code).toLowerCase().includes(query));
    $('#selectionTableCount').textContent=query?`${stocks.length} / ${report.candidates.length} 只`:`${stocks.length} 只`;
    $('#selectionTableBody').innerHTML=stocks.map(s=>`<tr data-selection-stock="${esc(s.code)}"><td><strong>${esc(s.name)}</strong><div class="small">${esc(s.code)}</div></td><td><span class="pill ${s.group==='pass'?'':'neutral'}">${esc(report.groups[s.group].label)}</span></td><td class="num">${fmt(s.price)}</td><td class="num ${s.pct>=0?'positive':'negative'}">${s.pct>0?'+':''}${fmt(s.pct)}%</td><td class="num">${fmt(s.score)}</td><td class="num">${fmt(s.stop)}</td><td class="num">${fmt(s.target)}</td><td class="num">${fmt(s.rr)}</td><td class="num">${fmt(s.shares)}</td><td class="selection-table-reason">${esc(s.reason||s.raw_reason||'—')}</td><td><a href="#messages?session=${encodeURIComponent(report.session_id)}&q=${encodeURIComponent(s.name)}">对话</a></td></tr>`).join('')||"<tr><td colspan='11'>"+(query?'暂无匹配股票':'该记录未保存股票明细')+"</td></tr>";
  }
  $('#selectionSearch').addEventListener('input',render);
  $('#selectionOriginal').addEventListener('click',async()=>{try{const source=await api('reports?id='+encodeURIComponent(report.id));$('#detailTitle').textContent=report.source;$('#detailBody').innerHTML=`<p class="small">${date(report.timestamp)}</p><div class="prose">${rich(source.text)}</div>`;$('#detail').showModal();}catch(e){error(e.message);}});
  render();$('.selection-content').scrollTop=0;
}async function loadReports(offset=0,q=''){
  const generation=state.generation;
  const reports=await api('reports?limit=6&offset='+offset+'&q='+encodeURIComponent(q));
  if(generation!==state.generation||!$('#reportsPanel'))return;
  $('#reportsPanel').innerHTML=`<div class="panel-heading"><h2>脚本扫描报告 <span class="small">${reports.total} 份</span></h2></div><form id="reportFilter" class="toolbar"><input id="reportQuery" type="search" placeholder="搜索报告中的股票、策略或关键词" aria-label="搜索扫描报告" value="${esc(q)}"><button>搜索报告</button></form>${reports.items.map(r=>`<div class="record"><div class="record-head"><strong>${esc(r.title)}</strong><time class="small">${date(r.timestamp)}</time></div><button data-report="${esc(r.id)}">查看报告</button> <a class="small" href="#messages?session=${encodeURIComponent(r.session_id)}">来源会话</a></div>`).join('')||empty('暂无匹配的脚本报告')}<div class="pagination"><button id="prevReports" ${offset<=0?'disabled':''}>上一页</button><span>${reports.total?offset+1:0}–${Math.min(offset+6,reports.total)} / ${reports.total}</span><button id="nextReports" ${offset+6>=reports.total?'disabled':''}>下一页</button></div>`;
  $('#reportFilter').addEventListener('submit',e=>{e.preventDefault();loadReports(0,$('#reportQuery').value).catch(e=>error(e.message));});
  $('#prevReports').addEventListener('click',()=>loadReports(Math.max(0,offset-6),q).catch(e=>error(e.message)));
  $('#nextReports').addEventListener('click',()=>loadReports(offset+6,q).catch(e=>error(e.message)));
  document.querySelectorAll('[data-report]').forEach(b=>b.addEventListener('click',async()=>{try{const r=await api('reports?id='+encodeURIComponent(b.dataset.report));$('#detailTitle').textContent=r.title;$('#detailBody').innerHTML=`<p class="small">${date(r.timestamp)}</p><div class="prose">${rich(r.text)}</div>`;$('#detail').showModal();}catch(e){error(e.message);}}));
}
function filteredStocks(){
  const q=$('#stockSearch').value.trim().toLowerCase(), sort=$('#stockSort').value;
  return state.stocks.filter(s=>(String(s.code)+' '+s.name).toLowerCase().includes(q)).sort((a,b)=>sort==='name'?String(a.name).localeCompare(b.name,'zh-CN'):(Number(b[sort])||0)-(Number(a[sort])||0));
}
function stockTable(){
  const items=filteredStocks();
  $('#stocksBody').innerHTML=items.map(s=>`<tr><td>${esc(s.name)}<div class="small">${esc(s.code)}</div></td><td class="num">${fmt(s.price)}</td><td class="num ${Number(s.pct)>=0?'positive':'negative'}">${fmt(s.pct)}%</td><td class="num">${fmt(s.vq)}</td><td class="num">${fmt(s.amt==null?null:s.amt/1e8)}</td><td class="small">${esc(s.ts||'未记录')}</td><td><button data-stock="${esc(s.code)}">详情</button></td></tr>`).join('') || '<tr><td colspan="7">暂无匹配股票</td></tr>';
  $('#stockCount').textContent=`显示 ${items.length} / ${state.stocks.length} 只`;
  $('#stocksBody').querySelectorAll('[data-stock]').forEach(b=>b.addEventListener('click',()=>stockDetail(b.dataset.stock)));
}
function stockDetail(code){
  const s=state.stocks.find(x=>String(x.code)===code); if(!s)return;
  const labels={price:'快照价格',pct:'涨跌幅 %',vq:'量比',pe:'市盈率',high:'日内最高',low:'日内最低',open:'开盘价',prev:'前收盘价',amp:'振幅 %',cap:'市值（亿元）'};
  $('#detailTitle').textContent=s.name+' · '+code;
  $('#detailBody').innerHTML=`<p class="small">${market(state.scan.market)} · 快照 ${date(state.scan.timestamp)} · 行情 ${esc(s.ts||'未记录')}</p><div class="detail-fields">${Object.entries(labels).map(([key,label])=>`<div class="field"><small>${label}</small><strong>${fmt(s[key])}</strong></div>`).join('')}</div><p class="help"><a id="stockConversation" href="#messages?q=${encodeURIComponent(s.name)}">相关对话</a></p>`;
  $('#stockConversation').addEventListener('click',()=>$('#detail').close());$('#detail').showModal();
}
function downloadScan(){
  const quote=v=>'"'+String(v??'').replace(/"/g,'""').replace(/^[=+@\t\r-]/,"'$&")+'"';
  const rows=[['代码','名称','快照价','涨跌幅%','量比','成交额','行情时间'],...filteredStocks().map(s=>[s.code,s.name,s.price,s.pct,s.vq,s.amt,s.ts])];
  const url=URL.createObjectURL(new Blob(['\uFEFF'+rows.map(r=>r.map(quote).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download=state.scan.id.replace('.json','.csv');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

async function messages(params,generation){
  let session=params.get('session')||'',days=await api('message-days'+(session?'?session='+encodeURIComponent(session):''));
  if(generation!==state.generation)return;
  let view=new URLSearchParams(),anchor='',navigationVersion=0,results=[],searchVersion=0,searchQuery='',searchRole='';
  $('#main').innerHTML=refinedHead('微信对话','按日期浏览对话 · 搜索关键词可定位上下文')+`<div class="message-workspace"><aside class="message-dates"><div class="message-pane-heading">日期</div><div class="message-date-list"></div></aside><section class="message-center"><div id="messageResults" aria-busy="true"></div></section><aside class="message-search"><div class="message-pane-heading">搜索</div><form id="conversationSearchForm"><input id="conversationSearch" type="search" aria-label="搜索对话" placeholder="搜索聊天记录"><div class="message-search-controls"><select id="searchRole" aria-label="消息类型"><option value="">全部消息</option><option value="user">我</option><option value="assistant">机器人</option></select><button type="submit">搜索</button></div></form><div id="searchSummary" class="small"></div><div id="conversationSearchResults" class="message-search-results" aria-live="polite"></div><button id="moreSearchResults" hidden>更多结果</button></aside></div>`;
  function renderDays(){
    const entries=[{day:'',count:days.reduce((sum,d)=>sum+d.count,0)},...days];
    $('.message-date-list').innerHTML=entries.map(d=>`<button class="message-date" data-day="${esc(d.day)}"><strong>${esc(d.day||'全部')}</strong><span>${fmt(d.count)} 条</span></button>`).join('');
  }
  function selectDay(day){
    document.querySelectorAll('[data-day]').forEach(b=>{const active=b.dataset.day===day;b.classList.toggle('active',active);b.setAttribute('aria-pressed',active);});
  }
  function writeHash(next){const hash='#messages?'+next;if(location.hash===hash)navigate(next).catch(e=>error(e.message));else location.hash=hash;}
  renderDays();
  $('.message-date-list').addEventListener('click',e=>{const button=e.target.closest('[data-day]');if(!button)return;const next=new URLSearchParams();if(button.dataset.day)next.set('date',button.dataset.day);if(session)next.set('session',session);if(searchQuery)next.set('q',searchQuery);if(searchRole)next.set('role',searchRole);writeHash(next);});
  function selectResult(){document.querySelectorAll('[data-hit]').forEach(b=>b.classList.toggle('active',b.dataset.hit===anchor));}
  async function search(append=false,restoreLimit=20){
    const version=++searchVersion;
    if(!append){searchQuery=$('#conversationSearch').value.trim();searchRole=$('#searchRole').value;}
    const query=searchQuery,role=searchRole,offset=append?results.length:0;
    $('#moreSearchResults').disabled=true;$('#searchSummary').textContent='搜索中…';
    try{
      const data=await api('messages?'+new URLSearchParams({q:query,role,limit:String(append?20:Math.min(200,Math.max(20,restoreLimit))),offset:String(offset)}));
      if(generation!==state.generation||version!==searchVersion)return;
      results=append?[...results,...data.items]:data.items;$('#searchSummary').textContent=data.total+' 条结果';
      const pane=$('#conversationSearchResults'),top=pane.scrollTop;
      pane.innerHTML=results.map(m=>{const index=m.text.toLowerCase().indexOf(query.toLowerCase());const from=Math.max(0,index-36);const snippet=(from?'…':'')+m.text.slice(from,from+140).replace(/\s+/g,' ')+(m.text.length>from+140?'…':'');return `<button class="message-search-result ${m.id===anchor?'active':''}" data-hit="${esc(m.id)}"><span class="search-result-meta"><strong>${m.role==='user'?'我':'机器人'}</strong><time>${date(m.timestamp)}</time></span><span>${highlightSearch(snippet,query)}</span></button>`;}).join('')||empty('无匹配记录');
      pane.scrollTop=append?top:0;$('#moreSearchResults').hidden=!data.has_more;
    }catch(e){if(generation===state.generation&&version===searchVersion){$('#searchSummary').textContent='搜索失败';error(e.message);}}
    finally{if(generation===state.generation&&version===searchVersion)$('#moreSearchResults').disabled=false;}
  }
  async function navigate(next){
    const version=++navigationVersion,nextSession=next.get('session')||'',nextAnchor=next.get('anchor')||'';
    const q=next.get('q')||'',role=next.get('role')||'';
    const cached=nextAnchor&&nextSession===session&&state.messages.find(m=>m.id===nextAnchor);
    const sameLoadedDay=cached&&view.get('date')===messageDay(cached.timestamp);
    const oldSession=session;
    const [nextDays,context]=await Promise.all([nextSession!==session?api('message-days'+(nextSession?'?session='+encodeURIComponent(nextSession):'')):Promise.resolve(days),nextAnchor&&!sameLoadedDay?api('message-context?id='+encodeURIComponent(nextAnchor)+(nextSession?'&session='+encodeURIComponent(nextSession):'')):Promise.resolve(null)]);
    if(generation!==state.generation||version!==navigationVersion)return;
    session=nextSession;days=nextDays;anchor=nextAnchor;
    const selected=context?.date||(sameLoadedDay?messageDay(cached.timestamp):next.get('date'))||'';
    view=new URLSearchParams();if(selected)view.set('date',selected);if(session)view.set('session',session);if(anchor)view.set('anchor',anchor);if(q)view.set('highlight',q);
    if(oldSession!==session)renderDays();selectDay(selected);selectResult();
    if(sameLoadedDay)state.focusChatMessage?.(anchor,q);else await loadMessages(view,generation,context);
    if(generation!==state.generation||version!==navigationVersion)return;
    if(q!==searchQuery||role!==searchRole){$('#conversationSearch').value=q;$('#searchRole').value=role;if(q||role)await search(false,Number(next.get('search_limit'))||20);else{results=[];searchQuery='';searchRole='';$('#conversationSearchResults').innerHTML='';$('#searchSummary').textContent='';$('#moreSearchResults').hidden=true;}}
  }
  state.chatController={navigate};
  $('#conversationSearchForm').addEventListener('submit',e=>{e.preventDefault();search().catch(e=>error(e.message));});
  $('#moreSearchResults').addEventListener('click',()=>search(true));
  $('#conversationSearchResults').addEventListener('click',e=>{const button=e.target.closest('[data-hit]');if(!button)return;const hit=results.find(m=>m.id===button.dataset.hit);if(!hit)return;writeHash(new URLSearchParams({anchor:hit.id,q:searchQuery,role:searchRole,search_limit:String(Math.min(200,results.length))}));});
  await navigate(params);
}
function messageDay(timestamp){
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Hong_Kong',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(timestamp)).map(p=>[p.type,p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}function highlightSearch(text,query){
  if(!query)return esc(text);
  let html='',offset=0,index;const lower=text.toLowerCase(),needle=query.toLowerCase();
  while((index=lower.indexOf(needle,offset))>=0){html+=esc(text.slice(offset,index))+'<mark>'+esc(text.slice(index,index+query.length))+'</mark>';offset=index+query.length;}
  return html+esc(text.slice(offset));
}
function highlightChat(content,query){
  if(!query)return null;
  if(content.querySelector('.chat-match'))return content.querySelector('.chat-match');
  const walker=document.createTreeWalker(content,NodeFilter.SHOW_TEXT),nodes=[];
  while(walker.nextNode())nodes.push(walker.currentNode);
  for(const node of nodes){
    const text=node.textContent,lower=text.toLowerCase(),needle=query.toLowerCase();
    if(!lower.includes(needle))continue;
    const fragment=document.createDocumentFragment();let offset=0,index;
    while((index=lower.indexOf(needle,offset))>=0){fragment.append(document.createTextNode(text.slice(offset,index)));const mark=document.createElement('mark');mark.className='chat-match';mark.textContent=text.slice(index,index+query.length);fragment.append(mark);offset=index+query.length;}
    fragment.append(document.createTextNode(text.slice(offset)));node.replaceWith(fragment);
  }
  return content.querySelector('.chat-match');
}
function chatBubble(m,previous,params){
  const mine=m.role==='user', collapsed=!mine&&m.text.length>700;
  const separator=!previous || m.session_id!==previous.session_id || new Date(m.timestamp)-new Date(previous.timestamp)>300000;
  return `${separator?`<div class="chat-time"><time>${date(m.timestamp)}</time>${!params.get('session')?`<span>${esc(channel(m.channel))}</span>`:''}</div>`:''}<article class="message ${m.role}" data-message-id="${esc(m.id)}" aria-label="${mine?'我的消息':'机器人回复'}"><div class="chat-avatar" aria-hidden="true">${mine?'我':'AI'}</div><div class="chat-message-body"><div class="chat-sender">${mine?'我':'机器人'} <time>${date(m.timestamp).slice(-5)}</time></div><div class="chat-bubble"><div class="content ${collapsed?'collapsed':''}" id="chat-content-${esc(m.id)}">${rich(m.text)}</div>${collapsed?`<button class="expand" data-expand="${esc(m.id)}" aria-expanded="false">展开</button>`:''}</div><div class="message-actions"><button class="text-button" data-copy="${esc(m.id)}">复制</button><a href="#messages?session=${encodeURIComponent(m.session_id)}">查看会话</a></div></div></article>`;
}
async function loadMessages(params,generation,context=null){
  const windowVersion=state.chatWindowVersion=(state.chatWindowVersion||0)+1;
  const current=()=>generation===state.generation&&windowVersion===state.chatWindowVersion;
  const query=new URLSearchParams(params);query.delete('anchor');query.set('limit','30');
  const data=context||await api('messages?'+query);if(!current())return;
  state.messages=[...data.items].reverse();
  let before=state.messages[0],after=state.messages.at(-1),hasBefore=context?data.has_before:data.has_more,hasAfter=!!data.has_after;
  const pending={before:false,after:false};
  $('#messageResults').setAttribute('aria-busy','false');
  $('#messageResults').innerHTML=`<section class="chat-window"><div class="chat-window-header"><div><strong>${esc(params.get('date')||'全部')}</strong><span class="small">${fmt(data.total)} 条</span></div><button id="latestMessages">${params.get('date')?'当日最新':'最新消息'}</button></div><div class="chat-timeline" id="chatTimeline" role="region" aria-label="聊天记录" tabindex="0"><div class="chat-load-status" id="chatLoadStatus" role="status"></div><div id="chatMessages">${state.messages.map((m,i)=>chatBubble(m,state.messages[i-1],params)).join('')||empty('暂无消息')}</div><div class="chat-load-status" id="chatNewerStatus" role="status"></div></div><div class="chat-window-footer"><span id="loadedMessageCount">已加载 ${state.messages.length} 条</span></div></section>`;
  const timeline=$('#chatTimeline'),list=$('#chatMessages'),topStatus=$('#chatLoadStatus'),bottomStatus=$('#chatNewerStatus');
  function statuses(){topStatus.textContent=hasBefore?'更早消息':'没有更早消息';bottomStatus.textContent=hasAfter?'较新消息':'';}
  async function load(direction){
    const older=direction==='before',cursor=older?before:after,status=older?topStatus:bottomStatus;
    if(pending[direction]||!(older?hasBefore:hasAfter)||!cursor||!current())return;
    pending[direction]=true;status.textContent='加载中…';timeline.setAttribute('aria-busy','true');
    try{
      const page=new URLSearchParams(query);page.set(direction+'_id',cursor.id);page.set(direction+'_time',cursor.timestamp);
      const next=await api('messages?'+page);if(!current())return;
      const known=new Set(state.messages.map(m=>m.id));const batch=next.items.filter(m=>!known.has(m.id)).reverse();
      const height=timeline.scrollHeight,top=timeline.scrollTop;
      if(batch.length){
        if(older){
          const first=state.messages[0],last=batch.at(-1);
          if(first&&first.session_id===last.session_id&&new Date(first.timestamp)-new Date(last.timestamp)<=300000&&list.firstElementChild?.classList.contains('chat-time'))list.firstElementChild.remove();
          list.insertAdjacentHTML('afterbegin',batch.map((m,i)=>chatBubble(m,batch[i-1],params)).join(''));state.messages=[...batch,...state.messages];before=state.messages[0];
        }else{
          const previous=state.messages.at(-1);list.insertAdjacentHTML('beforeend',batch.map((m,i)=>chatBubble(m,i?batch[i-1]:previous,params)).join(''));state.messages.push(...batch);after=state.messages.at(-1);
        }
      }
      if(older)hasBefore=next.has_more;else hasAfter=next.has_more;
      $('#loadedMessageCount').textContent=`已加载 ${state.messages.length} 条`;statuses();
      if(older)timeline.scrollTop=top+timeline.scrollHeight-height;
    }catch(e){if(current()){status.textContent='加载失败，点击重试';error(e.message);}}
    finally{pending[direction]=false;timeline.setAttribute('aria-busy','false');}
  }
  function focusMessage(id,query=params.get('highlight')){
    const target=[...list.querySelectorAll('[data-message-id]')].find(el=>el.dataset.messageId===id);if(!target)return;
    list.querySelectorAll('.is-target').forEach(el=>el.classList.remove('is-target'));
    target.classList.add('is-target');const content=target.querySelector('.content');content.classList.remove('collapsed');
    const expand=target.querySelector('[data-expand]');if(expand){expand.textContent='收起';expand.setAttribute('aria-expanded','true');}
    if(content.dataset.highlightQuery!==query){content.querySelectorAll('.chat-match').forEach(mark=>mark.replaceWith(document.createTextNode(mark.textContent)));content.normalize();content.dataset.highlightQuery=query||'';}
    const match=highlightChat(content,query)||target;
    const offset=Math.max(16,(timeline.clientHeight-Math.min(match.getBoundingClientRect().height,timeline.clientHeight))/2);
    timeline.scrollTop+=match.getBoundingClientRect().top-timeline.getBoundingClientRect().top-offset;
    target.tabIndex=-1;target.focus({preventScroll:true});
    if(matchMedia('(max-width:720px)').matches)$('.message-center').scrollIntoView({block:'nearest',behavior:'instant'});
  }
  state.focusChatMessage=(id,query)=>{if(current())focusMessage(id,query);};statuses();
  if(context)focusMessage(context.target);else timeline.scrollTop=timeline.scrollHeight;
  timeline.addEventListener('scroll',()=>{if(timeline.scrollTop<100)load('before');if(timeline.scrollHeight-timeline.clientHeight-timeline.scrollTop<100)load('after');},{passive:true});
  topStatus.addEventListener('click',()=>load('before'));bottomStatus.addEventListener('click',()=>load('after'));
  $('#latestMessages').addEventListener('click',()=>loadMessages(query,generation).catch(e=>error(e.message)));
  list.addEventListener('click',async e=>{
    const expand=e.target.closest('[data-expand]');if(expand){const content=document.getElementById('chat-content-'+expand.dataset.expand);content.classList.toggle('collapsed');expand.textContent=content.classList.contains('collapsed')?'展开':'收起';expand.setAttribute('aria-expanded',!content.classList.contains('collapsed'));}
    const copy=e.target.closest('[data-copy]');if(copy){const m=state.messages.find(m=>m.id===copy.dataset.copy);try{await navigator.clipboard.writeText(m.text);copy.textContent='已复制';}catch{copy.textContent='请选中文字复制';}}
  });
}
async function positions(generation){
  const data=await api('positions');if(generation!==state.generation)return;
  const items=data.items||[], isHk=p=>/^(hk|\d{5}$)/i.test(String(p.code));
  $('#main').innerHTML=refinedHead('持仓台账','持仓文件快照 · 人民币与港元分别展示')+`<div class="section-meta"><span>更新 ${date(data.updated)}</span><span>${items.length} 条记录</span></div><div class="filter-bar"><label class="search-field"><span>搜索持仓</span><input id="positionSearch" type="search" placeholder="股票名称或代码"></label><label><span>市场</span><select id="positionMarket"><option value="">全部市场</option><option value="a">A股 · CNY</option><option value="hk">港股 · HKD</option></select></label><label><span>排序</span><select id="positionSort"><option value="source">原始顺序</option><option value="amount">成本金额从高到低</option><option value="date">建仓日期从近到远</option></select></label><button id="positionReset">重置</button><span id="positionCount" class="filter-count" role="status"></span></div><div id="positionSummary"></div><div class="panel position-list"><table><thead><tr><th>股票</th><th>类型 / 币种</th><th class="numeric">成本价</th><th class="numeric">股数</th><th class="numeric">成本金额</th><th>建仓日期</th><th class="numeric">止损 / 目标</th><th>规则 / 备注</th><th>来源</th></tr></thead><tbody id="positionRows"></tbody></table></div>`;
  function render(){
    const query=$('#positionSearch').value.trim().toLowerCase(),selected=$('#positionMarket').value,sort=$('#positionSort').value;
    const visible=items.filter(p=>(!query||`${p.name} ${p.code}`.toLowerCase().includes(query))&&(!selected||(isHk(p)?'hk':'a')===selected));
    if(sort==='amount')visible.sort((a,b)=>(Number(b.cost)*Number(b.shares)||0)-(Number(a.cost)*Number(a.shares)||0));
    if(sort==='date')visible.sort((a,b)=>String(b.opened_date||'').localeCompare(String(a.opened_date||'')));
    $('#positionCount').textContent=`显示 ${visible.length} / ${items.length} 条`;
    $('#positionSummary').innerHTML=metricTiles(['a','hk'].map(m=>{const group=visible.filter(p=>(isHk(p)?'hk':'a')===m);return [m==='a'?'A股成本金额 · CNY':'港股成本金额 · HKD',fmt(group.reduce((sum,p)=>sum+(Number(p.cost)*Number(p.shares)||0),0)),`${group.length} 只 · 当前筛选结果`];}));
    $('#positionRows').innerHTML=visible.map(p=>{
      const rules=[p.rules?.watch_line?`观察位：${fmt(p.rules.watch_line)} · ${esc(p.rules.watch_note||'')}`:'',p.rules?.add_condition?`加仓条件：${esc(p.rules.add_condition)}`:'',p.rules?.exit_triggers?`退出条件：${esc(p.rules.exit_triggers.join('；'))}`:'',p.notes?`备注：${esc(p.notes)}`:''].filter(Boolean);
      return `<tr><td><strong>${esc(p.name)}</strong><div class="small">${esc(p.code)}</div></td><td>${esc(p.type||'持仓')}<div class="small">${isHk(p)?'HKD':'CNY'}</div></td><td class="numeric">${fmt(p.cost)}</td><td class="numeric">${fmt(p.shares)}</td><td class="numeric"><strong>${fmt(p.cost*p.shares)}</strong></td><td>${esc(p.opened_date||'未记录')}</td><td class="numeric">${p.stop?fmt(p.stop):'—'}<div class="small">目标 ${p.target?fmt(p.target):'—'}</div></td><td class="position-list-rules">${rules.length?`<details><summary>查看详情</summary>${rules.map(rule=>`<p>${rule}</p>`).join('')}</details>`:'—'}</td><td><a href="#messages?q=${encodeURIComponent(p.name)}">相关对话 ↗</a></td></tr>`;
    }).join('')||`<tr><td colspan="9">${empty(items.length?'没有匹配的持仓':'暂无持仓记录',items.length?'试试其他关键词，或点击重置。':'同步持仓文件后将在这里显示。')}</td></tr>`;
  }
  $('#positionSearch').addEventListener('input',render);$('#positionMarket').addEventListener('change',render);$('#positionSort').addEventListener('change',render);
  $('#positionReset').addEventListener('click',()=>{$('#positionSearch').value='';$('#positionMarket').value='';$('#positionSort').value='source';render();});render();
}
async function notes(params,generation){
  const items=await api('documents');if(generation!==state.generation)return;
  items.sort((a,b)=>(a.id==='MEMORY.md'?-2:a.id==='trade-ledger.md'?-1:0)-(b.id==='MEMORY.md'?-2:b.id==='trade-ledger.md'?-1:0)||b.title.localeCompare(a.title));
  const id=params.get('id')||items[0]?.id, noteName=d=>d.id==='MEMORY.md'?'长期记忆':d.title==='trade-ledger'?'交易台账':'每日笔记 · '+d.title;
  $('#main').innerHTML=refinedHead('智能体记忆','浏览长期记忆、交易台账和每日笔记')+`<div class="memory-workspace"><aside class="panel memory-index"><label class="search-field"><span>搜索文件</span><input id="noteSearch" type="search" placeholder="名称或日期"></label><div class="section-meta"><span>记忆文件</span><span>${items.length} 份</span></div><div class="note-list">${items.map(d=>`<button data-note="${esc(d.id)}" data-title="${esc(noteName(d))}" class="${d.id===id?'active':''}" aria-pressed="${d.id===id}"><strong>${esc(noteName(d))}</strong><span>${date(d.timestamp)}</span></button>`).join('')||empty('暂无文件')}</div><p id="noteSearchEmpty" class="small" hidden>没有匹配的文件</p></aside><article class="panel memory-document" id="noteContent">${empty(id?'正在读取记忆…':'暂无记忆文件')}</article></div>`;
  $('#noteSearch').addEventListener('input',()=>{const q=$('#noteSearch').value.trim().toLowerCase();let count=0;document.querySelectorAll('[data-note]').forEach(b=>{b.hidden=!b.dataset.title.toLowerCase().includes(q);if(!b.hidden)count++;});$('#noteSearchEmpty').hidden=!!count;});
  document.querySelectorAll('[data-note]').forEach(b=>b.addEventListener('click',()=>{location.hash='notes?id='+encodeURIComponent(b.dataset.note);}));
  if(id){const d=await api('documents?id='+encodeURIComponent(id));if(generation!==state.generation)return;if(!d){$('#noteContent').innerHTML=empty('未找到该文件','请从左侧选择其他文件。');return;}$('#noteContent').innerHTML=`<div class="document-heading"><div><h2>${esc(d.id==='MEMORY.md'?'长期记忆':d.title==='trade-ledger'?'交易台账':d.title)}</h2><p class="small">更新 ${date(d.timestamp)}</p></div><span class="pill neutral">只读</span></div><div class="prose">${rich(d.text)}</div><a class="back-top" href="#" id="noteTop">返回文首 ↑</a>`;$('#noteTop').addEventListener('click',e=>{e.preventDefault();$('#noteContent').scrollTo({top:0,behavior:'smooth'});});}
}
async function tasks(generation,params){
  const offset=Math.max(0,Number(params.get('offset'))||0), status=params.get('status')||'';
  const result=await api('tasks?limit=30&offset='+offset+'&status='+encodeURIComponent(status));if(generation!==state.generation)return;
  const items=result.items;
  const label={completed:'已完成',failed:'失败',running:'执行中（历史）',unconfirmed:'完成状态未知'};
  const toolLabel=t=>({'exec':'执行命令','read':'读取文件','write':'写入文件','edit':'修改文件','process':'查询进度','web_search':'搜索资料','web_fetch':'读取网页'}[t]||t);
  $('#main').innerHTML=header('执行记录')+`<div class="toolbar"><label for="taskStatus">事件状态</label><select id="taskStatus"><option value="">全部状态</option>${Object.entries(label).map(([k,v])=>`<option value="${k}" ${status===k?'selected':''}>${v}</option>`).join('')}</select><span class="small">共 ${fmt(result.total)} 条记录</span></div><section class="panel table-panel"><div class="scroll"><table><thead><tr><th>工具 / 脚本</th><th>开始时间</th><th>返回时间</th><th title="历史工具事件，非实时进程状态">事件状态</th><th>来源</th></tr></thead><tbody>${items.map(t=>`<tr><td>${esc(t.label===t.tool?toolLabel(t.tool):t.label)}<div class="small">${esc(t.tool)}</div></td><td>${date(t.started)}</td><td>${date(t.finished)}</td><td><span class="pill ${t.status==='failed'?'warn':'neutral'}">${label[t.status]||'未知'}</span></td><td><a href="#messages?session=${encodeURIComponent(t.session_id)}">来源会话</a></td></tr>`).join('')||'<tr><td colspan="5">暂无匹配的工具执行记录</td></tr>'}</tbody></table></div></section><div class="pagination"><button id="prevTasks" ${offset<=0?'disabled':''}>上一页</button><span>${result.total?offset+1:0}–${Math.min(offset+30,result.total)} / ${result.total}</span><button id="nextTasks" ${offset+30>=result.total?'disabled':''}>下一页</button></div>`;
  $('#main .page-head').outerHTML=refinedHead('执行记录','按事件状态查阅历史工具执行 · 这里不代表实时进程状态');
  $('#main .toolbar').insertAdjacentHTML('afterbegin','<label class="search-field"><span>搜索本页</span><input id="taskSearch" type="search" placeholder="工具或脚本名称"></label>');
  $('#main .toolbar').classList.add('filter-bar');
  const statusField=document.createElement('div');statusField.className='task-status-field';
  const statusLabel=$('#main label[for="taskStatus"]'),statusSelect=$('#taskStatus');statusLabel.before(statusField);statusField.append(statusLabel,statusSelect);
  $('#main tbody').insertAdjacentHTML('beforeend','<tr id="taskSearchEmpty" hidden><td colspan="5">没有匹配的记录，请修改关键词。</td></tr>');
  $('#taskSearch').addEventListener('input',()=>{const q=$('#taskSearch').value.trim().toLowerCase();let count=0;document.querySelectorAll('#main tbody tr:not(#taskSearchEmpty)').forEach(row=>{row.hidden=!row.cells[0].textContent.toLowerCase().includes(q);if(!row.hidden)count++;});$('#taskSearchEmpty').hidden=!!count;});
  $('#main .pagination').insertAdjacentHTML('afterbegin',`<span class="small">第 ${result.total?Math.floor(offset/30)+1:0} / ${Math.ceil(result.total/30)} 页</span>`);
  $('#taskStatus').addEventListener('change',()=>{location.hash='tasks?status='+$('#taskStatus').value;});
  $('#prevTasks').addEventListener('click',()=>{location.hash='tasks?status='+status+'&offset='+Math.max(0,offset-30);});$('#nextTasks').addEventListener('click',()=>{location.hash='tasks?status='+status+'&offset='+(offset+30);});
}
async function route(){
  const [tab,query='']=location.hash.slice(1).split('?'),nextTab=names[tab]?tab:'overview';
  if(nextTab==='messages'&&state.tab==='messages'&&state.chatController&&$('.message-workspace')){
    error('');try{await state.chatController.navigate(new URLSearchParams(query));}catch(e){error(e.message);}return;
  }
  const generation=++state.generation;state.tab=nextTab;state.chatController=null;
  $('#main').classList.add('refined-page');
  $('#main').dataset.page=nextTab;
  document.querySelectorAll('[data-tab]').forEach(a=>{a.classList.toggle('active',a.dataset.tab===state.tab);if(a.dataset.tab===state.tab)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  error('');$('#main').setAttribute('aria-busy','true');if(!$('#main').childElementCount)$('#main').innerHTML='<div class="loading">加载中…</div>';
  try{
    const params=new URLSearchParams(query);
    if(state.tab==='overview'){state.overview=await api('overview');if(generation!==state.generation)return;noticeStatus(state.overview);overview(state.overview);}
    else if(state.tab==='scans')await scans(params,generation);
    else if(state.tab==='messages')await messages(params,generation);
    else if(state.tab==='positions')await positions(generation);
    else if(state.tab==='paper')await paper(generation);
    else if(['automation','rules','project'].includes(state.tab))await projectPage(generation);
    else if(state.tab==='notes')await notes(params,generation);
    else await tasks(generation,params);
  }catch(e){if(generation!==state.generation)return;error(e.message);}
  finally{if(generation===state.generation){$('#main').setAttribute('aria-busy','false');bindPageRefresh();}}
}
async function start(){
  try{state.overview=await api('overview');$('#login').hidden=true;$('#shell').hidden=false;noticeStatus(state.overview);await route();}catch(e){if(!$('#login').hidden)$('#loginError').textContent=e.message.includes('过期')?'':e.message;else error(e.message);}
}
$('#loginForm').addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;$('#loginError').textContent='';try{await api('login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$('#password').value})});$('#password').value='';await start();}catch(e){$('#loginError').textContent=e.message;}finally{b.disabled=false;}});
async function logout(){try{await api('logout',{method:'POST'});showLogin();$('#password').value='';$('#loginError').textContent='';}catch(e){error(e.message);}}
async function paper(generation, refresh=false){
  const data=await api('paper');if(generation!==state.generation||state.tab!=='paper')return;
  if(!refresh||!$('#paperContent')){
    $('#main').innerHTML=refinedHead('模拟盘','独立模拟账户 · 人民币与港元分别统计')+`<div class="toolbar"><select id="paperView" aria-label="模拟盘类型"><option value="live">当前模拟盘</option><option value="history">11月以来逐次回放</option></select><select id="paperMarket" aria-label="模拟账户"><option value="a">A股 · 人民币</option><option value="hk">港股 · 港元</option></select><select id="paperFilter" aria-label="记录类型"><option value="filled">持仓 / 成交</option><option value="pending">待成交</option><option value="other">未执行</option><option value="all">全部</option></select><span class="small">推荐按现价买入 · 缺少仓位默认10% · 不等待原文条件 · 未计手续费及滑点</span></div><div id="paperContent"></div>`;
    $('#paperView').addEventListener('change',()=>{$('#paperFilter').value=$('#paperView').value==='history'?'all':'filled';renderPaper(state.paper);});$('#paperMarket').addEventListener('change',()=>renderPaper(state.paper));$('#paperFilter').addEventListener('change',()=>renderPaper(state.paper));
  }
  state.paper=data;renderPaper(data);
}
function renderPaper(data){
  if($('#paperView')?.value==='history'){renderReplay(data);return;}
  const m=$('#paperMarket').value,filter=$('#paperFilter').value,a=data.accounts.find(x=>x.market===m);
  if(!a){$('#paperContent').innerHTML=empty('模拟盘正在初始化');return;}
  const orders=data.orders.filter(o=>o.market===m&&(filter==='all'||(filter==='other'?!['filled','pending'].includes(o.status):o.status===filter)));
  const statuses={filled:'已成交',pending:'待成交',review:'需核对',skipped:'未执行',expired:'已过期'};
  const h=`<div class="metrics">${[['总资产',a.equity],['可用资金',a.cash],['持仓市值',a.value],['浮动盈亏',a.pnl]].map(([label,value])=>`<div class="metric"><span class="metric-label">${label}</span><strong>${fmt(value)}</strong></div>`).join('')}</div><p class="small">初始资金 ${fmt(a.initial)} · 启用 ${date(data.started)} · 检查 ${date(data.sync.time)}${a.stale?' · 持仓估值含过期报价':''}${data.sync.error?' · 行情连接失败':''}</p><section class="panel table-panel"><div class="scroll"><table><thead><tr><th>股票</th><th>状态</th><th>模型仓位</th><th>股数</th><th>成交价</th><th>最新价</th><th>浮动盈亏</th><th>时间 / 原因</th><th>来源</th></tr></thead><tbody>${orders.map(o=>`<tr><td><strong>${esc(o.name)}</strong><div class="small">${esc(o.code)}</div></td><td>${esc(statuses[o.status]||o.status)}</td><td>${esc(o.plan.source?.position||'—')}</td><td>${fmt(o.shares)}</td><td>${fmt(o.price)}</td><td>${o.status==='filled'?fmt(o.mark):'—'}${o.status==='filled'&&o.stale?'<div class="small">历史报价</div>':''}</td><td>${fmt(o.pnl)}</td><td>${date(o.filled||o.signal_time)}<div class="small">${esc(o.reason)}</div>${o.mark_time&&o.status==='filled'?`<div class="small">报价 ${date(o.mark_time)}</div>`:''}</td><td><a href="#messages?anchor=${encodeURIComponent(o.result_id)}">原文</a></td></tr>`).join('')}</tbody></table></div>${orders.length?'':empty(filter==='filled'?'暂无模拟持仓':'暂无记录')}</section>`;
  if($('#paperContent').innerHTML!==h)$('#paperContent').innerHTML=h;
}
function renderReplay(data){
  const report=data.replay;if(!report){$('#paperContent').innerHTML=empty('暂无历史回放');return;}
  const m=$('#paperMarket').value,filter=$('#paperFilter').value,a=report.accounts.find(x=>x.market===m);
  const orders=report.orders.filter(o=>o.market===m&&(filter==='all'||(filter==='filled'?o.status==='estimated':filter==='pending'?false:o.status!=='estimated')));
  const labels={estimated:'估算买入',review:'需核对',missing:'行情缺失',unmet:'快照未满足',skipped:'未执行'};
  const events=report.events||[];
  const eventList=`<details><summary>逐次回复时间线 · ${events.length} 次</summary><div class="scroll"><table><thead><tr><th>发布时间</th><th>请求</th><th>已提取股票数</th><th>原文</th></tr></thead><tbody>${events.map(e=>`<tr><td>${date(e.timestamp)}</td><td>${esc(e.prompt)}</td><td>${e.stock_count}</td><td><a href="#messages?anchor=${encodeURIComponent(e.id)}">查看</a></td></tr>`).join('')}</tbody></table></div></details>`;
  $('#paperContent').innerHTML=`<div class="metrics">${[['估算总资产',a.equity],['可用资金',a.cash],['持仓估值',a.value],['估算盈亏',a.pnl]].map(([label,value])=>`<div class="metric"><span class="metric-label">${label}</span><strong>${fmt(value)}</strong></div>`).join('')}</div><p class="small">${messageDay(report.start)} 起 · ${report.result_count} 条最终回复 · ${report.stock_count} 条逐股结果 · ${report.counts.estimated||0} 笔估算买入</p><details><summary>回放口径</summary><p>${esc(report.assumptions)}</p></details><section class="panel table-panel"><div class="scroll"><table><thead><tr><th>股票</th><th>状态</th><th>仓位</th><th>股数</th><th>估算买入价</th><th>期末快照价</th><th>估算盈亏</th><th>时间 / 原因</th><th>来源</th></tr></thead><tbody>${orders.map(o=>`<tr><td>${esc(o.name)}<div class="small">${esc(o.code)}</div></td><td>${esc(labels[o.status])}</td><td>${esc(o.position||'—')}</td><td>${fmt(o.shares)}</td><td>${fmt(o.price)}</td><td>${fmt(o.mark)}${o.mark_time?`<div class="small">${date(o.mark_time)}</div>`:''}</td><td>${fmt(o.pnl)}</td><td>${date(o.filled||o.signal_time)}<div class="small">${esc(o.reason)}</div></td><td><a href="#messages?anchor=${encodeURIComponent(o.result_id)}">原文</a></td></tr>`).join('')}</tbody></table></div>${orders.length?'':empty('暂无记录')}</section>`;
  $('#paperContent').insertAdjacentHTML('beforeend',eventList);
  if(report.provider==='tushare')$('#paperContent').insertAdjacentHTML('afterbegin','<p class="small">行情来源：Tushare · A股1分钟 · 港股分钟未授权 · 不使用模型现价补单</p>');
  if(a.valuation_incomplete)$('#paperContent').insertAdjacentHTML('afterbegin','<p class="small">部分持仓缺少后续行情，按成本占位；估算总资产不是完整期末净值。</p>');
}
$('#logout').addEventListener('click',()=>$('#logoutDialog').showModal());
$('#cancelLogout').addEventListener('click',()=>$('#logoutDialog').close());
$('#confirmLogout').addEventListener('click',async()=>{const b=$('#confirmLogout');b.disabled=true;try{await logout();}finally{b.disabled=false;$('#logoutDialog').close();}});
$('#closeDetail').addEventListener('click',()=>$('#detail').close());
window.addEventListener('hashchange',()=>{if(!$('#shell').hidden)route();});
setInterval(async()=>{if($('#shell').hidden||document.hidden)return;try{state.overview=await api('overview');noticeStatus(state.overview);if(state.tab==='paper')await paper(state.generation,true);}catch(e){error(e.message);}},30000);
start();

function heartbeatLine(stamp){
  return !stamp?'未取得心跳':Date.now()-new Date(stamp)>120000?'心跳超过两分钟未更新':'最近两分钟有心跳';
}
function scheduleText(s){
  if(!s)return '未记录';
  if(s.kind==='every')return `每 ${Number(s.everyMs)/1000} 秒`;
  if(s.kind==='at')return date(s.at);
  if(s.kind==='cron')return `${s.expr||'未记录表达式'} · ${s.tz||'调度器默认时区'}`;
  return '未识别的计划类型';
}
function scheduledJob(j){
  const s=j.state||{},result=({ok:'成功',error:'失败',skipped:'已跳过'})[s.lastStatus]||s.lastStatus||'未运行';
  return `<article class="job-card"><div class="panel-heading"><h3>${esc(j.name||j.id)}</h3><span class="pill ${j.enabled?'':'neutral'}">${j.enabled?'已启用':'已停用'}</span></div><p class="job-schedule">${esc(scheduleText(j.schedule))}</p><dl class="info-pairs"><div><dt>上次运行</dt><dd>${date(s.lastRunAtMs)}</dd></div><div><dt>运行结果</dt><dd><span class="pill ${s.lastStatus==='error'?'warn':'neutral'}">${esc(result)}</span></dd></div><div><dt>下次运行</dt><dd>${j.enabled?date(s.nextRunAtMs):'已停用'}</dd></div><div><dt>连续错误</dt><dd>${fmt(s.consecutiveErrors||0)} 次</dd></div></dl>${s.lastError?`<details class="job-error"><summary>查看错误详情</summary><p>${esc(s.lastError)}</p></details>`:''}</article>`;
}
async function projectPage(generation){
  const d=await api('project');if(generation!==state.generation)return;
  const sync=d.sync||{},paperSync=d.paper_sync||{},scheduler=d.scheduler||{},rules=d.rules||[];
  const ruleName=r=>({'a-share-short-term-analysis':'A股短线分析','a-share-t1-entry-discipline':'A股 T+1 入场纪律',AGENTS:'工作规则',SOUL:'行为原则',IDENTITY:'智能体身份',HEARTBEAT:'心跳检查'})[r.title]||r.title;
  const health=(stamp,failed)=>`<span class="pill ${failed||!stamp||Date.now()-new Date(stamp)>120000?'warn':'good'}">${failed?'存在异常':!stamp?'尚无记录':Date.now()-new Date(stamp)>120000?'更新延迟':'更新正常'}</span>`;
  const syncCard=(title,stamp,interval,errors)=>`<section class="panel service-card"><div class="panel-heading"><h2>${title}</h2>${health(stamp,errors.length)}</div><dl class="info-pairs"><div><dt>最近更新</dt><dd>${date(stamp)}</dd></div><div><dt>检查间隔</dt><dd>${interval} 秒</dd></div><div><dt>心跳状态</dt><dd>${heartbeatLine(stamp)}</dd></div></dl>${errors.length?`<details class="job-error"><summary>异常详情 · ${errors.length} 项</summary>${errors.map(e=>`<p>${esc(e)}</p>`).join('')}</details>`:'<p class="small service-ok">本次处理未记录错误</p>'}</section>`;
  let h='';
  if(state.tab==='rules'){
    h=refinedHead('策略与规则','查看智能体保存的规则文件 · 仅供查阅')+`<div class="filter-bar"><label class="search-field"><span>搜索规则</span><input id="ruleSearch" type="search" placeholder="标题或正文关键词"></label><button id="expandRules">全部展开</button><button id="collapseRules">全部收起</button><span class="filter-count" id="ruleCount" role="status">${rules.length} 份文件</span></div><div class="rule-list">${rules.map((r,i)=>`<section class="panel rule-card" data-rule="${i}"><details><summary><span class="rule-title">${esc(ruleName(r))}<small>${esc(r.id)}</small></span><span class="small">${date(r.timestamp)}</span></summary><div class="prose">${rich(r.text)}</div></details></section>`).join('')||empty('暂无规则文件')}<div id="ruleEmpty" hidden>${empty('没有匹配的规则','试试其他标题或正文关键词。')}</div></div>`;
  }else if(state.tab==='automation'){
    const jobs=scheduler.items||[];
    h=refinedHead('自动任务','查看后台更新与智能体定时计划')+`<div class="refined-grid">${syncCard('文件采集',sync.last_sync,sync.interval||30,sync.errors||[])}${syncCard('模拟盘',paperSync.time,30,paperSync.error?[paperSync.error]:[])}</div><div class="section-heading"><div><h2>智能体定时计划</h2><p class="small">${esc(scheduler.note||'调度来源尚未核验')}</p></div><a href="#tasks">查看执行记录 ↗</a></div><div class="filter-bar"><label class="search-field"><span>搜索任务</span><input id="jobSearch" type="search" placeholder="任务名称"></label><label><span>状态</span><select id="jobStatus"><option value="">全部任务</option><option value="enabled">已启用</option><option value="disabled">已停用</option><option value="error">运行失败</option></select></label><span class="filter-count" id="jobCount" role="status"></span></div><div class="job-grid" id="jobList"></div><p class="small">心跳表示最近一次处理记录，不等同于系统服务状态。<a href="#rules">查看心跳规则 ↗</a></p>`;
    $('#main').innerHTML=h;
    function render(){const q=$('#jobSearch').value.trim().toLowerCase(),status=$('#jobStatus').value;const visible=jobs.filter(j=>String(j.name||j.id).toLowerCase().includes(q)&&(!status||(status==='enabled'?j.enabled:status==='disabled'?!j.enabled:j.state?.lastStatus==='error')));$('#jobCount').textContent=`显示 ${visible.length} / ${jobs.length} 个任务`;$('#jobList').innerHTML=visible.map(scheduledJob).join('')||empty(jobs.length?'没有匹配的任务':'暂无定时任务',jobs.length?'调整搜索词或状态筛选。':'当前可见数据源中未记录定时计划。');}
    $('#jobSearch').addEventListener('input',render);$('#jobStatus').addEventListener('change',render);render();return;
  }else{
    h=refinedHead('项目状态','检查同步状态、数据覆盖与项目边界')+`<div class="refined-grid">${syncCard('采集同步',sync.last_sync,sync.interval||30,sync.errors||[])}${syncCard('模拟盘更新',paperSync.time,30,paperSync.error?[paperSync.error]:[])}</div><div class="section-heading"><h2>数据覆盖</h2><span class="small">持仓文件更新 ${date(d.positions_updated)}</span></div><section class="panel coverage-panel"><div class="scroll"><table><thead><tr><th>数据类型</th><th>市场 / 来源</th><th class="numeric">记录数</th><th>开始时间</th><th>最近时间</th></tr></thead><tbody>${(d.scans||[]).map(x=>`<tr><td>选股扫描</td><td>${market(x.market)}</td><td class="numeric">${fmt(x.count)} 份</td><td>—</td><td>${date(x.timestamp)}</td></tr>`).join('')}${(d.history||[]).map(x=>`<tr><td>历史分钟</td><td>${esc(x.provider)} · ${market(x.market)}</td><td class="numeric">${fmt(x.count)} 条</td><td>${esc(x.start)}</td><td>${esc(x.end)}</td></tr>`).join('')||''}</tbody></table></div>${!(d.scans||[]).length&&!(d.history||[]).length?empty('暂无覆盖数据'):''}</section><section class="panel project-about"><details><summary>项目说明与数据口径</summary><div class="data-flow"><span>OpenClaw 文件</span><span>→</span><span>只读采集</span><span>→</span><span>产品数据库</span><span>→</span><span>网页展示</span></div><p>微信对话展示原始消息，选股记录展示模型最终结果，持仓台账来自持仓文件，智能体记忆展示保存的文件。</p><p>推荐、模拟成交与用户确认的实际交易分别展示；人民币与港元分别统计。模拟盘只操作独立模拟账户。</p><p class="small">网页不调用模型、不连接券商、不发送微信消息。</p></details></section>`;
  }
  $('#main').innerHTML=h;
  if(state.tab==='rules'){
    $('#ruleSearch').addEventListener('input',()=>{const q=$('#ruleSearch').value.trim().toLowerCase();let count=0;document.querySelectorAll('[data-rule]').forEach(card=>{const r=rules[Number(card.dataset.rule)];card.hidden=!`${ruleName(r)} ${r.id} ${r.text}`.toLowerCase().includes(q);if(!card.hidden)count++;});$('#ruleCount').textContent=`显示 ${count} / ${rules.length} 份文件`;$('#ruleEmpty').hidden=!!count;});
    $('#expandRules').addEventListener('click',()=>document.querySelectorAll('[data-rule]:not([hidden]) details').forEach(x=>x.open=true));$('#collapseRules').addEventListener('click',()=>document.querySelectorAll('[data-rule] details').forEach(x=>x.open=false));
  }
}
$('#changePassword').addEventListener('click',()=>{$('#passwordForm').reset();$('#passwordError').textContent='';$('#passwordDialog').showModal();});
$('#cancelPassword').addEventListener('click',()=>$('#passwordDialog').close());
$('#passwordDialog').addEventListener('close',()=>{$('#passwordForm').reset();$('#passwordError').textContent='';});
$('#passwordForm').addEventListener('submit',async e=>{
  e.preventDefault();
  if($('#newPassword').value!==$('#confirmPassword').value){$('#passwordError').textContent='两次输入的新口令不一致';return;}
  const button=e.currentTarget.querySelector('[type="submit"]');button.disabled=true;
  try{
    await api('change-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({current_password:$('#currentPassword').value,new_password:$('#newPassword').value})});
    $('#passwordDialog').close();$('#passwordForm').reset();showLogin();$('#password').value='';$('#loginError').textContent='口令已修改，请使用新口令登录';
  }catch(err){$('#passwordError').textContent=err.message;}finally{button.disabled=false;}
});
