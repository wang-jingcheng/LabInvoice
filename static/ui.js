/* Shared accessible dialogs, safe text rendering, and request feedback. */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const money = cents => cents == null ? '—' : new Intl.NumberFormat('zh-CN', {minimumFractionDigits:2,maximumFractionDigits:2}).format(cents / 100);
const csrf = () => $('meta[name="csrf-token"]').content;

function toast(message, error = false) {
  const item = document.createElement('div');
  item.className = `toast ${error ? 'error' : ''}`;
  item.textContent = message;
  $('#toastRegion').append(item);
  setTimeout(() => item.remove(), error ? 9000 : 4200);
}

async function api(url, options = {}) {
  const headers = {'X-CSRF-Token':csrf(), ...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  let response;
  try { response = await fetch(url, {...options, headers}); }
  catch { throw new Error('连接中断，请检查网络后重试。已选文件仍保留在页面。'); }
  if (!response.ok) {
    let message = response.status === 413 ? '本次文件超过服务器限制，请减少文件后分批保存' : `操作未完成（${response.status}），请刷新后重试`;
    try { message = (await response.json()).message || message; } catch {}
    throw new Error(message);
  }
  return options.binary ? response.blob() : response.json();
}

async function busy(button, action) {
  if (button?.disabled) return;
  const original = button?.innerHTML;
  if (button) { button.disabled = true; button.textContent = '处理中…'; }
  try { return await action(); }
  catch (error) { toast(error.message, true); }
  finally { if (button) { button.disabled = false; button.innerHTML = original; } }
}

let dialogCleanup = null;
function openDialog(title, html, options = {}) {
  const dialog = $('#appDialog');
  // Replace the contents of an open modal without closing it. A queued native
  // close event would otherwise cancel the confirmation that replaces it.
  if (dialogCleanup) { dialogCleanup(); dialogCleanup = null; }
  $('#dialogTitle').textContent = title;
  $('#dialogBody').innerHTML = html;
  dialog.classList.toggle('wide', !!options.wide);
  dialogCleanup = options.cleanup || null;
  if (!dialog.open) dialog.showModal();
  const focusTarget = $('[autofocus], input:not([type="hidden"]), textarea, select, button', $('#dialogBody'));
  focusTarget?.focus({preventScroll:true});
  const form = $('#dialogForm');
  if (form && options.submit) form.addEventListener('submit', event => {
    event.preventDefault();
    busy($('[type="submit"]', form), () => options.submit(new FormData(form), form));
  });
  return dialog;
}
function closeDialog() { $('#appDialog').close(); }

function confirmAction(title, text, label = '确定') {
  return new Promise(resolve => {
    const dialog = openDialog(title, `<p class="dialog-description">${esc(text)}</p><div class="dialog-actions"><button class="button secondary" data-close-dialog>取消</button><button class="button primary" id="confirmAction">${esc(label)}</button></div>`);
    let confirmed = false;
    $('#confirmAction').addEventListener('click', () => { confirmed = true; dialog.close(); });
    dialog.addEventListener('close', () => resolve(confirmed), {once:true});
  });
}

function amountReminder() {
  openDialog('草稿已保存', `<div class="notice-mark">${icon('check')}</div><p class="reminder-text">请检查发票金额识别是否正确</p><p class="muted centered">可在草稿清单预览原票，并手动修正金额。</p><div class="dialog-actions"><button class="button primary" data-close-dialog autofocus>确定</button></div>`);
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

document.addEventListener('click', event => { if (event.target.closest('[data-close-dialog]')) closeDialog(); });
document.addEventListener('click', event => {
  const button=event.target.closest('[data-toggle-password]');
  if(!button)return;
  const input=$('input',button.closest('.password-input'));
  input.type=input.type==='password'?'text':'password';
  const hidden=input.type==='password';
  const label=hidden?'显示密码':'隐藏密码';
  const icon=$('use',button);
  if(icon){
    icon.setAttribute('href',hidden?'#i-eye':'#i-eye-off');
    button.title=label;
  }else button.textContent=hidden?'显示':'隐藏';
  button.setAttribute('aria-label',label);
});
document.addEventListener('DOMContentLoaded', () => {
  $('#appDialog').addEventListener('close', () => { if (!$('#appDialog').open && dialogCleanup) { dialogCleanup(); dialogCleanup = null; } });
});
