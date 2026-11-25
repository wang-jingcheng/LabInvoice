const member = {tasks:[], taskId:null, pending:{}, category:null, uploading:false,
  get invoices(){return pendingGroup().invoices;},set invoices(files){pendingGroup().invoices=files;},
  get attachments(){return pendingGroup().attachments;},set attachments(files){pendingGroup().attachments=files;}};
function pendingGroup(){
  if(!member.category)return {invoices:[],attachments:[],remark:''};
  return member.pending[member.category] ||= {invoices:[],attachments:[],remark:''};
}
const pendingGroups=()=>Object.entries(member.pending).filter(([,g])=>g.invoices.length||g.attachments.length);
const hasPendingFiles=()=>pendingGroups().length>0;
const dailyCategories = ['市内交通费','专用材料费','科研招待费','邮电费','印刷费','其他'];
const travelCategories = ['交通费','住宿费','其他费用'];
const invoiceTypes = ['pdf','png','jpg','jpeg','jfif','webp','bmp','gif','tif','tiff'];
const attachmentTypes = [...invoiceTypes,'doc','docx','xls','xlsx','zip','rar','7z'];
const getTask = () => member.tasks.find(t => t.id === member.taskId);
const readOnly = task => task.manualOnly || task.is_completed || task.is_collection_closed || task.status === 2;
const statusStyle = task => task.is_completed || task.is_collection_closed ? 'gray' : task.status === 2 ? 'submission-completed' : task.status === 1 ? 'submission-draft' : 'gray';
const taskStatus = task => task.is_completed ? '已结项' : task.is_collection_closed ? '收集已结束' : task.status === 2 ? '已提交' : task.status === 1 ? '草稿待提交' : task.manualOnly ? '管理员登记' : '待填写';

async function loadMember() {
  const [tasks,manual]=await Promise.all([api('/api/user/tasks'),api('/api/user/manual-expenses')]);
  const byTask = new Map(tasks.map(task => [task.id, {...task, manualExpenses:[]}]));
  for (const expense of manual) {
    const id = expense.task_id;
    if (!byTask.has(id)) {
      byTask.set(id, {
        id, title:expense.task_title, manualOnly:true,
        is_completed:expense.task_completed, is_collection_closed:expense.is_collection_closed,
        manualExpenses:[]
      });
    }
    byTask.get(id).manualExpenses.push(expense);
  }
  member.tasks = [...byTask.values()].sort((a, b) => b.id - a.id);
  if (!getTask()) member.taskId = tasks.find(t => !readOnly(t))?.id ?? tasks[0]?.id ?? member.tasks[0]?.id ?? null;
  $('#memberPending').innerHTML = `${tasks.filter(t => !readOnly(t)).length}<small>项</small>`;
  $('#memberSubmitted').innerHTML = `${tasks.filter(t => t.status === 2).length}<small>项</small>`;
  const pendingCount = tasks.reduce((n,t) => n+t.pending_amount_count, 0);
  $('#memberTotal').innerHTML = `<span class="currency">¥</span>${money(pendingCount ? null : tasks.reduce((n,t) => n + Math.round(t.final_amount * 100),0))}`;
  const totalNote = $('#memberTotalNote');
  totalNote.hidden = !pendingCount;
  totalNote.textContent = pendingCount ? `${pendingCount} 张发票待填写金额` : '';
  const taskCount = $('#memberTaskCount');
  taskCount.hidden = !member.tasks.length;
  taskCount.textContent = member.tasks.length;
  const warnings = [...new Set(member.tasks.flatMap(t => t.warnings || []))];
  $('#memberWarnings').innerHTML = warnings.length ? `<div class="alert error">${esc(warnings.join('\n'))}</div>` : '';
  renderMemberTasks();
  renderMemberDesk();
}

