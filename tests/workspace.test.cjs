// Real workspace script under Node's built-in runner; no browser/runtime downloads.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const markdownSource = readFileSync(path.join(__dirname, '../app/web/markdown.js'), 'utf8');
const workspaceSource = readFileSync(path.join(__dirname, '../app/web/workspace.js'), 'utf8');
const businessSource = readFileSync(path.join(__dirname, '../app/web/business.js'), 'utf8');
const source = markdownSource.replace(/^export /gm, '') + '\n' + businessSource.replace(/^export /gm, '') + '\n' + workspaceSource.replace(/^import \{ (?:renderMarkdown|installBusinessWorkspace) \} from '.\/(?:markdown|business)\.js[^']*';\r?\n/gm, '');

function runtime(fetch, navigation=null) {
  const fields = new Map();
  const document = {
    querySelector(selector) {
      if (!fields.has(selector)) fields.set(selector, {
        textContent:'', value:'', options:[], hidden:false, disabled:false,
        listeners:{}, addEventListener(name,callback){this.listeners[name]=callback;}, classList:{toggle(){},add(){},remove(){}},
        children:[], attributes:{}, append(...children){this.children.push(...children);},
        replaceChildren(...children){this.children=children;},querySelector(){return null;},querySelectorAll(){return [];},contains(){return false;},
        showModal(){this.open=true;},close(){this.open=false;this.listeners.close?.();},reportValidity(){return true;},
        setAttribute(name,value){this.attributes[name]=value;},getAttribute(name){return this.attributes[name];},removeAttribute(name){delete this.attributes[name];},focus(){},
      });
      return fields.get(selector);
    },
    querySelectorAll(selector){
      if (!navigation) return [];
      if (selector==='[data-view]') return [document.querySelector('#main'),...navigation];
      return selector==='.nav-item[data-view]' ? navigation : [];
    }, listeners:{}, addEventListener(name,callback){this.listeners[name]=callback;},
    createElement:tag=>new Element(tag),
    createTextNode(text){const item=new Element('#text');item.textContent=text;return item;},
  };
  const context = vm.createContext({document, fetch, console, setInterval(){}, URL, URLSearchParams, AbortController});
  vm.runInContext(source, context);
  vm.runInContext('renderDatasets=()=>{}; renderDocuments=()=>{};', context);
  return {context, fields};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = (data, status=200) => ({ok:status===200,status,json:async()=>data});

test('business profile saves create a revision without any model request',async()=>{
  const calls=[],profiles=[{id:'ecommerce_v1',name:'演示',revision:1,readonly:true}];
  const {context,fields}=runtime(async(url,options)=>{
    if(url==='/workspace-config')return new Promise(()=>{});
    calls.push({url,options});
    if(options?.method==='PUT'){const body=JSON.parse(options.body);profiles.push({id:body.profile.id,name:body.profile.name,revision:1});return response({id:body.profile.id,revision:1,definition:body.profile});}
    return response({profiles});
  });
  vm.runInContext('state.config={api_prefix:"/api/v1",business_profiles_available:true}',context);
  await vm.runInContext('business.initialize()',context);
  const profile={id:'finance',name:'财务',metrics:[{id:'income',name:'收入',table:'uploads.receipts',storage_unit:'分'}]};
  fields.get('#business-json').value=JSON.stringify(profile);
  await fields.get('#business-save').listeners.click();
  const saved=calls.find(call=>call.options?.method==='PUT');
  assert.equal(saved.url,'/api/v1/business-profiles/finance');
  assert.equal(JSON.parse(saved.options.body).expected_revision,0);
  assert.ok(!calls.some(call=>/chat|model-settings.*check/.test(call.url)));
  assert.equal(fields.get('#business-profile-select').value,'finance');
  assert.ok(fields.get('#business-status').textContent.includes('v1'));
});

test('business version conflict keeps the unsaved JSON draft',async()=>{
  const {context,fields}=runtime(async(url,options)=>{
    if(url==='/workspace-config')return new Promise(()=>{});
    return options?.method==='PUT'?response({detail:'version conflict'},409):response({profiles:[]});
  });
  vm.runInContext('state.config={api_prefix:"/api/v1",business_profiles_available:true}',context);
  const draft='{"id":"finance","name":"保留的草稿","metrics":[]}';
  fields.get('#business-json').value=draft;
  await fields.get('#business-save').listeners.click();
  assert.equal(fields.get('#business-json').value,draft);
  assert.ok(fields.get('#business-status').textContent.includes('草稿已保留'));
});

test('late business initialization and definition reads cannot replace a newer draft',async()=>{
  let finishList,finishRead;
  const {context,fields}=runtime(url=>{
    if(url==='/workspace-config')return new Promise(()=>{});
    if(url.endsWith('/business-profiles'))return new Promise(resolve=>{finishList=resolve;});
    return new Promise(resolve=>{finishRead=resolve;});
  });
  vm.runInContext('state.config={api_prefix:"/api/v1",business_profiles_available:true}',context);
  const opening=fields.get('#business-open').listeners.click();
  await fields.get('#business-new').listeners.click();
  const draft=fields.get('#business-json').value;
  finishList(response({profiles:[{id:'ecommerce_v1',name:'演示',revision:1,readonly:true}]}));await opening;
  assert.equal(fields.get('#business-json').value,draft);
  fields.get('#business-existing').value='ecommerce_v1';
  const reading=fields.get('#business-load').listeners.click();
  fields.get('#business-json').value='{"id":"new_draft","name":"保留输入","metrics":[]}';
  fields.get('#business-json').listeners.input();
  finishRead(response({definition:{id:'ecommerce_v1',metrics:[]}}));await reading;
  assert.equal(fields.get('#business-json').value,'{"id":"new_draft","name":"保留输入","metrics":[]}');
  assert.equal(fields.get('#business-json').readOnly,false);
});

test('updated business revision blocks continuation without discarding the question',async()=>{
  const {context,fields}=runtime(async url=>url==='/workspace-config'?new Promise(()=>{}):response({profiles:[{id:'finance',name:'财务',revision:2}]}));
  vm.runInContext('state.config={api_prefix:"/api/v1",business_profiles_available:true}',context);
  await vm.runInContext('business.initialize();',context);
  vm.runInContext('business.bind({id:"finance",revision:1});$("#question").value="那11月呢？"',context);
  assert.equal(vm.runInContext('business.beforeSend()',context),false);
  assert.equal(fields.get('#question').value,'那11月呢？');
  assert.ok(fields.get('#notice').textContent.includes('新建分析'));
});

test('task inspection is read only and recovery requires a second explicit click',async()=>{
  const calls=[],task={id:'00000000-0000-0000-0000-000000000001',question:'原任务',session_id:'original-session',mode:'react',status:'cancelled',attempt:1,profile_ref:{id:'ecommerce_v1',revision:1},stages:{executing:'cancelled'},events:[],resumable:true,resume_notice:'重新查询，可能产生模型费用。'};
  const {context,fields}=runtime((url,options)=>{
    if(url==='/workspace-config')return new Promise(()=>{});
    calls.push({url,options});
    if(url.endsWith('/chat/stream'))return new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>{const error=new Error('stopped');error.name='AbortError';reject(error);}));
    return Promise.resolve(response(task));
  });
  vm.runInContext('state.config={api_prefix:"/api/v1",analysis_tasks_available:true};message=()=>({querySelector:()=>({textContent:""})});$("#question").value="未提交的草稿"',context);
  await vm.runInContext('business.openTask("00000000-0000-0000-0000-000000000001")',context);
  assert.equal(calls.length,1);
  const resume=descendants(fields.get('#inspector-body'),'button').find(item=>item.textContent==='恢复任务');
  await resume.listeners.click();assert.equal(calls.length,1);
  const pending=resume.listeners.click();await tick();
  const sent=JSON.parse(calls.at(-1).options.body);
  assert.equal(sent.resume_task_id,task.id);assert.equal(sent.question,task.question);assert.equal(sent.session_id,task.session_id);
  assert.equal(fields.get('#question').value,'未提交的草稿');
  vm.runInContext('state.chat.abort()',context);await pending;
  assert.equal(fields.get('#question').value,'未提交的草稿');
});

