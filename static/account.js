document.addEventListener('click',event=>{
  if(!event.target.closest('[data-account=password]'))return;
  openDialog('修改我的密码',`<form id="dialogForm" class="stack"><label>当前密码<input name="current_password" type="password" autocomplete="current-password" required maxlength="120"></label><label>新密码<span class="password-input"><input name="new_password" type="password" autocomplete="new-password" required minlength="6" maxlength="120"><button type="button" class="password-toggle" data-toggle-password>显示</button></span></label><label>确认新密码<input name="confirm_password" type="password" autocomplete="new-password" required minlength="6" maxlength="120"></label><p class="muted">保存后，其他已登录的浏览器需用新密码重新登录。</p><p id="passwordError" class="alert error" hidden></p><div class="dialog-actions"><button type="button" class="button secondary" data-close-dialog>取消</button><button type="submit" class="button primary">保存新密码</button></div></form>`,{submit:async form=>{
    const data=Object.fromEntries(form);
    if(data.new_password!==data.confirm_password){$('#passwordError').textContent='两次输入的新密码不一致';$('#passwordError').hidden=false;return;}
    try{await api('/api/user/password',{method:'POST',body:data});}
    catch(error){$('#passwordError').textContent=error.message;$('#passwordError').hidden=false;return;}
    try {
      const key='labinvoice.rememberedCredentials.v1',saved=JSON.parse(localStorage.getItem(key)||'null');
      if(saved&&saved.username===document.body.dataset.username){saved.password=data.new_password.trim();localStorage.setItem(key,JSON.stringify(saved));}
    }catch{}
    closeDialog();toast('密码已修改');
  }});
});
