"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let offset=0, selected=null, me=null, pendingId=null;
  const planNames={free:"免費會員",monthly:"限期進階會員",complimentary:"永久招待會員"};
  const roleNames={member:"一般會員",manager:"會員管理員",owner:"擁有者"};
  const status=(text,error=false)=>{ $("admin-status").textContent=text; $("admin-status").className=error?"error":""; };
  const node=(tag,text)=>{const el=document.createElement(tag);el.textContent=text;return el;};
  const date=value=>value?new Date(value*1000).toLocaleString("zh-TW"):"無";
  async function api(path,options={}) {
    const response=await fetch(path,{...options,credentials:"same-origin",headers:{"X-Equity-Request":"1","Content-Type":"application/json"}});
    const data=await response.json();
    if(response.status===401){$("admin-login").hidden=false;$("admin-workspace").hidden=true;}
    if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"操作未完成，請檢查輸入。");
    return data;
  }
  async function task(button, action){if(button)button.disabled=true;try{await action();}catch(error){status(error.message,true);}finally{if(button)button.disabled=false;}}
  function edit(member){
    selected=member; pendingId=crypto.randomUUID();
    $("member-title").textContent=member.email;$("member-plan").value=member.plan;
    $("member-role").value=member.role==="owner"?"member":member.role;
    $("member-role").disabled=me.role!=="owner"||member.role==="owner";
    $("member-action").value="plan";$("member-action").disabled=me.role!=="owner"||member.role==="owner";
    const expiry=member.expires_at?new Date(member.expires_at*1000):null;
    $("member-expiry").value=expiry?new Date(expiry.getTime()-expiry.getTimezoneOffset()*60000).toISOString().slice(0,16):"";
    $("member-reason").value="";$("member-editor").hidden=false;$("member-editor").scrollIntoView({behavior:"smooth"});
  }
  async function load(){
    me=await api("/api/membership");
    if(!me.can_manage_members)throw new Error("此帳號沒有會員管理權限。請由擁有者授權。");
    const data=await api("/api/admin/members?q="+encodeURIComponent($("member-query").value)+"&offset="+offset);
    $("admin-login").hidden=true;$("admin-workspace").hidden=false;
    $("member-count").textContent=`共 ${data.total} 位會員 · 第 ${Math.floor(offset/30)+1} 頁`;
    $("member-list").replaceChildren();
    for(const member of data.items){
      const card=node("article","");card.className="panel";
      card.append(node("h2",member.email),node("p",`${roleNames[member.role]} · ${planNames[member.effective_plan]}${member.expired?"（資格已到期）":""}`),node("p",`Email：${member.is_verified?"已驗證":"待驗證"} · 帳號：${member.is_active?"啟用":"停用"}`),node("p","到期時間："+date(member.expires_at)),node("p","最近登入："+date(member.last_login_at)));
      const change=node("button","調整資格"),history=node("button","操作紀錄");
      change.className="quiet";history.className="quiet";
      change.disabled=me.role==="manager"&&(member.role!=="member"||member.id===me.user_id);
      change.onclick=()=>edit(member);
      history.onclick=()=>task(history,async()=>{
        const result=await api(`/api/admin/members/${member.id}/audit`);$("audit-list").replaceChildren();
        for(const item of result.items){const after=JSON.parse(item.after_json);$("audit-list").append(node("p",`${date(item.created_at)} · 操作者 ${item.actor_id===null?"主機初始化":item.actor_id} · ${item.action==="role"?roleNames[after.role]:planNames[after.plan]||"初始化"} · ${item.reason}`));}
        if(!result.items.length)$("audit-list").append(node("p","尚無調整紀錄。"));
        $("member-audit").hidden=false;$("member-audit").scrollIntoView({behavior:"smooth"});
      });
      card.append(change,history);$("member-list").append(card);
    }
    $("member-prev").disabled=offset===0;$("member-next").disabled=offset+30>=data.total;
  }
  $("admin-login-form").onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{await api("/api/auth/login",{method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});e.target.reset();await load();status("已登入。");});};
  $("member-search").onsubmit=e=>{e.preventDefault();offset=0;task(e.submitter,load);};
  $("member-prev").onclick=()=>{offset=Math.max(0,offset-30);task(null,load);};
  $("member-next").onclick=()=>{offset+=30;task(null,load);};
  $("member-cancel").onclick=()=>{$("member-editor").hidden=true;selected=null;};
  $("member-change").oninput=()=>{pendingId=crypto.randomUUID();};
  $("member-change").onsubmit=e=>{e.preventDefault();task(e.submitter,async()=>{
    if(!selected)return;
    const plan=$("member-plan").value,action=$("member-action").value;
    const expires=plan==="monthly"?Date.parse($("member-expiry").value)/1000:null;
    if(action==="plan"&&plan==="monthly"&&!Number.isFinite(expires))throw new Error("請設定到期時間。");
    await api(`/api/admin/members/${selected.id}`,{method:"PATCH",body:JSON.stringify({action,plan,role:$("member-role").value,expires_at:expires,version:selected.version,reason:$("member-reason").value,request_id:pendingId})});
    $("member-editor").hidden=true;selected=null;await load();status("資格已更新，操作紀錄已保存。");
  });};
  task(null,load);
})();
