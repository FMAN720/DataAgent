import { renderMarkdown } from './markdown.js?v=20261103-fix1';
import { installBusinessWorkspace } from './business.js?v=20261105-core1';

const $ = (selector) => document.querySelector(selector);
const state = {view:'chat', datasets:[], documents:[], config:null, selected:null, sequence:0,
  session:null, chat:null, loads:{}, polling:false, uploading:false, opener:null, noticeDataset:null, datasetData:null,
  restoring:false, historyGeneration:0, historyListGeneration:0, title:'新建分析',
  followOutput:true, navOpen:false, navCollapsed:false, modelSettings:null,
  modelSettingsBusy:false, modelSettingsOpener:null, clearModelArmed:false, analysisEntry:null,
  modelPurpose:'chat',modelProfiles:{},modelGeneration:0,modelManual:{},modelLists:{}};
const labels = {previewed:'待确认', queued:'等待导入', importing:'正在导入', ready:'可分析', failed:'导入失败', deleting:'正在清理'};
const types = {reference:'参考资料', weekly_report:'运营周报', review:'活动复盘', campaign:'活动方案', support:'客服汇总'};
const kinds = {text:'文本', integer:'整数', decimal:'精确小数', boolean:'布尔', date:'日期', datetime:'日期时间', datetime_tz:'带时区时间'};
const headings = {datasets:['表格数据','把报表变成可查询的数据，从一次预览开始。'], documents:['业务文档','收好业务背景，让每个结论都有资料可查。'], search:['检索资料','按问题、日期和类型，找到可以核对的原文。'], chat:['分析工作台','先选一个示例，或直接描述你的业务问题。']};
const toolLabels = {list_tables:'查找数据表',describe_tables:'核对字段',search_glossary:'业务口径',run_sql:'查询数据',query_metric:'固定指标',get_metric_definition:'指标定义',get_business_definition:'口径定义',query_business_metric:'业务指标',search_documents:'检索资料',compare_periods:'周期对比',prepare_analysis:'确认条件',profile_dataset:'数据体检',list_datasets:'数据集',calculator:'计算',search_sql_examples:'参考写法'};
function node(tag,text,className) {const item=document.createElement(tag); if(text!==undefined&&text!==null)item.textContent=String(text); if(className)item.className=className; return item;}
function button(text,action,className='quiet') {const item=node('button',text,className); item.type='button'; item.addEventListener('click',()=>Promise.resolve().then(action).catch(showError)); return item;}
function clear(target) {target.replaceChildren(); return target;}
function notice(message,error=false,datasetId=null) {state.noticeDataset=datasetId; $('#notice').textContent=message; $('#notice').className=error?'notice error':'notice'; $('#notice').hidden=!message;}
function showError(error) {if(error.name!=='AbortError')notice(error.message||'操作没有完成，请稍后重试。',true);}
function describeError(value) {if(typeof value==='string')return value; if(Array.isArray(value))return value.map(item=>`${item.loc?.slice(-1)[0]||'参数'}：${item.msg||'格式不正确'}`).join('；'); return '服务暂时不可用，请检查连接后重试。';}
async function request(path,options={}) {
  if(!state.config)throw new Error('工作台配置尚未加载，请稍候或刷新页面。');
  const response=await fetch(state.config.api_prefix+path,options);
  const data=await response.json().catch(()=>({}));
  if(!response.ok){const error=new Error(describeError(data.detail)); error.status=response.status; throw error;} return data;
}
function jsonOptions(body,method='POST') {return {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};}
function applyModelSettings(value) {
  const profiles=value.profiles||{chat:value};
  for(const purpose of Object.keys(profiles)){const item=profiles[purpose];state.modelProfiles[purpose]={configured:item.configured===true,base_url:item.base_url||'',model:item.model||'',has_api_key:item.has_api_key===true,enabled:item.enabled??item.configured===true,dimensions:item.dimensions||null,error:item.error||''};}
  state.modelSettings=state.modelProfiles.chat||{configured:false,base_url:'',model:'',has_api_key:false};
  if(state.config)state.config.model_configured=state.modelSettings.configured;
  $('#model-settings-label').textContent=state.modelSettings.configured?state.modelSettings.model:'连接你自己的模型';
  $('#model-setup-hint').hidden=state.modelSettings.configured||!state.config?.model_configuration_required;
  if(state.config){
    state.config.embedding_enabled=Boolean(state.modelProfiles.embedding?.configured);
    state.config.vision_recognition_available=Boolean(state.config.vision_available&&state.modelProfiles.vision?.configured);
    state.config.vision_model=state.modelProfiles.vision?.model||'';
    for(const option of $('#search-mode').options)if(option.value!=='keyword')option.disabled=!state.config.embedding_enabled;
    if(!state.config.embedding_enabled)$('#search-mode').value='keyword';
    visionControls();
  }
  $('#model-settings-clear').disabled=!state.modelProfiles[state.modelPurpose]?.has_api_key;
}
const MODEL_PURPOSES=['chat','vision','embedding'];
const MODEL_TITLES={chat:'聊天分析',vision:'图片 / OCR',embedding:'向量检索'};
const MODEL_PROVIDERS={deepseek:'https://api.deepseek.com',siliconflow:'https://api.siliconflow.cn/v1',openai:'https://api.openai.com/v1'};
function modelField(name,purpose=state.modelPurpose){return $('#'+(purpose==='chat'?'model-':purpose+'-model-')+name);}
function fillModelSettings(purpose=state.modelPurpose) {
  const current=state.modelProfiles[purpose];
  modelField('base-url',purpose).value=current?.base_url||'';
  modelField('provider',purpose).value=Object.keys(MODEL_PROVIDERS).find(key=>MODEL_PROVIDERS[key]===current?.base_url)||'custom';
  modelField('api-key',purpose).value=''; modelField('api-key',purpose).type='password'; modelField('key-toggle',purpose).textContent='显示'; modelField('key-toggle',purpose).setAttribute('aria-pressed','false');
  modelField('key-state',purpose).textContent=current?.has_api_key?'已在本机保存':'';
  modelField('api-key',purpose).placeholder=current?.has_api_key?'留空保留此用途已保存的 Key':'粘贴此用途的 API Key';
  modelField('enabled',purpose).checked=current?.has_api_key?current.enabled:purpose!=='embedding';
  modelField('manual-toggle',purpose).checked=false;state.modelManual[purpose]=false;
  modelField('manual',purpose).value=current?.model||'';modelField('manual-wrap',purpose).hidden=true;
  renderModelList(purpose,[],current?.model||'');
  if(purpose==='embedding')modelField('dimensions',purpose).value=current?.dimensions||'';
  state.clearModelArmed=false; $('#model-settings-clear').textContent='清除此用途配置'; modelKeyRequirement(purpose);
}
function modelKeyRequirement(purpose=state.modelPurpose) {
  const sameAddress=modelField('base-url',purpose).value.trim().replace(/\/+$/,'')===state.modelProfiles[purpose]?.base_url;
  modelField('api-key',purpose).required=!state.modelProfiles[purpose]?.has_api_key||!sameAddress;
}
function modelPurposePath(path='') {return '/model-settings'+path+(state.modelPurpose==='chat'?'':'?purpose='+state.modelPurpose);}
function renderModelList(purpose,models,current='') {
  const select=clear(modelField('name',purpose));select.append(node('option','先获取模型，再从列表选择'));select.children[0].value='';
  const ids=models.map(item=>item.id);state.modelLists[purpose]=ids;
  if(current&&!ids.includes(current)){const option=node('option',current+'（当前填写）');option.value=current;select.append(option);}
  for(const id of ids){const option=node('option',id);option.value=id;select.append(option);}select.value=current;
}
function chooseModelPurpose(purpose) {
  if(state.modelSettingsBusy)return;
  state.modelPurpose=purpose;state.clearModelArmed=false;$('#model-settings-clear').textContent='清除此用途配置';
  for(const kind of MODEL_PURPOSES){const active=kind===purpose;$('#model-panel-'+kind).hidden=!active;$('#model-panel-'+kind).disabled=!active;$('#model-purpose-'+kind).setAttribute('aria-pressed',String(active));}
  $('#model-settings-check').textContent='测试'+MODEL_TITLES[purpose];
  $('#model-purpose-note').textContent={chat:'用于理解问题、调用分析工具和回答。请选择支持工具调用的聊天模型。',vision:'用于读取图片和扫描件。请选择支持图片输入的模型；实际识别仍需逐次同意外发。',embedding:'用于文档向量索引与检索。请选择文本向量模型；更换后需手动重建现有文档索引。'}[purpose];
  $('#model-cost-note').textContent={chat:'获取模型只请求列表；保存不调用模型。测试会发送一次不含业务数据的工具调用请求，可能计费。',vision:'获取模型只请求列表；保存不识别图片。测试会发送一张仅含 TEST 的合成图片，可能计费。',embedding:'获取模型只请求列表；保存不批量重建索引。测试会发送一小段测试文字并读取实际维度，可能计费。'}[purpose];
  modelSettingsStatus(state.modelProfiles[purpose]?.error||'此用途独立保存地址、模型和 Key。');modelSettingsBusy(false);
}
function modelSettingsStatus(text,error=false) {
  $('#model-settings-status').textContent=text; $('#model-settings-status').className=error?'field-hint model-settings-error':'field-hint';
}
function modelSettingsBusy(busy) {
  state.modelSettingsBusy=busy; $('#model-settings-form').setAttribute('aria-busy',String(busy));
  for(const item of $('#model-settings-form').querySelectorAll('input,button,select'))item.disabled=busy;
  for(const purpose of MODEL_PURPOSES){$('#model-panel-'+purpose).disabled=busy||purpose!==state.modelPurpose;$('#model-purpose-'+purpose).disabled=busy;modelField('name',purpose).disabled=busy||Boolean(state.modelManual[purpose]);}
  $('#model-settings-clear').disabled=busy||!state.modelProfiles[state.modelPurpose]?.has_api_key;
}
async function loadModelSettings(generation=state.modelGeneration) {const value=await request('/model-settings');if(generation===state.modelGeneration)applyModelSettings(value);return value;}
async function openModelSettings() {
  if($('#model-settings-dialog').open)return;
  state.modelSettingsOpener=document.activeElement; closeSourceMenu(); closeMobileNavigation();
  for(const purpose of MODEL_PURPOSES)fillModelSettings(purpose);chooseModelPurpose(state.modelPurpose);
  $('#model-settings-dialog').showModal(); const generation=++state.modelGeneration;modelSettingsBusy(true); modelSettingsStatus('正在读取本机配置…');
  try{const value=await loadModelSettings(generation);if(generation!==state.modelGeneration)return;for(const purpose of MODEL_PURPOSES)fillModelSettings(purpose);modelSettingsStatus(state.modelProfiles[state.modelPurpose]?.error||'选择用途后，填写供应商地址和 Key，点击获取模型。',Boolean(value.error));}
  catch(error){if(generation===state.modelGeneration)modelSettingsStatus(error.message,true);}
  finally{if(generation===state.modelGeneration)modelSettingsBusy(false);}
}
function closeModelSettings() {$('#model-settings-dialog').close();}
function modelSettingsPayload(endpointOnly=false) {
  const payload={base_url:modelField('base-url').value.trim()};
  const key=modelField('api-key').value.trim(); if(key)payload.api_key=key;
  if(endpointOnly)return payload;
  payload.model=(state.modelManual[state.modelPurpose]?modelField('manual'):modelField('name')).value.trim();
  payload.enabled=state.modelPurpose==='chat'||Boolean(modelField('enabled').checked);
  if(state.modelPurpose==='embedding'&&modelField('dimensions').value)payload.dimensions=Number(modelField('dimensions').value);
  return payload;
}
async function fetchModels() {
  if(state.modelSettingsBusy||!modelField('base-url').reportValidity()||!modelField('api-key').reportValidity())return;
  const purpose=state.modelPurpose,generation=++state.modelGeneration,current=modelSettingsPayload().model;
  modelSettingsBusy(true);modelSettingsStatus('正在向供应商获取模型列表…');
  try{const result=await request(modelPurposePath('/models'),jsonOptions(modelSettingsPayload(true)));if(generation!==state.modelGeneration)return;
    renderModelList(purpose,result.models||[],current);modelSettingsStatus(result.models?.length?`已获取 ${result.models.length} 个模型，请从列表选择。列表不保证当前用途可用，建议再测试。`:'供应商返回空列表；可以检查 Key 权限，或改用手动填写。');
  }catch(error){if(generation===state.modelGeneration)modelSettingsStatus(error.message+' 可使用“手动填写模型名称”。',true);}
  finally{if(generation===state.modelGeneration)modelSettingsBusy(false);}
}
async function submitModelSettings(test=false) {
  if(state.modelSettingsBusy)return;
  if(!modelSettingsPayload().model){modelSettingsStatus('请先获取并选择模型，或勾选“手动填写模型名称”。',true);modelField('name').focus();return;}
  if($('#model-settings-form').reportValidity()===false)return;
  const purpose=state.modelPurpose,generation=++state.modelGeneration;
  modelSettingsBusy(true); modelSettingsStatus(test?'正在测试连接，不会保存配置…':'正在保存到本机…');
  try{
    const result=await request(modelPurposePath(test?'/check':''),jsonOptions(modelSettingsPayload(),test?'POST':'PUT'));if(generation!==state.modelGeneration)return;
    if(test&&purpose==='chat')modelSettingsStatus(result.tools_supported?'连接成功，已确认模型返回工具调用。请保存后使用。':'连接成功，但未确认工具调用；请检查模型是否支持 tools。',!result.tools_supported);
    else if(test&&purpose==='embedding'){modelField('dimensions').value=result.dimensions;modelSettingsStatus(`测试成功，实际维度为 ${result.dimensions}。确认启用后保存。`);}
    else if(test)modelSettingsStatus('测试成功，已确认图片输入及转录格式。请保存后使用。');
    else{applyModelSettings(result);fillModelSettings(purpose);modelSettingsStatus(purpose==='embedding'?'此用途已保存。已有文档不会自动重建索引，可在文档详情手动发起。':'此用途已保存，下一次调用使用新配置。');}
  }catch(error){if(generation===state.modelGeneration)modelSettingsStatus(error.message,true);}
  finally{if(generation===state.modelGeneration)modelSettingsBusy(false);}
}
async function clearModelSettings() {
  if(state.modelSettingsBusy)return;
  if(!state.clearModelArmed){state.clearModelArmed=true; $('#model-settings-clear').textContent='再次点击确认清除'; modelSettingsStatus('仅清除“'+MODEL_TITLES[state.modelPurpose]+'”的配置，其他用途保留。'); return;}
  const generation=++state.modelGeneration;
  modelSettingsBusy(true);
  try{const value=await request(modelPurposePath(),{method:'DELETE'});if(generation!==state.modelGeneration)return;applyModelSettings(value);fillModelSettings();modelSettingsStatus('此用途配置已清除，请重新配置后使用。');}
  catch(error){modelSettingsStatus(error.message,true);}
  finally{if(generation===state.modelGeneration)modelSettingsBusy(false);}
}
function formatTime(value) {if(!value)return '未知'; const parsed=new Date(value); return Number.isNaN(parsed.valueOf())?value:parsed.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});}
function badge(status) {return node('span',labels[status]||status,'status '+status);}
function metadata(target,pairs) {const list=node('dl',null,'metadata'); for(const [label,value] of pairs)list.append(node('dt',label),node('dd',value??'未知')); target.append(list);}
function table(target,columns,rows) {
  const scroll=node('div',null,'table-scroll'), sheet=node('table'), head=node('thead'), header=node('tr'), body=node('tbody');
  columns.forEach(value=>header.append(node('th',value))); head.append(header);
  for(const values of rows){const row=node('tr'); values.forEach(value=>row.append(node('td',value===null?'NULL':value))); body.append(row);}
  sheet.append(head,body); scroll.append(sheet); target.append(scroll); return scroll;
}
function analysisTable(target,columns,rows,label='分析结果') {
  target.append(node('p','宽表可左右滑动查看；键盘聚焦表格后可用左右方向键。','analysis-note'));
  const scroll=table(target,columns,rows);
  scroll.setAttribute('tabindex','0'); scroll.setAttribute('role','region'); scroll.setAttribute('aria-label',label+'，可左右滚动');
  return scroll;
}
function active(kind,id,sequence) {return state.sequence===sequence&&state.selected?.kind===kind&&state.selected.id===id;}
function closeInspector(restore=true) {
  if(state.analysisEntry?.detailsButton)state.analysisEntry.detailsButton.setAttribute('aria-expanded','false');
  state.analysisEntry=null; $('#inspector-back').hidden=true;
  $('.app-shell').classList.remove('has-analysis');
  state.sequence++; state.selected=null; state.datasetData=null; $('#inspector').hidden=true; $('.app-shell').classList.remove('has-inspector');
  if(restore){const target=state.opener?.isConnected&&state.opener.getClientRects().length?state.opener:$('#main'); target.focus({preventScroll:true});}
}
function beginInspector(kind,id,title) {
  if(!$('#inspector').contains(document.activeElement))state.opener=document.activeElement;
  const sequence=++state.sequence; state.selected={kind,id}; state.datasetData=null;
  $('#inspector').setAttribute('data-kind',kind); $('#inspector-back').hidden=kind==='analysis'||!state.analysisEntry;
  $('.app-shell').classList.toggle('has-analysis',kind==='analysis');
  if(kind!=='analysis')$('#history-note').hidden=true;
  $('#inspector').hidden=false; $('.app-shell').classList.add('has-inspector'); $('#inspector-title').textContent=title;
  clear($('#inspector-body')).append(node('p','正在读取…','muted')); return sequence;
}
function focusInspector(scroll=true) {
  $('#inspector-title').focus({preventScroll:true});
  if(scroll&&innerWidth<=900)$('#inspector').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
}
function inspectorError(error,kind,id,sequence,retry) {
  if(!active(kind,id,sequence))return;
  const target=clear($('#inspector-body')); target.append(node('p',error.status===404?'这份资料已删除或不可用。请返回列表查看最新状态。':error.message,'notice error panel-error'));
  if(error.status===404){state.selected=null; refresh(kind==='dataset'?'datasets':'documents',true).catch(showError);}
  else target.append(button('重新读取',retry,'secondary'));
  target.append(button('返回列表',()=>closeInspector())); focusInspector(false);
}
function mobileNavigation() {return typeof matchMedia==='function'&&matchMedia('(max-width: 900px)').matches;}
function renderNavigation(restoreFocus=false) {
  const mobile=mobileNavigation(), opened=mobile?state.navOpen:!state.navCollapsed;
  const nav=$('#workspace-navigation'), shell=$('.app-shell');
  shell.classList.toggle('nav-open',mobile&&opened); shell.classList.toggle('nav-collapsed',!mobile&&!opened);
  nav.inert=!opened; $('.work-area').inert=mobile&&opened; $('#nav-backdrop').hidden=!mobile||!opened;
  $('#sidebar-toggle').setAttribute('aria-expanded',String(opened));
  $('#sidebar-toggle').setAttribute('aria-label',opened?'收起会话侧栏':'打开会话侧栏');
  if(mobile&&opened){nav.setAttribute('role','dialog'); nav.setAttribute('aria-modal','true');}
  else{nav.removeAttribute('role'); nav.removeAttribute('aria-modal');}
  if(restoreFocus)$('#sidebar-toggle').focus();
}
function toggleNavigation() {
  if(mobileNavigation())state.navOpen=!state.navOpen; else state.navCollapsed=!state.navCollapsed;
  closeSourceMenu(); renderNavigation();
  if(mobileNavigation()&&state.navOpen)$('#sidebar-close').focus();
  else if(mobileNavigation()||state.navCollapsed)$('#sidebar-toggle').focus();
}
function closeMobileNavigation() {
  if(!state.navOpen)return;
  state.navOpen=false; renderNavigation(true);
}
function closeSourceMenu(restoreFocus=false) {
  $('#source-menu').hidden=true; $('#source-menu-toggle').setAttribute('aria-expanded','false');
  if(restoreFocus)$('#source-menu-toggle').focus();
}
function toggleSourceMenu() {
  const menu=$('#source-menu'); menu.hidden=!menu.hidden;
  $('#source-menu-toggle').setAttribute('aria-expanded',String(!menu.hidden));
  if(!menu.hidden)menu.querySelector('button:not(:disabled)')?.focus();
}
async function sourceAction(kind) {
  closeSourceMenu();
  const feature=kind==='vision'?'vision_available':kind+'_available';
  if(!state.config?.[feature])return notice('数据服务尚未就绪，请稍候或检查配置。',true);
  switchView(kind==='vision'?'documents':kind);
  if(kind==='vision')await openVision(); else openUpload();
}
function setConversationTitle(title='新建分析') {
  state.title=Array.from(title.trim()||'新建分析').slice(0,80).join('');
  if(state.view==='chat'){
    $('#breadcrumb').textContent=state.title; document.title=state.title+' · 数据分析助手';
  }
}
function switchView(view) {
  closeSourceMenu(); closeMobileNavigation();
  state.view=view; $('#main').setAttribute('data-active-view',view); closeInspector(false); $('#upload-section').hidden=true;
  $('#vision-section').hidden=true; $('#vision-open').hidden=!['datasets','documents'].includes(view);
  document.querySelectorAll('.view').forEach(item=>item.hidden=item.id!==view+'-view');
  document.querySelectorAll('.nav-item[data-view]').forEach(item=>{item.classList.toggle('active',item.dataset.view===view); if(item.dataset.view===view)item.setAttribute('aria-current','page'); else item.removeAttribute('aria-current');});
  const [title,description]=headings[view], displayTitle=view==='chat'?state.title:title;
  $('#page-title').textContent=title; $('#breadcrumb').textContent=displayTitle; $('#page-description').textContent=description; document.title=displayTitle+' · 数据分析助手';
  $('#upload-open').hidden=!['datasets','documents'].includes(view); $('#upload-open').textContent=view==='documents'?'上传文档':'上传表格'; notice('');
  if(['datasets','documents'].includes(view))refresh(view).catch(showError);
  syncGuideTarget();
}
function openUpload() {
  if(!state.config)return notice('工作台配置尚未加载，请稍候。',true);
  if(state.uploading)return notice('文件正在处理中，可先查看其他数据源。');
  const isDoc=state.view==='documents'; $('#vision-section').hidden=true; $('#upload-section').hidden=false; $('#upload-title').textContent=isDoc?'上传业务文档':'上传表格';
  $('#csv-options').hidden=isDoc; $('#doc-options').hidden=!isDoc; $('#source-file').accept=isDoc?'.txt,.md,.pdf,.docx':'.csv,.xlsx';
  $('#file-label').textContent='选择文件'+(isDoc?'，保留资料来源':'，先预览再确认');
  $('#file-hint').textContent=isDoc?`TXT、Markdown、DOCX${state.config.pdf_available?'、文本 PDF':''} · 不超过 ${state.config.upload_max_bytes/1048576} MiB`:`CSV、XLSX · 不超过 ${state.config.import_max_bytes/1048576} MiB`;
  $('#upload-explanation').textContent=isDoc?'上传后可用关键词检索；不会自动调用聊天模型。':'预览不会建表。确认字段后，才开始导入。';
  $('#preview-submit').textContent=isDoc?'上传文档':'生成预览'; $('#source-file').value=''; $('#sheet-name').value=''; $('#sheet-options').replaceChildren(); $('#source-file').focus();
}
async function refresh(view=state.view,force=false) {
  if(!['datasets','documents'].includes(view))return;
  if(state.loads[view]){
    if(!force)return state.loads[view];
    // Mutation completion needs a new snapshot, never a GET begun before the write.
    try{await state.loads[view];}catch{/* A failed old read cannot suppress revalidation. */}
    return refresh(view,true);
  }
  const loading=(async()=>{const rows=await request('/'+view); state[view]=rows; view==='datasets'?renderDatasets():renderDocuments();})();
  state.loads[view]=loading; try{await loading;}finally{if(state.loads[view]===loading)delete state.loads[view];}
}
function renderDatasets() {
  const body=clear($('#dataset-rows')), query=$('#dataset-filter').value.trim().toLocaleLowerCase();
  const rows=state.datasets.filter(row=>(row.filename+row.display_name).toLocaleLowerCase().includes(query));
  $('#dataset-count').textContent=`${rows.length} 份数据源`; $('#dataset-empty').hidden=state.datasets.length!==0;
  for(const row of rows){const tr=node('tr'), name=node('td'), status=node('td'), action=node('td');
    name.append(node('span',row.filename.split('.').pop().toUpperCase(),'file-mark'),button(row.display_name,()=>selectDataset(row.id),'resource-name'),node('small',row.filename));
    status.append(badge(row.status)); action.append(button('查看',()=>selectDataset(row.id)));
    tr.append(name,node('td',row.row_count.toLocaleString('zh-CN')),status,node('td',formatTime(row.created_at)),action); body.append(tr);}
  if(!rows.length&&state.datasets.length){const cell=node('td','没有匹配的数据源，请调整筛选词。'), tr=node('tr'); cell.colSpan=5; tr.append(cell); body.append(tr);}
}
function renderDocuments() {
  const body=clear($('#document-rows')); $('#document-count').textContent=`${state.documents.length} 份文档 · 最近 100 份`; $('#document-empty').hidden=state.documents.length!==0;
  for(const row of state.documents){const tr=node('tr'),title=node('td'),status=node('td'),action=node('td');
    title.append(button(row.filename,()=>selectDocument(row.id),'resource-name'),node('small',`${row.chunk_count} 个片段`));
    status.append(node('span',row.index_status==='ready'?'关键词 + 向量':'关键词可用','status')); action.append(button('查看来源',()=>selectDocument(row.id)));
    tr.append(title,node('td',row.document_date||'日期未知'),node('td',types[row.document_type]||row.document_type),status,action); body.append(tr);}
}
async function selectDataset(id,scroll=true) {
  const sequence=beginInspector('dataset',id,'表格详情');
  try{const data=await request('/datasets/'+encodeURIComponent(id)); if(!active('dataset',id,sequence))return; renderDataset(data); focusInspector(scroll);}
  catch(error){inspectorError(error,'dataset',id,sequence,()=>selectDataset(id,false));}
}
function importNotice(data) {
  if(state.noticeDataset!==data.id)return;
  if(data.status==='ready')notice(`导入完成，可以分析“${data.display_name}”。`,false,data.id);
  if(data.status==='failed')notice('导入未完成。请查看错误原因，再重试或重新上传。',true,data.id);
}
function updateDatasetProgress(data) {
  const target=$('#inspector-body'),status=target.querySelector('[data-role="status"]'); if(!status)return;
  status.textContent=labels[data.status]||data.status; status.className='status '+data.status;
  const progress=target.querySelector('[data-role="progress"]'); progress.hidden=!['queued','importing'].includes(data.status);
  progress.querySelector('progress').max=data.row_count; progress.querySelector('progress').value=data.imported_rows;
  progress.querySelector('p').textContent=`已处理 ${data.imported_rows} / ${data.row_count} 行，完成后统一提交。`;
  const error=target.querySelector('[data-role="error"]'); error.hidden=!data.error; error.textContent=data.error?`上次未完成：${data.error}`:'';
  importNotice(data);
}
function renderDatasetActions(actions,data,nameInput,overrides) {
  const hadFocus=actions.contains(document.activeElement),focusedText=document.activeElement?.textContent; clear(actions);
  if(data.status==='previewed')actions.append(button('确认字段并导入',async()=>{
    if(!nameInput.value.trim())throw new Error('请填写数据集名称。'); const sequence=state.sequence;
    await request(`/datasets/${data.id}/import`,jsonOptions({display_name:nameInput.value,type_overrides:overrides}));
    if(active('dataset',data.id,sequence)){notice('已确认，后台开始导入。完成后即可分析。',false,data.id); await selectDataset(data.id,false);} await refresh('datasets',true);
  },'primary'));
  if(data.status==='failed')actions.append(button('重试导入',async()=>{const sequence=state.sequence; await request(`/datasets/${data.id}/retry`,{method:'POST'}); if(active('dataset',data.id,sequence)){notice('已重新排队，等待导入。',false,data.id); await selectDataset(data.id,false);} await refresh('datasets',true);},'secondary'));
  if(data.status==='ready')actions.append(button('分析这份数据',()=>{switchView('chat'); $('#question').value=`请先查看数据集“${data.display_name}”的字段和样例，再概述可以分析哪些内容。`; $('#question').focus();},'primary'));
  if(['queued','importing','deleting'].includes(data.status))actions.append(button('刷新状态',()=>pollDataset(true)));
  actions.append(button('删除数据源',()=>confirmDelete($('#inspector-body'),'dataset',data.id,data.filename),'quiet danger'));
  if(hadFocus){const replacement=[...actions.querySelectorAll('button')].find(item=>item.textContent===focusedText); (replacement||$('#inspector-title')).focus({preventScroll:true});}
}
function renderDataset(data) {
  state.datasetData=data; const target=clear($('#inspector-body')); target.append(node('h3',data.display_name,'source-title'));
  const status=badge(data.status); status.dataset.role='status'; target.append(status);
  metadata(target,[['源文件',data.filename],['上传时间',formatTime(data.created_at)],['工作表',data.options.sheet_name||'CSV'],['数据行数',data.row_count.toLocaleString('zh-CN')],['分析表',data.table]]);
  if(data.options.vision_source){const source=data.options.vision_source; target.append(node('p',`图片来源：${source.filename}，第 ${source.page_numbers.join('、')} 页；转录已由用户确认。`,'field-hint'),button('查看图片来源',()=>openVision(source.draft_id,source.page_numbers[0]),'secondary'));}
  if(data.options.sheet_names?.length)$('#sheet-options').replaceChildren(...data.options.sheet_names.map(name=>{const option=node('option'); option.value=name; return option;}));
  const error=node('p',null,'notice error'); error.dataset.role='error'; target.append(error);
  const progress=node('div'); progress.dataset.role='progress'; const bar=node('progress'); bar.setAttribute('aria-label','已处理的数据行'); progress.append(bar,node('p',null,'muted')); target.append(progress);
  const editable=data.status==='previewed',nameLabel=node('label','数据集名称'),nameInput=node('input'); nameInput.value=data.display_name; nameInput.maxLength=128; nameInput.disabled=!editable; nameLabel.append(nameInput); target.append(nameLabel,node('h3',editable?'确认字段类型':'字段映射'));
  const overrides={}; for(const column of data.columns){const row=node('div',null,'schema-column'),label=node('span',column.label),select=node('select'); label.append(node('small',column.name+(column.nullable?' · 可为空':''))); select.setAttribute('aria-label',column.label+'的字段类型');
    for(const [value,title] of Object.entries(kinds)){const option=node('option',title); option.value=value; select.append(option);} select.value=column.type; select.disabled=!editable;
    select.addEventListener('change',()=>overrides[column.name]=select.value); row.append(label,select); target.append(row);}
  for(const warning of data.warnings)target.append(node('p',warning,'field-hint'));
  target.append(node('h3','源数据预览'),node('p','前 10 行，长文本仅在预览中缩短。首列是源记录序号。','field-hint'));
  table(target,['源记录',...data.columns.map(item=>item.label)],data.preview_rows);
  const actions=node('div',null,'actions'); actions.dataset.role='actions'; target.append(actions); renderDatasetActions(actions,data,nameInput,overrides); updateDatasetProgress(data);
}
async function pollDataset(force=false) {
  if(state.polling||!state.config||state.selected?.kind!=='dataset')return;
  const current=state.datasetData;if(!current||(!force&&!['queued','importing','deleting'].includes(current.status)))return;
  const sequence=state.sequence,id=current.id; state.polling=true;
  try{const data=await request('/datasets/'+id); if(!active('dataset',id,sequence))return;
    const previous=state.datasetData.status; state.datasetData=data; updateDatasetProgress(data);
    const position=state.datasets.findIndex(item=>item.id===id); if(position>=0)state.datasets[position]=data;
    // Do not replace focused list controls on every passive tick.
    if(data.status!==previous){renderDatasets(); renderDatasetActions($('#inspector-body').querySelector('[data-role="actions"]'),data);}
  }catch(error){if(active('dataset',id,sequence)){if(error.status===404)inspectorError(error,'dataset',id,sequence,()=>selectDataset(id,false)); else notice('状态暂未更新，请使用“刷新状态”重试。',true,id);}}
  finally{state.polling=false;}
}
async function selectDocument(id,chunkId=null,offset=0) {
  const sequence=beginInspector('document',id,'文档来源'),query=new URLSearchParams({offset,limit:5}); if(chunkId)query.set('chunk_id',chunkId);
  try{const data=await request(`/documents/${encodeURIComponent(id)}?${query}`); if(!active('document',id,sequence))return;
    const target=clear($('#inspector-body')); target.append(node('h3',data.filename,'source-title'));
    metadata(target,[['业务日期',data.document_date||'未知（不参与日期筛选）'],['上传时间',formatTime(data.created_at)],['文档类型',types[data.document_type]],['片段数量',data.chunk_count],['向量状态',data.requires_reindex?'需要按当前模型重建':data.index_status==='ready'?'已完成':'尚未完成']]);
    if(data.requires_reindex){target.append(node('p','此文档索引与当前向量模型不匹配。关键词检索仍可用；重建将发送文档内容并可能计费。','field-hint'),button('使用当前模型重建索引',async()=>{await request(`/documents/${data.id}/index?force=true`,jsonOptions({}));if(active('document',id,sequence))notice('已按本次请求排队重建向量索引。');},'secondary'));}
    if(data.metadata.vision_source){const source=data.metadata.vision_source; target.append(node('p',`识别转录来源：第 ${source.page_numbers.join('、')} 页 / 共 ${source.total_pages} 页。`,'field-hint'),button('查看图片来源',()=>openVision(source.draft_id,source.page_numbers[0]),'secondary'));}
    if(data.metadata.warning)target.append(node('p',data.metadata.warning,'field-hint'));
    const dateLabel=node('label','业务文档日期'),dateInput=node('input'),typeLabel=node('label','文档类型'),typeInput=node('select'); dateInput.type='date'; dateInput.value=data.document_date||''; dateLabel.append(dateInput);
    for(const [value,title] of Object.entries(types)){const option=node('option',title); option.value=value; typeInput.append(option);} typeInput.value=data.document_type; typeLabel.append(typeInput);
    target.append(dateLabel,typeLabel,button('保存资料信息',async()=>{
      const operation=state.sequence; await request(`/documents/${data.id}`,jsonOptions({document_date:dateInput.value||null,document_type:typeInput.value},'PATCH'));
      if(active('document',id,operation)){notice('资料信息已更新。不会重新发送正文或生成向量。'); await selectDocument(id,chunkId,offset);} await refresh('documents',true);
    },'secondary'),node('h3','原文片段'));
    for(const chunk of data.chunks){const section=node('section',null,'source-chunk'),location=[chunk.page_number?`第 ${chunk.page_number} 页`:null,chunk.section,`片段 ${chunk.chunk_index+1}`].filter(Boolean).join(' · '); section.append(node('small',location),node('p',chunk.content)); target.append(section);}
    const actions=node('div',null,'actions'); if(offset>0)actions.append(button('上一组',()=>selectDocument(id,null,Math.max(0,offset-5)))); if(data.has_more)actions.append(button('下一组片段',()=>selectDocument(id,null,offset+5)));
    actions.append(button('删除文档',()=>confirmDelete(target,'document',data.id,data.filename),'quiet danger')); target.append(actions); focusInspector();
  }catch(error){inspectorError(error,'document',id,sequence,()=>selectDocument(id,chunkId,offset));}
}
function confirmDelete(target,kind,id,filename) {
  if(target.querySelector('.confirm-delete'))return;
  const sequence=state.sequence,area=node('div',null,'confirm-delete'),opener=document.activeElement;
  area.append(node('p',`确认删除“${filename}”？${kind==='dataset'?'源文件、导入表和记录会一并清理。':'源文件、片段和索引会一并清理。'}`));
  area.append(button('确认删除',async()=>{const result=await request(`/${kind==='dataset'?'datasets':'documents'}/${id}`,{method:'DELETE'});
    if(active(kind,id,sequence)){closeInspector(); notice(result.status==='cleanup_pending'?'已标记删除，后台会继续清理占用文件。':'数据源已删除。');} await refresh(kind==='dataset'?'datasets':'documents',true);
  },'secondary danger'),button('取消',()=>{area.remove(); if(opener.isConnected)opener.focus();})); target.append(area); area.querySelector('button').focus();
}
$('#upload-form').addEventListener('submit',async event=>{
  event.preventDefault(); if(state.uploading)return; const submit=$('#preview-submit'),file=$('#source-file').files[0]; if(!file)return;
  if(!state.config)return notice('工作台尚未就绪，请稍候。',true);
  notice('');
  const view=state.view,sequence=state.sequence,isDoc=view==='documents',maximum=isDoc?state.config.upload_max_bytes:state.config.import_max_bytes;
  if(file.size>maximum)return notice(`文件超过 ${maximum/1048576} MiB，请拆分后重试。`,true);
  const data=new FormData(); data.set('file',file);
  if(isDoc){if($('#document-date').value)data.set('document_date',$('#document-date').value); data.set('document_type',$('#document-type').value);}
  else{data.set('encoding',$('#csv-encoding').value); data.set('delimiter',$('#csv-delimiter').value==='tab'?'\t':$('#csv-delimiter').value); if($('#sheet-name').value)data.set('sheet_name',$('#sheet-name').value);}
  state.uploading=true; submit.disabled=true; submit.textContent=isDoc?'正在解析文档…':'正在生成预览…';
  try{const result=await request(isDoc?'/documents/upload':'/datasets/preview',{method:'POST',body:data}); await refresh(view,true);
    if(state.view===view&&state.sequence===sequence){notice(result.duplicate?'相同内容已存在，已打开现有数据源。':isDoc?'文档已上传，可以检索并核对来源。':'预览已生成。请在详情中核对字段，再确认导入。'); if(isDoc)await selectDocument(result.document_id); else await selectDataset(result.id);}
  }catch(error){if(state.view===view)showError(error);}finally{state.uploading=false; submit.disabled=false; submit.textContent=state.view==='documents'?'上传文档':'生成预览';}
});
$('#search-form').addEventListener('submit',async event=>{
  event.preventDefault(); const submit=event.submitter; submit.disabled=true; const target=clear($('#search-results')); target.append(node('p','正在检索资料…','muted'));
  try{const body={query:$('#search-query').value.trim(),mode:$('#search-mode').value}; if($('#date-from').value)body.date_from=$('#date-from').value; if($('#date-to').value)body.date_to=$('#date-to').value; if($('#search-type').value)body.document_type=$('#search-type').value;
    if(body.date_from&&body.date_to&&body.date_from>body.date_to)throw new Error('开始日期不能晚于结束日期。');
    const result=await request('/documents/search',jsonOptions(body)); clear(target); for(const warning of result.warnings)target.append(node('p',warning,'notice'));
    if(!result.hits.length)target.append(node('h2','没有找到支持这个问题的资料'),node('p','可以调整问法或日期范围；资料不足时请不要据此推断结论。','muted'));
    for(const hit of result.hits){const article=node('article',null,'search-hit'),location=[hit.document_date||'日期未知',types[hit.document_type],hit.page_number?`第 ${hit.page_number} 页`:null,hit.section].filter(Boolean).join(' · '); article.append(node('h2',hit.filename),node('p',location,'source-meta'),node('p',hit.content),button('查看原文来源',()=>selectDocument(hit.document_id,hit.chunk_id),'secondary')); target.append(article);}
  }catch(error){clear(target).append(node('p',error.message,'notice error'));}finally{submit.disabled=false;}
});
function conversationState(hasMessages) {
  $('#analysis-welcome').hidden=hasMessages; $('#prompt-suggestions').hidden=hasMessages;
  $('#chat-view').classList.toggle('has-conversation',hasMessages);
  if(hasMessages)$('#guide-invite').hidden=true;
  $('#question').placeholder=hasMessages?'继续追问，或描述新的分析问题…':'描述你的问题，或从下面的示例开始…';
  if(!hasMessages){state.followOutput=true; $('#jump-latest').hidden=true; $('#thread-scroll').scrollTop=0;}
}
function revealLatest(force=false) {
  if(!force&&!state.followOutput)return;
  const scroll=$('#thread-scroll'); scroll.scrollTop=scroll.scrollHeight;
  state.followOutput=true; $('#jump-latest').hidden=true;
}
function trackThreadScroll() {
  const scroll=$('#thread-scroll');
  state.followOutput=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<80;
  $('#jump-latest').hidden=state.followOutput||!$('#conversation').children.length;
}
function recordActivity(entry,event) {
  const phaseLabels={loading:'读取分析条件',planning:'制定分析计划',executing:'查询与分析',reviewing:'核对答案与证据',validating:'校验数字与口径',saving:'保存分析结果'};
  const text=event.type==='phase'?phaseLabels[event.phase]:event.type==='tool'?`${toolLabels[event.tool]||event.tool||'工具调用'} · ${event.ok===false?'未完成':'完成'}`:
    ({start:'开始处理你的问题',plan:'整理分析步骤',llm:'正在分析数据',reflection:'核对答案与证据',revision:'根据审查结果修正回答'})[event.type];
  if(!text)return;
  if(!entry.activity){
    const panel=node('details',null,'live-activity'), summary=node('summary','正在分析…'), list=node('ol',null,'activity-list');
    panel.setAttribute('data-state','running'); panel.open=true; panel.append(summary,list);
    entry.activity={panel,summary,list,last:null}; ensureAnalysisButton(entry);
    if(state.analysisEntry===entry&&state.selected?.kind==='analysis')renderAnalysisPane(entry);
  }
  const activity=entry.activity;
  activity.summary.textContent=event.type==='tool'?'正在核对工具结果…':text;
  answerBody(entry).textContent=activity.summary.textContent;
  if(text!==activity.last||event.type==='tool'){activity.list.append(node('li',text)); activity.last=text;}
  revealLatest();
}
function finishActivity(entry,result=null) {
  answerBody(entry).hidden=false;
  if(!entry.activity)return;
  const {panel,summary}=entry.activity;
  panel.setAttribute('data-state',result?'complete':'failed'); panel.open=false;
  summary.textContent=result?(result.needs_clarification?'等待补充分析条件':`分析结束 · ${(result.steps||[]).length} 条工具记录`):'本次执行已结束，可展开查看已完成步骤';
}
function fillQuestion(question) {
  if(state.chat||state.restoring){notice('请先等待当前分析或历史恢复结束。'); return;}
  $('#question').value=question; resizeQuestion(); $('#question').focus();
}
function resizeQuestion(reset=false) {
  const area=$('#question'); if(!area.style)return;
  area.style.height='';
  if(!reset&&area.value){area.style.height='auto'; area.style.height=Math.min(220,area.scrollHeight)+'px';}
}
function message(role,text) {
  const entry=node('article',null,'message '+role);
  entry.append(node('h3',role==='user'?'你':'分析助手'),node(role==='assistant'?'div':'p',text,role==='assistant'?'answer-body':null));
  if(role==='assistant')ensureAnalysisButton(entry);
  $('#conversation').append(entry); conversationState(true); return entry;
}
function answerBody(entry) {return entry.querySelector('.answer-body')||entry.querySelector('p');}
function ensureAnalysisButton(entry) {
  if(entry.detailsButton)return;
  const trigger=button('分析详情',()=>openAnalysisDetails(entry),'quiet analysis-details-button');
  trigger.setAttribute('aria-controls','inspector'); trigger.setAttribute('aria-expanded','false');
  (entry.querySelector('h3')||entry).append(trigger); entry.detailsButton=trigger;
}
function renderAnalysisPane(entry) {
  const target=clear($('#inspector-body'));
  if(entry.report?.question)target.append(node('p',entry.report.question,'report-question'));
  if(entry.activity)target.append(entry.activity.panel);
  if(entry.reportDetails)target.append(entry.reportDetails);
  else if(!entry.activity)target.append(node('p','正在等待执行记录…','muted'));
  $('#history-note').hidden=!entry.snapshot;
}
function openAnalysisDetails(entry) {
  if(state.analysisEntry?.detailsButton)state.analysisEntry.detailsButton.setAttribute('aria-expanded','false');
  beginInspector('analysis',entry.report?.trace_id||'current','分析详情');
  state.analysisEntry=entry; entry.detailsButton?.setAttribute('aria-expanded','true');
  renderAnalysisPane(entry); focusInspector(false);
}
function measure(value,suffix='',digits=null) {
  if(value===null||value===undefined)return '未定义';
  const text=String(value); if(!/^-?\d+(\.\d+)?$/.test(text))return text;
  let [whole,fraction='']=text.split('.');
  if(digits!==null){const negative=whole.startsWith('-');
    let scaled=BigInt(whole.replace('-','')+(fraction+'0'.repeat(digits)).slice(0,digits));
    if(Number(fraction[digits]||'0')>=5)scaled+=1n;
    const rounded=scaled.toString().padStart(digits+1,'0');
    whole=(negative&&scaled!==0n?'-':'')+(digits?rounded.slice(0,-digits):rounded);
    fraction=digits?rounded.slice(-digits):'';
  }
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction?'.'+fraction:'')+suffix;
}
function renderAnalysis(entry,analysis) {
  if(!analysis)return;
  if(analysis.scope&&!(analysis.comparisons||[]).length){const scope=analysis.scope; entry.append(node('h4','分析口径'));
    metadata(entry,[['指标 / 单位',[scope.metric,scope.unit].filter(Boolean).join(' / ')||'待确认'],['数据来源',scope.source||'待确认'],['拆解维度',scope.dimension||'待确认'],
      ['基期',scope.base_start&&scope.base_end?`${scope.base_start} 至 ${scope.base_end}（不含结束日）`:'待确认'],
      ['当期',scope.current_start&&scope.current_end?`${scope.current_start} 至 ${scope.current_end}（不含结束日）`:'待确认']]);
    for(const condition of scope.filters||[])entry.append(node('p','筛选：'+condition,'analysis-note'));
    for(const assumption of scope.assumptions||[])entry.append(node('p','采用假设：'+assumption,'analysis-note'));
  }
  for(const comparison of analysis.comparisons||[]){
    const scope=comparison.scope,isRate=scope.metric_kind==='rate',summary=comparison.summary,details=node('details',null,'analysis-breakdown'); details.open=!comparison.reconciliation.matched;
    details.append(node('summary',`${scope.metric} · 按${scope.dimension}拆解${comparison.reconciliation.matched?'':' · 口径未匹配'}`));
    details.append(node('p',`${scope.source}；基期 ${scope.base_start} 至 ${scope.base_end}，当期 ${scope.current_start} 至 ${scope.current_end}（均不含结束日）`,'analysis-note'));
    for(const condition of scope.filters||[])details.append(node('p','筛选：'+condition,'analysis-note'));
    for(const assumption of scope.assumptions||[])details.append(node('p','采用假设：'+assumption,'analysis-note'));
    const change=isRate?summary.delta_pp:summary.delta;
    analysisTable(details,['基期整体','当期整体',isRate?'变化（百分点）':`变化（${scope.unit}）`],[[measure(summary.base,isRate?'%':' '+scope.unit,isRate?2:null),measure(summary.current,isRate?'%':' '+scope.unit,isRate?2:null),measure(change,'',isRate?4:null)]],scope.metric+'整体对比');
    if(!comparison.reconciliation.matched)details.append(node('p','分项与独立总量不一致，贡献未计算。请检查优惠分摊、重复关联和筛选条件。','notice error'));
    if(isRate)analysisTable(details,[scope.dimension,'基期比例','当期比例','组内变化（百分点）','结构变化（百分点）','合计贡献（百分点）'],comparison.groups.map(group=>[
      group.segment===null?'未分组（NULL）':group.segment,measure(group.base_rate,'%',2),measure(group.current_rate,'%',2),measure(group.within_pp,'',4),measure(group.mix_pp,'',4),measure(group.contribution_pp,'',4)]),scope.metric+'比例拆解');
    else analysisTable(details,[scope.dimension,`基期（${scope.unit}）`,`当期（${scope.unit}）`,`增减（${scope.unit}）`,'净变化贡献'],comparison.groups.map(group=>[
      group.segment===null?'未分组（NULL）':group.segment,measure(group.base_value),measure(group.current_value),measure(group.delta),measure(group.contribution_pct,'%',2)]),scope.metric+'分组拆解');
    if(comparison.group_count>8)details.append(node('p',`共 ${comparison.group_count} 个分组，展示变化最大的 8 组，其余合并展示；核对使用了全部分组。`,'analysis-note'));
    entry.append(details);
  }
  if(analysis.comparison_count>(analysis.comparisons||[]).length)entry.append(node('p','此处展示最近 4 次拆解，完整查询见分析过程。','analysis-note'));
  for(const warning of analysis.warnings||[])entry.append(node('p',warning,'analysis-note'));
}
function renderAnswer(entry,result) {
  finishActivity(entry,result);
  renderMarkdown(answerBody(entry),result.answer||result.error||'本次没有得到完整回答。');
  entry.report=result; ensureAnalysisButton(entry);
  entry.reportDetails=node('div',null,'analysis-report'); renderReportDetails(entry.reportDetails,result);
  const needsAttention=result.reflection_status==='failed'||Boolean(result.error)||Boolean(result.citation_errors?.length);
  entry.detailsButton.className=needsAttention?'quiet analysis-details-button needs-attention':'quiet analysis-details-button';
  entry.detailsButton.setAttribute('aria-label',needsAttention?'查看分析详情，有待核对的信息':'查看分析详情、依据与导出');
  if(state.analysisEntry===entry&&state.selected?.kind==='analysis')renderAnalysisPane(entry);
}
function renderReportDetails(entry,result) {
  if(result.error||result.citation_errors?.length)entry.append(node('p',[result.error,...(result.citation_errors||[])].filter(Boolean).join('；'),'notice error'));
  entry.append(node('small',result.needs_clarification?'等待补充分析条件':`审查状态：${({passed:'通过',failed:'未通过',unavailable:'未完成',skipped:'未执行'})[result.reflection_status]||'未知'}`,'muted'));
  if(result.business_profile?.id)entry.append(node('p',`业务口径：${result.business_profile.name||result.business_profile.id} · v${result.business_profile.revision}`,'analysis-note'));
  if(result.validation?.status){
    const validation=node('details',null,'numeric-validation'); validation.open=result.validation.status==='failed';
    validation.append(node('summary',`程序校验：${({passed:'已识别断言通过',partial:'部分可核对',failed:'发现不一致',not_applicable:'不适用'})[result.validation.status]||'尚未核验'}`));
    if(result.validation.status==='not_applicable')validation.append(node('p','本轮没有可核对的带单位数字，或仍需补充条件；未进行数值认证。','field-hint'));
    const checks=node('ul');for(const check of result.validation.checks||[]){const item=node('li');item.append(node('strong',`${({supported:'可核对',unverified:'未确认',failed:'不一致'})[check.status]||'待核对'} · ${check.claim}`),node('p',check.message,'field-hint'));if(check.source_paths?.length)item.append(node('small','记录位置：'+check.source_paths.join('、'),'analysis-note'));checks.append(item);}validation.append(checks);
    for(const limit of result.validation.limitations||[])validation.append(node('p',limit,'field-hint'));entry.append(validation);
  }
  const stepsPanel=renderEvidenceIndex(entry,result.steps);
  renderCharts(entry,result);
  renderAnalysis(entry,result.analysis);
  renderResultTables(entry,result);
  renderExports(entry,result);
  if(result.archive_error)entry.append(node('p',result.archive_error,'notice error'));
  if(result.created_at)entry.append(node('p','保存于 '+formatTime(result.created_at)+' · 历史快照','analysis-note'));
  if(result.reflection?.checks?.length){const review=node('details',null,'claim-review'); review.append(node('summary','逐项证据审查'));
    table(review,['待核对结论','审查结果','证据编号','依据'],result.reflection.checks.map(check=>[check.claim,({supported:'有证据支持',conflicting:'存在冲突',insufficient:'证据不足'})[check.status]||'未核验',check.evidence_ids.join('、')||'无',check.reason])); entry.append(review);}
  if(result.reflection?.issues?.length){const list=node('ul',null,'review-issues'); for(const issue of result.reflection.issues)list.append(node('li',issue)); entry.append(node('h4','需要核对的问题'),list);}
  for(const citation of result.citations||[])entry.append(button(`来源：${citation.filename}${citation.page_number?` · 第 ${citation.page_number} 页`:''}${citation.section?' · '+citation.section:''}`,()=>selectDocument(citation.document_id,citation.chunk_id),'citation-button'));
  if(stepsPanel)entry.append(stepsPanel);
}