test('deterministic validation and profile version stay in optional details',()=>{
  const {answerEntry}=rendered({answer:'收入：100 元。',business_profile:{id:'finance',name:'财务',revision:3},validation:{status:'partial',checks:[{status:'unverified',claim:'100 元',message:'单位待核对',source_paths:['E1.rows[0][0]']}],limitations:['不证明任意SQL口径正确']}});
  assert.ok(!answerEntry.textContent.includes('程序校验'));
  assert.ok(answerEntry.reportDetails.textContent.includes('部分可核对'));
  assert.ok(answerEntry.reportDetails.textContent.includes('财务 · v3'));
  assert.ok(answerEntry.reportDetails.textContent.includes('E1.rows[0][0]'));
  const notApplicable=rendered({answer:'请补充统计周期。',validation:{status:'not_applicable',checks:[],limitations:[]}}).answerEntry;
  assert.ok(!notApplicable.textContent.includes('程序校验'));
  assert.ok(notApplicable.reportDetails.textContent.includes('程序校验：不适用'));
  assert.ok(notApplicable.reportDetails.textContent.includes('未进行数值认证'));
  assert.ok(!notApplicable.reportDetails.textContent.includes('已识别断言通过'));
});

test('question suggestions only fill the composer and preserve an active analysis',()=>{
  const calls=[];
  const {context,fields}=runtime(path=>{calls.push(path);return new Promise(()=>{});});
  vm.runInContext('fillQuestion("2025 年 12 月的退款率是多少？")',context);
  assert.equal(fields.get('#question').value,'2025 年 12 月的退款率是多少？');
  assert.deepEqual(calls,['/workspace-config']);
  vm.runInContext('state.chat={}; fillQuestion("不应替换正在分析的问题")',context);
  assert.equal(fields.get('#question').value,'2025 年 12 月的退款率是多少？');
  vm.runInContext('state.chat=null; state.restoring=true; fillQuestion("不应打断历史恢复")',context);
  assert.equal(fields.get('#question').value,'2025 年 12 月的退款率是多少？');
  assert.deepEqual(calls,['/workspace-config']);
});

test('only explicit Ctrl or Meta Enter requests form submission',()=>{
  const {fields}=runtime(()=>new Promise(()=>{}));
  let submitted=0, prevented=0;
  fields.get('#chat-form').requestSubmit=()=>submitted++;
  const keydown=fields.get('#question').listeners.keydown;
  keydown({key:'Enter',preventDefault(){prevented++;}});
  assert.equal(submitted,0);
  keydown({key:'Enter',ctrlKey:true,preventDefault(){prevented++;}});
  keydown({key:'Enter',metaKey:true,preventDefault(){prevented++;}});
  assert.equal(submitted,2);
  assert.equal(prevented,2);
});

test('post-mutation refresh waits for old read and then obtains a fresh snapshot', async()=>{
  const reads=[];
  const {context}=runtime(path=>path==='/workspace-config'?new Promise(()=>{}):new Promise(resolve=>reads.push(resolve)));
  vm.runInContext('state.config={api_prefix:"/api/v1"}',context);
  const before=vm.runInContext('refresh("datasets")',context);
  const after=vm.runInContext('refresh("datasets",true)',context);
  assert.equal(reads.length,1);
  reads[0](response([{id:'before-mutation'}]));
  await before; await tick();
  assert.equal(reads.length,2);
  reads[1](response([{id:'after-mutation'}]));
  await after;
  assert.equal(vm.runInContext('state.datasets[0].id',context),'after-mutation');
});

test('different resource lists load independently',async()=>{
  const calls=[];
  const {context}=runtime(path=>{
    if(path==='/workspace-config')return new Promise(()=>{});
    calls.push(path); return Promise.resolve(response([]));
  });
  vm.runInContext('state.config={api_prefix:"/api/v1"}',context);
  await Promise.all([vm.runInContext('refresh("datasets")',context),vm.runInContext('refresh("documents")',context)]);
  assert.deepEqual(calls.sort(),['/api/v1/datasets','/api/v1/documents']);
});

