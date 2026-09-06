"use strict";
(() => {
  let token = "";
  const form = document.getElementById("password-form"), status = document.getElementById("password-status");
  function readLink() {
    token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
    history.replaceState(null, "", location.pathname);
    const valid = /^[A-Za-z0-9_-]{43}$/.test(token);
    form.reset(); form.hidden = !valid;
    document.getElementById("password-login").hidden = valid;
    status.textContent = valid ? "請輸入新密碼，完成後重新登入。" : "請從 Email 開啟修改密碼連結；連結已使用或過期時，請登入後臺重新寄送。";
  }
  readLink();
  window.addEventListener("hashchange", readLink);
  form.onsubmit = async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    if (values.new_password !== values.confirm_password) { status.textContent = "兩次新密碼輸入不一致"; return; }
    const button = event.submitter;
    button.disabled = true;
    try {
      const response = await fetch("/api/admin/password/complete", {method:"POST", credentials:"same-origin", cache:"no-store", headers:{"Content-Type":"application/json", "X-Equity-Request":"1"}, body:JSON.stringify({...values,token})});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "請檢查新密碼格式或重新寄送連結。");
      token = ""; form.reset(); form.hidden = true;
      status.textContent = data.message;
      document.getElementById("password-login").hidden = false;
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  };
})();
