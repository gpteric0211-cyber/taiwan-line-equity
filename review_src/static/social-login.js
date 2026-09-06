"use strict";
(() => {
 const status=text=>{const el=document.getElementById("social-status")||document.getElementById("account-status")||document.getElementById("status");if(el)el.textContent=text;};
 async function api(path,body){
  const response=await fetch("/api/auth/social/"+path,{method:body?"POST":"GET",credentials:"same-origin",headers:{"Content-Type":"application/json","X-Equity-Request":"1"},...(body?{body:JSON.stringify(body)}:{})});
  const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"操作未完成");return data;
 }
 async function render(linking=false){
  try{
   const options=await api("options"), links=linking?(await api("links")).items:[];
   const containers=linking?[document.getElementById("social-links")]:[...document.querySelectorAll(".social-login:not(#social-links)")];
   for(const container of containers){if(!container)continue;container.replaceChildren();
    for(const item of options.items){const bound=links.includes(item.provider),button=document.createElement("button");
     button.type="button";button.dataset.provider=item.provider;button.disabled=!item.enabled&&!bound;
     button.textContent=(linking?(bound?"解除綁定 ":"綁定 "):"使用 ")+item.label+(linking?"":" 登入")+(!item.enabled&&!bound?"（尚未開通）":"");
     button.onclick=async()=>{button.disabled=true;try{
      const input=document.getElementById("social-password"),password=linking?input.value:"";
      if(linking&&!password)throw new Error("請輸入目前密碼確認綁定操作");
      if(bound){await api(item.provider+"/unlink",{password});input.value="";status("已解除綁定");await render(true);}
      else{const data=await api(item.provider+"/start",{action:linking?"link":"login",password});if(input)input.value="";location.assign(data.url);}
     }catch(error){status(error.message);}finally{button.disabled=false;}};
     container.append(button);
    }
   }
  }catch(error){status(error.message);}
 }
 window.addEventListener("equity-account-ready",()=>render(true));render();
})();
