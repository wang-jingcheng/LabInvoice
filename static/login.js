/* Remember credentials only after a successful, explicitly opted-in login. */
const credentialStorageKey='labinvoice.rememberedCredentials.v1';
const loginForm=$('#memberLoginForm'), rememberCredentials=$('#rememberCredentials');
try {
  const saved=JSON.parse(localStorage.getItem(credentialStorageKey)||'null');
  if(saved&&typeof saved.username==='string'&&typeof saved.password==='string'){
    loginForm.elements.username.value=saved.username;
    loginForm.elements.password.value=saved.password;
    rememberCredentials.checked=true;
  }
}catch{}
rememberCredentials.addEventListener('change',()=>{
  if(!rememberCredentials.checked){try{localStorage.removeItem(credentialStorageKey);}catch{}}
});
loginForm.addEventListener('submit',async event=>{
  event.preventDefault();
  if(!loginForm.reportValidity())return;
  const error=$('#loginError');
  error.hidden=true;
  await busy($('[type=submit]',loginForm),async()=>{
    const data=new FormData(loginForm);
    const response=await fetch('/login',{method:'POST',body:data,headers:{Accept:'application/json'}});
    const result=await response.json();
    if(!response.ok){
      error.textContent=result.message||'登录未成功，请重试';
      error.hidden=false;
      error.focus();
      return;
    }
    try {
      if(rememberCredentials.checked)localStorage.setItem(credentialStorageKey,JSON.stringify({username:data.get('username').trim(),password:data.get('password').trim()}));
      else localStorage.removeItem(credentialStorageKey);
    }catch{toast('浏览器未允许保存账号密码，本次仍可正常登录',true);}
    location.assign(result.redirect);
  });
});