function renderEvidenceIndex(entry,steps) {
  if(!steps?.length)return null;
  const panel=node('details',null,'steps-panel'), index=node('div',null,'evidence-index');
  panel.append(node('summary',`查看 ${steps.length} 步分析过程`)); index.setAttribute('role','group'); index.setAttribute('aria-label','本轮工具记录索引'); index.append(node('span','本轮记录'));
  const reveal=section=>{panel.open=true; section.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'}); section.focus({preventScroll:true});};
  for(const [number,step] of steps.entries()){
    const key=`E${number+1}`, section=node('section',null,'tool-evidence'); section.tabIndex=-1;
    section.append(node('h4',`${key} · ${step.tool} · ${step.ok?'成功':'未完成'}`),node('pre',`参数\n${JSON.stringify(step.arguments)}\n\n工具返回\n${step.output}`)); panel.append(section);
    if(number<8)index.append(button(`${key} · ${toolLabels[step.tool]||step.tool}`,()=>reveal(section),'evidence-link'));
  }
  if(steps.length>8)index.append(button(`全部 ${steps.length} 步`,()=>reveal(panel),'evidence-link'));
  panel.tabIndex=-1; entry.append(index); return panel;
}

function rememberSession(id) {
  state.session=id;
  for(const item of document.querySelectorAll('.history-open')){
    if(item.getAttribute('data-session-id')===id)item.setAttribute('aria-current','true'); else item.removeAttribute('aria-current');
  }
  try{if(id)localStorage.setItem('analyst:last-session',id); else localStorage.removeItem('analyst:last-session');}catch{/* 本地存储被限制时仍可通过历史列表恢复。 */}
}
function renderContext(value) {
  const scope=value?.scope; $('#analysis-context').hidden=!scope&&!value?.dataset_table;
  if($('#analysis-context').hidden)return;
  const target=clear($('#context-values'));
  const period=(start,end)=>[start,end].filter(Boolean).join(' 至 ')+(end?'（不含结束日）':'');
  metadata(target,[['来源',scope?.source||value?.dataset_table||'待确认'],['指标 / 单位',[scope?.metric,scope?.unit].filter(Boolean).join(' / ')||'待确认'],
    ['当期',period(scope?.current_start||scope?.start,scope?.current_end||scope?.end)||'待确认'],
    ['基期',period(scope?.base_start,scope?.base_end)||'未指定'],
    ['地区 / 渠道筛选',[scope?.region_code&&`地区：${scope.region_code}`,scope?.order_channel&&`渠道：${scope.order_channel}`].filter(Boolean).join('；')||'未追加地区或渠道限制'],
    ['口径规则',(scope?.filters||[]).map(filterDescription).join('；')||'沿用指标默认规则，见本轮报告详情']]);
}
function filterDescription(value){return typeof value==='string'?value:[value.column,value.operator,(value.values||[]).join('、')].filter(Boolean).join(' ');}
function archiveFeedback(result) {
  $('#archive-state').textContent=result?.archive_status==='saved'?'分析已保存，可刷新后恢复':result?.archive_status==='unconfirmed'?'未能确认保存，请刷新历史核对':
    state.config?.analysis_history_available?'分析完成后自动保存':'历史存储未启用，本次对话仅保留近期上下文';
}
async function refreshConversations() {
  const generation=++state.historyListGeneration, target=$('#history-list');
  clear(target).append(node('p','正在读取历史…','muted'));
  try{
    const rows=await request('/conversations'); if(generation!==state.historyListGeneration)return; clear(target);
    if(!rows.length){target.append(node('p','还没有分析会话。开始一次分析，保存后就能从这里继续。','muted')); return;}
    for(const row of rows){const item=node('div',null,'history-row');
      const open=button(row.title||'未命名会话',()=>restoreConversation(row.session_id),'history-open');
      open.setAttribute('data-session-id',row.session_id); open.setAttribute('title',(row.title||'未命名会话')+' · '+formatTime(row.updated_at));
      if(row.session_id===state.session)open.setAttribute('aria-current','true');
      item.append(open,node('span',`${formatTime(row.updated_at)} · ${row.turn_count} 次分析`,'muted')); target.append(item);}
  }catch(error){if(generation===state.historyListGeneration)clear(target).append(node('p',error.message,'notice error'),button('重新读取历史',refreshConversations,'secondary'));}
}
async function restoreConversation(id) {
  if(state.chat){notice('请先等待本轮分析结束，或停止分析后再切换历史。'); return;}
  if(state.view!=='chat')switchView('chat'); else closeMobileNavigation();
  const generation=++state.historyGeneration; state.restoring=true; $('#chat-submit').disabled=true;
  $('#archive-state').textContent='正在恢复会话…';
  try{
    const data=await request('/conversations/'+encodeURIComponent(id));
    if(generation!==state.historyGeneration)return;
    closeInspector(false); rememberSession(data.session_id); const target=clear($('#conversation'));
    setConversationTitle(data.title||data.reports[0]?.question||'历史分析');
    for(const result of data.reports){message('user',result.question||'历史问题'); const entry=message('assistant',''); entry.snapshot=true; renderAnswer(entry,result);}
    conversationState(data.reports.length>0);
    if(!data.reports.length)target.append(node('p','这个会话尚无可显示的分析。','muted'));
    renderContext(data.analysis_state);business.bind(data.reports[data.reports.length-1]?.business_profile||{id:'ecommerce_v1',revision:1}); $('#history-note').hidden=false;
    $('#archive-state').textContent=`已恢复最近 ${data.reports.length} 次分析`; revealLatest(true);
  }catch(error){if(generation===state.historyGeneration){if(error.status===404&&state.session===null)rememberSession(null); showError(error); archiveFeedback();}}
  finally{if(generation===state.historyGeneration){state.restoring=false; $('#chat-submit').disabled=false;}}
}
function renderResultTables(entry,result) {
  for(const value of result.tables||[]){const detail=node('details');
    detail.append(node('summary',`${value.title} · 查看 ${value.rows.length} 行结果数据${value.truncated?'（已截断）':''}`));
    detail.append(node('p','证据：'+value.evidence_ids.join('、')+'。导出仅包含本次实际返回的数据。','analysis-note'));
    analysisTable(detail,value.columns,value.rows.map(row=>row.map(value=>value===null?'未定义':String(value))),value.title+'结果表'); entry.append(detail);}
}
function renderExports(entry,result) {
  if(result.archive_status!=='saved'||!result.trace_id||!state.config)return;
  const actions=node('div',null,'report-actions'), base=state.config.api_prefix+'/analyses/'+encodeURIComponent(result.trace_id)+'/export?format=';
  const link=(label,format)=>{const item=node('a',label,'export-link'); item.href=base+format; item.setAttribute('download',''); return item;};
  actions.append(link('导出报告 · Markdown','md'));
  if(state.config.xlsx_available)actions.append(link('导出全部结果 · XLSX','xlsx'));
  if(result.tables?.length){const label=node('label','CSV 结果表'),select=node('select'); select.setAttribute('aria-label','CSV 结果表');
    for(const table of result.tables){const option=node('option',table.title); option.value=table.id; select.append(option);}
    select.value=result.tables[0].id; label.append(select); const csv=link('导出选中表 · CSV','csv');
    const update=()=>{csv.href=base+'csv&table_id='+encodeURIComponent(select.value);}; select.addEventListener('change',update); update();
    actions.append(label,csv);}
  entry.append(actions);
  renderExampleAction(entry,result);
}
// 用户确认 SQL 正确后存为示例，后续同类问题可参考写法；审查未通过或没有成功 SQL 时不提供。
function renderExampleAction(entry,result) {
  const hasSql=(result.steps||[]).some(step=>step.tool==='run_sql'&&step.ok);
  if(!state.config?.sql_examples_available||!hasSql||result.reflection_status==='failed')return;
  const status=node('small','','muted'); status.setAttribute('role','status');
  const save=button('SQL 正确，存为示例',async()=>{
    save.disabled=true;
    try{const saved=await request(`/analyses/${encodeURIComponent(result.trace_id)}/sql-example`,jsonOptions({}));
      status.textContent=saved.created?'已存为示例，同类问题会参考这条 SQL 写法。':'这份报告的 SQL 已经是示例。';}
    catch(error){save.disabled=false; throw error;}
  });
  const wrap=node('div',null,'report-actions'); wrap.append(save,status); entry.append(wrap);
}
function svgNode(tag,attributes={},text) {
  const element=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [name,value] of Object.entries(attributes))element.setAttribute(name,String(value));
  if(text!==undefined)element.textContent=String(text); return element;
}
function renderCharts(entry,result) {
  if(!result.charts?.length){if(result.tables?.length)entry.append(node('p','可用图表需要至少两个有效数值观测；精确结果与未定义值见数据表。','analysis-note')); return;}
  const figure=node('figure',null,'analysis-figure'),heading=node('div',null,'chart-heading'), title=node('h4','分析图表'), select=node('select');
  select.setAttribute('aria-label','选择分析图表');
  for(const chart of result.charts){const option=node('option',chart.title+' · '+({bar:'分组对比',contribution:'变化贡献',line:'时间趋势'}[chart.kind]||'图表')); option.value=chart.id; select.append(option);}
  select.value=result.charts[0].id; heading.append(title,select); figure.append(heading);
  const area=node('div',null,'chart-scroll'),caption=node('figcaption',null,'analysis-note');
  area.setAttribute('tabindex','0'); area.setAttribute('role','region'); area.setAttribute('aria-label','分析图表，窄屏可左右滚动查看完整图表与数值');
  figure.append(node('p','窄屏可左右滑动查看完整图表与数值；键盘聚焦图表后可用左右方向键。','analysis-note chart-scroll-hint'),area,caption);
  const draw=()=>{const chart=result.charts.find(value=>value.id===select.value); clear(area);
    const values=chart?.series.flatMap(series=>series.values.map(Number))||[];
    if(!chart||!values.length||!values.every(Number.isFinite)){area.append(node('p','图表数据不可用，请查看结果表。')); return;}
    const legend=node('div',null,'chart-legend');
    const keys=chart.kind==='contribution'?[['增加（正值）','legend-positive'],['减少（负值）','legend-negative']]:chart.series.map((series,index)=>[series.name,'legend-'+index]);
    for(const [label,className] of keys){const item=node('span',label); item.className=className; legend.append(item);} area.append(legend);
    const scope=chart.scope||{},period=[scope.base_start&&`基期 ${scope.base_start} 至 ${scope.base_end}`, (scope.current_start||scope.start)&&`当期 ${scope.current_start||scope.start} 至 ${scope.current_end||scope.end}`].filter(Boolean).join('；');
    area.append(drawChart(chart)); caption.textContent=`${chart.title}；单位：${chart.unit}；${period?period+'（结束日不包含）；':''}证据：${chart.evidence_ids.join('、')}。核验状态：${({passed:'通过',failed:'未通过',unavailable:'未完成',skipped:'未执行'})[result.reflection_status]||'未知'}。精确数值见数据表。`;
  }; select.addEventListener('change',draw); draw(); entry.append(figure);
}
function drawChart(chart) {
  const width=720,left=132,plotWidth=425, values=chart.series.flatMap(series=>series.values.map(Number));
  const low=Math.min(0,...values),high=Math.max(0,...values),span=high-low||1;
  if(chart.kind==='line'){
    const height=280,top=22,bottom=235,plotLeft=75,plotRight=660;
    const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':chart.title+'，'+chart.unit,class:'analysis-chart'});
    svg.append(svgNode('title',{},chart.title+'；数据及完整数值见对应表格'));
    const times=chart.labels.map(value=>Date.parse(value.length===7?value+'-01':value));
    const timeLow=Math.min(...times),timeSpan=Math.max(...times)-timeLow||1;
    const x=index=>plotLeft+(times[index]-timeLow)/timeSpan*(plotRight-plotLeft),y=value=>bottom-(Number(value)-low)/span*(bottom-top);
    for(let tick=0;tick<=4;tick++){const value=low+span*tick/4,position=y(value); svg.append(svgNode('line',{x1:plotLeft,y1:position,x2:plotRight,y2:position,class:'chart-grid'}),svgNode('text',{x:plotLeft-10,y:position+4,'text-anchor':'end'},Number(value.toPrecision(4))));}
    chart.series.forEach((series,index)=>{svg.append(svgNode('polyline',{points:series.values.map((value,i)=>`${x(i)},${y(value)}`).join(' '),class:'line-series line-'+index}));
      series.values.forEach((value,i)=>{const point=svgNode('circle',{cx:x(i),cy:y(value),r:3,class:'point-'+index}); point.append(svgNode('title',{},`${chart.labels[i]} · ${series.name}：${value}`)); svg.append(point);});});
    const ticks=[0,Math.floor((chart.labels.length-1)/2),chart.labels.length-1];
    for(const index of [...new Set(ticks)])svg.append(svgNode('text',{x:x(index),y:260,'text-anchor':'middle'},chart.labels[index]));
    return svg;
  }
  const rowHeight=chart.series.length*18+22,height=Math.max(120,chart.labels.length*rowHeight+35),zero=left+(0-low)/span*plotWidth;
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':chart.title+'，'+chart.unit,class:'analysis-chart'});
  svg.append(svgNode('title',{},chart.title+'；数据及完整数值见对应表格'),svgNode('line',{x1:zero,y1:10,x2:zero,y2:height-12,class:'chart-axis'}));
  chart.labels.forEach((label,index)=>{const y=20+index*rowHeight; const text=svgNode('text',{x:0,y:y+12},label.length>12?label.slice(0,11)+'…':label); text.append(svgNode('title',{},label)); svg.append(text);
    chart.series.forEach((series,seriesIndex)=>{const raw=series.values[index],value=Number(raw),position=left+(value-low)/span*plotWidth,barY=y+seriesIndex*18;
      const bar=svgNode('rect',{x:Math.min(zero,position),y:barY,width:Math.abs(position-zero),height:11,class:chart.kind==='contribution'?(value<0?'bar-negative':'bar-primary'):'bar-'+(seriesIndex?'secondary':'primary')});
      bar.append(svgNode('title',{},`${label} · ${series.name}：${raw} ${chart.unit}`));
      svg.append(bar,svgNode('text',{x:710,y:barY+10,'text-anchor':'end'},measure(raw,chart.unit==='%'?'%':'',chart.unit==='%'?2:null)));});});
  return svg;
}
async function sendQuestion(resumeTask=null) {
  if(state.chat){if(!resumeTask)state.chat.abort();return;} if(state.restoring)return; const question=resumeTask?.question||$('#question').value.trim(); if(!question)return;
  if(!state.config)return notice('工作台尚未就绪，请稍候。',true);
  if(state.config.model_configuration_required&&!state.modelSettings?.configured){await openModelSettings(); return;}
  if(!resumeTask&&!business.beforeSend())return;
  if(resumeTask){closeInspector(false);state.historyGeneration++;clear($('#conversation'));rememberSession(resumeTask.session_id);setConversationTitle(question);renderContext(null);$('#history-note').hidden=true;business.bind(resumeTask.profile_ref);if(state.view!=='chat')switchView('chat');}
  if(!state.session&&!$('#conversation').children.length)setConversationTitle(question);
  closeSourceMenu(); state.followOutput=true;
  const controller=new AbortController(); state.chat=controller; const submit=$('#chat-submit'); submit.textContent='停止分析'; submit.setAttribute('aria-label','停止分析'); submit.formNoValidate=true; $('#new-chat').disabled=true; $('#chat-form').setAttribute('aria-busy','true');
  message('user',question); const entry=message('assistant','正在开始分析…'); if(!resumeTask)$('#question').value='';business.sync();resizeQuestion(true); revealLatest(true); let completed=false;
  const fields=resumeTask?{business_profile_id:resumeTask.profile_ref.id,resume_task_id:resumeTask.id,mode:resumeTask.mode}:business.fields();
  if(!resumeTask&&$('#plan-analysis').checked)fields.mode='plan_execute';
  try{const response=await fetch(state.config.api_prefix+'/chat/stream',{...jsonOptions({question,session_id:state.session,...fields}),signal:controller.signal});
    if(!response.ok){const data=await response.json(); if(response.status===428)applyModelSettings({configured:false}); throw new Error(describeError(data.detail));}
    const reader=response.body.getReader(),decoder=new TextDecoder(); let buffer='';
    while(true){const {value,done}=await reader.read(); buffer+=decoder.decode(value,{stream:!done}); const events=buffer.split(/\r?\n\r?\n/); buffer=events.pop();
      for(const block of events){const raw=block.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n'); if(!raw)continue;
        const data=JSON.parse(raw); if(data.type==='error')throw new Error(data.error||'分析未完成');
        if(data.type==='start'){rememberSession(data.session_id||state.session);entry.taskId=data.task_id;entry.taskAttempt=data.task_attempt;business.sync();}
        if(data.type==='final'){completed=true; rememberSession(data.session_id||state.session);business.bind(data.business_profile); renderAnswer(entry,data); renderContext(data.analysis_state); archiveFeedback(data); $('#history-note').hidden=true; revealLatest(); if(data.archive_status==='saved'&&state.config.analysis_history_available)refreshConversations();}
        else if(!completed)recordActivity(entry,data);}
      if(done)break;} if(!completed)throw new Error('连接已结束，未收到完整答案，请重试。');
  }catch(error){if(!completed){finishActivity(entry); answerBody(entry).textContent=error.name==='AbortError'?'本次分析已停止。':error.message; revealLatest();}}
  finally{state.chat=null; submit.textContent='发送'; submit.setAttribute('aria-label','发送分析问题'); submit.formNoValidate=false; $('#new-chat').disabled=false; $('#chat-form').setAttribute('aria-busy','false');business.sync();}
}
$('#chat-form').addEventListener('submit',event=>{event.preventDefault();return sendQuestion();});
$('#new-chat').addEventListener('click',()=>{if(state.chat)return; closeInspector(false); state.historyGeneration++; state.restoring=false; $('#chat-submit').disabled=false; rememberSession(null); setConversationTitle(); renderContext(null); $('#history-note').hidden=true; archiveFeedback(); clear($('#conversation')); conversationState(false); $('#question').value=''; resizeQuestion(true); closeSourceMenu(); closeMobileNavigation(); notice('');business.reset(); if(state.view!=='chat')switchView('chat'); $('#question').focus();});
$('#history-toggle').addEventListener('click',()=>{const panel=$('#analysis-history'); panel.hidden=!panel.hidden; $('#history-toggle').setAttribute('aria-expanded',String(!panel.hidden)); if(!panel.hidden)refreshConversations();});
$('#history-refresh').addEventListener('click',refreshConversations);
$('#sidebar-toggle').addEventListener('click',toggleNavigation);
$('#sidebar-close').addEventListener('click',toggleNavigation);
$('#nav-backdrop').addEventListener('click',closeMobileNavigation);
$('#source-menu-toggle').addEventListener('click',toggleSourceMenu);
document.querySelectorAll('[data-source-action]').forEach(item=>item.addEventListener('click',()=>sourceAction(item.dataset.sourceAction).catch(showError)));
document.addEventListener('click',event=>{if(!event.target.closest?.('.composer-attachment'))closeSourceMenu();});
$('#thread-scroll').addEventListener('scroll',trackThreadScroll);
$('#jump-latest').addEventListener('click',()=>revealLatest(true));
$('#question').addEventListener('input',()=>resizeQuestion());
if(typeof matchMedia==='function')matchMedia('(max-width: 900px)').addEventListener('change',()=>{
  const restoreFocus=state.navOpen||$('#workspace-navigation').contains(document.activeElement);
  state.navOpen=false; renderNavigation(restoreFocus);
});
document.querySelectorAll('.nav-item[data-view]').forEach(item=>item.addEventListener('click',()=>switchView(item.dataset.view)));
document.querySelectorAll('[data-target-view]').forEach(item=>item.addEventListener('click',()=>switchView(item.dataset.targetView)));
document.querySelectorAll('[data-question]').forEach(item=>item.addEventListener('click',()=>fillQuestion(item.dataset.question)));
$('#question').addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault(); $('#chat-form').requestSubmit();}});
document.querySelectorAll('.refresh').forEach(item=>item.addEventListener('click',()=>refresh().catch(showError)));
$('#upload-open').addEventListener('click',openUpload); $('#empty-upload').addEventListener('click',openUpload); $('#empty-doc-upload').addEventListener('click',openUpload);
$('#upload-close').addEventListener('click',()=>{$('#upload-section').hidden=true; $('#upload-open').focus();});
$('#inspector-close').addEventListener('click',()=>closeInspector()); $('#dataset-filter').addEventListener('input',renderDatasets);
$('#inspector-back').addEventListener('click',()=>{if(state.analysisEntry)openAnalysisDetails(state.analysisEntry);});
$('#model-settings-open').addEventListener('click',()=>openModelSettings().catch(showError));
$('#model-settings-start').addEventListener('click',()=>openModelSettings().catch(showError));
$('#model-settings-close').addEventListener('click',closeModelSettings);
$('#model-settings-form').addEventListener('submit',event=>{event.preventDefault(); submitModelSettings();});
$('#model-settings-check').addEventListener('click',()=>submitModelSettings(true));
$('#model-settings-clear').addEventListener('click',clearModelSettings);
for(const purpose of MODEL_PURPOSES){
  $('#model-purpose-'+purpose).addEventListener('click',()=>chooseModelPurpose(purpose));
  modelField('fetch',purpose).addEventListener('click',fetchModels);
  modelField('base-url',purpose).addEventListener('input',()=>{modelField('api-key',purpose).value='';modelKeyRequirement(purpose);renderModelList(purpose,[]);if(purpose==='embedding')modelField('dimensions',purpose).value='';});
  modelField('provider',purpose).addEventListener('change',()=>{const url=MODEL_PROVIDERS[modelField('provider',purpose).value];if(url){modelField('base-url',purpose).value=url;modelField('api-key',purpose).value='';renderModelList(purpose,[]);if(purpose==='embedding')modelField('dimensions',purpose).value='';}modelKeyRequirement(purpose);});
  modelField('key-toggle',purpose).addEventListener('click',()=>{const input=modelField('api-key',purpose),show=input.type==='password';input.type=show?'text':'password';modelField('key-toggle',purpose).textContent=show?'隐藏':'显示';modelField('key-toggle',purpose).setAttribute('aria-pressed',String(show));});
  modelField('manual-toggle',purpose).addEventListener('change',()=>{state.modelManual[purpose]=Boolean(modelField('manual-toggle',purpose).checked);modelField('manual-wrap',purpose).hidden=!state.modelManual[purpose];modelField('name',purpose).disabled=state.modelManual[purpose];if(state.modelManual[purpose])modelField('manual',purpose).focus();});
  modelField('name',purpose).addEventListener('change',()=>{if(purpose==='embedding')modelField('dimensions',purpose).value='';});
  modelField('manual',purpose).addEventListener('input',()=>{if(purpose==='embedding')modelField('dimensions',purpose).value='';});
}
$('#model-settings-dialog').addEventListener('close',()=>{
  state.modelGeneration++;modelSettingsBusy(false);
  for(const purpose of MODEL_PURPOSES){modelField('api-key',purpose).value='';modelField('api-key',purpose).type='password';}
  const opener=state.modelSettingsOpener;
  if(opener?.isConnected&&opener.getClientRects().length&&!opener.closest?.('[inert]'))opener.focus(); else $('#sidebar-toggle').focus();
});
$('#source-file').addEventListener('change',()=>{$('#file-label').textContent=$('#source-file').files[0]?.name||'选择文件';});
document.addEventListener('keydown',event=>{
  if($('#model-settings-dialog').open||$('#business-dialog').open)return;
  if(event.key==='Tab'&&mobileNavigation()&&state.navOpen){
    const controls=[...$('#workspace-navigation').querySelectorAll('a[href],button:not(:disabled)')].filter(item=>item.getClientRects().length);
    const first=controls[0],last=controls.at(-1);
    if(first&&event.shiftKey&&document.activeElement===first){event.preventDefault(); last.focus();}
    else if(last&&!event.shiftKey&&document.activeElement===last){event.preventDefault(); first.focus();}
  }
  if(event.key!=='Escape')return;
  if(guide.open){closeGuide(); return;}
  if(!$('#source-menu').hidden){closeSourceMenu(true); return;}
  if(state.navOpen){closeMobileNavigation(); return;}
  if(!$('#inspector').hidden)closeInspector();
});
const visionState={draft:null,page:1,busy:false,dirty:false,editors:[]};
const visionLabels={previewed:'本地预览',recognizing:'正在识别',needs_review:'待人工核对',failed:'识别未完成'};
function parseLiteralCSV(text) {
  if(text.length>500000)throw new Error('表格文字过长，请拆分。');
  const rows=[];let row=[],cell='',quoted=false,closed=false;
  text=text.replace(/^\uFEFF/,'');
  for(let i=0;i<text.length;i++){const char=text[i];
    if(quoted){if(char==='"'){if(text[i+1]==='"'){cell+='"';i++;}else{quoted=false;closed=true;}}else cell+=char;continue;}
    if(char==='"'&&!cell&&!closed){quoted=true;continue;}
    if(char===','){row.push(cell);cell='';closed=false;continue;}
    if(char==='\n'||char==='\r'){if(char==='\r'&&text[i+1]==='\n')i++;row.push(cell);rows.push(row);row=[];cell='';closed=false;continue;}
    if(closed||char==='"')throw new Error('CSV 引号格式不正确；含逗号或换行的单元格请用双引号包围。');
    cell+=char;
  }
  if(quoted)throw new Error('CSV 的双引号尚未闭合。');
  if(row.length||cell||closed){row.push(cell);rows.push(row);}
  const columns=rows.shift();
  if(!columns?.length||columns.some(value=>!value.trim())||new Set(columns).size!==columns.length)throw new Error('CSV 第一行需要互不重复且非空的表头。');
  if(rows.some(values=>values.length!==columns.length))throw new Error('CSV 每行列数必须与表头一致。');
  return {columns,rows:rows.map(values=>values.map(value=>value===''?null:value))};
}
function literalCSV(table) {const cell=value=>{const text=value===null?'':String(value);return /[",\r\n]/.test(text)?'"'+text.replaceAll('"','""')+'"':text;};return [table.columns,...table.rows].map(row=>row.map(cell).join(',')).join('\n');}
function visionDirty() {visionState.dirty=true;$('#vision-status').textContent='有未保存修改，请先保存核对稿或放弃修改。';visionControls();}
function visionControls() {
  for(const control of $('#vision-section').querySelectorAll('input,select,textarea,button'))control.disabled=visionState.busy;
  const page=visionState.draft?.pages?.[String(visionState.page)];
  $('#vision-recognize').disabled=visionState.busy||visionState.dirty||!state.config?.vision_recognition_available;
  $('#vision-consent').disabled=!state.config?.vision_recognition_available||visionState.busy;
  $('#vision-index').disabled=!state.config?.embedding_enabled||visionState.busy;
  $('#vision-save').disabled=visionState.busy||!visionState.dirty;
  $('#vision-discard').disabled=visionState.busy||!visionState.dirty;
  $('#vision-to-table').disabled=visionState.busy||visionState.dirty||!page?.edited.tables.length;
  $('#vision-to-document').disabled=visionState.busy||visionState.dirty||!page;
}
async function visionRun(action,{recover=false}={}) {
  if(visionState.busy)return;visionState.busy=true;visionControls();$('#vision-status').textContent='正在处理，请稍候…';
  try{await action();}
  catch(error){if(recover&&visionState.draft){try{visionState.draft=await request('/vision/drafts/'+visionState.draft.id);renderVision();}catch{/* 保留本地已有界面，避免自动重试付费识别。 */}} $('#vision-status').textContent=error.message;}
  finally{visionState.busy=false;visionControls();}
}
async function refreshVision() {
  const rows=await request('/vision/drafts'),select=clear($('#vision-drafts'));select.append(node('option','选择草稿'));
  select.children[0].value='';for(const row of rows){const option=node('option',row.filename+' · '+(visionLabels[row.status]||row.status));option.value=row.id;select.append(option);}
  select.value=visionState.draft?.id||'';
}
async function openVision(id=null,page=null) {
  if(!state.config?.vision_available)return notice('图片草稿服务尚未就绪，请检查数据库迁移和解析库。',true);
  if(visionState.dirty&&id&&id!==visionState.draft?.id)return notice('请先保存或放弃当前页修改，再切换来源。',true);
  closeInspector(false);$('#upload-section').hidden=true;$('#vision-section').hidden=false;
  $('#vision-config-note').textContent=state.config.vision_recognition_available?`识别模型：${state.config.vision_model}。上传不自动识别，每次调用均需主动确认。`:'图片识别尚未配置；可本地预览或人工转录。请在左侧“API 设置”的“图片 / OCR”中配置模型与独立 Key。';
  $('#vision-file-limit').textContent=`PNG、JPG、静态 WEBP、PDF · 不超过 ${state.config.vision_max_bytes/1048576} MiB；PDF 最多 ${state.config.vision_max_pdf_pages} 页，单次识别最多 ${state.config.vision_max_pages_per_request} 页。`;
  await visionRun(async()=>{await refreshVision();if(id){visionState.draft=await request('/vision/drafts/'+encodeURIComponent(id));visionState.page=page||1;renderVision();}});
  $('#vision-title').focus();
}
function readVisionEditor() {
  return {text:$('#vision-text').value,tables:visionState.editors.map(editor=>({title:editor.title.value,...parseLiteralCSV(editor.csv.value),notes:editor.notes.value})),
    warnings:visionState.draft?.pages?.[String(visionState.page)]?.edited.warnings||[]};
}
function drawVisionEditor(result) {
  $('#vision-text').value=result.text;const target=clear($('#vision-tables'));visionState.editors=[];
  result.tables.forEach((table,index)=>{const section=node('div',null,'vision-table-edit'),titleLabel=node('label',`表格 ${index+1} 名称`),title=node('input');title.value=table.title;title.maxLength=200;titleLabel.append(title);
    const csvLabel=node('label','CSV 转录（首行是表头）'),csv=node('textarea');csv.rows=8;csv.value=literalCSV(table);csv.setAttribute('aria-label',`表格 ${index+1} CSV 转录`);csvLabel.append(csv);
    const notesLabel=node('label','单位及表格说明'),notes=node('input');notes.value=table.notes;notes.maxLength=2000;notesLabel.append(notes);
    for(const input of [title,csv,notes])input.addEventListener('input',visionDirty);
    section.append(titleLabel,csvLabel,notesLabel,node('p','空单元格按缺失值保留，不补零；原始数字、前导零和单位均保留文本。','field-hint'),button('移除此表',()=>{const edited=readVisionEditor();edited.tables.splice(index,1);drawVisionEditor(edited);visionDirty();}));target.append(section);visionState.editors.push({title,csv,notes});});
}
function renderVision() {
  const draft=visionState.draft;$('#vision-content').hidden=!draft;if(!draft)return;
  visionState.dirty=false;$('#vision-drafts').value=draft.id;$('#vision-consent').checked=false;$('#vision-reviewed').checked=false;$('#vision-index').checked=false;
  metadata(clear($('#vision-source-info')),[['来源',draft.filename],['状态',visionLabels[draft.status]||draft.status],['原文件页数',draft.page_count],['已转录页',Object.keys(draft.pages).join('、')||'尚无'],['来源 SHA-256',draft.content_hash]]);
  $('#vision-pages').value=Array.from({length:Math.min(draft.page_count,state.config.vision_max_pages_per_request)},(_,i)=>i+1).join(',');
  const pages=clear($('#vision-page'));for(let i=1;i<=draft.page_count;i++){const option=node('option',`第 ${i} 页${draft.pages[String(i)]?' · 有转录':' · 尚未转录'}`);option.value=String(i);pages.append(option);}pages.value=String(visionState.page);
  const url=state.config.api_prefix+`/vision/drafts/${encodeURIComponent(draft.id)}/pages/${visionState.page}/image`;
  $('#vision-image').src=url;$('#vision-image').alt=`${draft.filename}，第 ${visionState.page} 页来源`;
  $('#vision-image-link').href=url;const current=draft.pages[String(visionState.page)];
  $('#vision-page-note').textContent=current?`${current.origin==='manual'?'人工首次转录':'视觉识别转录'}。本页修改需保存后才能发布；原始转录留存备查。`:'此页尚未识别。可启用视觉 API 后主动识别，或直接人工输入文字、添加表格。';
  drawVisionEditor(current?.edited||{text:'',tables:[],warnings:[]});
  $('#vision-raw').textContent=current?JSON.stringify({origin:current.origin,model:current.model,first_transcription:current.raw,usage:current.usage},null,2):'尚无首次转录。';
  const tables=clear($('#vision-table-choice'));for(const [index,table] of (current?.edited.tables||[]).entries()){const option=node('option',table.title);option.value=String(index);tables.append(option);}
  $('#vision-status').textContent=draft.error||`当前核对第 ${visionState.page} 页 / 共 ${draft.page_count} 页；只会发布主动确认的当前页。`;
  visionControls();
}
$('#vision-open').addEventListener('click',()=>openVision());
$('#vision-close').addEventListener('click',()=>{$('#vision-section').hidden=true;$('#vision-open').focus();});
$('#vision-refresh').addEventListener('click',()=>visionRun(refreshVision));
$('#vision-upload-form').addEventListener('submit',event=>{event.preventDefault();if(visionState.dirty)return notice('请先保存或放弃当前修改。',true);visionRun(async()=>{const file=$('#vision-file').files[0];if(!file)return;const form=new FormData();form.set('file',file);visionState.draft=await request('/vision/drafts/upload',{method:'POST',body:form});visionState.page=1;await refreshVision();renderVision();});});
$('#vision-drafts').addEventListener('change',()=>{if(visionState.dirty){$('#vision-drafts').value=visionState.draft.id;return notice('请先保存或放弃当前修改。',true);}const id=$('#vision-drafts').value;if(id)visionRun(async()=>{visionState.draft=await request('/vision/drafts/'+id);visionState.page=1;renderVision();});});
$('#vision-page').addEventListener('change',()=>{if(visionState.dirty){$('#vision-page').value=String(visionState.page);return notice('请先保存或放弃当前页修改。',true);}visionState.page=Number($('#vision-page').value);renderVision();});
$('#vision-text').addEventListener('input',visionDirty);
$('#vision-add-table').addEventListener('click',()=>{try{const value=readVisionEditor();value.tables.push({title:'人工转录表',columns:['字段1','字段2'],rows:[],notes:''});drawVisionEditor(value);visionDirty();}catch(error){showError(error);}});
$('#vision-discard').addEventListener('click',()=>visionRun(async()=>{visionState.draft=await request('/vision/drafts/'+visionState.draft.id);renderVision();}));
$('#vision-save').addEventListener('click',()=>visionRun(async()=>{const draft=visionState.draft;const result=readVisionEditor();visionState.draft=await request(`/vision/drafts/${draft.id}/pages/${visionState.page}`,jsonOptions({revision:draft.revision,result,manual:true},'PUT'));renderVision();$('#vision-status').textContent='核对稿已保存，请核对并勾选确认后发布。';}));
$('#vision-recognize').addEventListener('click',()=>visionRun(async()=>{const draft=visionState.draft;const value=$('#vision-pages').value;if(!/^\d+(\s*,\s*\d+)*$/.test(value.trim()))throw new Error('页码格式应为 1,2,3。');const pages=value.split(',').map(item=>Number(item.trim()));visionState.draft=await request(`/vision/drafts/${draft.id}/recognize`,jsonOptions({revision:draft.revision,pages,consent:$('#vision-consent').checked}));renderVision();},{recover:true}));
$('#vision-to-table').addEventListener('click',()=>visionRun(async()=>{const draft=visionState.draft;const data=await request(`/vision/drafts/${draft.id}/table`,jsonOptions({revision:draft.revision,page_number:visionState.page,table_index:Number($('#vision-table-choice').value),reviewed:$('#vision-reviewed').checked}));visionState.draft=data.draft;renderVision();await refresh('datasets',true);$('#vision-section').hidden=true;await selectDataset(data.dataset.id);notice('转录表已生成预览。核对字段类型后，再确认导入。');}));
$('#vision-to-document').addEventListener('click',()=>visionRun(async()=>{const draft=visionState.draft;const data=await request(`/vision/drafts/${draft.id}/document`,jsonOptions({revision:draft.revision,pages:[visionState.page],reviewed:$('#vision-reviewed').checked,allow_embedding:$('#vision-index').checked}));visionState.draft=data.draft;renderVision();await refresh('documents',true);$('#vision-section').hidden=true;await selectDocument(data.document_id);notice(data.embedding_requested?'本页参考资料已保存，已按本次确认排队建立向量索引。':'本页参考资料已保存，可本地关键词检索；未发送 Embedding 请求。');}));
const GUIDE_KEY='analyst:onboarding:v1';
const GUIDE_STEPS=[
  {title:'先用现成数据体验一次',description:'这是一个用自然语言查数据、找资料并核对结论的工作台，不需要你编写查询代码。',
    points:['先从侧栏“API 设置”填写你自己的接入地址、模型和 Key；保存到本机，不会使用开发者的 Key。','项目附带 2025 年电商演示数据，可以先试下面的问题；换过数据源时，请调整指标和年份。','“填入示例”只填写输入框。由你点击“发送”后才运行，可能产生模型 API 费用。'],
    example:'2025 年 12 月的退款率是多少？',action:'填入示例，开始体验',kind:'example',target:'[data-view="chat"]'},
  {title:'自己的表格，先预览再导入',description:'订单、预算、活动名单等结构化数据，放在“表格数据”。',
    points:['选择 CSV 或 Excel（XLSX），先生成预览，此时还不会建分析表。','核对字段类型、金额单位和编号。确认导入后，等状态变为“可分析”。','提问时写出数据集名称，助手才能更清楚地知道你想查哪份数据。'],
    action:'去上传表格',kind:'datasets',feature:'datasets_available',target:'[data-view="datasets"]'},
  {title:'用业务资料补充背景',description:'周报、活动说明、指标口径等文字内容，放在“业务文档”，不必和数值表格混在一起。',
    points:['支持 TXT、Markdown、Word（DOCX）和文字 PDF，资料会保留可核对的来源。','图片或扫描 PDF 请走“图片 / 扫描件”，核对转录后再使用；不要直接把识别结果当成准确事实。','开启向量化时，上传文字会发送至配置的外部服务并可能计费；图片识别也需要你主动同意。'],
    action:'去准备业务文档',kind:'documents',feature:'documents_available',target:'[data-view="documents"]'},
  {title:'提问时，把三个条件说清楚',description:'明确“看什么、哪段时间、哪些数据”，比只问“为什么变差了”更容易得到可核对的回答。',
    points:['指标：例如净销售额、退款率或订单数。','时间：明确年份、月份或起止日期；比较时说明两个周期。','范围：补充品类、地区或数据集名称。条件不清楚时，助手会继续询问。'],
    example:'2025 年 12 月相比 11 月，哪个品类销售额下降最多？',action:'把这个问题填入输入框',kind:'example',target:'#question'},
  {title:'看答案，需要时再核对依据',description:'正文只显示答案。点击该条回答右上角“分析详情”，可在旁边查看审查、数据和执行记录。',
    points:['在详情中点击 E 编号可定位工具记录；文档引用可回到原文。记录存在，不代表结论一定正确。','详情内审查“未通过”或“未完成”时，先核对问题和证据，不要把回答当成已确认事实。','历史保存的是当时快照；详情中可导出已保存报告。想看最新数据，需要重新提问。'],
    action:'查看历史分析入口',kind:'history',feature:'analysis_history_available',target:'#history-toggle'}
];
const guide={open:false,step:0,target:null,opener:null};
function guideSeen() {
  try{return Boolean(localStorage.getItem(GUIDE_KEY));}catch{return false;}
}
function rememberGuide(status) {
  try{localStorage.setItem(GUIDE_KEY,status);}catch{/* 存储被限制时仍能阅读和关闭指南。 */}
}
function initializeGuide() {
  $('#guide-invite').hidden=guideSeen()||Boolean(state.session||state.chat||state.restoring||$('#question').value.trim());
}
function syncGuideTarget() {
  guide.target?.classList.toggle('guide-highlight',false); guide.target=null;
  if(!guide.open)return;
  let target=$(GUIDE_STEPS[guide.step].target);
  if(!target||target.hidden||target.closest?.('[hidden]'))target=$('[data-view="chat"]');
  if(target){target.classList.toggle('guide-highlight',true); guide.target=target;}
}
function renderGuide(focus=false) {
  const step=GUIDE_STEPS[guide.step];
  $('#guide-progress').textContent=`第 ${guide.step+1} 步，共 ${GUIDE_STEPS.length} 步`;
  $('#guide-step-title').textContent=step.title; $('#guide-description').textContent=step.description;
  const list=clear($('#guide-points')); for(const point of step.points)list.append(node('li',point));
  $('#guide-example').hidden=!step.example; $('#guide-example').textContent=step.example||'';
  $('#guide-action').textContent=step.action;
  const busy=Boolean(state.chat||state.restoring||state.uploading), unavailable=Boolean(step.feature&&!state.config?.[step.feature]);
  $('#guide-action').disabled=busy||unavailable;
  $('#guide-action-note').textContent=busy?'当前有分析、恢复或上传任务，结束后再操作即可。':unavailable?'当前功能尚未启用，你仍可继续阅读指南。':'';
  $('#guide-back').disabled=guide.step===0;
  $('#guide-next').textContent=guide.step===GUIDE_STEPS.length-1?'完成指南':'下一步';
  document.querySelectorAll('[data-guide-step]').forEach(item=>item.setAttribute('aria-current',Number(item.dataset.guideStep)===guide.step?'step':'false'));
  $('#guide-content').scrollTop=0; syncGuideTarget();
  if(focus)$('#guide-step-title').focus({preventScroll:true});
}
function openGuide() {
  guide.opener=document.activeElement; guide.open=true; guide.step=0;
  $('#guide-panel').hidden=false; $('#guide-invite').hidden=true; $('#guide-open').setAttribute('aria-expanded','true'); renderGuide(true);
}
function closeGuide(status='dismissed',restore=true) {
  guide.open=false; $('#guide-panel').hidden=true; $('#guide-invite').hidden=true;
  $('#guide-open').setAttribute('aria-expanded','false'); syncGuideTarget(); rememberGuide(status);
  if(restore){const opener=guide.opener?.isConnected&&guide.opener.tabIndex>=0&&guide.opener.getClientRects?.().length?guide.opener:$('#guide-open'); opener.focus({preventScroll:true});}
}
function moveGuide(index) {
  if(!guide.open||!Number.isInteger(index)||index<0||index>=GUIDE_STEPS.length)return;
  guide.step=index; renderGuide(true);
}
function finishGuide() {
  closeGuide('completed',false); if(state.view!=='chat')switchView('chat'); $('#question').focus();
}
function guideAction() {
  const step=GUIDE_STEPS[guide.step];
  if(state.chat||state.restoring||state.uploading)return renderGuide();
  if(step.feature&&!state.config?.[step.feature])return renderGuide();
  closeGuide('dismissed',false);
  if(step.kind==='example'){
    if(state.view!=='chat')switchView('chat');
    if(!$('#question').value.trim())fillQuestion(step.example);
    else{notice('已保留你输入的问题，可继续编辑后再提交。'); $('#question').focus();}
  }else if(step.kind==='history'){
    if(state.view!=='chat')switchView('chat');
    $('#analysis-history').hidden=false; $('#history-toggle').setAttribute('aria-expanded','true'); refreshConversations();
    if(mobileNavigation()){state.navOpen=true; renderNavigation();} $('#history-toggle').focus();
  }else{switchView(step.kind); openUpload();}
}
$('#guide-open').addEventListener('click',openGuide); $('#guide-start').addEventListener('click',openGuide);
$('#guide-close').addEventListener('click',()=>closeGuide());
$('#guide-dismiss').addEventListener('click',()=>{closeGuide('dismissed',false); $('#question').focus();});
$('#guide-back').addEventListener('click',()=>moveGuide(guide.step-1));
$('#guide-next').addEventListener('click',()=>guide.step===GUIDE_STEPS.length-1?finishGuide():moveGuide(guide.step+1));
$('#guide-action').addEventListener('click',guideAction);
document.querySelectorAll('[data-guide-step]').forEach(item=>item.addEventListener('click',()=>moveGuide(Number(item.dataset.guideStep))));
async function initialize() {
  renderNavigation();
  try{const response=await fetch('/workspace-config'); if(!response.ok)throw new Error('无法读取工作台配置，请刷新重试。'); state.config=await response.json();}
  catch(error){$('#health').textContent='配置暂不可用'; showError(error); initializeGuide(); return;}
  if(state.config.model_configuration_required){try{await loadModelSettings();}catch(error){applyModelSettings({configured:false}); showError(error);}}
  await business.initialize();
  for(const option of $('#search-mode').options)if(option.value!=='keyword')option.disabled=!state.config.embedding_enabled;
  $('#history-toggle').disabled=!state.config.analysis_history_available; archiveFeedback();
  $('#vision-open').disabled=!state.config.vision_available;
  for(const item of document.querySelectorAll('[data-source-action]')){
    const kind=item.dataset.sourceAction; item.disabled=!state.config[kind==='vision'?'vision_available':kind+'_available'];
  }
  try{const health=await request('/health/ready'); $('#health').textContent=health.status==='ready'?'服务已就绪':'部分服务暂不可用'; $('#health').classList.toggle('online',health.status==='ready');}
  catch{ $('#health').textContent='分析服务暂不可用'; }
  try{await refresh('datasets');}catch(error){showError(error);}
  if(state.config.analysis_history_available){
    refreshConversations(); let previous=null; try{previous=localStorage.getItem('analyst:last-session');}catch{/* 历史列表仍然可用。 */} if(previous)await restoreConversation(previous);
  }else $('#history-list').textContent='历史存储未启用；当前对话仍可继续分析。';
  initializeGuide();
}
const business=installBusinessWorkspace({$,state,node,button,request,jsonOptions,beginInspector,active,focusInspector,closeMobileNavigation,notice,formatTime,renderMarkdown,renderReportDetails,sendQuestion});
setInterval(()=>{if(!document.hidden)pollDataset().catch(showError);},2500);
initialize();