test('readiness 503 does not block usable data resources',async()=>{
  const calls=[];
  const {context,fields}=runtime(async path=>{
    calls.push(path);
    if(path==='/workspace-config')return response({api_prefix:'/api/v1',embedding_enabled:false});
    if(path==='/api/v1/health/ready')return response({detail:'analysis unavailable'},503);
    return response([]);
  });
  await tick(); await tick();
  assert.ok(calls.includes('/api/v1/datasets'));
  assert.equal(fields.get('#health').textContent,'分析服务暂不可用');
});

test('stop bypasses the now-empty required question and aborts the live request',async()=>{
  const {context,fields}=runtime((path,options)=>{
    if(path==='/workspace-config')return new Promise(()=>{});
    return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{
      const error=new Error('stopped'); error.name='AbortError'; reject(error);
    }));
  });
  vm.runInContext('state.config={api_prefix:"/api/v1"}; message=()=>({querySelector:()=>({textContent:""})});',context);
  vm.runInContext('$("#question").value="合成分析问题";',context);
  const submit=fields.get('#chat-form').listeners.submit;
  const started=submit({preventDefault(){}});
  assert.equal(fields.get('#question').value,'');
  assert.equal(fields.get('#chat-submit').formNoValidate,true);
  await submit({preventDefault(){}});
  await started;
  assert.equal(fields.get('#chat-submit').formNoValidate,false);
  assert.equal(vm.runInContext('state.chat',context),null);
});

class Element {
  constructor(tag){this.tag=tag;this.children=[];this.value='';this.listeners={};this._text='';}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join(' ');}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;this._text='';}
  addEventListener(name,callback){this.listeners[name]=callback;}
  setAttribute(name,value){this.attributes??={};this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes?.[name]??null;}
  querySelector(selector){for(const child of this.children){if(selector.startsWith('.')?child.className?.split(' ').includes(selector.slice(1)):child.tag===selector)return child;const found=child.querySelector(selector);if(found)return found;}return null;}
}
function rendered(payload,config=null){
  const {context}=runtime(()=>new Promise(()=>{}));
  context.document.createElement=tag=>new Element(tag);
  context.document.createTextNode=text=>{const node=new Element('#text');node.textContent=text;return node;};
  context.document.createElementNS=(namespace,tag)=>new Element(tag);
  const entry=new Element('article'),body=new Element('div');body.className='answer-body';entry.append(body);
  context.testEntry=entry;context.testPayload=payload;
  context.testConfig=config;
  vm.runInContext('state.config=testConfig; renderAnswer(testEntry,testPayload)',context);
  // Verify reusable report components as a separate surface, never as answer content.
  const surfaces=new Element('div');surfaces.append(entry,entry.reportDetails);
  return {entry:surfaces,answerEntry:entry,context};
}
const scope={metric:'净销售额',metric_kind:'additive',unit:'元',source:'合成资料',dimension:'区域',base_start:'2025-11-01',base_end:'2025-12-01',current_start:'2025-12-01',current_end:'2026-01-01',filters:[],assumptions:[]};

test('answer Markdown renders the reported bold and list syntax with decimal strings intact',()=>{
  const {entry}=rendered({answer:'**2025 年 12 月（2025-12-01 至 2025-12-31） **\n\n- **净销售额：734,981.67 元**\n- **退款率：8.0%**（退款订单 64 单 / 有效订单 797 单）'});
  assert.equal(descendants(entry,'strong').length,3);
  assert.equal(descendants(entry,'ul').length,1);
  assert.equal(descendants(entry,'li').length,2);
  assert.ok(entry.textContent.includes('734,981.67')&&entry.textContent.includes('8.0%'));
  assert.ok(!entry.querySelector('.answer-body').textContent.includes('**'));
});

test('answer Markdown supports headings, nested lists, quotes, code and scrollable tables',()=>{
  const {entry}=rendered({answer:'# 分析结论\n\n1. 查看 **销售额**\n   - 核对 `orders`\n2. 查看退款\n\n> 需结合原始凭证\n\n| 指标 | 金额 |\n| --- | ---: |\n| 净额 | 9007199254740993.25 |\n\n```sql\nSELECT * FROM orders;\n```'});
  for(const tag of ['h3','ol','ul','blockquote','pre','table'])assert.equal(descendants(entry,tag).length,1,tag);
  assert.equal(descendants(entry,'code').length,2);
  const table=descendants(entry,'div').find(item=>item.className==='table-scroll markdown-table');
  assert.equal(table.getAttribute('role'),'region');
  assert.equal(table.tabIndex,0);
  assert.ok(entry.textContent.includes('9007199254740993.25'));
});

test('Markdown never interprets raw HTML or image URLs and rejects unsafe link protocols',()=>{
  const {entry}=rendered({answer:'<img src=x onerror=alert(1)>\n\n[bad](javascript:alert(1)) [data](data:text/html,unsafe) [file](file:///secret) ![remote](https://invalid.example/image.png)\n\n[官方资料](https://example.com/help)'});
  assert.equal(descendants(entry,'img').length,0);
  assert.equal(descendants(entry,'script').length,0);
  const links=descendants(entry,'a');
  assert.equal(links.length,1);
  assert.equal(links[0].href,'https://example.com/help');
  assert.equal(links[0].rel,'noopener noreferrer');
  assert.ok(entry.textContent.includes('<img src=x onerror=alert(1)>'));
  assert.ok(entry.textContent.includes('![remote](https://invalid.example/image.png)'));
});

test('Markdown preserves malformed syntax, escaped table pipes and unfinished code without losing text',()=>{
  const {entry}=rendered({answer:'Unclosed **bold\n\n| 表达式 | 说明 |\n| --- | --- |\n| `a|b` | a\\|b |\n| extra | columns | retained |\n\n```text\n<script>kept()</script>'});
  assert.equal(descendants(entry,'table').length,1);
  assert.equal(descendants(entry,'td').length,2);
  assert.ok(entry.textContent.includes('Unclosed **bold'));
  assert.ok(entry.textContent.includes('extra | columns | retained'));
  assert.ok(entry.textContent.includes('<script>kept()</script>'));
  assert.equal(descendants(entry,'script').length,0);
});

test('unusually long Markdown falls back to complete literal text',()=>{
  const text='**数据**'.repeat(15000),{entry}=rendered({answer:text});
  assert.equal(entry.querySelector('.answer-body').textContent,text);
  assert.equal(descendants(entry,'strong').length,0);
});

