from __future__ import annotations

"""Local-only HTML control panel for the TradingView -> MT5 DEMO bridge."""

from fastapi.responses import HTMLResponse


def control_panel_html() -> HTMLResponse:
    return HTMLResponse(
        r'''<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>پنل اتصال TradingView و MT5 Demo</title>
<style>
:root{--bg:#0b1020;--card:#121a2e;--card2:#17223c;--line:#263656;--text:#eef4ff;--muted:#9fb0ca;--ok:#27c499;--warn:#f6b84b;--bad:#ff6b7a;--accent:#66a3ff}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(160deg,#08111f,#10192c 55%,#0a1020);color:var(--text);font-family:Tahoma,Segoe UI,Arial,sans-serif;min-height:100vh}
header{padding:24px clamp(16px,4vw,48px);border-bottom:1px solid #1c2944;background:#0b1324cc;backdrop-filter:blur(10px);position:sticky;top:0;z-index:5}
h1{font-size:22px;margin:0 0 8px}.sub{color:var(--muted);font-size:13px}
main{max-width:1180px;margin:22px auto;padding:0 16px 50px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}
.card{grid-column:span 6;background:linear-gradient(180deg,var(--card),#10182a);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 14px 35px #0004}.full{grid-column:1/-1}
@media(max-width:900px){.card{grid-column:1/-1}}
.row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}@media(max-width:620px){.row{grid-template-columns:1fr}}
label{display:block;color:#c7d5ec;font-size:12px;margin:10px 0 6px}input,textarea,select{width:100%;background:#0a1222;border:1px solid #334769;color:white;border-radius:10px;padding:11px;outline:none}textarea{min-height:90px;resize:vertical;font-family:Consolas,monospace;direction:ltr;text-align:left}input:focus,textarea:focus{border-color:var(--accent)}
button{border:0;border-radius:10px;padding:10px 14px;font-weight:700;cursor:pointer;margin:8px 4px 0 0}.primary{background:var(--accent);color:#07111f}.ok{background:var(--ok);color:#041812}.warn{background:var(--warn);color:#2b1b00}.danger{background:#b83b4c;color:white}.ghost{background:#202d49;color:#dce8fb;border:1px solid #314568}
.badge{display:inline-flex;align-items:center;gap:7px;padding:7px 10px;border-radius:999px;background:#202d49;font-size:12px;color:#d8e6fb}.dot{width:8px;height:8px;border-radius:50%;background:#66758c}.dot.on{background:var(--ok);box-shadow:0 0 12px var(--ok)}.dot.off{background:var(--bad)}
.statusline{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.kv{display:grid;grid-template-columns:140px 1fr;gap:6px;border-top:1px solid #22304c;padding-top:10px;margin-top:10px;font-size:13px}.muted{color:var(--muted)}code{direction:ltr;unicode-bidi:embed;background:#08101e;border:1px solid #243551;border-radius:7px;padding:2px 6px;color:#cbe2ff}.codebox{direction:ltr;text-align:left;background:#07101e;border:1px solid #273958;border-radius:11px;padding:12px;word-break:break-all;font:12px Consolas,monospace;color:#bfe0ff;min-height:44px}
table{width:100%;border-collapse:collapse;font-size:12px;direction:ltr;text-align:left}th,td{padding:8px;border-bottom:1px solid #23314d}th{color:#a9b9d2}.note{background:#0e2032;border:1px solid #244869;border-radius:12px;padding:12px;color:#cde4ff;font-size:12px}.error{color:#ff9aa6}.success{color:#67dfbb}.toolbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.title2{font-size:16px;margin:0 0 5px}.tiny{font-size:11px;color:var(--muted)}
</style>
</head>
<body>
<header>
  <h1>پنل اتصال TradingView ↔ Python ↔ MetaTrader 5 Demo</h1>
  <div class="sub">اجرای پول واقعی عمداً مسدود است. این پنل فقط حساب DEMO متاتریدر را می‌پذیرد.</div>
</header>
<main>
<div class="grid">
<section class="card">
  <h2 class="title2">۱) اتصال MetaTrader 5 Demo</h2>
  <div class="statusline">
    <span class="badge"><span id="mt5Dot" class="dot off"></span><span id="mt5State">قطع</span></span>
    <span class="badge">ارسال Demo: <b id="submitState">خاموش</b></span>
  </div>
  <div class="row">
    <div><label>Login حساب Demo</label><input id="login" inputmode="numeric" placeholder="مثلاً 12345678"></div>
    <div><label>Demo Server</label><input id="server" placeholder="نام دقیق سرور Demo بروکر"></div>
  </div>
  <label>Password حساب Demo</label><input id="password" type="password" autocomplete="off" placeholder="فقط در حافظه فرایند استفاده می‌شود">
  <label>مسیر terminal64.exe</label><input id="terminal" dir="ltr" placeholder="C:\Program Files\MetaTrader 5\terminal64.exe">
  <div class="row">
    <div><label>حداکثر Notional</label><input id="maxNotional" type="number" value="5000" min="1"></div>
    <div><label>حداکثر Spread (bps)</label><input id="maxSpread" type="number" value="35" min="0.1" step="0.1"></div>
  </div>
  <label>نمادهای مجاز (با ویرگول)</label>
  <input id="allowed" dir="ltr" value="BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT">
  <label>Symbol Map — JSON</label>
  <textarea id="symbolMap">{"BTC/USDT":"BTCUSD","ETH/USDT":"ETHUSD","SOL/USDT":"SOLUSD","XRP/USDT":"XRPUSD","DOGE/USDT":"DOGEUSD"}</textarea>
  <div class="toolbar">
    <button class="primary" onclick="connectMT5()">اتصال به MT5 Demo</button>
    <button class="danger" onclick="disconnectMT5()">قطع اتصال</button>
    <button class="ghost" onclick="refreshStatus()">به‌روزرسانی</button>
  </div>
  <div id="accountBox" class="kv"><span class="muted">وضعیت</span><span>هنوز متصل نشده</span></div>
</section>

<section class="card">
  <h2 class="title2">۲) کنترل اجرای Demo</h2>
  <div class="note">ابتدا Dry‑Run را نگه دار. وقتی اتصال، Symbol Mapping و TradingView webhook را تست کردی، ارسال Demo را روشن کن.</div>
  <div class="toolbar" style="margin-top:12px">
    <button class="warn" onclick="setSubmit(false)">Dry‑Run / توقف ارسال</button>
    <button class="ok" onclick="setSubmit(true)">فعال‌سازی سفارش Demo</button>
  </div>
  <hr style="border:0;border-top:1px solid #263656;margin:18px 0">
  <h3 class="title2">جستجوی نمادهای بروکر</h3>
  <div class="row">
    <input id="symbolQuery" dir="ltr" placeholder="BTC یا ETH یا USD">
    <button class="ghost" onclick="searchSymbols()">جستجو</button>
  </div>
  <div id="symbolsBox" class="codebox" style="margin-top:10px">بعد از اتصال قابل استفاده است.</div>
</section>

<section class="card full">
  <h2 class="title2">۳) اتصال TradingView</h2>
  <div class="row">
    <div>
      <label>Public HTTPS Base URL</label>
      <input id="publicBase" dir="ltr" placeholder="https://your-domain.example">
      <div class="tiny">این URL باید از اینترنت توسط TradingView قابل دسترس باشد و به همین سرویس برسد.</div>
    </div>
    <div>
      <label>Webhook Route Token</label>
      <div class="toolbar">
        <input id="routeToken" dir="ltr" readonly>
        <button class="ghost" onclick="regenToken()">توکن جدید</button>
      </div>
    </div>
  </div>
  <div class="toolbar">
    <button class="primary" onclick="configureTradingView()">ذخیره در حافظه سرویس</button>
    <button class="ghost" onclick="copyText('webhookUrl')">کپی Webhook URL</button>
  </div>
  <label>Webhook URL برای TradingView</label>
  <div id="webhookUrl" class="codebox">ابتدا Public URL را وارد کن.</div>

  <label>نمونه JSON Alert</label>
  <div id="sampleJson" class="codebox"></div>
  <div class="toolbar">
    <button class="ghost" onclick="copyText('sampleJson')">کپی JSON</button>
    <button class="warn" onclick="localDryRunTest()">تست محلی Dry‑Run</button>
  </div>

  <div class="note" style="margin-top:12px">
    در TradingView: Create Alert → Webhook URL را از بالا Paste کن. اسکریپت آماده Pine در فایل
    <code>tradingview/mt5_demo_bridge_test.pine</code> قرار دارد.
  </div>
</section>

<section class="card full">
  <h2 class="title2">۴) رویدادهای اخیر</h2>
  <div class="toolbar"><button class="ghost" onclick="loadEvents()">بروزرسانی رویدادها</button></div>
  <div style="overflow:auto;margin-top:10px">
    <table>
      <thead><tr><th>type</th><th>event_id</th><th>time</th><th>status/error</th></tr></thead>
      <tbody id="eventsBody"><tr><td colspan="4">هنوز رویدادی نیست.</td></tr></tbody>
    </table>
  </div>
</section>

<section class="card full">
  <div id="msg" class="muted">پنل آماده است.</div>
</section>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
function msg(t,ok=true){$('msg').textContent=t;$('msg').className=ok?'success':'error'}
async function api(url,opts={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opts});let j={};try{j=await r.json()}catch(e){}if(!r.ok)throw new Error(j.detail||('HTTP '+r.status));return j}
function sample(){
  const now=new Date().toISOString().replace('.000','');
  return JSON.stringify({
    event_id:'BTCUSDT-'+Date.now()+'-LONG',
    symbol:'BTC/USDT',action:'BUY',quantity:0.01,
    reference_price:62000,stop_loss:61000,take_profit:64000,
    event_time:now,strategy_version:'ICHIMOKU_ICT_BROOKS_V59',
    model_version:'champion-001',metadata:{timeframe:'4h'}
  },null,2)
}
$('sampleJson').textContent=sample();
async function refreshStatus(){
  try{
    const s=await api('/api/ui/status');
    $('mt5Dot').className='dot '+(s.mt5.connected?'on':'off');
    $('mt5State').textContent=s.mt5.connected?'متصل DEMO':'قطع';
    $('submitState').textContent=s.mt5.submission_enabled?'روشن':'خاموش';
    $('routeToken').value=s.tradingview.route_token||'';
    $('publicBase').value=s.tradingview.public_base_url||'';
    $('webhookUrl').textContent=s.tradingview.webhook_url||'Public HTTPS URL را وارد کن.';
    if(s.mt5.connected){
      const a=s.mt5;
      $('accountBox').innerHTML =
        '<span class="muted">Login</span><span>'+a.login+'</span>'+
        '<span class="muted">Server</span><span>'+a.server+'</span>'+
        '<span class="muted">Balance</span><span>'+a.balance+' '+a.currency+'</span>'+
        '<span class="muted">Equity</span><span>'+a.equity+' '+a.currency+'</span>'+
        '<span class="muted">Broker</span><span>'+a.company+'</span>';
    }else $('accountBox').innerHTML='<span class="muted">وضعیت</span><span>متصل نیست</span>';
  }catch(e){msg(e.message,false)}
}
async function connectMT5(){
  try{
    const body={
      login:$('login').value.trim(),password:$('password').value,
      server:$('server').value.trim(),terminal_path:$('terminal').value.trim(),
      max_order_notional:Number($('maxNotional').value),
      max_spread_bps:Number($('maxSpread').value),
      allowed_symbols:$('allowed').value.split(',').map(x=>x.trim()).filter(Boolean),
      symbol_map:JSON.parse($('symbolMap').value)
    };
    const r=await api('/api/ui/mt5/connect',{method:'POST',body:JSON.stringify(body)});
    $('password').value='';
    msg('اتصال MT5 Demo برقرار شد.');
    await refreshStatus();
  }catch(e){msg('خطا در اتصال MT5: '+e.message,false)}
}
async function disconnectMT5(){try{await api('/api/ui/mt5/disconnect',{method:'POST',body:'{}'});msg('اتصال MT5 قطع شد.');await refreshStatus()}catch(e){msg(e.message,false)}}
async function setSubmit(enabled){try{await api('/api/ui/demo/submission',{method:'POST',body:JSON.stringify({enabled})});msg(enabled?'ارسال سفارش Demo فعال شد.':'Dry‑Run فعال شد؛ سفارش ارسال نمی‌شود.');await refreshStatus()}catch(e){msg(e.message,false)}}
async function searchSymbols(){try{const q=encodeURIComponent($('symbolQuery').value.trim());const r=await api('/api/ui/mt5/symbols?q='+q);$('symbolsBox').textContent=r.items.join(', ')||'نتیجه‌ای نبود'}catch(e){msg(e.message,false)}}
async function regenToken(){try{const r=await api('/api/ui/tradingview/token',{method:'POST',body:'{}'});$('routeToken').value=r.route_token;msg('توکن جدید ساخته شد؛ Webhook URL قبلی دیگر معتبر نیست.');await configureTradingView()}catch(e){msg(e.message,false)}}
async function configureTradingView(){try{const r=await api('/api/ui/tradingview/configure',{method:'POST',body:JSON.stringify({public_base_url:$('publicBase').value.trim()})});$('webhookUrl').textContent=r.webhook_url||'Public URL تنظیم نشده';$('routeToken').value=r.route_token;msg('تنظیم TradingView آماده شد.')}catch(e){msg(e.message,false)}}
function copyText(id){navigator.clipboard.writeText($(id).textContent);msg('کپی شد.')}
async function localDryRunTest(){
  try{
    const p=JSON.parse($('sampleJson').textContent);
    const r=await api('/api/ui/tradingview/dry-run-test',{method:'POST',body:JSON.stringify(p)});
    msg('Dry‑Run محلی ثبت شد: '+r.status);
    $('sampleJson').textContent=sample();
    await loadEvents();
  }catch(e){msg(e.message,false)}
}
async function loadEvents(){
  try{
    const r=await api('/api/ui/events?limit=30');
    const b=$('eventsBody');b.innerHTML='';
    if(!r.items.length){b.innerHTML='<tr><td colspan="4">رویدادی نیست.</td></tr>';return}
    r.items.slice().reverse().forEach(x=>{
      const tr=document.createElement('tr');
      const t=x.completed_at||x.received_at||'';
      const info=x.error||(x.result&&x.result.status)||'';
      tr.innerHTML='<td>'+String(x.record_type||'')+'</td><td>'+String(x.event_id||'')+'</td><td>'+String(t)+'</td><td>'+String(info)+'</td>';
      b.appendChild(tr)
    })
  }catch(e){msg(e.message,false)}
}
refreshStatus();loadEvents();setInterval(refreshStatus,10000);
</script>
</body></html>'''
    )
