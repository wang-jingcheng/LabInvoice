const admin = {data:null, page:'invoices', scope:'all', projectId:null, selected:new Set(), collapsedCategories:new Set(), saving:false, guideDraft:null, guideOriginal:null, guideFormat:'text',guideOriginalFormat:'text', listPage:0};
const projectFor = id => admin.data.projects.find(p => p.id === Number(id));
const activeProjects = () => admin.data.projects.filter(project => !project.is_archived);
const invoiceFor = id => admin.data.invoices.find(r => r.id === id);
const totalMoney = rows => money(admin.data?.loading?null:rows.some(r=>r.amount_pending)?null:rows.reduce((n,r)=>n+r.amount_cents,0));
const workbenchRows = () => admin.data.invoices.filter(row => !row.task_completed);
const activeManualExpenses = () => (admin.data?.manual_expenses||[]).filter(e=>!e.task_completed);
const manualFor = id => admin.data.manual_expenses.find(e=>e.id===id);
const manualTotal = entries => entries.reduce((n,e)=>n+e.amount_cents,0);
const selectable = row => row.assignable || row.project_id !== null;
const formFooter = label => `<div class="dialog-actions"><button class="button secondary" type="button" data-close-dialog>取消</button><button class="button primary" type="submit">${label}</button></div>`;
const emptyState = (title,text) => `<div class="empty-state">${icon('folder')}<h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;
const collectionState = task => task.is_completed?'已结项':task.is_collection_closed?'收集已结束 · 待结项':'收集中';

async function updateTask(action) {
  const previous=admin.taskMutation;
  if(previous)await previous;
  const update=(async()=>{await action();await loadAdmin();})();
  admin.taskMutation=update;
  try{await update;}finally{if(admin.taskMutation===update)admin.taskMutation=null;}
}

function taskRenameDialog(task) {
  openDialog('重命名收集任务',`<form id="dialogForm"><label>任务名称<input name="title" required maxlength="120" value="${esc(task.title)}"></label>${formFooter('保存任务名称')}</form>`,{submit:async form=>{
    await updateTask(async()=>{await api(`/api/admin/window/${task.id}`,{method:'PATCH',body:{title:form.get('title')}});closeDialog();});toast('收集任务名称已更新');
  }});
}

function taskStateButtons(task) {
  const button=(action,label,style='secondary')=>`<button class="button ${style} small" data-action="task-state" data-task-state="${action}" data-id="${task.id}">${label}</button>`;
  if(task.is_completed)return button('uncomplete','撤销结项');
  if(task.is_collection_closed)return button('reopen','重新开放收集')+button('complete','结项','primary');
  return button('close','结束收集','primary');
}

async function loadAdmin() {
  const data = await api('/api/admin/workbench');
  admin.data = data;
  const active = workbenchRows();
  admin.selected = new Set([...admin.selected].filter(id => active.some(r => r.id === id)));
  if (admin.scope === 'project' && !activeProjects().some(project => project.id === admin.projectId)) {
    admin.scope = 'all';
    admin.projectId = null;
    admin.selected.clear();
    admin.listPage = 0;
  }
  $('#navInvoiceCount').textContent = active.length;
  $('#navTaskCount').textContent = data.windows.filter(w=>!w.is_completed).length;
  $('#adminWarnings').innerHTML = data.warnings.length ? `<div class="alert error">${esc(data.warnings.join('\n'))}</div>` : '';
  updateSelect($('#taskFilter'),data.windows.filter(w=>!w.is_completed).map(w=>[w.id,w.title]),'全部未结项任务');
  const ownerNames = new Map([...active, ...activeManualExpenses()].map(row => [row.owner, row.real_name]));
  updateSelect($('#ownerFilter'), [...ownerNames], '全部成员');
  updateCategoryFilter(); renderProjects(); renderProjectBanner(); renderTable();renderCollectionSummary();
  if(admin.page!=='invoices') renderManagement();
}

function updateSelect(select, values, placeholder) {
  const previous = select.value;
  select.innerHTML = `<option value="">${esc(placeholder)}</option>`+values.map(([value,label])=>`<option value="${esc(value)}">${esc(label)}</option>`).join('');
  if(values.some(([value])=>String(value)===previous)) select.value=previous;
}

function renderCollectionSummary() {
  if(!admin.data||admin.data.loading)return;
  const taskId=Number($('#taskFilter').value),tasks=(admin.data.task_summaries||[]).filter(t=>!t.is_completed&&(!taskId||t.id===taskId));
  const pending=tasks.reduce((n,t)=>n+t.pending_amount_count,0),total=tasks.reduce((n,t)=>n+t.known_amount_cents,0);
  const allocated=workbenchRows().filter(r=>r.submission_status===2&&r.project_id!==null&&(!taskId||r.task_id===taskId));
  const manual=activeManualExpenses().filter(e=>!taskId||e.task_id===taskId),allocatedManual=manual.filter(e=>e.project_id!==null);
  $('#collectionTaskCount').textContent=`${tasks.length} 个任务`;
  $('#collectionTotal').innerHTML=`<span>¥</span>${money(pending?null:total)}`;
  $('#collectionSummaryNote').textContent=`${tasks.reduce((n,t)=>n+t.invoice_count,0)} 张正式提交发票${pending?` · ${pending} 张金额待填写，已填写 ¥ ${money(total)}`:''}`;
  if(manual.length)$('#collectionSummaryNote').textContent+=` · 另有 ${manual.length} 项无电子发票报销 ¥ ${money(manualTotal(manual))}`;
  $('#taskAmountList').innerHTML=tasks.length?tasks.map(t=>`<article class="task-amount-card" data-task-summary="${t.id}"><div class="task-amount-head"><button data-action="filter-summary-task" data-id="${t.id}" title="只看此任务">${esc(t.title)}</button><strong class="amount">¥ ${money(t.amount_cents)}</strong></div><span class="task-amount-status">${t.is_collection_closed?'收集已结束 · 待结项':'收集中'} · ${t.invoice_count} 张已提交</span><dl class="task-category-amounts">${t.categories.map(c=>`<div><dt>${esc(c.name)}</dt><dd class="amount">${money(c.amount_cents)}</dd></div>`).join('')}</dl>${t.manual_count?`<div class="task-manual-totals"><span>无电子发票报销 ${t.manual_count} 项 ¥ ${money(t.manual_amount_cents)}</span>${t.manual_categories.map(c=>`<div><span>${esc(c.name)}</span><span class="amount">${money(c.amount_cents)}</span></div>`).join('')}<strong>任务报销合计 ¥ ${money(t.reimbursement_amount_cents)}</strong></div>`:''}</article>`).join(''):'<p class="muted">当前没有未结项的收集任务。</p>';
  $('#reimbursementTotal').innerHTML=`<span>¥</span>${totalMoney([...allocated,...allocatedManual])}`;
  $('#reimbursementSummaryNote').textContent=`${allocated.length} 张发票${allocatedManual.length?` · ${allocatedManual.length} 项无电子发票报销`:''}`;
  const projects=admin.data.projects.map(p=>({project:p,rows:[...allocated,...allocatedManual].filter(r=>r.project_id===p.id)})).filter(p=>p.rows.length);
  $('#reimbursementBreakdown').innerHTML=projects.length?projects.map(({project:p,rows})=>`<div class="reimbursement-project"><div><strong>${esc(p.name)}</strong><small>${esc(p.code||'未填写项目号')}</small></div><span class="amount">¥ ${totalMoney(rows)}</span></div>`).join(''):'<p class="muted">本次暂无已归入经费项目的正式提交票据。</p>';
}

function scopedRows() {
  return workbenchRows().filter(row=>admin.scope==='project' ? row.project_id===admin.projectId : admin.scope==='unassigned' ? row.project_id===null && row.assignable : true);
}

function visibleManualExpenses() {
  const tid=Number($('#taskFilter').value),category=$('#categoryFilter').value,owner=$('#ownerFilter').value,search=$('#invoiceSearch').value.trim().toLocaleLowerCase();
  return activeManualExpenses().filter(e=>(admin.scope==='project'?e.project_id===admin.projectId:admin.scope==='unassigned'?e.project_id===null:true)&&(!tid||e.task_id===tid)&&(!category||e.category===category)&&(!owner||e.owner===owner)&&(!search||[e.real_name,e.category,e.remark,e.task_title].join(' ').toLocaleLowerCase().includes(search)));
}

function renderManualExpenses() {
  const entries=visibleManualExpenses(),host=$('#manualExpenseSection');
  const groups=[...new Set(entries.map(entry=>entry.category))].map(category=>{
    const collapsed=admin.collapsedCategories.has(category);
    const cards=collapsed?'':entries.filter(entry=>entry.category===category).map(entry=>`
      <article class="manual-expense-card" data-manual-expense="${entry.id}" draggable="true">
        <div>
          <strong>${esc(entry.real_name)}</strong>
          <p class="muted">${esc(entry.task_title)} · ${esc(projectFor(entry.project_id)?.name||'待归类')}</p>
          ${entry.remark?`<p class="manual-expense-remark">${esc(entry.remark)}</p>`:''}
        </div>
        <div class="manual-expense-actions">
          <strong class="amount">¥ ${money(entry.amount_cents)}</strong>
          <div class="row">
            <button class="button soft small" data-action="allocate-manual-expense" data-id="${entry.id}">${entry.project_id===null?'归入项目':'调整归类'}</button>
            <button class="icon-button" data-action="edit-manual-expense" data-id="${entry.id}" title="编辑报销项" aria-label="编辑 ${esc(entry.real_name)} 的报销项">${icon('edit')}</button>
            <button class="button ghost small danger-text" data-action="delete-manual-expense" data-id="${entry.id}">删除</button>
          </div>
        </div>
      </article>`).join('');
    return `<div class="manual-expense-group"><button class="category-fold-button" data-action="toggle-category-fold" data-category="${esc(category)}" aria-expanded="${!collapsed}">${icon('chevron-down')}${esc(category)}</button>${cards}</div>`;
  });
  host.innerHTML=`<div class="panel-heading"><h2>无电子发票报销项 <span class="count-pill">${entries.length} 项</span></h2><span class="amount">¥ ${money(manualTotal(entries))}</span></div>${groups.join('')||'<p class="manual-empty muted">当前无报销项</p>'}`;
}

function expenseCategoryList(current='') {
  return [...new Set([current,'劳务费',...admin.data.invoices.map(r=>r.category),...admin.data.projects.flatMap(p=>p.categories.map(c=>c.name)),...(admin.data.manual_expenses||[]).map(e=>e.category)])].filter(Boolean);
}

function manualExpenseDialog(entry=null) {
  const tasks=admin.data.windows.filter(w=>!w.is_completed),taskId=entry?.task_id||Number($('#taskFilter').value)||tasks[0]?.id;
  if(!tasks.length){toast('请先创建或重新开放一个未结项收集任务',true);return;}
  const pid=entry?entry.project_id:admin.scope==='project'?admin.projectId:null;
  const members = [...admin.data.users];
  if (entry && !members.some(member => member.id === entry.user_id)) {
    members.push({id: entry.user_id, real_name: entry.real_name, username: entry.username + ' · 已删除'});
  }
  openDialog(entry?'编辑无电子发票报销项':'新增无电子发票报销项',`<p class="dialog-help">用于纸质发票未上传电子文件或劳务费等手工登记情况，不表示没有纸质发票。</p><form id="dialogForm" class="stack"><div class="form-grid"><label>收集任务<select name="task_id" required>${tasks.map(t=>`<option value="${t.id}" ${taskId===t.id?'selected':''}>${esc(t.title)}</option>`).join('')}</select></label><label>成员<select name="user_id" required>${members.map(u=>`<option value="${u.id}" ${entry?.user_id===u.id?'selected':''}>${esc(u.real_name)}（${esc(u.username)}）</option>`).join('')}</select></label></div><label>报销项<input name="category" maxlength="80" required value="${esc(entry?.category||'劳务费')}" list="expenseCategoryOptions"></label><datalist id="expenseCategoryOptions">${expenseCategoryList(entry?.category).map(v=>`<option value="${esc(v)}"></option>`).join('')}</datalist><label>金额（元）<input name="amount" type="number" min="0" max="9999999999.99" step="0.01" required value="${entry?(entry.amount_cents/100).toFixed(2):''}"></label><label>经费项目<select name="project_id"><option value="">暂不归入项目</option>${activeProjects().map(p=>`<option value="${p.id}" ${p.id===pid?'selected':''}>${esc(p.name)}${p.code?' · '+esc(p.code):''}</option>`).join('')}</select></label><label>备注说明<textarea name="remark" rows="3" maxlength="4000" placeholder="例如：参与实验人数、工作内容或计算依据">${esc(entry?.remark||'')}</textarea></label>${formFooter('保存报销项')}</form>`,{submit:async form=>{
    await api(entry?`/api/admin/manual-expenses/${entry.id}`:'/api/admin/manual-expenses',{method:entry?'PATCH':'POST',body:{task_id:Number(form.get('task_id')),user_id:Number(form.get('user_id')),category:form.get('category'),amount:form.get('amount'),project_id:form.get('project_id')?Number(form.get('project_id')):null,remark:form.get('remark'),...(entry?{version:entry.version}:{})}});
    closeDialog();await loadAdmin();toast('无电子发票报销项已保存，报销汇总已更新');
  }});
}

async function allocateManualExpense(entry,projectId,category=entry.category) {
  await api(`/api/admin/manual-expenses/${entry.id}`,{method:'PATCH',body:{task_id:entry.task_id,user_id:entry.user_id,category,amount:(entry.amount_cents/100).toFixed(2),remark:entry.remark,project_id:projectId,version:entry.version}});
  await loadAdmin();toast(projectId===null?'已移回待归类':'无电子发票报销项已归入经费项目');
}

function manualAllocationDialog(entry) {
  openDialog('归类无电子发票报销项',`<p class="dialog-help">${esc(entry.real_name)} · ¥ ${money(entry.amount_cents)}。归类会保留金额、备注和任务归属。</p><form id="dialogForm" class="stack"><label>经费项目<select name="project_id"><option value="">移回待归类</option>${activeProjects().map(p=>`<option value="${p.id}" ${p.id===entry.project_id?'selected':''}>${esc(p.name)}${p.code?' · '+esc(p.code):''}</option>`).join('')}</select></label><label>报销项<input name="category" required maxlength="80" value="${esc(entry.category)}" list="allocationCategoryOptions"></label><datalist id="allocationCategoryOptions">${expenseCategoryList(entry.category).map(v=>`<option value="${esc(v)}"></option>`).join('')}</datalist>${formFooter('保存归类')}</form>`,{submit:async form=>{
    await allocateManualExpense(entry,form.get('project_id')?Number(form.get('project_id')):null,form.get('category'));closeDialog();
  }});
}

function updateCategoryFilter() {
  const extra=admin.scope==='project'?(projectFor(admin.projectId)?.categories||[]).map(c=>c.name):[];
  const task=Number($('#taskFilter').value);
  const manual=activeManualExpenses().filter(e=>(!task||e.task_id===task)&&(admin.scope==='project'?e.project_id===admin.projectId:admin.scope==='unassigned'?e.project_id===null:true));
  updateSelect($('#categoryFilter'),[...new Set([...scopedRows().filter(r=>!task||r.task_id===task).map(r=>r.category),...manual.map(e=>e.category),...extra])].sort().map(v=>[v,v]),'全部报销项');
}

function visibleRows() {
  const search = $('#invoiceSearch').value.trim().toLocaleLowerCase();
  const task = $('#taskFilter').value, category = $('#categoryFilter').value, owner = $('#ownerFilter').value;
  return scopedRows().filter(row=>(!task||row.task_id===Number(task))&&(!category||row.category===category)&&(!owner||row.owner===owner)&&(!search||[row.filename,row.real_name,row.category,row.original_category,row.remark,row.task_title].join(' ').toLocaleLowerCase().includes(search)));
}

function renderTable() {
  if (!admin.data || admin.data.loading) return;
  const rows=visibleRows().sort((a,b)=>a.category.localeCompare(b.category,'zh-CN')||a.real_name.localeCompare(b.real_name,'zh-CN')), groups=new Map();
  const expanded=rows.filter(r=>!admin.collapsedCategories.has(r.category));
  const pageSize=window.matchMedia('(max-width:760px)').matches?15:30;
  admin.listPage=Math.min(admin.listPage,Math.max(0,Math.ceil(expanded.length/pageSize)-1));
  const start=admin.listPage*pageSize,pageRows=expanded.slice(start,start+pageSize);
  for(const row of rows){if(!groups.has(row.category))groups.set(row.category,[]);}
  for(const row of pageRows){groups.get(row.category).push(row);}
  const scopeLabel=admin.scope==='project'?'项目内票据':admin.scope==='unassigned'?'待归类发票':'全部票据';
  $('#listTitle').innerHTML=`${esc(scopeLabel)} <span class="count-pill">${rows.length}</span>`;
  $('#listSubtitle').textContent=admin.scope==='project'?'未结项任务 · 项目汇总含历史金额':'未结项任务';
  $('#showAll').setAttribute('aria-pressed',String(admin.scope==='all'));
  $('#showUnassigned').setAttribute('aria-pressed',String(admin.scope==='unassigned'));
  $('#showCurrentProject').hidden=admin.scope!=='project';$('#showCurrentProject').setAttribute('aria-pressed',String(admin.scope==='project'));
  $('#invoiceRows').innerHTML=rows.length?[...groups].map(([category,items])=>{
    const allOfCategory=rows.filter(r=>r.category===category),eligible=allOfCategory.filter(selectable),checked=eligible.length>0&&eligible.every(r=>admin.selected.has(r.id));
    const collapsed=admin.collapsedCategories.has(category);
    return `<tr class="group-row"><td colspan="6"><div class="row"><input type="checkbox" aria-label="选择 ${esc(category)} 的全部可整理发票" data-select-group="${esc(category)}" ${checked?'checked':''} ${!eligible.length?'disabled':''}><button class="category-fold-button" data-action="toggle-category-fold" data-category="${esc(category)}" aria-expanded="${!collapsed}">${icon('chevron-down')}${esc(category)}</button><span class="count-pill">${allOfCategory.length} 张</span><span class="group-total">¥ ${totalMoney(allOfCategory)}</span>${!collapsed&&!items.length?'<small class="category-page-note">切换分页查看票据</small>':''}</div></td></tr>`+items.map(row=>{
      const project=projectFor(row.project_id);
      return `<tr class="invoice-row ${admin.selected.has(row.id)?'selected':''}" data-invoice="${row.id}" draggable="${selectable(row)}">
        <td><input type="checkbox" data-select-invoice="${row.id}" aria-label="选择 ${esc(row.filename)}" ${admin.selected.has(row.id)?'checked':''} ${!selectable(row)?'disabled':''}></td>
        <td><div class="invoice-file"><span class="pdf-icon" aria-hidden="true">${row.filename.toLowerCase().endsWith('.pdf')?'PDF':'IMG'}</span><div class="file-text">
          <a class="filename" href="${esc(row.url)}" target="_blank" rel="noopener" title="${esc(row.filename)}">${esc(row.filename.replace(/^主发票_/,''))}</a>
          <span class="filename-meta" title="${esc(row.task_title)}">${esc(row.task_title)}${row.original_category!==row.category?' · 原：'+esc(row.original_category):''}</span>
          ${row.missing?'<span class="tag red">原文件缺失</span>':!row.assignable?'<span class="tag gray">尚未正式提交</span>':row.amount_pending?'<span class="tag amber">金额待填写</span>':row.amount_cents===0?'<span class="tag amber">金额待核对</span>':''}
        </div></div></td>
        <td><span class="name-cell"><span class="avatar small">${esc(row.real_name.slice(0,1))}</span>${esc(row.real_name)}</span></td>
        <td class="cell-project" title="${esc(project?.name||'待归类')}">${project?esc(project.name):'<span class="tag gray">待归类</span>'}</td>
        <td class="amount">${money(row.amount_pending?null:row.amount_cents)}</td>
        <td><button class="icon-button" data-action="invoice-detail" data-id="${row.id}" title="查看并调整发票" aria-label="查看并调整 ${esc(row.filename)}">${icon('eye')}</button></td>
      </tr>`;
    }).join('');
  }).join(''):`<tr><td colspan="6">${emptyState('这里暂时没有票据',admin.scope==='project'?'从全部票据中勾选发票，再归入此项目。':'可以调整筛选条件，或等待成员提交发票。')}</td></tr>`;
  const eligible=rows.filter(selectable), chosen=eligible.filter(r=>admin.selected.has(r.id));
  $('#selectAllInvoices').checked=!!eligible.length&&chosen.length===eligible.length;
  $('#selectAllInvoices').indeterminate=chosen.length>0&&chosen.length<eligible.length;
  $('#selectAllInvoices').disabled=!eligible.length;
  $('#mobileSelectAll').checked=$('#selectAllInvoices').checked;
  $('#mobileSelectAll').indeterminate=$('#selectAllInvoices').indeterminate;
  $('#mobileSelectAll').disabled=!eligible.length;
  $('#tableCount').textContent=`${expanded.length?start+1:0}–${Math.min(start+pageSize,expanded.length)} / ${rows.length} 张${expanded.length!==rows.length?` · 已折叠 ${rows.length-expanded.length} 张`:''} · ${rows.filter(r=>!r.assignable).length} 张尚未提交`;
  $('#filteredTotal').textContent=totalMoney(rows);
  $('#invoicePagination').hidden=expanded.length<=pageSize;
  $('#invoicePagination').innerHTML=`<button class="button secondary small" data-action="page-prev" ${admin.listPage===0?'disabled':''}>上一页</button><span>${admin.listPage+1} / ${Math.max(1,Math.ceil(expanded.length/pageSize))}</span><button class="button secondary small" data-action="page-next" ${(admin.listPage+1)*pageSize>=expanded.length?'disabled':''}>下一页</button>`;
  renderBulk();
  renderManualExpenses();
}

function renderProjects() {
  const projects = activeProjects();
  const list = $('#projectList');
  const previousScroll = list.scrollTop;
  $('#projectArchiveButton').disabled = Boolean(admin.data.loading);
  $('#projectArchiveCount').textContent = admin.data.projects.length - projects.length;
  list.innerHTML=projects.length?projects.map(project=>{
    const current=admin.projectId===project.id&&admin.scope==='project';
    const categories=project.categories.map(category=>`
      <div class="category-manage-row">
        <button class="category-target" data-action="open-category" data-id="${project.id}" data-category="${category.id}" data-drop-project="${project.id}" data-drop-category="${category.id}">
          <span>${esc(category.name)} <small>${category.invoice_count} 张${category.manual_count?` + ${category.manual_count} 项`:''}</small></span>
          <span class="mono">${money(category.amount_cents)}</span>
        </button>
        <button class="icon-button" data-action="rename-category" data-id="${category.id}" title="编辑报销项" aria-label="编辑报销项 ${esc(category.name)}">${icon('edit')}</button>
      </div>`).join('');
    return `<article class="project-card ${current?'active':''}" data-drop-project="${project.id}">
      <button class="project-title" aria-pressed="${current}" data-action="open-project" data-id="${project.id}">
        <span class="folder-top">${icon('folder')}<strong>${esc(project.name)}</strong></span>
        ${project.code?`<span class="project-code">${esc(project.code)}</span>`:''}
      </button>
      <div class="project-card-total"><strong>¥ ${money(project.total_cents)}</strong><small>${project.invoice_count} 张发票${project.manual_count?` + ${project.manual_count} 项无电子发票`:''}</small></div>
      ${categories?`<div class="project-categories">${categories}</div>`:''}
      <div class="project-card-actions">
        <button class="button ghost small" data-action="new-category" data-id="${project.id}">${icon('plus')}报销项</button>
        <button class="button secondary small" data-action="edit-project" data-id="${project.id}">管理项目</button>
      </div>
    </article>`;
  }).join(''):`<div class="project-list-empty">${emptyState('暂无待整理经费项目','新建经费项目后即可归类票据。')}</div>`;
  list.scrollTop = previousScroll;
}

function renderProjectBanner() {
  const p=admin.scope==='project'?projectFor(admin.projectId):null;
  $('#projectBanner').innerHTML=p?`<div class="project-banner compact-project-banner">
    <div>
      ${p.code?`<span class="project-code">${esc(p.code)}</span>`:''}
      <h2 tabindex="-1">${esc(p.name)}</h2>
      <small>累计 ${p.invoice_count} 张发票${p.manual_count?` + ${p.manual_count} 项无电子发票`:''} · ¥ ${money(p.total_cents)} · ${p.users.length} 位成员</small>
    </div>
    <div class="row wrap">
      <button class="button secondary small" data-action="return-all-invoices">返回全部票据</button>
      <button class="button secondary small" data-action="project-summary" data-id="${p.id}">汇总明细</button>
      <button class="button secondary small" data-action="download-project" data-id="${p.id}">${icon('download')}打包下载</button>
      <button class="button primary small" data-action="print-project" data-id="${p.id}">${icon('print')}合并打印</button>
      <button class="icon-button" data-action="edit-project" data-id="${p.id}" title="编辑项目及报销项" aria-label="编辑项目及报销项">${icon('edit')}</button>
    </div>
  </div>`:'';
}

function focusProjectBanner() {
  $('#projectBanner h2')?.focus();
}

function renderBulk() {
  const selected=[...admin.selected].map(invoiceFor).filter(Boolean),bar=$('#bulkBar');
  const previous=$('#targetProject')?.value, previousCategory=$('#targetCategory')?.value;
  bar.hidden=!selected.length;
  if(!selected.length){bar.innerHTML='';return;}
  bar.innerHTML=`<div class="bulk-info">已选 ${selected.length} 张发票<small>¥ ${totalMoney(selected)}</small></div><select id="targetProject" aria-label="目标经费项目"><option value="">选择经费项目</option>${activeProjects().map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select><select id="targetCategory" aria-label="目标报销项"></select><button class="button primary" data-action="allocate-selected">归入项目 ${icon('arrow')}</button><button class="button ghost small" data-action="unassign-selected">移回待归类</button><button class="icon-button" data-action="clear-selection" aria-label="取消选择">${icon('close')}</button>`;
  const previousIsActive = previous && activeProjects().some(p => String(p.id) === previous);
  $('#targetProject').value = previousIsActive ? previous : admin.scope === 'project' ? String(admin.projectId) : '';
  updateTargetCategories(previousCategory);
}

function updateTargetCategories(previous='') {
  const p=projectFor($('#targetProject')?.value);
  $('#targetCategory').innerHTML='<option value="">按当前报销项自动归类</option>'+(p?.categories||[]).map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');
  if(p?.categories.some(c=>String(c.id)===previous))$('#targetCategory').value=previous;
}

function setScope(scope,id=null,categoryId=null) {
  admin.scope=scope;admin.projectId=id;admin.selected.clear();
  admin.listPage=0;
  ['invoiceSearch','categoryFilter','ownerFilter'].forEach(name=>$('#'+name).value='');
  updateCategoryFilter();
  if(categoryId){const category=projectFor(id)?.categories.find(c=>c.id===categoryId);if(category)$('#categoryFilter').value=category.name;}
  renderProjectBanner();renderProjects();renderTable();
}

async function allocateInvoices(ids,pid,cid=null) {
  if(admin.saving)return;
  admin.saving=true;
  try{
    await api('/api/admin/allocate',{method:'POST',body:{invoice_ids:ids,project_id:pid,category_id:cid,versions:Object.fromEntries(ids.map(id=>[id,invoiceFor(id)?.version||'']))}});
    admin.selected.clear();await loadAdmin();toast(pid===null?'已移回待归类':`已整理 ${ids.length} 张发票`);
  }finally{admin.saving=false;}
}

function projectFields(value={},suggestions=true) {
  return [['name','项目名称','例如：脑科学研究项目'],['code','项目号','输入经费项目号（选填）']].map(([key,label,placeholder])=>`<div class="project-field"><label for="project-${key}">${label}</label><div class="history-input-wrap"><input id="project-${key}" name="${key}" maxlength="120" placeholder="${placeholder}" value="${esc(value[key]||'')}" autocomplete="off" ${suggestions?`data-project-history="${key}" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="history-${key}"`:''}>${suggestions?`<div class="history-options" id="history-${key}" role="listbox" aria-label="${label}历史记录" hidden></div>`:''}</div></div>`).join('');
}

function hideProjectSuggestions() {
  $$('.history-options').forEach(list=>list.hidden=true);
  $$('[data-project-history]').forEach(input=>{input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');});
}

function showProjectSuggestions(input) {
  const query=input.value.trim().toLocaleLowerCase(),key=input.dataset.projectHistory,list=$('#history-'+key);
  const matches=(admin.projectHistory||[]).filter(h=>[h.name,h.code].some(v=>v.toLocaleLowerCase().includes(query))).slice(0,20);
  hideProjectSuggestions();input.dataset.activeOption='-1';
  list.innerHTML=matches.map(h=>`<button type="button" role="option" aria-selected="false" id="history-${key}-${h.id}" data-history-pick="${h.id}"><strong>${esc(h.name)}</strong><small>${esc(h.code||'未填写项目号')}</small></button>`).join('');
  list.hidden=!matches.length;input.setAttribute('aria-expanded',String(!!matches.length));
}

function selectProjectHistory(id) {
  const entry=admin.projectHistory.find(h=>h.id===id);if(!entry)return;
  $('#project-name').value=entry.name;$('#project-code').value=entry.code;hideProjectSuggestions();
}

async function projectDialog(project=null) {
  admin.projectHistory=await api('/api/admin/project-history');
  openDialog(project?'编辑经费项目':'新建经费项目',`<p class="dialog-help">项目可跨收集任务使用。名称或项目号至少填写一项；选择历史记录可同时填入名称与项目号。</p><form id="dialogForm" class="stack"><div class="history-toolbar"><span class="muted">已保存 ${admin.projectHistory.length} 条填写记录</span><button type="button" class="button ghost small" data-action="manage-project-history">管理历史记录</button></div>${projectFields(project||{})}${formFooter(project?'保存项目':'创建项目')}</form>${project?`<div class="outline-note">报销项改名会同步更新此项目内的归类显示，原上传报销项仍保留。</div><div class="project-category-editor">${project.categories.map(c=>`<div class="category-editor-row"><span>${esc(c.name)} <small class="muted">${c.invoice_count} 张</small></span><div><button class="button ghost small" data-action="rename-category" data-id="${c.id}">编辑</button><button class="button ghost small danger-text" data-action="delete-category" data-id="${c.id}">删除</button></div></div>`).join('')}</div><button class="button soft small" data-action="new-category" data-id="${project.id}">＋ 新建报销项</button><div class="project-removal"><p>撤销项目后，其中票据移回待归类，原文件和金额保留。</p><button class="button danger small" data-action="delete-project" data-id="${project.id}">${project.invoice_count?'撤销项目并移回票据':'撤销建立 / 删除项目'}</button></div>`:''}`,{submit:async form=>{
    const result=await api(project?`/api/admin/projects/${project.id}`:'/api/admin/projects',{method:project?'PATCH':'POST',body:{name:form.get('name'),code:form.get('code')}});
    closeDialog();await loadAdmin();
    if(!project){if(admin.selected.size){$('#targetProject').value=String(result.id);updateTargetCategories();}else setScope('project',result.id);}
    toast(project?'项目已更新':'经费项目已创建');
  }});
}

function categoryDialog(pid,category=null) {
  openDialog(category?'编辑报销项':'新建报销项',`<p class="dialog-help">经费项目：${esc(projectFor(pid).name)}</p><form id="dialogForm"><label>报销项名称<input name="name" maxlength="80" required placeholder="例如：设备配件费" value="${esc(category?.name||'')}"></label>${formFooter(category?'保存名称':'创建报销项')}</form>${category?`<div class="project-removal"><p>删除后，该项票据移回待归类，原文件和已核对金额保留。</p><button class="button danger small" data-action="delete-category" data-id="${category.id}">删除报销项</button></div>`:''}`,{submit:async form=>{
    await api(category?`/api/admin/categories/${category.id}`:`/api/admin/projects/${pid}/categories`,{method:category?'PATCH':'POST',body:{name:form.get('name')}});
    closeDialog();await loadAdmin();toast('报销项已保存，可拖入发票或批量归类');
  }});
}

async function historyManager() {
  admin.projectHistory=await api('/api/admin/project-history');
  openDialog('项目填写历史',`<p class="dialog-help">用于新建或编辑项目时快速填写。修改或删除历史记录，不会改变现有项目。</p><div class="history-records">${admin.projectHistory.length?admin.projectHistory.map(h=>`<article class="history-record" data-history-entry="${h.id}"><div><strong>${esc(h.name)}</strong><span class="mono muted">${esc(h.code||'未填写项目号')}</span></div><div class="row"><button class="button secondary small" data-action="edit-project-history" data-id="${h.id}">编辑</button><button class="button ghost small danger-text" data-action="delete-project-history" data-id="${h.id}">删除</button></div></article>`).join(''):emptyState('暂无填写历史','创建或保存经费项目后，将自动记录名称和项目号。')}</div><div class="dialog-actions"><button class="button secondary" data-close-dialog>关闭</button></div>`);
}

function historyEditDialog(id) {
  const entry=admin.projectHistory.find(h=>h.id===id);
  openDialog('编辑填写历史',`<p class="dialog-help">只修改下次填写时的候选记录，现有项目不受影响。</p><form id="dialogForm" class="stack">${projectFields(entry,false)}${formFooter('保存历史记录')}</form>`,{submit:async form=>{
    await api(`/api/admin/project-history/${id}`,{method:'PATCH',body:{name:form.get('name'),code:form.get('code')}});
    await historyManager();toast('填写历史已更新');
  }});
}

function projectArchiveDialog() {
  const projects = admin.data.projects.filter(p => p.is_archived).reverse();
  const records = projects.map(project => `<article class="history-record project-archive-record" data-archived-project="${project.id}">
    <div>
      <strong>${esc(project.name)}</strong>
      <span class="mono muted">${esc(project.code || '未填写项目号')}</span>
      ${project.task_titles.length ? `<small class="muted">${esc(project.task_titles.join(' / '))}</small>` : ''}
      <span class="amount">¥ ${money(project.total_cents)} · ${project.invoice_count} 张发票 · ${project.manual_count} 项无电子发票</span>
    </div>
    <div class="row wrap">
      <button class="button secondary small" data-action="project-summary" data-id="${project.id}">汇总明细</button>
      <button class="button secondary small" data-action="download-project" data-id="${project.id}">打包下载</button>
      <button class="button secondary small" data-action="print-project" data-id="${project.id}">合并打印</button>
    </div>
  </article>`).join('');
  openDialog('已归档经费项目', `<div class="history-records">${records || emptyState('暂无已归档项目','经费项目关联的任务全部结项后，项目将显示在这里。')}</div>`, {wide:true});
}

function summaryDialog(project) {
  const archiveLink = project.is_archived ? '<button class="button ghost" data-action="project-archive">返回已归档项目</button>' : '';
  const table=(headers,rows)=>`<table class="data-table"><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.name)}</td><td>${r.count}</td><td class="amount">${money(r.amount)}</td></tr>`).join('')}</tbody></table>`;
  openDialog(project.name+' · 报销汇总',`<div class="summary-total"><span>项目总金额 · ${project.invoice_count} 张发票 + ${project.manual_count||0} 项无电子发票</span><strong>¥ ${money(project.total_cents)}</strong></div><div class="summary-columns"><section><h3>按报销项汇总</h3>${table(['报销项','发票 / 无电子发票','金额（元）'],project.categories.map(c=>({name:c.name,count:`${c.invoice_count} 张 / ${c.manual_count||0} 项`,amount:c.amount_cents})))}</section><section><h3>按成员汇总</h3>${table(['成员','发票 / 无电子发票','金额（元）'],project.users.map(u=>({name:u.real_name,count:`${u.invoice_count} 张 / ${u.manual_count||0} 项`,amount:u.amount_cents})))}</section></div><p class="outline-note">金额为当前整理结果，尚不表示财务审核通过。汇总 CSV 会随原始票据一并打包。</p><div class="dialog-actions wrap">${archiveLink}<button class="button secondary" data-action="download-project" data-id="${project.id}">${icon('download')}打包下载</button><button class="button primary" data-action="print-project" data-id="${project.id}">${icon('print')}合并打印</button></div>`,{wide:true});
}