test('answer surface contains the answer and one details action, while audit, tables and exports stay separate',()=>{
  const {answerEntry,context}=rendered({answer:'净销售额为 123.45 元。',reflection_status:'passed',archive_status:'saved',trace_id:'report-one',
    steps:[{tool:'run_sql',ok:true,arguments:{sql:'SELECT 1'},output:'1'}],
    tables:[{id:'t1',title:'查询结果',columns:['金额'],rows:[['123.45']],evidence_ids:['E1']}],
    created_at:'2026-11-04T00:00:00Z'}, {api_prefix:'/api/v1'});
  assert.ok(answerEntry.textContent.includes('净销售额为 123.45 元。'));
  assert.ok(answerEntry.textContent.includes('分析详情'));
  for(const text of ['审查状态','本轮记录','导出报告','SQL 正确','保存于'])assert.ok(!answerEntry.textContent.includes(text),text);
  assert.equal(descendants(answerEntry,'table').length,0);
  assert.ok(answerEntry.reportDetails.textContent.includes('审查状态：通过'));
  assert.equal(descendants(answerEntry.reportDetails,'table').length,1);
  vm.runInContext('openAnalysisDetails(testEntry)',context);
  assert.equal(context.document.querySelector('#inspector').hidden,false);
  assert.equal(context.document.querySelector('#inspector-title').textContent,'分析详情');
});

test('running activity remains outside the answer DOM and can be opened independently',()=>{
  const {answerEntry,context}=rendered({answer:'temporary'});
  vm.runInContext('recordActivity(testEntry,{type:"tool",tool:"run_sql",ok:true})',context);
  assert.equal(descendants(answerEntry,'ol').length,0);
  assert.equal(answerEntry.querySelector('.live-activity'),null);
  assert.equal(answerEntry.activity.list.children.length,1);
});

test('missing personal configuration opens settings without clearing or submitting the question',async()=>{
  const calls=[];
  const {context,fields}=runtime(async(url,options)=>{
    calls.push({url,options});
    if(url==='/workspace-config')return new Promise(()=>{});
    return response({configured:false,base_url:'',model:'',has_api_key:false});
  });
  vm.runInContext('state.config={api_prefix:"/api/v1",model_configuration_required:true}; $("#question").value="保留待分析的问题"',context);
  await fields.get('#chat-form').listeners.submit({preventDefault(){}});
  assert.equal(fields.get('#model-settings-dialog').open,true);
  assert.equal(fields.get('#question').value,'保留待分析的问题');
  assert.equal(vm.runInContext('state.chat',context),null);
  assert.ok(!calls.some(call=>call.url.includes('/chat')));
});

test('saving personal API settings never tests the model or stores a key in browser state',async()=>{
  const calls=[];
  const {context,fields}=runtime(async(url,options)=>{
    calls.push({url,options});
    if(url==='/workspace-config')return new Promise(()=>{});
    return response({configured:true,base_url:'https://offline.test/v1',model:'mine',has_api_key:true});
  });
  vm.runInContext('state.config={api_prefix:"/api/v1",model_configuration_required:true}; $("#model-base-url").value="https://offline.test/v1"; $("#model-name").value="mine"; $("#model-api-key").value="offline-personal-key"',context);
  await vm.runInContext('submitModelSettings()',context);
  assert.equal(calls.at(-1).url,'/api/v1/model-settings');
  assert.equal(calls.at(-1).options.method,'PUT');
  assert.equal(fields.get('#model-api-key').value,'');
  assert.equal(fields.get('#model-setup-hint').hidden,true);
  assert.ok(!vm.runInContext('JSON.stringify(state.modelSettings)',context).includes('offline-personal-key'));
  assert.ok(!calls.some(call=>call.url.includes('/check')||call.url.includes('/chat')));
});

test('fetching models uses endpoint and key only and never saves or runs inference',async()=>{
  const calls=[];
  const {context,fields}=runtime(async(url,options)=>{
    if(url==='/workspace-config')return new Promise(()=>{});
    calls.push({url,options});return response({models:[{id:'chosen-model'},{id:'other-model'}]});
  });
  vm.runInContext('state.config={api_prefix:"/api/v1"};$("#model-base-url").value="https://offline.test/v1";$("#model-api-key").value="offline-catalog-secret"',context);
  await vm.runInContext('fetchModels()',context);
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/v1/model-settings/models');
  assert.deepEqual(JSON.parse(calls[0].options.body),{base_url:'https://offline.test/v1',api_key:'offline-catalog-secret'});
  assert.ok(fields.get('#model-name').children.some(option=>option.value==='chosen-model'));
  assert.ok(!vm.runInContext('JSON.stringify(state)',context).includes('offline-catalog-secret'));
});

test('purposes keep separate field drafts and save only the selected model configuration',async()=>{
  const calls=[];
  const {context,fields}=runtime(async(url,options)=>{
    if(url==='/workspace-config')return new Promise(()=>{});
    calls.push({url,options});return response({profiles:{chat:{configured:true,model:'chat'},vision:{configured:true,base_url:'https://image.test/v1',model:'image',has_api_key:true}}});
  });
  vm.runInContext('state.config={api_prefix:"/api/v1",vision_available:true};$("#model-api-key").value="offline-unsaved-chat-key";chooseModelPurpose("vision");$("#vision-model-base-url").value="https://image.test/v1";$("#vision-model-name").value="image";$("#vision-model-api-key").value="offline-image-secret";$("#vision-model-enabled").checked=true',context);
  await vm.runInContext('submitModelSettings()',context);
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/v1/model-settings?purpose=vision');
  assert.equal(JSON.parse(calls[0].options.body).api_key,'offline-image-secret');
  assert.equal(fields.get('#model-api-key').value,'offline-unsaved-chat-key');
  assert.equal(fields.get('#vision-model-api-key').value,'');
  assert.equal(vm.runInContext('state.config.vision_recognition_available',context),true);
  fields.get('#model-settings-dialog').listeners.close();
  assert.equal(fields.get('#model-api-key').value,'');assert.equal(fields.get('#vision-model-api-key').value,'');
});

