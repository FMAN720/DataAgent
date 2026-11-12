// Business definitions and task inspection extend the existing workspace surfaces.
export function installBusinessWorkspace(ui) {
  const {$,state,node,button,request,jsonOptions,beginInspector,active,focusInspector,closeMobileNavigation,notice,formatTime,renderMarkdown,renderReportDetails,sendQuestion}=ui;
  let profiles=[],selected='ecommerce_v1',bound=null,editing=null,opener=null,loaded=false,editorGeneration=0;
  const stages={loading:'读取条件',planning:'制定计划',executing:'查询与分析',reviewing:'模型审查',validating:'程序校验',saving:'保存结果'};
  const statuses={completed:'已完成',failed:'未完成',cancelled:'已停止',interrupted:'已中断',running:'执行中',...stages};
  const template={schema_version:1,id:'my_business',name:'我的业务',description:'示例模板：请按实际数据替换映射并核对单位。',metrics:[{id:'total_amount',name:'金额合计',table:'uploads.your_table',primary_key:['col_0'],date_column:'col_1',aggregation:'sum',storage_unit:'元',value_column:'col_2',dimensions:[]}],document_ids:[]};
  const feedback=(text,error=false)=>{const target=$('#business-status'); target.textContent=text; target.classList.toggle('error',error);};
  function sync() {
    const control=$('#business-profile-select'); control.replaceChildren();
    for(const profile of profiles){const option=node('option',`${profile.name} · v${profile.revision}`); option.value=profile.id; control.append(option);}
    control.value=selected;
    control.disabled=Boolean(state.chat||state.session||$('#conversation').children.length);
    control.title=control.disabled?'新建分析后可切换业务口径':'选择此会话使用的业务口径';
    $('#business-context').hidden=!state.config?.business_profiles_available||(profiles.length<2&&selected==='ecommerce_v1');
  }
  async function loadProfiles() {
    const result=await request('/business-profiles'); profiles=result.profiles||[]; loaded=true;
    if(!profiles.some(profile=>profile.id===selected))selected='ecommerce_v1';
    sync();
    const choice=$('#business-existing'),before=choice.value; choice.replaceChildren();
    for(const profile of profiles){const option=node('option',`${profile.name} · v${profile.revision}${profile.readonly?'（只读）':''}`); option.value=profile.id; choice.append(option);}
    choice.value=profiles.some(profile=>profile.id===before)?before:selected;
    return profiles;
  }
  function preview() {
    const target=$('#business-preview'); target.replaceChildren();
    if(editing?.readonly){target.append(node('p','内置电商指标使用固定口径；可新建独立业务口径。','field-hint'));return;}
    try {
      const value=JSON.parse($('#business-json').value);
      if(!Array.isArray(value.metrics)||!value.metrics.length)throw new Error('请配置至少一个指标。');
      target.append(node('p',`${value.name||'未命名'} · ${value.metrics.length} 个指标`,'muted'));
      for(const metric of value.metrics.slice(0,20))target.append(node('p',`${metric.name||metric.id||'未命名指标'}：${metric.table||'待填写表名'} · ${metric.storage_unit||'待确认单位'}`,'field-hint'));
    } catch(error){target.append(node('p','JSON 尚未完成：'+error.message,'field-hint'));}
  }
  function setEditor(value,reference=null) {
    editorGeneration++;
    editing=reference; $('#business-json').value=JSON.stringify(value,null,2); $('#business-json').readOnly=Boolean(reference?.readonly);
    $('#business-save').disabled=Boolean(reference?.readonly);
    $('#business-revision').textContent=reference?`${reference.name||reference.id} · v${reference.revision}${reference.readonly?'（内置只读）':'；保存将生成新版本'}`:'新口径，首次保存为 v1';
    feedback(reference?.readonly?'内置口径保留原有保护。点击“新建草稿”配置自己的业务。':'表名、字段、单位和唯一键须与真实数据一致。保存不会调用模型。'); preview();
  }
  async function readSelected() {
    const identifier=$('#business-existing').value; if(!identifier)return;
    const generation=++editorGeneration;
    try{const value=await request('/business-profiles/'+encodeURIComponent(identifier)); if(generation!==editorGeneration)return; const metadata=profiles.find(profile=>profile.id===identifier); setEditor(value.definition||value,metadata);}
    catch(error){if(generation===editorGeneration)feedback(error.message,true);}
  }
  async function openBusiness() {
    if(!state.config?.business_profiles_available)return notice('本机业务口径服务尚未启用。',true);
    opener=document.activeElement; closeMobileNavigation();
    const dialog=$('#business-dialog'); if(!dialog.open)dialog.showModal();
    const generation=editorGeneration;
    try{await loadProfiles(); if(generation===editorGeneration&&!$('#business-json').value)await readSelected();}
    catch(error){if(generation===editorGeneration)feedback('无法读取口径，请点击“读取所选口径”重试：'+error.message,true);}
    if(generation===editorGeneration&&dialog.open)$('#business-existing').focus();
  }
  async function saveProfile() {
    const save=$('#business-save'); if(save.disabled)return;
    let value; try{value=JSON.parse($('#business-json').value);}catch{feedback('JSON 格式不完整，请先修正后保存。',true);return;}
    if(!/^[a-z][a-z0-9_-]{0,63}$/.test(value.id||'')||value.id==='ecommerce_v1'){feedback('请填写新的口径 id：小写字母开头，可包含数字、下划线和短横线。',true);return;}
    save.disabled=true; const generation=editorGeneration;
    try{
      const revision=editing?.id===value.id?editing.revision:0;
      const result=await request('/business-profiles/'+encodeURIComponent(value.id),jsonOptions({expected_revision:revision,profile:value},'PUT'));
      await loadProfiles(); if(generation!==editorGeneration)return; setEditor(result.definition,{id:result.id,revision:result.revision,name:result.definition.name});
      if(!state.session&&!$('#conversation').children.length){selected=result.id;sync();}
      feedback(`已保存 v${result.revision}。${state.session?'新建分析后使用新版口径，当前回答仍对应原版本。':'可以关闭此窗口并开始分析。'}`);
    }catch(error){if(generation===editorGeneration)feedback(error.status===409?'口径已有新版本；当前草稿已保留，请导出草稿并重新读取后保存。':error.message,true);}
    finally{save.disabled=Boolean(editing?.readonly);}
  }
  async function openTasks() {
    if(!state.config?.analysis_tasks_available)return notice('任务记录尚未启用。',true);
    closeMobileNavigation(); const sequence=beginInspector('tasks','all','分析任务'),target=$('#inspector-body');
    target.replaceChildren(node('p','正在读取任务…','muted'));
    try{
      const data=await request('/analysis-tasks'); if(!active('tasks','all',sequence))return;
      target.replaceChildren(node('p','记录执行阶段与每次尝试；中断任务需要主动恢复。','field-hint'));
      target.append(button('刷新任务',()=>openTasks(),'quiet'));
      if(!data.tasks?.length)target.append(node('p','还没有分析任务。提交问题后会在这里记录执行过程。','muted'));
      for(const task of data.tasks||[]){const row=node('section',null,'task-row'); row.append(button(task.question,()=>openTask(task.id),'quiet task-question'),node('p',`${statuses[task.status]||'状态待核对'} · 第 ${task.attempt} 次 · ${formatTime(task.updated_at)}`,'field-hint')); target.append(row);}
      focusInspector(false);
    }catch(error){if(active('tasks','all',sequence)){target.replaceChildren(node('p',error.message,'notice error'),button('重新读取',()=>openTasks()));}}
  }
  async function openTask(identifier) {
    const sequence=beginInspector('task',identifier,'任务详情'),target=$('#inspector-body'); target.replaceChildren(node('p','正在读取执行记录…','muted'));
    try{
      const task=await request('/analysis-tasks/'+encodeURIComponent(identifier)); if(!active('task',identifier,sequence))return;
      target.replaceChildren(button('返回任务列表',()=>openTasks(),'quiet'),node('h3',task.question),node('p',`${statuses[task.status]||'状态待核对'} · 第 ${task.attempt} 次尝试`,'muted'));
      target.append(node('p',`${task.profile_ref.name||task.profile_ref.id} · v${task.profile_ref.revision}`,'field-hint'));
      const list=node('ol',null,'task-stages');
      for(const [phase,status] of Object.entries(task.stages||{}))list.append(node('li',`${stages[phase]||phase}：${statuses[status]||status}`)); target.append(list);
      if(task.error)target.append(node('p',task.error,'notice error'));
      if(task.resumable){target.append(node('p',task.resume_notice+'恢复时会切换到此任务，输入框中的草稿保留。','field-hint')); let armed=false;
        const resume=button('恢复任务',()=>{if(state.chat)return notice('请先停止当前分析，再恢复其他任务。',true); if(!armed){armed=true;resume.textContent='确认恢复（重新查询）';return;} resume.disabled=true; sendQuestion(task).finally(()=>{resume.disabled=false;armed=false;resume.textContent='恢复任务';});},'primary'); target.append(resume);}
      if(task.response){const answer=node('div',null,'task-answer'); renderMarkdown(answer,task.response.answer||task.response.error||'未生成完整答案');target.append(answer);renderReportDetails(target,task.response);}
      const records=node('details');records.append(node('summary','各次尝试的实际执行记录'));
      if(task.events_complete===false)records.append(node('p','此处仅展示最近的执行记录；完整记录仍保存在本机工作区。','field-hint'));
      for(const record of task.events||[]){const event=record.event;if(event.type==='tool'){records.append(node('h4',`第 ${record.attempt} 次 · ${event.tool} · ${event.ok?'成功':'未完成'}`),node('pre',event.output));}else if(event.type==='plan'&&event.steps?.length){records.append(node('p',`第 ${record.attempt} 次计划：${event.steps.join('；')}`,'field-hint'));}}
      target.append(records,button('刷新此任务',()=>openTask(identifier),'quiet'));focusInspector(false);
    }catch(error){if(active('task',identifier,sequence))target.replaceChildren(node('p',error.message,'notice error'),button('重新读取',()=>openTask(identifier)));}
  }
  $('#business-open').addEventListener('click',()=>openBusiness());
  $('#business-close').addEventListener('click',()=>$('#business-dialog').close());
  $('#business-dialog').addEventListener('close',()=>{if(opener?.isConnected&&opener.getClientRects?.().length&&!opener.closest?.('[inert]'))opener.focus();else $('#sidebar-toggle').focus();});
  $('#business-load').addEventListener('click',readSelected);
  $('#business-new').addEventListener('click',()=>setEditor(template));
  $('#business-save').addEventListener('click',saveProfile);
  $('#business-json').addEventListener('input',()=>{editorGeneration++;preview();});
  $('#business-import').addEventListener('click',()=>$('#business-file').click());
  $('#business-export').addEventListener('click',()=>{
    const content=$('#business-json').value;if(!content)return feedback('当前没有草稿可以导出。',true);
    const url=URL.createObjectURL(new Blob([content],{type:'application/json;charset=utf-8'}));const link=node('a');link.href=url;link.download='business-profile.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  $('#business-file').addEventListener('change',async()=>{
    const file=$('#business-file').files?.[0]; if(!file)return;
    const generation=++editorGeneration;
    try{if(file.size>131072)throw new Error('口径文件超过 128 KiB，请拆分。');const value=JSON.parse(await file.text());if(generation!==editorGeneration)return;setEditor(value,value.id===editing?.id?editing:null);}
    catch(error){if(generation===editorGeneration)feedback('文件未导入：'+error.message,true);}finally{$('#business-file').value='';}
  });
  $('#tasks-open').addEventListener('click',()=>openTasks());
  $('#business-profile-select').addEventListener('change',()=>{selected=$('#business-profile-select').value;bound=null;sync();});
  return {
    async initialize(){ $('#business-open').hidden=!state.config?.business_profiles_available;$('#tasks-open').hidden=!state.config?.analysis_tasks_available; if(state.config?.business_profiles_available){try{await loadProfiles();}catch(error){notice('业务口径暂未读取，可从左侧“业务口径”重试：'+error.message,true);}} },
    beforeSend(){if(!state.config?.business_profiles_available)return true;if(!loaded){notice('业务口径尚未读取，请从左侧“业务口径”重试。',true);return false;}const latest=profiles.find(profile=>profile.id===selected);if(bound&&latest&&bound.id===latest.id&&bound.revision!==latest.revision){notice('业务口径已有新版本，请先新建分析；当前问题草稿已保留。',true);return false;}return true;},
    fields(){return state.config?.business_profiles_available?{business_profile_id:selected}:{};},
    bind(reference=null){bound=reference||profiles.find(profile=>profile.id===selected)||null;if(reference){selected=reference.id;sync();}},
    reset(){bound=null;sync();},sync,
    openTask,openTasks,
  };
}