function invoiceDialog(row) {
  const project=projectFor(row.project_id);
  openDialog('核对发票与金额',`<div class="preview-links"><h3>${esc(row.filename)}</h3><p class="muted">${esc(row.real_name)} · ${esc(row.task_title)}</p><a href="${esc(row.url)}" target="_blank" rel="noopener">${icon('file')} 打开主发票 ↗</a></div><p class="outline-note">原上传报销项：${esc(row.original_category)}<br>当前归类：${project?esc(project.name)+' / '+esc(row.category):'待归类'}</p>${row.remark?`<p class="draft-remark">${esc(row.remark)}</p>`:''}<form id="dialogForm" style="margin-top:20px"><label>报销项<input name="category" maxlength="80" required value="${esc(row.category)}" list="invoiceCategoryOptions"></label><datalist id="invoiceCategoryOptions">${expenseCategoryList(row.category).map(v=>`<option value="${esc(v)}"></option>`).join('')}</datalist><label>本张发票金额（元）<input name="amount" type="number" min="0" max="9999999999.99" step="0.01" placeholder="—" required value="${row.amount_pending?'':(row.amount_cents/100).toFixed(2)}"></label>${formFooter('保存修改')}</form>${row.attachments.length?`<div class="preview-links" style="margin-top:20px"><h3>本批次配套附件</h3>${row.attachments.map(a=>`<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.name)} ↗</a>`).join('')}<small class="muted">同批发票共享附件，项目打印时可按需勾选。</small></div>`:''}`,{submit:async form=>{
    await api('/api/admin/invoices/edit',{method:'POST',body:{invoice_id:row.id,amount:form.get('amount'),category:form.get('category'),version:row.version}});
    closeDialog();await loadAdmin();toast('本张发票金额已保存，相关汇总已更新');
  }});
}