test('late model discovery after closing cannot overwrite a later settings session',async()=>{
  let finish;
  const {context,fields}=runtime(url=>url==='/workspace-config'?new Promise(()=>{}):new Promise(resolve=>{finish=resolve;}));
  vm.runInContext('state.config={api_prefix:"/api/v1"};$("#model-base-url").value="https://offline.test/v1";$("#model-api-key").value="offline-secret"',context);
  const pending=vm.runInContext('fetchModels()',context);
  fields.get('#model-settings-dialog').listeners.close();
  fields.get('#model-name').value='keep-this-selection';
  finish(response({models:[{id:'late-model'}]}));await pending;
  assert.equal(fields.get('#model-name').value,'keep-this-selection');assert.equal(fields.get('#model-api-key').value,'');
});

test('embedding connection test fills dimensions without saving or sharing credentials',async()=>{
  const calls=[];
  const {context,fields}=runtime(async(url,options)=>{if(url==='/workspace-config')return new Promise(()=>{});calls.push({url,options});return response({connected:true,dimensions:3});});
  vm.runInContext('state.config={api_prefix:"/api/v1"};chooseModelPurpose("embedding");$("#embedding-model-base-url").value="https://vector.test/v1";$("#embedding-model-name").value="vector";$("#embedding-model-api-key").value="offline-vector-key"',context);
  await vm.runInContext('submitModelSettings(true)',context);
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/v1/model-settings/check?purpose=embedding');
  assert.equal(fields.get('#embedding-model-dimensions').value,3);assert.equal(fields.get('#embedding-model-enabled').checked,undefined);
});

test('main content never receives navigation click handling, so upload remains open after bubbling',()=>{
  const navigation={dataset:{view:'documents'},listeners:{},classList:{toggle(){}},setAttribute(){},removeAttribute(){},addEventListener(name,handler){this.listeners[name]=handler;}};
  const {context,fields}=runtime(()=>new Promise(()=>{}),[navigation]);
  vm.runInContext('state.config={api_prefix:"/api/v1",pdf_available:true,upload_max_bytes:1048576};',context);
  navigation.listeners.click();
  fields.get('#upload-open').listeners.click();
  fields.get('#main').listeners.click?.();
  assert.equal(fields.get('#upload-section').hidden,false);
  assert.equal(fields.get('#source-file').accept,'.txt,.md,.pdf,.docx');
  assert.equal(fields.get('#main').listeners.click,undefined);
  assert.equal(fields.get('#main').getAttribute('data-active-view'),'documents');
});

test('mobile session drawer updates focus availability and closes without changing the draft or active request',()=>{
  const {context,fields}=runtime(()=>new Promise(()=>{}));
  context.matchMedia=()=>({matches:true});
  context.activeRequest={abort(){throw new Error('Navigation must not stop analysis');}};
  vm.runInContext('state.chat=activeRequest; $("#question").value="保留追问"; renderNavigation(); toggleNavigation()',context);
  assert.equal(fields.get('#workspace-navigation').inert,false);
  assert.equal(fields.get('#workspace-navigation').getAttribute('aria-modal'),'true');
  assert.equal(fields.get('.work-area').inert,true);
  assert.equal(fields.get('#nav-backdrop').hidden,false);
  assert.equal(fields.get('#sidebar-toggle').getAttribute('aria-expanded'),'true');
  vm.runInContext('closeMobileNavigation()',context);
  assert.equal(fields.get('#workspace-navigation').inert,true);
  assert.equal(fields.get('.work-area').inert,false);
  assert.equal(fields.get('#nav-backdrop').hidden,true);
  assert.equal(fields.get('#sidebar-toggle').getAttribute('aria-expanded'),'false');
  assert.equal(fields.get('#question').value,'保留追问');
  assert.equal(vm.runInContext('state.chat===activeRequest',context),true);
});

test('composer document action opens the real upload form and preserves the question',async()=>{
  const {context,fields}=runtime(()=>new Promise(()=>{}));
  vm.runInContext('state.config={api_prefix:"/api/v1",documents_available:true,upload_max_bytes:1048576}; $("#question").value="问题草稿"',context);
  await vm.runInContext('sourceAction("documents")',context);
  assert.equal(vm.runInContext('state.view',context),'documents');
  assert.equal(fields.get('#upload-section').hidden,false);
  assert.equal(fields.get('#source-file').accept,'.txt,.md,.pdf,.docx');
  assert.equal(fields.get('#source-menu').hidden,true);
  assert.equal(fields.get('#question').value,'问题草稿');
});

test('unavailable data actions show a recovery message without navigating',async()=>{
  const {context,fields}=runtime(()=>new Promise(()=>{}));
  vm.runInContext('state.config={documents_available:false}',context);
  await vm.runInContext('sourceAction("documents")',context);
  assert.equal(vm.runInContext('state.view',context),'chat');
  assert.equal(fields.get('#notice').hidden,false);
  assert.ok(fields.get('#notice').textContent.includes('尚未就绪'));
});

test('live activity uses actual event outcomes and keeps reasoning and raw tool output out of the progress list',()=>{
  const {answerEntry:entry,context}=rendered({answer:'待显示的回答',steps:[{tool:'run_sql',ok:false,arguments:{},output:'失败'}]});
  vm.runInContext('recordActivity(testEntry,{type:"start"}); recordActivity(testEntry,{type:"tool",tool:"run_sql",ok:false,thought:"private reasoning",output:"raw tool payload"})',context);
  assert.equal(entry.activity.panel.getAttribute('data-state'),'running');
  assert.equal(entry.querySelector('.answer-body').hidden,false);
  assert.ok(entry.activity.list.textContent.includes('查询数据 · 未完成'));
  assert.ok(!entry.activity.list.textContent.includes('private reasoning'));
  assert.ok(!entry.activity.list.textContent.includes('raw tool payload'));
  vm.runInContext('finishActivity(testEntry,testPayload)',context);
  assert.equal(entry.querySelector('.answer-body').hidden,false);
  assert.equal(entry.activity.panel.open,false);
  assert.equal(entry.activity.summary.textContent,'分析结束 · 1 条工具记录');
});