function renderMemberTasks() {
  $('#memberTasks').innerHTML = member.tasks.length ? member.tasks.map(task => {
    const manualCount = task.manualExpenses.length;
    const count = task.manualOnly ? `${manualCount} 项管理员登记` : `${(task.groups || []).length} 个报销项`;
    const amount = task.manualOnly ? manualTotal(task) : task.pending_amount_count ? null : Math.round(task.final_amount * 100);
    return `<button type="button" class="member-task ${task.id === member.taskId ? 'active' : ''}" data-task="${task.id}" aria-pressed="${task.id === member.taskId}" aria-controls="memberDesk">
      <span class="tag ${statusStyle(task)}">${taskStatus(task)}</span>
      <span class="member-task-title">${esc(task.title)}</span>
      <span class="between"><span>${count}</span><span class="amount">¥ ${money(amount)}</span></span>
    </button>`;
  }).join('') : `<div class="empty-state">${icon('folder')}<h3>还没有收集任务</h3><p>管理员发布任务后会出现在这里。</p></div>`;
}

const manualTotal = task => task.manualExpenses.reduce((sum, expense) => sum + expense.amount_cents, 0);

function renderManualExpenses(task) {
  if (!task.manualExpenses.length) return '';
  const rows = task.manualExpenses.map(expense => `<article>
    <div><strong>${esc(expense.category)}</strong>${expense.remark ? `<p>${esc(expense.remark)}</p>` : ''}</div>
    <span class="amount">¥ ${money(expense.amount_cents)}</span>
  </article>`).join('');
  return `<section class="panel member-manual-expenses" id="memberManualExpenses" aria-labelledby="memberManualTitle">
    <div class="panel-heading">
      <h2 id="memberManualTitle">管理员登记的无电子发票报销项</h2>
      <strong class="amount"><span class="sr-only">本任务登记合计 </span>¥ ${money(manualTotal(task))}</strong>
    </div>
    <div class="member-manual-list">${rows}</div>
  </section>`;
}