async function printDialog(pid) {
  const manifest=await api(`/api/admin/projects/${pid}/print-manifest`);
  if(!manifest.files.length){toast('此项目没有可打印的发票。无电子发票报销项可随项目打包下载。');return;}
  openDialog(manifest.project.name+' · 合并打印',`<p class="dialog-help">主发票默认选中，附件默认不选。按报销项、成员排序，同批次共享附件只打印一次。可取消不需要打印的文件。</p><form id="dialogForm"><label class="check-label" style="margin-bottom:14px"><input type="checkbox" id="selectPrintAttachments">选中全部可打印附件</label><div class="print-list">${manifest.files.map(file=>`<label class="print-row"><input type="checkbox" name="file_ids" value="${file.id}" data-print-kind="${file.kind}" ${file.selected?'checked':''} ${!file.printable?'disabled':''}><span><strong>${esc(file.name)}</strong><small>${esc(file.owner)} · ${esc(file.category)}${file.missing?' · 文件缺失，请检查':''}${!file.printable?' · 此格式请下载后单独处理':''}</small></span><span class="tag ${file.kind==='invoice'?'':'gray'}">${file.kind==='invoice'?'主发票':'附件'}</span></label>`).join('')}</div><p class="muted" id="printSelectedCount" style="margin-top:12px;font-size:11px"></p>${formFooter('生成合并 PDF')}</form>`,{wide:true,submit:async form=>{
    const blob=await api(`/admin/projects/${pid}/print`,{method:'POST',body:{file_ids:form.getAll('file_ids')},binary:true});
    const url=URL.createObjectURL(blob);
    openDialog('合并 PDF 已生成',`<p class="dialog-help">在 PDF 阅读器中选择打印，或先下载保存。</p><div class="row wrap"><a class="button primary" href="${url}" target="_blank" rel="noopener">${icon('print')}打开 PDF 打印</a><a class="button secondary" href="${url}" download="${esc(manifest.project.name)}-合并打印.pdf">${icon('download')}下载 PDF</a></div><iframe class="pdf-frame" src="${url}" title="项目合并 PDF 预览"></iframe>`,{wide:true,cleanup:()=>URL.revokeObjectURL(url)});
  }});
  const update=()=>$('#printSelectedCount').textContent=`已选 ${$$('input[name="file_ids"]:checked').length} 个文件；不支持打印的附件仍可在 ZIP 中下载。`;
  $('#selectPrintAttachments').addEventListener('change',event=>{$$('input[data-print-kind="attachment"]:not(:disabled)').forEach(input=>input.checked=event.target.checked);update();});
  $$('input[name="file_ids"]').forEach(input=>input.addEventListener('change',update));update();
}

