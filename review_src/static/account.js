"use strict";
(() => {
 const $=id=>document.getElementById(id);
 let captchaToken="",captchaId=null;
 const message=(text,error=false)=>{$("account-status").textContent=text;$("account-status").className=error?"error":"";};
 const tab=name=>document.querySelectorAll(".account-page").forEach(el=>el.hidden=el.id!=="account-"+name);
 document.querySelectorAll("[data-account-tab]").forEach(button=>button.onclick=()=>{tab(button.dataset.accountTab);if(button.dataset.accountTab==="security")task(null,loadUser);});
 async function api(path,body) {
  const response=await fetch("/api/auth/"+path,{method:body?"POST":"GET",credentials:"same-origin",headers:{"Content-Type":"application/json","X-Equity-Request":"1"},...(body?{body:JSON.stringify({...body,turnstile_token:captchaToken||undefined})}:{})});
  const data=await response.json();
  if(body&&window.turnstile&&captchaId!==null){window.turnstile.reset(captchaId);captchaToken="";}
  if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"請確認 Email、密碼與驗證碼格式，兩次密碼必須一致。");
  return data;
 }
 async function task(button,action){if(button)button.disabled=true;try{await action();}catch(error){message(error.message,true);}finally{if(button)button.disabled=false;}}
 function form(id,path,after){$(id).onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{const body=Object.fromEntries(new FormData(e.target));if(body.confirm_password!==undefined&&body.confirm_password!==(body.password||body.new_password))throw new Error("兩次密碼輸入不一致。");const result=await api(path,body);message(result.message||"操作完成。");await after(body,e.target,result);});};}
 async function loadUser(){
  const data=await api("me");$("signed-in-email").textContent="目前帳號："+data.user.email;
  const phone=await api("phone");$("phone-readiness").textContent=phone.verified?"手機已驗證。":phone.configured?"輸入手機號碼收取驗證碼，10 分鐘內有效。":"手機驗證服務尚未開通，請聯絡管理員。";
  $("account-phone-form").hidden=phone.verified;$("account-phone-verify-form").hidden=phone.verified;$("phone-send").disabled=!phone.configured;
  tab("security");window.dispatchEvent(new Event("equity-account-ready"));return data.user;
 }
 form("account-login-form","login",async(body,form)=>{form.reset();const user=await loadUser();if(user.phone_required&&!user.phone_verified)message("已登入，請先完成手機驗證。");else message("已登入，可返回研究空間。");});
 form("account-register-form","register",async(body,form)=>{$("account-verify-form").elements.email.value=body.email;form.reset();tab("verify");});
 form("account-verify-form","verify-email",async(body,form)=>{$("account-login-form").elements.email.value=body.email;form.reset();tab("login");message("Email 已驗證，請登入後完成手機驗證。");});
 $("resend-email").onclick=()=>task($("resend-email"),async()=>{const result=await api("resend-verification",{email:$("account-verify-form").elements.email.value});message(result.message);});
 form("account-forgot-form","forgot-password",async(body)=>{$("account-reset-form").elements.email.value=body.email;});
 form("account-reset-form","reset-password",async(body,form)=>{form.reset();tab("login");});
 $("change-email-send").onclick=()=>task($("change-email-send"),async()=>{const result=await api("change-password/code",{});message(result.message);});
 form("account-change-form","change-password",async(body,form)=>{form.reset();tab("login");});
 form("account-phone-form","phone/start",async()=>{});
 form("account-phone-verify-form","phone/verify",async(body,form)=>{form.reset();await loadUser();message("手機驗證完成，可以返回研究空間。");});
 $("account-logout").onclick=()=>task($("account-logout"),async()=>{await api("logout",{});tab("login");message("已登出。");});
 task(null,async()=>{
  const options=await api("options");$("register-submit").disabled=!options.registration_enabled;
  $("registration-readiness").textContent=options.registration_enabled?"先驗證 Email，再登入完成手機驗證。":"註冊尚未開放：Email 或手機驗證服務尚未完成設定。";
  if(options.turnstile_site_key){const script=document.createElement("script");script.src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";script.onload=()=>{captchaId=window.turnstile.render($("captcha"),{sitekey:options.turnstile_site_key,callback:token=>{captchaToken=token;}});};document.head.append(script);}
  const requested=location.hash.slice(1);if(requested==="social-error"){tab("login");message("第三方登入未完成。首次使用請先註冊並以 Email 登入，再到帳號安全綁定。",true);}if(["login","register","verify","reset","security"].includes(requested)){tab(requested);if(requested==="security")await loadUser();}
 });
})();