function renderMemberDesk() {
  const task = getTask();
  const desk = $('#memberDesk');
  desk.hidden = !task;
  if (!task) {
    desk.replaceChildren();
    return;
  }
  const detail = task.manualOnly ? '' : task.task_type === '国内差旅费' ? task.task_type : '日常业务报销';
  const heading = `<div class="task-desk-head"><div><h2 id="memberDeskTitle" tabindex="-1">${esc(task.title)}</h2>${detail ? `<span class="muted">${esc(detail)}</span>` : ''}</div><span class="tag ${statusStyle(task)}">${taskStatus(task)}</span></div>`;
  if (task.manualOnly) {
    desk.innerHTML = heading + renderManualExpenses(task);
    return;
  }
  const locked = readOnly(task);
  const categories = task.task_type === '国内差旅费' ? travelCategories : dailyCategories;
  desk.innerHTML = `${heading}
    ${locked ? `<div class="alert">${task.is_completed ? '已结项，票据可查阅。' : task.is_collection_closed ? '收集已结束，尚未结项。如需补充，请联系管理员重新开放。' : '如需修改，请联系管理员退回。'}</div>` : `<section class="panel member-upload" aria-labelledby="memberUploadTitle"><div class="panel-heading"><h3 id="memberUploadTitle">上传票据</h3></div><div class="panel-body"><form id="uploadForm">
      <fieldset class="category-fieldset" aria-describedby="categoryHint">
      <legend class="step-title">报销项</legend>
      <div class="category-options">${categories.map((cat,i) => `<label class="category-option"><input type="radio" name="type" value="${esc(cat)}" ${i === 0 ? 'required' : ''}><span>${esc(cat)}</span></label>`).join('')}</div><p id="categoryHint" class="category-hint" role="status" hidden></p></fieldset>
      <div class="upload-columns">${uploadZone('invoices','主发票文件','PDF / 图片','invoice_files',invoiceTypes.map(v=>'.'+v).join(','),'upload')}${uploadZone('attachments','配套附件','PDF / 图片 / 文档','attachments',attachmentTypes.map(v=>'.'+v).join(','),'clip')}</div>
      <label>备注 <span class="muted">（选填）</span><textarea name="remark" rows="2" maxlength="4000" placeholder="用途、行程等"></textarea></label>
      <section id="pendingGroups" class="pending-groups" aria-label="待保存的报销项"></section><div class="upload-footer"><small id="uploadSummary">每次最多 128 MB</small><button class="button soft" type="submit" id="saveDraftBtn">${icon('save')}保存至草稿箱</button></div>
    </form></div></section>`}
    <section class="draft-section" aria-labelledby="memberDraftTitle"><div class="draft-heading"><h3 id="memberDraftTitle">${locked ? '已保存的票据' : '草稿清单'} <span class="count-pill">${(task.groups||[]).length}</span></h3><span class="amount"><span class="sr-only">票据合计</span>¥ ${money(task.pending_amount_count ? null : Math.round(task.final_amount * 100))}</span></div>${task.pending_amount_count ? `<p class="alert">${task.pending_amount_count} 张发票金额待填写。填写并保存后即可提交。已填写合计 ¥ ${money(Math.round(task.final_amount*100))}。</p>` : ''}<div id="draftList">${renderDrafts(task)}</div></section>
    ${renderManualExpenses(task)}
    ${!locked ? `<div class="final-submit"><p>正式提交后，如需修改请联系管理员退回。</p><button type="button" class="button primary" id="submitFinal">正式提交 ${icon('arrow')}</button></div>` : ''}`;
  $('#uploadForm')?.addEventListener('submit', event => { event.preventDefault(); saveDraft(); });
  $$('input[name="type"]').forEach(input => {
    input.checked=input.value===member.category;
    input.addEventListener('change',()=>{
      if(member.category)pendingGroup().remark=$('#uploadForm textarea').value;
      member.category=input.value;
      $('#uploadForm textarea').value=pendingGroup().remark;
      updateCategoryHint();renderPicked();
    });
  });
  const remark=$('#uploadForm textarea');
  if(remark){remark.value=pendingGroup().remark;remark.addEventListener('input',()=>{if(member.category)pendingGroup().remark=remark.value;});}
  $$('[data-picker]').forEach(input => input.addEventListener('change', () => { addFiles(input.dataset.picker, [...input.files]); input.value = ''; }));
  $$('.dropzone').forEach(zone => {
    zone.addEventListener('dragover', event => { event.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', event => { event.preventDefault(); zone.classList.remove('drag-over'); addFiles(zone.dataset.zone, [...event.dataTransfer.files]); });
  });
  renderPicked();
  if(member.category&&$('#categoryHint'))updateCategoryHint();
}

function uploadZone(kind, title, sub, name, accept, glyph) {
  return `<section class="upload-group"><label class="upload-label" for="picker-${kind}">${title} ${kind === 'invoices' ? '<span class="required" aria-hidden="true">*</span>' : '<span class="muted" id="attachmentRequirement">（选填）</span>'}</label>
    <div class="dropzone" data-zone="${kind}">${icon(glyph)}<span><strong>选择文件<span class="drop-instruction">或拖放至此</span></strong><small id="format-${kind}">${sub}</small></span>
    <input id="picker-${kind}" class="file-picker" type="file" name="${name}" multiple accept="${accept}" data-picker="${kind}" aria-label="选择${title}" aria-describedby="format-${kind}"></div>
    <ul class="picked-list" id="picked-${kind}" aria-label="已选${title}"></ul></section>`;
}

function updateCategoryHint() {
  const type = $('input[name="type"]:checked')?.value;
  const hints = {'专用材料费':'按财务要求补充材料明细和支付记录。','科研招待费':'按财务要求补充支付记录。','邮电费':'可附业务相关证明。','印刷费':'可附印刷明细和支付记录。','其他':'请在备注中说明用途。'};
  const hint = $('#categoryHint');
  hint.textContent = hints[type] || '';
  hint.hidden = !hint.textContent;
  $('#attachmentRequirement').textContent = type === '市内交通费' ? '（须附对应行程单）' : '（选填）';
}

function addFiles(kind, files) {
  if (member.uploading) return;
  if(!member.category){toast('请先选择这些文件所属的报销项',true);return;}
  const allowed = kind === 'invoices' ? invoiceTypes : attachmentTypes;
  const valid = files.filter(file => allowed.includes(file.name.split('.').pop().toLowerCase()));
  if (valid.length !== files.length) toast('有不支持的文件未加入，请检查文件格式', true);
  for (const file of valid) member[kind].push(file);
  renderPicked();
}

function renderPicked() {
  for (const kind of ['invoices','attachments']) {
    const list = $(`#picked-${kind}`);
    if (!list) continue;
    list.innerHTML = member[kind].map((file,i) => `<li class="picked-file">${icon(kind === 'invoices' ? 'file' : 'clip')}<span title="${esc(file.name)}">${esc(file.name)}</span><small>${file.size >= 1048576 ? (file.size/1048576).toFixed(1)+' MB' : Math.max(1,Math.round(file.size/1024))+' KB'}</small><button type="button" class="icon-button" data-remove-kind="${kind}" data-index="${i}" title="移除此文件" aria-label="移除 ${esc(file.name)}" ${member.uploading ? 'disabled' : ''}>${icon('close')}</button></li>`).join('');
  }
  const groups = pendingGroups();
  const files = groups.flatMap(([, group]) => [...group.invoices, ...group.attachments]);
  const totalMb = files.reduce((sum, file) => sum + file.size, 0) / 1048576;
  const uploadSummary = $('#uploadSummary');
  if (uploadSummary) {
    const limit = '每次最多 128 MB';
    uploadSummary.textContent = files.length ? `已选 ${files.length} 个文件 · ${totalMb < .1 ? '< 0.1' : totalMb.toFixed(1)} MB` : limit;
    if (totalMb > 128) uploadSummary.textContent += `（${limit}）`;
    uploadSummary.classList.toggle('over-limit', totalMb > 128);
  }
  $$('input[name=type]').forEach(input=>{
    const span=$('span',input.closest('label')),g=member.pending[input.value],count=g?g.invoices.length+g.attachments.length:0;
    span.textContent=input.value+(count?` · ${count}`:'');
  });
  const pendingSection = $('#pendingGroups');
  if (pendingSection) pendingSection.innerHTML = groups.length ? `<div class="pending-heading"><strong>待保存的报销项</strong></div>${groups.map(([type, group]) => `<button type="button" class="pending-group ${type === member.category ? 'active' : ''}" data-pending-category="${esc(type)}" aria-pressed="${type === member.category}"><span><strong>${esc(type)}</strong><small>${group.invoices.length} 张主发票 · ${group.attachments.length} 份附件</small></span>${icon('chevron')}</button>`).join('')}` : '';
}

function renderDrafts(task) {
  const groups=task.groups||[];
  if(!groups.length)return `<div class="panel empty-state">${icon('file')}<p>${readOnly(task) ? '暂无电子票据' : '暂无草稿'}</p></div>`;
  if(!member.expanded){member.expanded=new Set([groups.at(-1).type]);}
  return groups.map(group=>{
    const taskLocked=readOnly(task),groupLocked=taskLocked||group.project_locked;
    const fileRow=(file,kind)=>{
      const locked=taskLocked||file.locked,pending=file.amount==null;
      const amount = locked
        ? `<span class="amount saved-file-amount">¥ ${money(pending ? null : Math.round(file.amount * 100))}</span>`
        : `<span class="compact-amount-editor"><span class="amount-input"><span aria-hidden="true">¥</span><input type="number" data-file-amount value="${pending ? '' : Number(file.amount).toFixed(2)}" placeholder="—" min="0" max="9999999999.99" step="0.01" required aria-label="${esc(file.filename)} 金额（元）"></span><button type="button" class="icon-button amount-save" data-member-action="file-amount" data-item="${esc(file.item_id)}" data-file="${esc(file.filename)}" title="保存金额" aria-label="保存 ${esc(file.filename)} 金额">${icon('save')}</button></span>`;
      const remove = locked ? '' : `<button type="button" class="icon-button compact-remove" data-member-action="remove-saved" data-item="${esc(file.item_id)}" data-kind="${kind}" data-file="${esc(file.filename)}" title="移除此文件" aria-label="移除 ${esc(file.filename)}">${icon('close')}</button>`;
      return `<div class="draft-file-row ${kind === 'invoice' ? 'draft-invoice' : 'draft-attachment'}"><span class="draft-file-link">${icon(kind === 'invoice' ? 'file' : 'clip')}<a href="${esc(file.url)}" target="_blank" rel="noopener" title="${esc(file.filename)}">${esc(file.filename.replace(/^(主发票_|附件_)/,''))}</a></span>${kind === 'invoice' ? amount : ''}${remove}</div>`;
    };
    return `<details class="draft-item" data-category="${esc(group.type)}" ${member.expanded.has(group.type) ? 'open' : ''}>
      <summary><span class="draft-summary-main">${icon('chevron')}<span><strong>${esc(group.type)} ${group.project_locked ? '<span class="tag gray">含已归类票据</span>' : ''}</strong><small>${group.invoices.length} 张主发票 · ${group.attachments.length} 份附件</small></span></span><span class="amount">¥ ${money(group.amount == null ? null : Math.round(group.amount * 100))}</span></summary>
      <div class="draft-item-body"><div class="draft-columns">
        <section class="draft-main-files"><h4>主发票文件</h4>${group.invoices.length ? group.invoices.map(file => fileRow(file, 'invoice')).join('') : '<p class="muted empty-files">尚无主发票</p>'}</section>
        <section class="draft-support-files"><h4>配套附件</h4>${group.attachments.length ? group.attachments.map(file => fileRow(file, 'attachment')).join('') : '<p class="muted empty-files">暂无附件</p>'}</section></div>
        ${group.remarks.length ? `<p class="draft-remark">${group.remarks.map(esc).join('<br>')}</p>` : ''}
        ${taskLocked ? '' : `<div class="draft-group-footer"><button type="button" class="button ghost small delete-draft" data-member-action="delete-category" data-category="${esc(group.type)}" ${groupLocked ? 'disabled title="请先联系管理员将已归类票据移回待归类"' : ''}>删除整个条目</button></div>`}
      </div></details>`;
  }).join('');
}

async function saveDraft() {
  const form = $('#uploadForm');
  if (!form.reportValidity() || member.uploading) return;
  if(member.category)pendingGroup().remark=$('textarea',form).value;
  const groups=pendingGroups();
  if(!groups.length){toast('请至少选择一张主发票（PDF 或图片）',true);return;}
  for(const [type,g] of groups){
    const existing=getTask().groups?.some(group=>group.type===type&&group.invoices.length);
    if(!g.invoices.length&&!existing){toast(`${type}：请至少添加一张主发票`,true);return;}
    if(type==='市内交通费'&&g.invoices.length&&!g.attachments.length){toast('市内交通费请上传对应行程单附件',true);return;}
  }
  if(groups.flatMap(([,g])=>[...g.invoices,...g.attachments]).reduce((n,f)=>n+f.size,0)>128*1048576){toast('本次文件总计超过 128 MB，请减少文件后分批保存',true);return;}
  const data = new FormData();
  data.append('groups',JSON.stringify(groups.map(([type,g])=>({type,remark:g.remark}))));
  groups.forEach(([,g],index)=>{g.invoices.forEach(file=>data.append(`invoices_${index}`,file));g.attachments.forEach(file=>data.append(`attachments_${index}`,file));});
  await busy($('#saveDraftBtn'), async () => {
    member.uploading = true;
    $$('[data-picker],input[name="type"],#uploadForm textarea').forEach(input=>input.disabled=true);
    renderPicked();
    try {
      await api(`/api/user/upload_items/${member.taskId}`,{method:'POST',body:data});
      member.pending={};
      member.expanded?.add(member.category);
      await loadMember();
      toast('已保存至草稿箱');
    } finally {
      member.uploading=false;
      $$('[data-picker],input[name="type"],#uploadForm textarea').forEach(input=>input.disabled=false);
      renderPicked();
    }
  });
}

async function switchMemberTask(id) {
  if (member.uploading) return;
  if (id === member.taskId) {
    $('#memberDeskTitle')?.focus();
    return;
  }
  if (hasPendingFiles() && !await confirmAction('切换收集任务','当前选择的文件尚未保存，切换后需要重新选择。继续切换？','继续切换')) return;
  member.pending={};member.category=null;member.expanded=null; member.taskId=id;
  renderMemberTasks(); renderMemberDesk();
  $('#memberDeskTitle')?.focus();
}

document.addEventListener('click', async event => {
  const categoryButton=event.target.closest('[data-pending-category]');
  if (categoryButton && !member.uploading) {
    const input = $$('input[name=type]').find(item => item.value === categoryButton.dataset.pendingCategory);
    input?.click();
    input?.focus();
    return;
  }
  const taskButton = event.target.closest('[data-task]');
  if (taskButton) return switchMemberTask(Number(taskButton.dataset.task));
  const remove = event.target.closest('[data-remove-kind]');
  if (remove && !member.uploading) {
    const {removeKind:kind, index} = remove.dataset;
    member[kind].splice(Number(index), 1);
    renderPicked();
    const removeButtons = $$(`[data-remove-kind="${kind}"]`);
    const next = removeButtons[Math.min(Number(index), removeButtons.length - 1)] || $(`[data-picker="${kind}"]`);
    next?.focus({preventScroll:true});
    return;
  }
  if (event.target.closest('#submitFinal')) {
    if (member.uploading) return;
    if (hasPendingFiles()) { toast('有尚未保存的文件，请先保存至草稿箱并核对金额',true); return; }
    if (getTask().pending_amount_count) { toast('请先逐张填写并保存未识别的发票金额，再正式提交',true); return; }
    if (await confirmAction('正式提交本次票据',getTask().items.length ? '请确认所有发票金额、附件和备注已核对。提交后将锁定编辑。' : '您还没有上传发票，确认提交空任务吗？','确认提交')) {
      await busy($('#submitFinal'),async()=>{await api(`/api/user/submit/${member.taskId}`,{method:'POST'});await loadMember();toast('已正式提交，交由管理员继续整理');});
    }
    return;
  }
  const button = event.target.closest('[data-member-action]');
  if (!button || member.uploading) return;
  const {memberAction:action,item} = button.dataset;
  if (action === 'file-amount') {
    const input = $('[data-file-amount]',button.closest('.draft-invoice'));
    if (!input.reportValidity()) return;
    await busy(button,async()=>{await api(`/api/user/item/${member.taskId}/${encodeURIComponent(item)}/amount`,{method:'POST',body:{filename:button.dataset.file,amount:input.value}});await loadMember();toast('本张发票金额已保存');});
  } else if(action==='delete-category') {
    if(!await confirmAction('删除整个草稿条目',`将移除“${button.dataset.category}”下全部已保存的发票、附件和备注。`,'确认删除'))return;
    await busy(button,async()=>{await api(`/api/user/category/${member.taskId}/delete`,{method:'POST',body:{type:button.dataset.category}});await loadMember();toast('已删除整个条目');});
  } else if (action === 'delete' || action === 'remove-saved') {
    if (!await confirmAction(action === 'delete' ? '删除这条草稿记录' : '移除此文件',action === 'delete' ? '这条记录中的发票和附件将从草稿清单移除。' : `将从本条记录移除 ${button.dataset.file}。`,'确认移除')) return;
    await busy(button,async()=>{
      await api(`/api/user/item/${member.taskId}/${encodeURIComponent(item)}${action === 'remove-saved' ? '/files' : ''}`,{method:'DELETE',...(action === 'remove-saved' ? {body:{kind:button.dataset.kind,filename:button.dataset.file}} : {})});
      await loadMember();toast('已从草稿清单移除');closeDialog();
    });
  }
});

document.addEventListener('toggle',event=>{
  if(event.target.matches('.draft-item[data-category]')){
    member.expanded ||= new Set();event.target.open?member.expanded.add(event.target.dataset.category):member.expanded.delete(event.target.dataset.category);
  }
},true);

window.addEventListener('beforeunload', event => {
  if (member.uploading || hasPendingFiles()) {event.preventDefault();event.returnValue='';}
});
loadMember().catch(error=>{toast(error.message,true);$('#memberTasks').innerHTML='<div class="empty-state"><h3>任务加载失败</h3><p>请刷新页面重新加载。</p></div>';});
