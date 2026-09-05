"use strict";
const $ = (id) => document.getElementById(id);
let state = {holdings: [], consented: false}, draft = null, setupRequired = false;
const number = (v) => v === null || v === undefined ? "—" : new Intl.NumberFormat("zh-TW", {maximumFractionDigits: 2}).format(Number(v));
function message(text, error=false) { $("status").textContent = text; $("status").className = error ? "error" : ""; }
async function api(path, options={}) {
  const headers = {"X-Equity-Request": "1", ...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers, credentials:"same-origin"});
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) { $("login").hidden=false; $("workspace").hidden=true; }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "操作未完成，請檢查輸入後重試。");
  return data;
}
function element(tag, text, cls) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(cls)el.className=cls; return el; }
async function task(button, action) { if(button)button.disabled=true; try { await action(); } catch(e) {message(e.message,true);} finally {if(button)button.disabled=false;} }
function tab(name) { document.querySelectorAll(".tab-page").forEach(p => p.hidden=p.id!=="tab-"+name); document.querySelectorAll("[data-tab]").forEach(b => b.classList.toggle("active",b.dataset.tab===name)); if(name==="news") task(null,loadNews); }
function openAdd() { $("add-panel").hidden=false; $("add-panel").scrollIntoView({behavior:"smooth",block:"nearest"}); }
function render() {
  $("consent").hidden=state.consented;
  $("empty").hidden=state.holdings.length>0;
  $("holdings-meta").textContent=state.holdings.length ? state.holdings.length+" 檔已確認持股 · 收盤資料日期顯示於各卡片" : "只顯示你已核對確認的紀錄";
  $("holdings").replaceChildren(); $("chat-code").replaceChildren();
  for(const h of state.holdings) {
    const card=element("article",undefined,"holding-card"), head=element("div",undefined,"holding-head");
    head.append(element("span",h.code,"stock-code"),element("span","已確認","tag"));
    card.append(head,element("div",number(h.close),"holding-price"),element("div",h.data_date ? h.data_date+" 最近收盤 · TWD" : "收盤資料尚未齊備","subtle"));
    const dl=element("dl");
    for(const [label,value,cls] of [["持有股數",number(h.quantity)+" 股"],["平均成本",number(h.average_cost)],["估算未實現損益",number(h.unrealized_pnl),Number(h.unrealized_pnl)>0?"gain":Number(h.unrealized_pnl)<0?"loss":""]]) {dl.append(element("dt",label),element("dd",value,cls));}
    card.append(dl);
    const actions=element("div",undefined,"holding-actions"), analyze=element("button","詢問分析 →"), detail=element("a","完整行情"), remove=element("button","移除");
    detail.href="/stock/"+encodeURIComponent(h.code); detail.className="text-link";
    analyze.onclick=()=>{tab("research");$("chat-code").value=h.code;$("chat-question").focus();};
    remove.onclick=()=>{if(confirm("移除 "+h.code+" 的持股紀錄？")) task(remove,async()=>{await api("/api/portfolio/holdings/"+h.code,{method:"DELETE"});await refresh();message("持股已移除。");});};
    actions.append(analyze,detail,remove);card.append(actions);$("holdings").append(card);
    const option=element("option",h.code);option.value=h.code;$("chat-code").append(option);
  }
}
async function refresh() {state=await api("/api/portfolio");$("login").hidden=true;$("workspace").hidden=false;render();}
function showDraft(data) {
  draft=data; $("draft-panel").hidden=false;$("draft-rows").replaceChildren();
  for(const row of data.holdings) {
    const wrapper=element("div",undefined,"draft-row");
    for(const [key,title] of [["code","代號"],["quantity","數量"],["unit","單位"],["average_cost","每股平均成本"]]) {
      const label=element("label",title);let input;
      if(key==="unit") {input=element("select");for(const [v,t] of [["unknown","請選擇"],["shares","股"],["lots","張"]]){const opt=element("option",t);opt.value=v;input.append(opt);}}
      else {input=element("input");input.inputMode=key==="code"?"numeric":"decimal";}
      input.dataset.field=key;input.value=row[key]===null?"":String(row[key]||"");label.append(input);wrapper.append(label);
    }
    $("draft-rows").append(wrapper);
  }
  $("draft-panel").scrollIntoView({behavior:"smooth",block:"start"});
}
$("login-form").onsubmit=(e)=>{e.preventDefault();task(e.submitter,async()=>{if(setupRequired){await api("/api/setup",{method:"POST",body:JSON.stringify({email:$("email").value,password:$("password").value})});setupRequired=false;}await api("/api/auth/login",{method:"POST",body:JSON.stringify({email:$("email").value,password:$("password").value})});$("password").value="";await refresh();message("已登入。");});};
$("accept-consent").onclick=()=>task($("accept-consent"),async()=>{await api("/api/portfolio/consent",{method:"POST",body:JSON.stringify({version:state.privacy_version,accepted:true})});await refresh();message("可以開始新增持股了。");});
document.querySelectorAll("[data-tab]").forEach(b=>b.onclick=()=>tab(b.dataset.tab));
$("show-add").onclick=openAdd;$("empty-add").onclick=openAdd;$("close-add").onclick=()=>$("add-panel").hidden=true;
$("holding-form").onsubmit=(e)=>{e.preventDefault();task(e.submitter,async()=>{const row=Object.fromEntries(new FormData(e.target));if(!row.average_cost)row.average_cost=null;showDraft(await api("/api/portfolio/drafts",{method:"POST",body:JSON.stringify({holdings:[row]})}));message("請核對後按確認保存。");});};
$("image-upload").onchange=()=>task(null,async()=>{const file=$("image-upload").files[0];if(!file)return;if(file.size>8*1024*1024)throw new Error("圖片超過 8 MiB，請縮小後再上傳。");const data=new FormData();data.append("file",file);message("正在辨識持股截圖，請稍候…");showDraft(await api("/api/portfolio/image",{method:"POST",body:data}));message("辨識完成，請核對每一筆資料。");$("image-upload").value="";});
$("cancel-draft").onclick=()=>{draft=null;$("draft-panel").hidden=true;};
$("confirm-draft").onclick=()=>task($("confirm-draft"),async()=>{if(!draft)return;const rows=[...$("draft-rows").children].map(el=>Object.fromEntries([...el.querySelectorAll("[data-field]")].map(i=>[i.dataset.field,i.value||null])));await api("/api/portfolio/confirm",{method:"POST",body:JSON.stringify({draft_id:draft.draft_id,holdings:rows})});draft=null;$("draft-panel").hidden=true;$("add-panel").hidden=true;await refresh();message("持股已保存。");});
document.querySelectorAll("[data-question]").forEach(b=>b.onclick=()=>$("chat-question").value=b.dataset.question);
$("chat-form").onsubmit=(e)=>{e.preventDefault();task(e.submitter,async()=>{if(!$("chat-code").value)throw new Error("請先新增持股。");$("chat-answer").textContent="正在整理資料與分析，請稍候…";let data;try {data=await api("/api/portfolio/chat",{method:"POST",body:JSON.stringify({code:$("chat-code").value,question:$("chat-question").value})});} catch(error){$("chat-answer").textContent="分析未完成，請稍後再試。";throw error;}$("chat-answer").textContent=data.answer;message("");});};
async function loadNews() {const data=await api("/api/portfolio/news");$("news-items").replaceChildren();if(!data.items.length){$("news-items").append(element("p","目前尚無新聞資料；完成新聞更新後會顯示於此。","panel"));return;}for(const item of data.items){const card=element("article",undefined,"news-item"),meta=element("div",undefined,"news-meta");meta.append(element("span",item.event_date),element("span",item.publisher));if(item.permanent)meta.append(element("span","永久保存","tag"));card.append(meta,element("h3",item.title));if(item.source_url){const a=element("a","查閱原始來源 ↗");a.href=item.source_url;a.target="_blank";a.rel="noopener noreferrer";card.append(a);}$("news-items").append(card);}}
$("refresh-news").onclick=()=>task($("refresh-news"),loadNews);
$("link-line").onclick=()=>task($("link-line"),async()=>{const data=await api("/api/portfolio/link",{method:"POST"});$("link-result").textContent=data.command+"\n\n請複製這行文字，私訊給 LINE 機器人。";});
$("export-data").onclick=()=>{const blob=new Blob([JSON.stringify({exported_at:new Date().toISOString(),holdings:state.holdings},null,2)],{type:"application/json"});const url=URL.createObjectURL(blob),a=element("a");a.href=url;a.download="my-holdings.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$("delete-data").onclick=()=>{if(confirm("確定刪除全部持股、草稿與 LINE 綁定？此操作無法復原。"))task($("delete-data"),async()=>{await api("/api/portfolio",{method:"DELETE"});await refresh();message("全部持股資料已刪除。");});};
$("logout").onclick=()=>task($("logout"),async()=>{await api("/api/auth/logout",{method:"POST"});$("workspace").hidden=true;$("login").hidden=false;state={holdings:[]};message("已登出。");});
task(null,async()=>{
 const setup=await api("/api/setup");setupRequired=setup.setup_required;
 if(setupRequired){$("login").hidden=false;$("login").querySelector("h2").textContent="建立你的研究帳號";$("login").querySelector(".subtle").textContent="首次設定只需在這台主機完成一次。請設定 Email 與至少 10 字元的混合密碼；不會發送 Email。";$("login-form").querySelector("button").textContent="建立帳號並登入";}
 else await refresh();
});