test('scrolling up preserves reading position until the user requests the newest output',()=>{
  const {context,fields}=runtime(()=>new Promise(()=>{}));
  vm.runInContext('$("#conversation").children=[{}]; Object.assign($("#thread-scroll"),{scrollHeight:2000,scrollTop:100,clientHeight:500}); trackThreadScroll(); revealLatest()',context);
  assert.equal(fields.get('#thread-scroll').scrollTop,100);
  assert.equal(fields.get('#jump-latest').hidden,false);
  vm.runInContext('revealLatest(true)',context);
  assert.equal(fields.get('#thread-scroll').scrollTop,2000);
  assert.equal(fields.get('#jump-latest').hidden,true);
});

test('a slower old sidebar history response cannot replace a newer refresh',async()=>{
  const reads=[];
  const {context,fields}=runtime(url=>url==='/workspace-config'?new Promise(()=>{}):new Promise(resolve=>reads.push(resolve)));
  vm.runInContext('state.config={api_prefix:"/api/v1"}',context);
  const older=vm.runInContext('refreshConversations()',context),newer=vm.runInContext('refreshConversations()',context);
  reads[1](response([{session_id:'newer',title:'新会话',updated_at:'2026-11-04',turn_count:1}]));await newer;
  reads[0](response([{session_id:'older',title:'旧会话',updated_at:'2026-11-03',turn_count:1}]));await older;
  const row=fields.get('#history-list').children[0];
  assert.equal(row.querySelector('button').textContent,'新会话');
});

test('comparison UI preserves decimal strings and marks mismatched contributions undefined',()=>{
  const {entry,context}=rendered({answer:'分项与总量不同，无法归因。',reflection_status:'unavailable',analysis:{scope,comparisons:[{
    scope,summary:{base:'100',current:'80',delta:'-20'},reconciliation:{matched:false},
    groups:[{segment:'华东',base_value:'120',current_value:'90',delta:'-30',contribution_pct:null}],group_count:1}],warnings:[]}});
  assert.ok(entry.textContent.includes('口径未匹配'));
  assert.ok(entry.textContent.includes('贡献未计算'));
  assert.ok(entry.textContent.includes('未定义'));
  const comparisonTables=descendants(entry,'div').filter(item=>item.className==='table-scroll');
  assert.equal(comparisonTables.length,2);
  assert.ok(comparisonTables.every(item=>item.getAttribute('tabindex')==='0'&&item.getAttribute('role')==='region'));
  assert.ok(entry.textContent.includes('宽表可左右滑动查看'));
  assert.equal(vm.runInContext('measure("9007199254740993.25")',context),'9,007,199,254,740,993.25');
  assert.equal(vm.runInContext('measure("-99.999", "%", 2)',context),'-100.00%');
  assert.equal(vm.runInContext('measure("9007199254740993.255", "", 2)',context),'9,007,199,254,740,993.26');
});

test('claim conflicts and evidence IDs are visible and untrusted text is rendered as text',()=>{
  const {entry}=rendered({answer:'<script>unsafe()</script>',reflection_status:'failed',
    reflection:{checks:[{claim:'活动导致下降',status:'conflicting',evidence_ids:['E2'],reason:'同期不证明因果'}],issues:['缺少因果证据']},
    steps:[{tool:'run_sql',ok:true,arguments:{sql:'SELECT 1'},output:'1'}]});
  assert.ok(entry.textContent.includes('存在冲突')&&entry.textContent.includes('E2'));
  assert.ok(entry.textContent.includes('需要核对的问题'));
  assert.ok(entry.textContent.includes('E1 · run_sql'));
  assert.ok(entry.textContent.includes('<script>unsafe()</script>'));
  assert.equal(entry.querySelector('script'),null);
});

function descendants(element,tag){return element.children.flatMap(child=>[...(child.tag===tag?[child]:[]),...descendants(child,tag)]);}

function guideRuntime(storage=new Map()) {
  const calls=[];
  const {context,fields}=runtime(url=>{calls.push(url);return new Promise(()=>{});});
  context.document.createElement=tag=>new Element(tag);
  context.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)};
  return {context,fields,storage,calls};
}

test('first-use invitation respects dismissal and can be explicitly reopened after reload',()=>{
  const {context,fields,storage}=guideRuntime(new Map([['analyst:last-session','saved-session']]));
  vm.runInContext('initializeGuide()',context);
  assert.equal(fields.get('#guide-invite').hidden,false);
  vm.runInContext('openGuide(); closeGuide()',context);
  assert.equal(storage.get('analyst:onboarding:v1'),'dismissed');
  assert.equal(storage.get('analyst:last-session'),'saved-session');
  const replay=guideRuntime(storage);
  vm.runInContext('initializeGuide()',replay.context);
  assert.equal(replay.fields.get('#guide-invite').hidden,true);
  vm.runInContext('openGuide()',replay.context);
  assert.equal(replay.fields.get('#guide-panel').hidden,false);
  assert.equal(replay.fields.get('#guide-open').getAttribute('aria-expanded'),'true');
});

test('guide step bounds and completion never clear the current question or session',()=>{
  const {context,fields,storage}=guideRuntime();
  vm.runInContext('state.session="current"; $("#question").value="保留这条问题"; openGuide(); moveGuide(-1); moveGuide(99)',context);
  assert.equal(vm.runInContext('guide.step',context),0);
  assert.equal(fields.get('#guide-back').disabled,true);
  vm.runInContext('moveGuide(4)',context);
  assert.equal(fields.get('#guide-next').textContent,'完成指南');
  fields.get('#guide-next').listeners.click();
  assert.equal(storage.get('analyst:onboarding:v1'),'completed');
  assert.equal(fields.get('#guide-panel').hidden,true);
  assert.equal(fields.get('#question').value,'保留这条问题');
  assert.equal(vm.runInContext('state.session',context),'current');
});

test('browsing all guide steps makes no API request or model call',()=>{
  const {context,calls}=guideRuntime();
  vm.runInContext('openGuide(); for(let i=0;i<5;i++)moveGuide(i); closeGuide()',context);
  assert.deepEqual(calls,['/workspace-config']);
});

test('guide example only fills an empty composer and never overwrites a draft',()=>{
  const {context,fields,calls}=guideRuntime();
  vm.runInContext('openGuide(); guideAction()',context);
  assert.equal(fields.get('#question').value,'2025 年 12 月的退款率是多少？');
  vm.runInContext('$("#question").value="我自己的分析问题"; openGuide(); guideAction()',context);
  assert.equal(fields.get('#question').value,'我自己的分析问题');
  assert.deepEqual(calls,['/workspace-config']);
});

