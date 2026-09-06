"use strict";
(() => {
 const params=new URLSearchParams(location.search), path=location.pathname.replace(/\/return$/,"/callback");
 history.replaceState(null,"",location.pathname);
 const form=document.createElement("form");form.method="POST";form.action=path;
 for(const key of ["code","state"]){const input=document.createElement("input");input.type="hidden";input.name=key;input.value=params.get(key)||"";form.append(input);}
 document.body.append(form);form.submit();
})();