function renderManagement() {
  if(admin.page==='tasks') {
    $('#managementView').innerHTML=`<div class="task-grid">${admin.data.windows.map(task=>{
      const assignments=admin.data.assignments.filter(a=>a.window_id===task.id),done=assignments.filter(a=>a.status===2).length,rows=admin.data.invoices.filter(r=>r.task_id===task.id);
      const percentage=assignments.length?Math.round(done/assignments.length*100):0;
      return `<article class="task-card">
        <div class="between"><span class="tag ${task.is_completed?'gray':task.is_collection_closed?'amber':'submission-completed'}">${collectionState(task)}</span><time class="muted">${esc((task.created_at||'').slice(0,10))}</time></div>
        <h2>${esc(task.title)}</h2>
        <p>${task.task_type==='国内差旅费'?'国内差旅费':'日常业务报销'}</p>
        <div class="task-progress" aria-hidden="true"><span style="width:${percentage}%"></span></div>
        <div class="between"><small class="muted">${done} / ${assignments.length} 人已提交</small><small class="muted">${admin.data.loading?'—':rows.length} 张发票</small></div>
        <div class="task-card-footer"><span class="amount">¥ ${totalMoney([...rows,...(admin.data.manual_expenses||[]).filter(e=>e.task_id===task.id)])}</span><button class="button secondary small" data-action="task-detail" data-id="${task.id}">管理任务 ${icon('arrow')}</button></div>
      </article>`;
    }).join('')||emptyState('还没有收集任务','点击右上方“发布收集任务”，向成员派发任务。')}</div>`;
  } else if(admin.page==='members') {
    const rows=admin.data.users.map(user=>{
      const taskCount=admin.data.assignments.filter(a=>a.user_id===user.id).length;
      return `<tr>
        <td><span class="name-cell"><span class="avatar small" aria-hidden="true">${esc(user.real_name.slice(0,1))}</span>${esc(user.real_name)}</span></td>
        <td class="mono">${esc(user.username)}</td>
        <td>${taskCount} 项</td>
        <td><div class="member-management-actions">
          <button class="icon-button" data-action="edit-member" data-id="${user.id}" title="编辑账户" aria-label="编辑 ${esc(user.real_name)} 的账户">${icon('edit')}</button>
          <button class="icon-button" data-action="member-password" data-id="${user.id}" title="设置密码" aria-label="设置 ${esc(user.real_name)} 的密码">${icon('key')}</button>
          <button class="button ghost small danger-text" data-action="delete-member" data-id="${user.id}" aria-label="删除 ${esc(user.real_name)} 的账户">删除账户</button>
        </div></td>
      </tr>`;
    }).join('')||'<tr><td colspan="4" class="loading">暂无成员</td></tr>';
    $('#managementView').innerHTML=`<section class="panel members-panel">
      <div class="panel-heading"><h2>成员 <span class="count-pill">${admin.data.users.length}</span></h2></div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>真实姓名</th><th>登录账号</th><th>收集任务</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>
    </section>`;
  } else if(admin.page==='guide') renderGuide();
}