test('guide respects unavailable features and ongoing analysis without aborting it',()=>{
  const {context,fields,calls}=guideRuntime();
  vm.runInContext('state.config={datasets_available:false}; openGuide(); moveGuide(1); guideAction()',context);
  assert.equal(fields.get('#guide-action').disabled,true);
  assert.equal(fields.get('#guide-panel').hidden,false);
  context.activeAnalysis={abort(){throw new Error('Guide must not abort analysis');}};
  vm.runInContext('state.config.datasets_available=true; state.chat=activeAnalysis; moveGuide(0); guideAction()',context);
  assert.equal(fields.get('#guide-action').disabled,true);
  assert.equal(vm.runInContext('state.chat===activeAnalysis',context),true);
  assert.deepEqual(calls,['/workspace-config']);
});

test('guide remains usable when browser storage throws and Escape only closes the guide',()=>{
  const {context,fields}=guideRuntime();
  context.localStorage={getItem(){throw new Error('blocked');},setItem(){throw new Error('blocked');}};
  vm.runInContext('initializeGuide(); openGuide(); $("#inspector").hidden=false',context);
  context.document.listeners.keydown({key:'Escape'});
  assert.equal(fields.get('#guide-panel').hidden,true);
  assert.equal(fields.get('#inspector').hidden,false);
});

test('existing sessions or a draft suppress the first-use invitation',()=>{
  const {context,fields}=guideRuntime();
  vm.runInContext('state.session="existing"; initializeGuide()',context);
  assert.equal(fields.get('#guide-invite').hidden,true);
  vm.runInContext('state.session=null; $("#question").value="draft"; initializeGuide()',context);
  assert.equal(fields.get('#guide-invite').hidden,true);
});

test('closing guide restores focus to a visible control when the welcome trigger is hidden',()=>{
  const {context,fields}=guideRuntime();
  let focused=false;
  context.document.activeElement={isConnected:true,getClientRects:()=>[],focus(){throw new Error('Cannot focus hidden invitation');}};
  vm.runInContext('openGuide()',context);
  fields.get('#guide-open').focus=()=>{focused=true;};
  vm.runInContext('closeGuide()',context);
  assert.equal(focused,true);
});

test('reconciled comparison details start collapsed, mismatch details stay visible',()=>{
  const payload=matched=>({answer:'结果',analysis:{scope,comparisons:[{scope,summary:{base:'100',current:'80',delta:'-20'},reconciliation:{matched},groups:[],group_count:0}]}});
  for(const matched of [true,false]){
    const {entry}=rendered(payload(matched));
    const detail=descendants(entry,'details').find(item=>item.className==='analysis-breakdown');
    assert.equal(detail.open,!matched);
  }
});

test('evidence index opens and focuses the selected real tool record without changing IDs',async()=>{
  const {entry,context}=rendered({answer:'仅作离线界面验证',reflection_status:'failed',steps:[
    {tool:'run_sql',ok:false,arguments:{sql:'SELECT missing'},output:'missing column'},
    {tool:'run_sql',ok:true,arguments:{sql:'SELECT 12.30'},output:'<script>unsafe()</script>'},
  ]});
  context.matchMedia=()=>({matches:true});
  const records=descendants(entry,'section').filter(item=>item.className==='tool-evidence');
  const links=descendants(entry,'button').filter(item=>item.className==='evidence-link');
  const panel=descendants(entry,'details').find(item=>item.className==='steps-panel');
  let scrolled=false,focused=false;
  records[1].scrollIntoView=options=>{scrolled=true;assert.equal(options.behavior,'auto');};
  records[1].focus=()=>{focused=true;};
  await links[1].listeners.click();
  assert.equal(panel.open,true);
  assert.ok(scrolled&&focused);
  assert.ok(records[0].textContent.includes('E1 · run_sql · 未完成'));
  assert.ok(records[1].textContent.includes('E2 · run_sql · 成功'));
  assert.ok(records[1].textContent.includes('<script>unsafe()</script>'));
  assert.equal(records[1].querySelector('script'),null);
});

test('compact evidence index preserves all records beyond its first eight shortcuts',()=>{
  const steps=Array.from({length:12},(_,i)=>({tool:'calculator',ok:true,arguments:{expression:String(i)},output:String(i)}));
  const {entry}=rendered({answer:'离线验证',reflection_status:'unavailable',steps});
  const records=descendants(entry,'section').filter(item=>item.className==='tool-evidence');
  const links=descendants(entry,'button').filter(item=>item.className==='evidence-link');
  assert.equal(records.length,12);
  assert.equal(links.length,9);
  assert.ok(records[11].textContent.includes('E12 · calculator'));
  assert.equal(links[8].textContent,'全部 12 步');
});
const chart={id:'c1',table_id:'t1',kind:'bar',title:'净销售额 · 区域',unit:'元',labels:['华东','华北'],
  series:[{name:'基期',values:['100.25','50']},{name:'当期',values:['80.1','70']}],evidence_ids:['E1'],scope};

test('charts use exact labels and declare units, periods, evidence and review state',()=>{
  const {entry}=rendered({answer:'合成结果',reflection_status:'unavailable',charts:[chart],analysis:{warnings:[]}});
  assert.equal(descendants(entry,'rect').length,4);
  assert.ok(entry.textContent.includes('100.25'));
  assert.ok(entry.textContent.includes('单位：元')&&entry.textContent.includes('2025-11-01')&&entry.textContent.includes('E1'));
  assert.ok(entry.textContent.includes('核验状态：未完成'));
  const region=descendants(entry,'div').find(item=>item.className==='chart-scroll');
  assert.equal(region.getAttribute('role'),'region');
  assert.equal(region.getAttribute('tabindex'),'0');
  assert.ok(region.getAttribute('aria-label').includes('左右滚动'));
  assert.ok(entry.textContent.includes('左右滑动查看完整图表与数值'));
});

