# SPDX-License-Identifier: AGPL-3.0-or-later
"""The web app's single token set and shared component styles.

``THEME_CSS`` holds the "editing room" palette (a warm charcoal base, an amber
accent for primary actions, teal only for clip blocks), the type, spacing and
radius tokens with a light and dark theme, and the component styles shared by
``serve.py``, ``explain.py`` and ``index.py``. ``PROGRESS_CSS`` styles the
progress bar with the accent token. No external fonts or CDNs.
"""

THEME_CSS = """
:root{color-scheme:dark light;
--water:#151619;--deck:#1e2024;--rope:#30333a;--stone:#efedea;--haze:#9c9b98;
--sea:#4fb0bd;--amber:#f0a53a;--keep:#9ec97a;--left:#e07a5f;--busy:#e0b64a;--hero:#e8c15a;
--bg:var(--water);--surface:var(--deck);--line:var(--rope);--text:var(--stone);
--muted:var(--haze);--accent:var(--amber);--clip:var(--sea);--ok:var(--keep);--warn:var(--busy);--danger:var(--left);
--fs-xs:.78rem;--fs-s:.88rem;--fs-m:1rem;--fs-l:1.25rem;--fs-xl:1.6rem;--fs-2xl:2rem;
--lh-tight:1.15;--lh-body:1.55;
--font-display:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--font-body:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--font-mono:ui-monospace,SFMono-Regular,Menlo,monospace;
--sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-5:24px;--sp-6:32px;
--radius-s:6px;--radius-l:14px;--radius-pill:999px;--tap:44px}
@media (prefers-color-scheme:light){:root{
--water:#f4f4f2;--deck:#ffffff;--rope:#dedcd8;--stone:#1b1c1f;--haze:#66655f;
--sea:#1f7f8c;--amber:#a8641a;--keep:#3d7a38;--left:#b0432e;--busy:#8a6410;--hero:#9a6a12}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);
font:var(--fs-m)/var(--lh-body) var(--font-body)}
a{color:var(--accent)}
a:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible,
textarea:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.wrap{max-width:760px;margin:0 auto;padding:0 var(--sp-4) var(--sp-6)}
.bar{display:flex;align-items:center;gap:var(--sp-4);padding:var(--sp-3) 0;margin-bottom:var(--sp-2);
border-bottom:1px solid var(--line)}
.brand-mark{display:inline-flex;align-items:center;flex:0 0 auto}
.brand-mark svg{width:26px;height:26px;display:block}
.mark{font-family:var(--font-display);font-size:var(--fs-l);font-weight:650;
color:var(--text);text-decoration:none;margin-right:auto;letter-spacing:.01em;
display:inline-flex;align-items:center;gap:var(--sp-2)}
.bar a.nav{color:var(--muted);text-decoration:none;min-height:var(--tap);
display:inline-flex;align-items:center}
.bar a.nav.on{color:var(--text)}
h1{font-family:var(--font-display);font-weight:680;letter-spacing:-.025em;
font-size:var(--fs-2xl);line-height:var(--lh-tight);margin:var(--sp-5) 0 var(--sp-2);overflow-wrap:anywhere;text-wrap:balance}
h2{font-family:var(--font-display);font-weight:650;letter-spacing:-.015em;font-size:var(--fs-l);margin:var(--sp-6) 0 var(--sp-3)}
.lede{color:var(--muted);margin:0 0 var(--sp-4);display:flex;flex-wrap:wrap;gap:var(--sp-1) var(--sp-4);align-items:center}
.num{font-variant-numeric:tabular-nums}
.projects{list-style:none;margin:var(--sp-2) 0 0;padding:0;display:grid;gap:var(--sp-3);
grid-template-columns:repeat(2,minmax(0,1fr))}
@media (min-width:720px){.projects{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (min-width:1000px){.projects{grid-template-columns:repeat(4,minmax(0,1fr))}}
.projects li{min-width:0}
.projects a.card{display:flex;flex-direction:column;text-decoration:none;color:inherit;
border:1px solid var(--line);border-radius:var(--radius-l);overflow:hidden;background:var(--surface)}
.projects .poster{display:block;aspect-ratio:16/9;background:var(--bg);overflow:hidden}
.projects .poster img{width:100%;height:100%;object-fit:cover;display:block}
.projects .poster.empty{background:repeating-linear-gradient(135deg,var(--surface) 0 10px,var(--line) 10px 20px)}
.projects .card-body{display:flex;flex-direction:column;gap:2px;padding:var(--sp-3)}
.projects .name{font-family:var(--font-display);font-weight:600;font-size:var(--fs-m);overflow-wrap:anywhere}
.projects .facts{color:var(--muted);font-size:var(--fs-s)}
.projects .date{color:var(--muted);font-size:var(--fs-xs)}
.projects .step-badge{align-self:flex-start;margin-top:var(--sp-1);padding:.1em .6em;
border-radius:var(--radius-pill);border:1px solid currentColor;color:var(--warn);
font-size:var(--fs-xs);font-weight:600}
.state{font-size:var(--fs-s);display:inline-flex;align-items:center;gap:6px}
.state::before{content:"";width:9px;height:9px;border-radius:50%;background:currentColor}
.badge-done{color:var(--ok)}.badge-running{color:var(--warn)}.badge-failed{color:var(--danger)}
.badge-stopped{color:var(--muted)}
.badge-running::before{animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.badge-running::before{animation:none}}
.empty{color:var(--muted)}
.tabs{display:flex;flex-wrap:wrap;gap:var(--sp-1);padding:var(--sp-1);margin:var(--sp-2) 0 var(--sp-4);
background:var(--surface);border-radius:var(--radius-l);width:fit-content;max-width:100%;overflow-x:auto}
.tabs a{min-height:var(--tap);display:inline-flex;align-items:center;padding:0 var(--sp-4);
border-radius:10px;color:var(--muted);text-decoration:none;white-space:nowrap}
.tabs a.active{background:var(--text);color:var(--bg)}
video{display:block;width:100%;aspect-ratio:16/9;background:#000;border-radius:var(--radius-l)}
video.vertical{aspect-ratio:9/16;max-width:360px;margin-top:var(--sp-3)}
.strip{margin:var(--sp-3) 0 var(--sp-1)}
.strip-track{display:flex;gap:3px;height:56px;overflow-x:auto}
.strip-track button{flex:var(--w) 1 0;min-width:44px;border:0;border-radius:var(--radius-s);
padding:0;font:inherit;font-size:.72rem;letter-spacing:-.02em;color:var(--bg);cursor:pointer;
background:var(--clip);background-size:cover;background-position:center;overflow:hidden;white-space:nowrap;
text-overflow:clip;text-align:center;opacity:.72;transition:opacity .15s;position:relative}
.strip-track button .n{position:relative;z-index:1;display:inline-flex;align-items:center;justify-content:center;
width:100%;height:100%;background:rgba(0,0,0,.35);color:#fff}
.strip-track button.photo{background:repeating-linear-gradient(135deg,var(--text) 0 6px,var(--line) 6px 12px)}
.strip-track button.now{opacity:1;box-shadow:inset 0 -4px 0 var(--bg)}
.strip-track button.picked{opacity:1;outline:3px solid var(--danger);outline-offset:-3px}
.shot-del{position:absolute;top:2px;right:2px;width:44px;height:44px;border-radius:50%;background:var(--danger);
display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:2}
.shot-del svg{width:13px;height:13px;fill:#fff}
.shot-ask{display:flex;align-items:center;gap:.6em;margin:.5em 0;flex-wrap:wrap}
.shot-ask button{border:0;border-radius:var(--radius-s);padding:.35em 1em;cursor:pointer;min-height:44px}
.shot-ask .yes{background:var(--danger);color:#fff}
.shot-ask .no{background:var(--surface);color:var(--text);border:1px solid var(--line)}
.shot-edit{display:flex;align-items:center;gap:.6em;margin:.5em 0;flex-wrap:wrap}
.shot-edit span{color:var(--muted);font-size:var(--fs-s)}
.shot-edit button{border:1px solid var(--line);border-radius:var(--radius-s);padding:.35em 1em;cursor:pointer;min-height:44px;background:var(--surface);color:var(--text)}
.strip-summary{display:flex;gap:var(--sp-4);margin-top:var(--sp-2);color:var(--muted);font-size:var(--fs-s)}
.download{display:inline-flex;align-items:center;min-height:var(--tap);margin-top:6px}
iframe{display:block;width:100%;height:78vh;border:0;border-radius:var(--radius-l);
background:var(--surface)}
details.reedit{margin-top:var(--sp-5);border-top:1px solid var(--line);padding-top:var(--sp-2)}
details.reedit summary{min-height:var(--tap);display:flex;align-items:center;cursor:pointer;
font-family:var(--font-display);font-size:var(--fs-l)}
form{display:grid;gap:var(--sp-3);max-width:520px;margin-top:var(--sp-2)}
form label{display:grid;gap:6px;font-size:.95rem}
.hint{color:var(--muted);font-size:var(--fs-s)}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:var(--sp-3)}
@media (max-width:420px){.pair{grid-template-columns:1fr}}
input,select,textarea{width:100%;min-height:var(--tap);background:var(--surface);color:var(--text);
border:1px solid var(--line);border-radius:10px;padding:var(--sp-2) var(--sp-3);font:inherit}
input[type=file]{padding:10px}
textarea{resize:vertical}
.notice{border-radius:var(--radius-l);padding:var(--sp-3) var(--sp-4);margin:0 0 var(--sp-3);background:var(--surface);
border-left:4px solid var(--warn)}
.notice.failed{border-left-color:var(--danger)}
.notice code{overflow-wrap:anywhere;font-size:var(--fs-s);color:var(--muted)}
.versions{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 var(--sp-3);color:var(--muted);font-size:var(--fs-s)}
.versions a{min-height:var(--tap);min-width:var(--tap);display:inline-flex;align-items:center;justify-content:center;
padding:0 var(--sp-3);border-radius:var(--radius-pill);border:1px solid var(--line);background:var(--surface);
color:var(--text);text-decoration:none}
.versions a.on{background:var(--accent);color:var(--bg);border-color:var(--accent)}
.versions a.failed{color:var(--danger)}
.versions a.on.failed{background:var(--danger);color:var(--bg);border-color:var(--danger)}
.brief-hints{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.brief-hints .chip{min-height:var(--tap);border:1px solid var(--line);border-radius:var(--radius-pill);
background:var(--surface);color:var(--muted);padding:0 var(--sp-3);font:inherit;font-size:var(--fs-s);cursor:pointer}
.brief-hints .chip:hover{color:var(--text)}
.consent-text{color:var(--muted);font-size:var(--fs-s);margin:0}
.consent{display:flex;align-items:center;gap:var(--sp-2);font-size:.95rem}
.consent input{width:auto;min-height:0}
.project-del{width:var(--tap);height:var(--tap);border:0;border-radius:50%;background:var(--danger);
display:inline-flex;align-items:center;justify-content:center;cursor:pointer;
vertical-align:middle;margin-left:10px}
.project-del svg{width:16px;height:16px;fill:#fff}
.player{margin-bottom:var(--sp-5)}
.player h2{font-family:var(--font-display);font-weight:500;font-size:var(--fs-l);margin:0 0 var(--sp-2)}
.vmeta{color:var(--muted);font-size:var(--fs-s);margin:6px 0 0;overflow-wrap:anywhere}
input.length-s{margin-top:var(--sp-2)}
input.length-s[hidden]{display:none}
button.go{min-height:48px;border:0;border-radius:12px;background:var(--accent);color:var(--bg);
font:inherit;font-weight:600;padding:0 var(--sp-5);cursor:pointer;justify-self:start}
button.ask{min-height:var(--tap);border:1px solid var(--line);border-radius:var(--radius-pill);
background:transparent;color:var(--text);font:inherit;padding:0 var(--sp-4);cursor:pointer}
.panel{display:block}
.storage,.credit{color:var(--muted);font-size:var(--fs-s);margin:0 0 var(--sp-2)}
.storage.warn,.credit.warn{color:var(--warn)}
.files-summary{color:var(--muted);font-size:var(--fs-s);margin:0 0 var(--sp-2)}
.files-new{color:var(--text);margin:0 0 var(--sp-3)}
.file-row{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-2) var(--sp-3);
padding:var(--sp-3) 0;border-top:1px solid var(--line)}
.file-row:last-of-type{border-bottom:1px solid var(--line)}
.file-name{flex:1 1 100%;overflow-wrap:anywhere}
.file-meta{color:var(--muted);font-size:var(--fs-s);font-variant-numeric:tabular-nums}
.file-state{color:var(--muted);font-size:var(--fs-s)}
form.file-row{display:inline-flex;align-items:center;border:0;padding:0;margin-left:auto}
.file-del{width:var(--tap);height:var(--tap);border:0;border-radius:50%;background:var(--danger);
display:inline-flex;align-items:center;justify-content:center;cursor:pointer}
.file-del svg{width:16px;height:16px;fill:#fff}
.bring-back{min-height:var(--tap);padding:0 var(--sp-3);border:1px solid var(--line);
border-radius:var(--radius-pill);background:transparent;color:var(--text);font:inherit;cursor:pointer}
.add-files{margin-top:var(--sp-4);border-top:1px solid var(--line);padding-top:var(--sp-2)}
.add-files summary{min-height:var(--tap);display:flex;align-items:center;cursor:pointer;
font-family:var(--font-display);font-size:var(--fs-l)}
.upload-progress{margin-top:var(--sp-3)}
.upload-status{color:var(--muted);font-size:var(--fs-s);margin:var(--sp-2) 0 0}
.reply{font-size:var(--fs-l);line-height:var(--lh-body);margin:0 0 var(--sp-2)}
.estimate{color:var(--muted);margin:0 0 var(--sp-4)}
.tick{font-variant-numeric:tabular-nums}
.brand-options{display:flex;flex-wrap:wrap;gap:var(--sp-4);margin:var(--sp-4) 0 0}
.brand-option{background:var(--surface);border-radius:var(--radius-l);padding:var(--sp-4);
display:flex;flex-direction:column;align-items:center;gap:var(--sp-2);min-width:120px}
.brand-option svg{width:48px;height:48px;display:block}
.brand-option .name{font-family:var(--font-mono);font-size:var(--fs-s);color:var(--muted)}
.brand-option .badge{color:var(--ok);font-size:var(--fs-s)}
.lede{max-width:60ch}
.balance{display:grid;gap:2px;padding:var(--sp-5) 0 var(--sp-4);border-bottom:1px solid var(--line)}
.balance-label{color:var(--muted);font-size:var(--fs-s)}
.balance-value{font-family:var(--font-display);font-weight:750;letter-spacing:-.045em;
font-size:clamp(3.4rem,14vw,5.6rem);line-height:.95}
.balance.minus .balance-value{color:var(--danger)}
.balance.plus .balance-value{color:var(--ok)}
.balance-note{color:var(--muted);font-size:var(--fs-s)}
.cost-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--sp-4);margin:var(--sp-4) 0 0}
.cost-stats div{display:grid;gap:2px;min-width:0}
.cost-stats dt{color:var(--muted);font-size:var(--fs-s)}
.cost-stats dd{margin:0;font-size:var(--fs-l);font-weight:600}
@media (max-width:420px){.cost-stats{grid-template-columns:1fr 1fr}}
.clip-costs{width:100%;border-collapse:collapse;margin:var(--sp-5) 0 0;font-size:var(--fs-s)}
.clip-costs caption{text-align:left;font-weight:650;font-size:var(--fs-m);padding-bottom:var(--sp-2)}
.clip-costs th,.clip-costs td{padding:var(--sp-3) var(--sp-2) var(--sp-3) 0;border-bottom:1px solid var(--line);text-align:left}
.clip-costs thead th{color:var(--muted);font-weight:500}
.clip-costs tbody th{font-weight:500;overflow-wrap:anywhere}
.clip-costs tbody th a{color:var(--text);text-decoration:none}
.clip-costs .cost{text-align:right;padding-right:0}
.cost-lines .why{display:block;color:var(--muted);font-size:var(--fs-xs);font-weight:400;margin-top:2px}
.cost-lines tfoot th,.cost-lines tfoot td{font-weight:700;font-size:var(--fs-m);border-bottom:0}
.costs-note{color:var(--muted);max-width:60ch;margin:var(--sp-3) 0 0}
.donation-list{list-style:none;margin:0;padding:0}
.donation-list li{display:grid;grid-template-columns:auto 1fr auto;gap:var(--sp-3);padding:var(--sp-3) 0;
border-bottom:1px solid var(--line);font-size:var(--fs-s)}
.donation-list .note{overflow-wrap:anywhere}
.donation-list .amount{color:var(--ok);font-weight:600}
.owner-time{display:grid;gap:2px;margin:var(--sp-5) 0 0;padding:var(--sp-4);border:1px dashed var(--line);border-radius:var(--radius-s)}
.owner-label,.owner-note{color:var(--muted);font-size:var(--fs-s)}
.owner-hours{font-size:var(--fs-xl);font-weight:700;letter-spacing:-.02em}
.owner-worth{font-size:var(--fs-m)}
.upload-form{max-width:560px;gap:var(--sp-4)}
.upload-form .drop{display:grid;justify-items:center;gap:var(--sp-1);cursor:pointer;
padding:var(--sp-6) var(--sp-4);border-radius:var(--radius-l);border:2px dashed var(--line);background:var(--surface);
text-align:center;transition:border-color .15s}
.upload-form .drop:hover,.upload-form .drop:focus-within{border-color:var(--accent)}
.drop-input{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}
.upload-form .drop .drop-icon{width:48px;height:48px;margin:0 0 var(--sp-2)}
.drop-title{font-weight:650;font-size:var(--fs-l);color:var(--text);letter-spacing:-.01em}
.upload-form .drop-hint{color:var(--muted);font-size:var(--fs-s);font-weight:400}
.upload-form .drop-list{width:100%}
.drop-limits{display:grid;gap:0;margin-top:var(--sp-3);color:var(--muted);font-size:var(--fs-xs);font-weight:400}
.drop-limits p{margin:0;font-size:inherit}
.upload-form .step{display:grid;gap:var(--sp-3)}
.upload-form .step h2{margin:var(--sp-2) 0 0;font-size:var(--fs-m)}
.upload-form .facts-card,.upload-form .brief-card{max-width:none;margin:0}
.upload-form .facts-card{grid-template-columns:1fr 1fr;gap:var(--sp-3);padding:0;border:0;background:none}
.upload-form .facts-card input{background:var(--surface)}
@media (max-width:440px){.upload-form .facts-card{grid-template-columns:1fr}}
.upload-form details.options{border-top:1px solid var(--line);padding-top:var(--sp-2)}
.upload-form details.options summary{min-height:var(--tap);display:flex;align-items:center;justify-content:space-between;
cursor:pointer;font-weight:650}
.options-state{color:var(--muted);font-weight:400;font-size:var(--fs-s)}
.upload-form button.start{justify-self:stretch;min-height:52px;font-size:var(--fs-m)}
.project-del{background:transparent;border:1px solid var(--line)}
.project-del svg{fill:var(--muted)}
.project-del:hover{border-color:var(--danger)}
.project-del:hover svg{fill:var(--danger)}
.meta-box .meta-field button.copy{order:2;margin-left:auto}
.limits{display:grid;gap:2px;margin:var(--sp-5) 0 0;padding:var(--sp-3) var(--sp-4);
background:var(--surface);border-radius:var(--radius-s)}
.limits p{margin:0}
.donate p,.requests p{color:var(--muted);max-width:60ch;margin:0 0 var(--sp-3)}
.donate-links{display:flex;flex-wrap:wrap;gap:var(--sp-2)}
.donate-link{min-height:var(--tap);display:inline-flex;align-items:center;padding:0 var(--sp-5);
border-radius:12px;background:var(--accent);color:var(--bg);font-weight:650;text-decoration:none}
.donate-link+.donate-link{background:transparent;color:var(--text);border:1px solid var(--line)}
form label.check{display:flex;align-items:center;gap:var(--sp-2)}
form label.check input{width:20px;height:20px;min-height:0;accent-color:var(--accent)}
.mark,.bar a.nav{white-space:nowrap}
@media (max-width:440px){.bar{gap:var(--sp-3)}.bar a.nav{font-size:var(--fs-s)}.mark{font-size:var(--fs-m)}}
.chat{margin:var(--sp-5) 0;border:1px solid var(--line);border-radius:var(--radius-l);background:var(--surface);padding:var(--sp-4)}
.chat h2{margin-top:0}
.chat-msg{display:flex;gap:var(--sp-2);align-items:flex-start;margin:var(--sp-2) 0}
.chat-msg p{margin:0}
.chat-msg.user{justify-content:flex-end}
.chat-msg.user p{background:var(--bg);border:1px solid var(--line);border-radius:var(--radius-l);padding:var(--sp-2) var(--sp-3);max-width:80%}
.chat-msg.director p{background:var(--deck);border:1px solid var(--line);border-radius:var(--radius-l);padding:var(--sp-2) var(--sp-3);max-width:80%}
.chat-shot{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--muted);border:1px solid var(--line);border-radius:var(--radius-pill);padding:0 var(--sp-2);min-width:var(--tap);text-align:center}
.chat-estimate{color:var(--muted);font-size:var(--fs-xs)}
.chat-form,.chat-approve{display:flex;gap:var(--sp-2);align-items:flex-end;margin-top:var(--sp-3)}
.chat-form textarea{flex:1}
.chat-earlier{margin:var(--sp-2) 0}
.chat-earlier summary{cursor:pointer;color:var(--muted)}
.stop-run{margin-top:var(--sp-3)}
"""

PROGRESS_CSS = (
    "progress{width:100%;height:10px;accent-color:var(--accent);border-radius:999px;display:block;margin:8px 0 0}\n"
    "progress::-webkit-progress-value{background:var(--accent);border-radius:999px}\n"
    "progress::-moz-progress-bar{background:var(--accent);border-radius:999px}\n"
)