async function renderGuide() {
  if(admin.guideDraft===null){
    $('#managementView').innerHTML='<div class="loading">正在读取使用说明…</div>';
    try {const data=await api('/api/admin/guide');admin.guideDraft=data.content;admin.guideOriginal=data.content;admin.guideFormat=data.format;admin.guideOriginalFormat=data.format;}catch(error){toast(error.message,true);return;}
    if(admin.page!=='guide')return;
  }
  $('#managementView').innerHTML=`<section class="panel"><div class="panel-heading"><h2>编辑说明</h2><a class="button secondary small" href="/help" target="_blank" rel="noopener">查看已发布说明 ${icon('arrow')}</a></div><div class="panel-body"></div></section>`;
  const body=$('.panel-body',$('#managementView'));
  body.innerHTML='<div id="guideEditorHost"></div><div class="editor-foot"><span id="guideSaveState"></span><button class="button primary" data-action="save-guide">保存使用说明</button></div>';
  const state=()=>$('#guideSaveState').textContent=`${admin.guideDraft.length} / 40,000 字 · ${admin.guideDraft===admin.guideOriginal&&admin.guideFormat===admin.guideOriginalFormat?'已保存':'尚未保存'}`;
  mountGuideEditor($('#guideEditorHost'),{content:admin.guideDraft,format:admin.guideFormat},value=>{admin.guideDraft=value.content;admin.guideFormat=value.format;state();});state();
}