test('chart selector changes to signed contributions and source labels remain plain text',()=>{
  const contribution={...chart,id:'c2',kind:'contribution',labels:['<script>bad()</script>','华北'],series:[{name:'变化贡献',values:['-20.15','20']}]};
  const {entry}=rendered({answer:'结果',reflection_status:'passed',charts:[chart,contribution]});
  const select=descendants(entry,'select')[0];select.value='c2';select.listeners.change();
  assert.equal(descendants(entry,'rect').length,2);
  assert.ok(descendants(entry,'rect').some(rect=>rect.getAttribute('class')==='bar-negative'));
  assert.ok(descendants(entry,'span').some(item=>item.className==='legend-negative'&&item.textContent==='减少（负值）'));
  assert.ok(descendants(entry,'span').some(item=>item.className==='legend-positive'&&item.textContent==='增加（正值）'));
  assert.equal(entry.querySelector('script'),null);
});

test('confirmed context distinguishes metric defaults from optional region and channel restrictions',()=>{
  const {context}=rendered({answer:'结果',reflection_status:'skipped'});
  vm.runInContext('renderContext({scope:{metric:"净销售额",start:"2025-11-01",end:"2025-12-01",region_code:"NORTH",filters:["排除测试账号","按下单时间归属"]}})',context);
  const values=vm.runInContext('$("#context-values").children[0].textContent',context);
  assert.ok(values.includes('地区 / 渠道筛选')&&values.includes('地区：NORTH'));
  assert.ok(values.includes('口径规则')&&values.includes('排除测试账号'));
  assert.ok(values.includes('2025-11-01 至 2025-12-01（不含结束日）'));
  vm.runInContext('renderContext({scope:{metric:"净销售额"}})',context);
  const defaults=vm.runInContext('$("#context-values").children[0].textContent',context);
  assert.ok(defaults.includes('未追加地区或渠道限制'));
  assert.ok(defaults.includes('沿用指标默认规则，见本轮报告详情'));
});

test('export links reference saved report and selected actual table',()=>{
  const payload={answer:'结果',reflection_status:'skipped',archive_status:'saved',trace_id:'report-id',
    tables:[{id:'result-1',title:'精确金额',columns:['区域','金额'],rows:[['华东','9007199254740993.25']],evidence_ids:['E1'],scope:{}}]};
  const {entry}=rendered(payload,{api_prefix:'/api/v1',xlsx_available:true});
  const links=descendants(entry,'a');
  assert.equal(links.length,3);
  assert.ok(links.some(link=>link.href==='/api/v1/analyses/report-id/export?format=csv&table_id=result-1'));
  assert.ok(entry.textContent.includes('9007199254740993.25'));
});

test('unconfirmed save keeps answer visible without broken export links',()=>{
  const {entry}=rendered({answer:'已生成答案',reflection_status:'skipped',archive_status:'unconfirmed',archive_error:'请刷新历史核对'},
    {api_prefix:'/api/v1',xlsx_available:true});
  assert.equal(descendants(entry,'a').length,0);
  assert.ok(entry.textContent.includes('已生成答案')&&entry.textContent.includes('请刷新历史核对'));
});

test('newest history selection wins even when the older request finishes later',async()=>{
  const pending={};
  const {context}=runtime(path=>path==='/workspace-config'?new Promise(()=>{}):new Promise(resolve=>{pending[path]=resolve;}));
  context.document.createElement=tag=>new Element(tag);
  vm.runInContext('state.config={api_prefix:"/api/v1",analysis_history_available:true}',context);
  const old=vm.runInContext('restoreConversation("old")',context);
  const latest=vm.runInContext('restoreConversation("latest")',context);
  pending['/api/v1/conversations/latest'](response({session_id:'latest',analysis_state:{},reports:[]}));await latest;
  pending['/api/v1/conversations/old'](response({session_id:'old',analysis_state:{},reports:[]}));await old;
  assert.equal(vm.runInContext('state.session',context),'latest');
  assert.equal(vm.runInContext('state.restoring',context),false);
});

test('new conversation cancels pending restoration and prevents stale conditions returning',async()=>{
  let resolve;
  const {context,fields}=runtime(path=>path==='/workspace-config'?new Promise(()=>{}):new Promise(done=>{resolve=done;}));
  context.document.createElement=tag=>new Element(tag);
  vm.runInContext('state.config={api_prefix:"/api/v1",analysis_history_available:true}',context);
  const pending=vm.runInContext('restoreConversation("old")',context);
  fields.get('#new-chat').listeners.click();
  resolve(response({session_id:'old',analysis_state:{scope},reports:[]}));await pending;
  assert.equal(vm.runInContext('state.session',context),null);
  assert.equal(fields.get('#analysis-context').hidden,true);
});

test('history replay is read-only and cannot submit a model request',async()=>{
  const calls=[];
  const {context}=runtime(path=>{
    if(path==='/workspace-config')return new Promise(()=>{});
    calls.push(path);return Promise.resolve(response({session_id:'saved',analysis_state:{},reports:[{question:'历史问题',answer:'历史答案',reflection_status:'failed'}]}));
  });
  context.document.createElement=tag=>new Element(tag);
  vm.runInContext('state.config={api_prefix:"/api/v1",analysis_history_available:true}',context);
  await vm.runInContext('restoreConversation("saved")',context);
  assert.deepEqual(calls,['/api/v1/conversations/saved']);
});

test('review CSV preserves quoted literal cells, leading zeroes and missing values',()=>{
  const {context}=runtime(()=>new Promise(()=>{}));
  context.csv='编号,金额,备注\r\n001,12.30,"含,逗号"\r\n002,,"引号""和\n换行"';
  const parsed=vm.runInContext('parseLiteralCSV(csv)',context);
  assert.deepEqual(JSON.parse(JSON.stringify(parsed.rows)),[['001','12.30','含,逗号'],['002',null,'引号"和\n换行']]);
  context.table={...JSON.parse(JSON.stringify(parsed)),title:'x',notes:''};
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(parseLiteralCSV(literalCSV(table)))',context)),JSON.parse(JSON.stringify(parsed)));
});

test('review CSV rejects malformed quotes, repeated headers and inconsistent widths',()=>{
  const {context}=runtime(()=>new Promise(()=>{}));
  for(const value of ['a,a\n1,2','a,b\n1','a\n"unclosed','a\n"closed"suffix']){
    context.csv=value;assert.throws(()=>vm.runInContext('parseLiteralCSV(csv)',context));
  }
});