function taskDialog(id) {
  const task=admin.data.windows.find(w=>w.id===id),assignments=admin.data.assignments.filter(a=>a.window_id===id);
  const manual=(admin.data.manual_expenses||[]).filter(e=>e.task_id===id);
  for(const uid of new Set(manual.map(e=>e.user_id))){if(!assignments.some(a=>a.user_id===uid)){const e=manual.find(e=>e.user_id===uid);assignments.push({user_id:uid,real_name:e.real_name,status:null,manual_only:true});}}
  openDialog(task.title,`<div class="between"><span class="tag ${task.is_completed?'gray':'amber'}">${collectionState(task)}</span><small class="muted">任务 #${id} · ${assignments.filter(a=>a.status===2).length} / ${assignments.filter(a=>!a.manual_only).length} 人已提交</small></div><div class="row task-dialog-actions">${taskStateButtons(task)}<button class="button secondary small" data-action="rename-task" data-id="${id}">重命名</button><button class="button secondary small" data-action="download-task" data-id="${id}">${icon('download')}任务原始资料</button><button class="button secondary small" data-action="print-task" data-id="${id}">${icon('print')}任务主发票合并</button></div><div class="table-scroll"><table class="data-table"><thead><tr><th>成员</th><th>状态</th><th>金额（元）</th><th>操作</th></tr></thead><tbody>${assignments.map(a=>`<tr><td>${esc(a.real_name)}${a.account_deleted?'（已删除）':''}</td><td><span class="tag ${a.status===2?'submission-completed':a.status===1?'submission-draft':'gray'}">${a.manual_only?'管理员登记':a.status===2?'已完成':a.status===1?'草稿中':'待填写'}</span></td><td class="amount">${totalMoney([...admin.data.invoices.filter(r=>r.task_id===id&&r.user_id===a.user_id),...manual.filter(e=>e.user_id===a.user_id)])}</td><td>${a.status===2&&!a.account_deleted?`<button class="button ghost small" data-action="return-member" data-id="${id}" data-user="${a.user_id}">退回修改</button>`:'—'}</td></tr>`).join('')}</tbody></table></div><p class="outline-note">结束收集不等于结项；成员需修改时请先重新开放收集。已结项任务需先撤销结项。已归入项目的记录须先移回待归类。任务资料下载包含草稿；项目报销以整理台为准。</p><div class="task-delete-section"><p>删除后任务与票据将从工作台移除，原始资料归档保留。</p><button class="button danger small" data-action="delete-task" data-id="${id}">删除收集任务</button></div>`,{wide:true});
}

function newTaskDialog() {
  openDialog('发布收集任务',`<form id="dialogForm" class="stack"><label>任务名称<input name="title" maxlength="120" required placeholder="例如：2026 年 10 月票据收集"></label><label>任务类型<select name="task_type"><option>日常业务报销</option><option>国内差旅费</option></select></label><div><div class="between" style="margin-bottom:10px"><label>选择成员</label><label class="check-label"><input type="checkbox" id="allTaskMembers">全选</label></div><div class="user-select-list">${admin.data.users.map(user=>`<label class="check-label"><input type="checkbox" name="user_ids" value="${user.id}">${esc(user.real_name)}<small>${esc(user.username)}</small></label>`).join('')||'<p class="muted">请先添加成员。</p>'}</div></div>${formFooter('发布任务')}</form>`,{submit:async form=>{
    await api('/api/admin/window/create',{method:'POST',body:{title:form.get('title'),task_type:form.get('task_type'),user_ids:form.getAll('user_ids').map(Number)}});
    closeDialog();await loadAdmin();toast('任务已派发给所选成员');
  }});
  $('#allTaskMembers').addEventListener('change',event=>{$$('input[name="user_ids"]').forEach(input=>input.checked=event.target.checked);});
}

function memberDialog(user = null) {
  const passwordField = user ? '' : '<label>初始密码<input name="password" type="password" maxlength="120" required autocomplete="new-password"></label>';
  openDialog(user ? '编辑成员账户' : '添加工作间成员', `<form id="dialogForm" class="stack">
    <label>真实姓名<input name="real_name" maxlength="120" required autocomplete="off" value="${esc(user?.real_name || '')}"></label>
    <label>登录账号<input name="username" maxlength="120" required autocomplete="off" value="${esc(user?.username || '')}"></label>
    ${passwordField}${formFooter(user ? '保存修改' : '创建成员账户')}</form>`, {submit: async form => {
      await api(user ? `/api/admin/users/${user.id}` : '/api/admin/users/add', {
        method: user ? 'PATCH' : 'POST', body: Object.fromEntries(form)
      });
      closeDialog();
      await loadAdmin();
      toast(user ? '成员账户已更新' : '成员账户已创建');
    }});
}

function memberPasswordDialog(id) {
  const user=admin.data.users.find(u=>u.id===id);
  openDialog(`${user.real_name} · 设置密码`,`<p class="dialog-help">账号：${esc(user.username)}。原密码为不可逆哈希保存，无法查看。可直接设置新密码，并点击“显示”核对。</p><form id="dialogForm" class="stack"><label>新密码<span class="password-input"><input name="new_password" type="password" autocomplete="new-password" minlength="6" maxlength="120" required><button type="button" class="password-toggle" data-toggle-password>显示</button></span></label><p class="muted">保存后，该成员需要用新密码重新登录；已上传票据不受影响。</p>${formFooter('保存新密码')}</form>`,{submit:async form=>{await api(`/api/admin/users/${id}/password`,{method:'POST',body:{new_password:form.get('new_password')}});closeDialog();toast('成员密码已更新');}});
}

function showPage(page) {
  admin.page=page;
  $$('#adminNav [data-nav]').forEach(button => {
    const current = button.dataset.nav === page;
    button.classList.toggle('active', current);
    if (current) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  const title = {
    invoices: '票据整理台',
    tasks: '收集任务',
    members: '成员管理',
    guide: '使用说明设置'
  }[page];
  $('#main').dataset.page = page;
  $('#pageTitle').textContent = title;
  document.title = title + ' · LabInvoice';
  $('.heading-actions [data-action="new-task"]').hidden=page!=='invoices'&&page!=='tasks';
  $('.heading-actions [data-action="new-project"]').hidden=page!=='invoices';
  $('.heading-actions [data-action="new-member"]').hidden=page!=='members';
  $('#invoiceView').hidden=page!=='invoices';$('#managementView').hidden=page==='invoices';
  if(page!=='invoices')renderManagement();
}

document.addEventListener('focusin',event=>{if(event.target.matches('[data-project-history]'))showProjectSuggestions(event.target);});
document.addEventListener('input',event=>{if(event.target.matches('[data-project-history]'))showProjectSuggestions(event.target);});
document.addEventListener('focusout',event=>{
  if(event.target.matches('[data-project-history]'))setTimeout(()=>{if(!document.activeElement?.closest('.history-input-wrap'))hideProjectSuggestions();},0);
});
document.addEventListener('pointerdown',event=>{if(event.target.closest('[data-history-pick]'))event.preventDefault();});
document.addEventListener('click',event=>{
  const option=event.target.closest('[data-history-pick]');
  if(option)selectProjectHistory(Number(option.dataset.historyPick));
  else if(!event.target.closest('.history-input-wrap'))hideProjectSuggestions();
});
document.addEventListener('keydown',event=>{
  const input=event.target;if(!input.matches('[data-project-history]'))return;
  const list=$('#history-'+input.dataset.projectHistory);
  if(event.key==='Escape'&&!list.hidden){event.preventDefault();event.stopPropagation();hideProjectSuggestions();return;}
  if(event.key==='ArrowDown'||event.key==='ArrowUp'){
    event.preventDefault();if(list.hidden)showProjectSuggestions(input);
    const options=$$('[data-history-pick]',list);if(!options.length)return;
    let active=Number(input.dataset.activeOption)+(event.key==='ArrowDown'?1:-1);
    active=(active+options.length)%options.length;input.dataset.activeOption=String(active);
    options.forEach((option,i)=>option.setAttribute('aria-selected',String(i===active)));
    input.setAttribute('aria-activedescendant',options[active].id);options[active].scrollIntoView({block:'nearest'});
  }else if(event.key==='Enter'&&!list.hidden&&Number(input.dataset.activeOption)>=0){
    event.preventDefault();$$('[data-history-pick]',list)[Number(input.dataset.activeOption)]?.click();
  }
});

document.addEventListener('click',async event=>{
  const nav=event.target.closest('#adminNav [data-nav]');if(nav){
    if(admin.data||nav.dataset.nav==='guide'){
      showPage(nav.dataset.nav);
      $('#main').focus();
    }
    return;
  }
  const button=event.target.closest('[data-action]');if(!button||!admin.data)return;
  const action=button.dataset.action,id=Number(button.dataset.id);
  try{
    if(action==='new-manual-expense') {manualExpenseDialog();return;}
    if(action==='edit-manual-expense') {manualExpenseDialog(manualFor(button.dataset.id));return;}
    if(action==='allocate-manual-expense') {manualAllocationDialog(manualFor(button.dataset.id));return;}
    if(action==='delete-manual-expense') {
      const entry=manualFor(button.dataset.id);
      if(await confirmAction('删除无电子发票报销项','此项将从报销汇总移除，原登记记录保留。是否继续？','确认删除报销项')){await api(`/api/admin/manual-expenses/${entry.id}`,{method:'DELETE',body:{version:entry.version}});await loadAdmin();toast('无电子发票报销项已删除');}
      return;
    }
    if(action==='return-all-invoices'){setScope('all');$('#listTitle').focus();return;}
    if(action==='collapse-categories'||action==='expand-categories'){
      [...visibleRows(),...visibleManualExpenses()].forEach(r=>action==='collapse-categories'?admin.collapsedCategories.add(r.category):admin.collapsedCategories.delete(r.category));admin.listPage=0;renderTable();
    }
    else if(action==='toggle-category-fold'){
      const category=button.dataset.category;admin.collapsedCategories.has(category)?admin.collapsedCategories.delete(category):admin.collapsedCategories.add(category);admin.listPage=0;renderTable();
    }
    else if(action==='filter-summary-task'){
      $('#taskFilter').value=String(id);admin.selected.clear();admin.listPage=0;updateCategoryFilter();renderTable();renderCollectionSummary();
    }
    else if(action==='new-project')await projectDialog();
    else if(action==='project-archive')projectArchiveDialog();
    else if(action==='edit-project')await projectDialog(projectFor(id));
    else if(action==='manage-project-history')await historyManager();
    else if(action==='edit-project-history')historyEditDialog(id);
    else if(action==='delete-project-history'){
      if(await confirmAction('删除填写历史','只删除此候选记录，不影响已创建项目及票据。','删除历史记录')){
        await api(`/api/admin/project-history/${id}`,{method:'DELETE'});await historyManager();toast('填写历史已删除');
      }
    }
    else if(action==='open-project'){showPage('invoices');setScope('project',id);focusProjectBanner();}
    else if(action==='open-category'){showPage('invoices');setScope('project',id,Number(button.dataset.category));focusProjectBanner();}
    else if(action==='new-category')categoryDialog(id);
    else if(action==='rename-category'){const p=admin.data.projects.find(p=>p.categories.some(c=>c.id===id));categoryDialog(p.id,p.categories.find(c=>c.id===id));}
    else if(action==='delete-category'){if(await confirmAction('删除报销项','删除后，该项全部票据（含已结项任务）移回待归类，原始文件和已核对金额保留。是否继续？','确认删除报销项')){await api(`/api/admin/categories/${id}?unassign=1`,{method:'DELETE'});await loadAdmin();toast('报销项已删除，票据归类已撤销');}}
    else if(action==='delete-project'){if(await confirmAction('撤销经费项目','撤销后项目及其报销项被删除，全部票据（含已结项任务）移回待归类；原文件、已核对金额及填写历史保留。是否继续？','确认撤销项目')){await api(`/api/admin/projects/${id}?unassign=1`,{method:'DELETE'});await loadAdmin();toast('经费项目已撤销，原始票据保留');}}
    else if(action==='project-summary')summaryDialog(projectFor(id));
    else if(action==='invoice-detail')invoiceDialog(invoiceFor(button.dataset.id));
    else if(action==='print-project')await busy(button,()=>printDialog(id));
    else if(action==='download-project')await busy(button,async()=>{const blob=await api(`/admin/projects/${id}/download`,{binary:true});downloadBlob(blob,projectFor(id).name+'-报销归档.zip');toast('项目原始票据及汇总已打包');});
    else if(action==='allocate-selected'){const pid=Number($('#targetProject').value);if(!pid)throw new Error('请先选择目标经费项目');await busy(button,()=>allocateInvoices([...admin.selected],pid,Number($('#targetCategory').value)||null));}
    else if(action==='unassign-selected'){if(await confirmAction('移回待归类','所选发票将移出项目，原始票据和核对金额会保留。','确认移出'))await busy(button,()=>allocateInvoices([...admin.selected],null));}
    else if(action==='clear-selection'){admin.selected.clear();renderTable();}
    else if(action==='page-prev'||action==='page-next'){admin.listPage+=action==='page-next'?1:-1;renderTable();$('#listTitle').scrollIntoView({block:'start'});}
    else if(action==='new-task')newTaskDialog();
    else if(action==='task-detail'){if(admin.taskMutation)await admin.taskMutation;const task=admin.data.windows.find(t=>t.id===id);if(task)taskDialog(id);else toast('此任务已删除');}
    else if(action==='rename-task')taskRenameDialog(admin.data.windows.find(t=>t.id===id));
    else if(action==='delete-task'){
      if(await confirmAction('删除收集任务','删除后，任务、全部成员提交记录及票据将从工作台移除，项目汇总相应减少。系统会先在服务器归档原文件和金额归类记录。是否继续？','确认删除任务')){
        await updateTask(()=>api(`/api/admin/window/delete/${id}`,{method:'POST',body:{confirmed:true}}));toast('任务已删除，原始资料已归档保留');
      }
    }
    else if(action==='task-state'){
      const state=button.dataset.taskState;
      const messages={close:['结束收集','成员将停止上传、修改和提交；票据继续留在整理台，任务尚未结项。'],reopen:['重新开放收集','未正式提交的成员可以继续填写；已提交记录仍需退回后修改。'],complete:['结项','结项后，此任务的票据和手工报销项将移出整理列表。关联任务均已结项的经费项目将移到“已归档项目”。'],uncomplete:['撤销结项','票据及关联经费项目将回到整理台，任务状态为收集已结束。如需成员继续填写，可再重新开放收集。']};
      if(await confirmAction(...messages[state],'确定')){await updateTask(()=>api(`/api/admin/window/${id}/${state}`,{method:'POST'}));toast(messages[state][0]+'操作已完成');}
    }
    else if(action==='return-member'){if(await confirmAction('退回成员修改','已归入项目的记录需要先移回待归类，成员才可编辑。','退回修改')){await api(`/api/admin/window/${id}/revert/${Number(button.dataset.user)}`,{method:'POST'});await loadAdmin();taskDialog(id);toast('已退回成员修改');}}
    else if(action==='download-task')await busy(button,async()=>downloadBlob(await api(`/admin/download/${id}`,{binary:true}),`任务-${id}-原始资料.zip`));
    else if(action==='print-task')await busy(button,async()=>downloadBlob(await api(`/admin/merge_print/${id}`,{binary:true}),`任务-${id}-主发票汇总.pdf`));
    else if(action==='new-member')memberDialog();
    else if(action==='edit-member')memberDialog(admin.data.users.find(user=>user.id===id));
    else if(action==='member-password')memberPasswordDialog(id);
    else if(action==='delete-member'){
      const user=admin.data.users.find(member=>member.id===id);
      if(await confirmAction('删除成员账户',`删除后，${user.real_name}（${user.username}）将无法登录。历史任务、票据及报销记录会保留。确定删除？`,'删除账户')){
        await api(`/api/admin/users/delete/${id}`,{method:'POST'});
        await loadAdmin();
        toast('成员账户已删除');
      }
    }
    else if(action==='save-guide')await busy(button,async()=>{await api('/api/admin/guide',{method:'POST',body:{content:admin.guideDraft,format:admin.guideFormat}});admin.guideOriginal=admin.guideDraft;admin.guideOriginalFormat=admin.guideFormat;$('#guideSaveState').textContent='已保存，成员可在使用说明页面查看';toast('使用说明已更新');});
  }catch(error){toast(error.message,true);}
});

document.addEventListener('change',event=>{
  const input=event.target;
  if(input.matches('[data-select-invoice]')){input.checked?admin.selected.add(input.dataset.selectInvoice):admin.selected.delete(input.dataset.selectInvoice);renderTable();}
  else if(input.matches('[data-select-group]')){visibleRows().filter(r=>r.category===input.dataset.selectGroup&&selectable(r)).forEach(r=>input.checked?admin.selected.add(r.id):admin.selected.delete(r.id));renderTable();}
  else if(input.id==='selectAllInvoices'||input.id==='mobileSelectAll'){visibleRows().filter(selectable).forEach(r=>input.checked?admin.selected.add(r.id):admin.selected.delete(r.id));renderTable();}
  else if(input.id==='targetProject')updateTargetCategories();
  else if(['taskFilter','categoryFilter','ownerFilter'].includes(input.id)){admin.selected.clear();admin.listPage=0;if(input.id==='taskFilter'){updateCategoryFilter();renderCollectionSummary();}renderTable();}
});
$('#invoiceSearch').addEventListener('input',()=>{admin.selected.clear();admin.listPage=0;renderTable();});
$('#showAll').addEventListener('click',()=>setScope('all'));
$('#showUnassigned').addEventListener('click',()=>setScope('unassigned'));
$('#clearFilters').addEventListener('click',()=>{['invoiceSearch','taskFilter','categoryFilter','ownerFilter'].forEach(id=>$('#'+id).value='');admin.selected.clear();admin.listPage=0;updateCategoryFilter();renderTable();renderCollectionSummary();});
window.matchMedia('(max-width:760px)').addEventListener('change',()=>{if(admin.data){admin.listPage=0;renderTable();}});

document.addEventListener('dragstart',event=>{
  const manual=event.target.closest('[data-manual-expense]');
  if(manual){event.dataTransfer.setData('application/x-labinvoice-manual',manual.dataset.manualExpense);event.dataTransfer.effectAllowed='move';manual.classList.add('dragging');return;}
  const row=event.target.closest('[data-invoice]');if(!row||!selectable(invoiceFor(row.dataset.invoice)))return;
  if(!admin.selected.has(row.dataset.invoice))admin.selected=new Set([row.dataset.invoice]);
  event.dataTransfer.setData('application/x-labinvoice',JSON.stringify([...admin.selected]));event.dataTransfer.effectAllowed='move';row.classList.add('dragging');renderBulk();
});
document.addEventListener('dragend',()=>{$$('.dragging,.drag-over').forEach(el=>el.classList.remove('dragging','drag-over'));});
document.addEventListener('dragover',event=>{const target=event.target.closest('[data-drop-project]');if(target&&[...event.dataTransfer.types].some(type=>['application/x-labinvoice','application/x-labinvoice-manual'].includes(type))){event.preventDefault();event.dataTransfer.dropEffect='move';target.classList.add('drag-over');}});
document.addEventListener('dragleave',event=>{const target=event.target.closest('[data-drop-project]');if(target&&!target.contains(event.relatedTarget))target.classList.remove('drag-over');});
document.addEventListener('drop',async event=>{
  const target=event.target.closest('[data-drop-project]');if(!target)return;
  event.preventDefault();$$('.drag-over').forEach(el=>el.classList.remove('drag-over'));
  try{
    const manualId=event.dataTransfer.getData('application/x-labinvoice-manual');
    if(manualId){const entry=manualFor(manualId);if(entry){const pid=Number(target.dataset.dropProject),category=projectFor(pid)?.categories.find(c=>c.id===Number(target.dataset.dropCategory))?.name||entry.category;await allocateManualExpense(entry,pid,category);}return;}
    const ids=JSON.parse(event.dataTransfer.getData('application/x-labinvoice'));if(Array.isArray(ids)&&ids.every(id=>invoiceFor(id)))await allocateInvoices(ids,Number(target.dataset.dropProject),Number(target.dataset.dropCategory)||null);
  }catch(error){toast(error.message,true);}
});
window.addEventListener('beforeunload',event=>{if(admin.guideDraft!==null&&admin.guideDraft!==admin.guideOriginal){event.preventDefault();event.returnValue='';}});
api('/api/admin/overview').then(data=>{if(!admin.data){admin.data=data;$('#navTaskCount').textContent=data.windows.filter(w=>!w.is_completed).length;if(admin.page!=='invoices')renderManagement();}return loadAdmin();}).catch(error=>{toast(error.message,true);$('#invoiceRows').innerHTML=`<tr><td colspan="6">${emptyState('票据加载失败','请刷新页面重新加载。')}</td></tr>`;});
